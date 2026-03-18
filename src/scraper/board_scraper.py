"""
LINE WORKS 사내게시판 크롤러.

공지사항, 사내업무가이드/규정및서식 게시판에서 게시글을 크롤링하여
data/board_documents.json에 저장한다.
인라인 이미지와 첨부파일(PDF 등)도 추출하며,
R2(S3) 업로드 + 이미지 설명 생성 + 벡터화까지 수행한다.
"""

import hashlib
import json
import os
import re
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page

load_dotenv()

# ── 설정 ──

BOARDS = {
    "공지사항": "https://board.worksmobile.com/main/board/4070000000141270881",
    "사내업무가이드, 규정및 서식": "https://board.worksmobile.com/main/board/4070000000141270911",
}

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
OUTPUT_PATH = DATA_DIR / "board_documents.json"
AUTH_STATE_PATH = DATA_DIR / ".auth_state.json"

PAGE_DELAY = 2.0
LOGIN_TIMEOUT = 180_000  # 수동 2FA/OTP 대기 시간 (ms) — 3분


# ── 인증 ──

def _login(page: Page) -> bool:
    """LINE WORKS에 로그인한다. 세션 재사용 우선."""
    lineworks_id = os.environ.get("LINEWORKS_ID", "")
    lineworks_pw = os.environ.get("LINEWORKS_PW", "")

    if not lineworks_id or not lineworks_pw:
        print("[error] LINEWORKS_ID, LINEWORKS_PW 환경변수를 설정하세요.")
        return False

    # 세션 복원 시도
    if AUTH_STATE_PATH.exists():
        try:
            with open(AUTH_STATE_PATH, encoding="utf-8") as f:
                cookies = json.load(f)
            page.context.add_cookies(cookies)
            page.goto(list(BOARDS.values())[0], wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            # 로그인 페이지로 리다이렉트되지 않았으면 세션 유효
            if "login" not in page.url.lower() and "auth" not in page.url.lower():
                print("[auth] 저장된 세션으로 로그인 성공")
                return True
            print("[auth] 저장된 세션 만료, 재로그인 필요")
        except Exception as e:
            print(f"[auth] 세션 복원 실패: {e}")

    # 로그인 페이지로 이동
    page.goto(list(BOARDS.values())[0], wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # 이미 로그인 상태면 스킵
    if "login" not in page.url.lower() and "auth" not in page.url.lower():
        print("[auth] 이미 로그인 상태")
        _save_auth_state(page)
        return True

    print(f"[auth] 로그인 시도: {lineworks_id}")
    try:
        # ID 입력
        id_input = page.wait_for_selector(
            'input[type="text"], input[name="userId"], input#userId',
            timeout=10000,
        )
        if id_input:
            id_input.fill(lineworks_id)

        # PW 입력
        pw_input = page.wait_for_selector(
            'input[type="password"], input[name="password"], input#password',
            timeout=5000,
        )
        if pw_input:
            pw_input.fill(lineworks_pw)

        # 로그인 버튼 클릭
        login_btn = page.query_selector(
            'button[type="submit"], input[type="submit"], .btn_login, .login-btn'
        )
        if login_btn:
            login_btn.click()
        else:
            pw_input.press("Enter")

        # 2FA/OTP 등 추가 인증 대기
        print(f"[auth] 로그인 처리 중... (최대 {LOGIN_TIMEOUT // 1000}초 대기)")
        page.wait_for_timeout(5000)

        # 로그인 완료 확인: 게시판 URL이면 성공
        if "login" not in page.url.lower() and "auth" not in page.url.lower():
            print("[auth] 자동 로그인 성공")
            _save_auth_state(page)
            return True

        # 수동 인증 대기 (2FA, OTP 등)
        print("[auth] 추가 인증이 필요합니다. 브라우저에서 수동으로 완료하세요.")
        try:
            page.wait_for_url(
                re.compile(r"board\.worksmobile\.com/main/board/"),
                timeout=LOGIN_TIMEOUT,
            )
            print("[auth] 수동 인증 완료")
            _save_auth_state(page)
            return True
        except Exception:
            print("[error] 로그인 타임아웃")
            return False

    except Exception as e:
        print(f"[error] 로그인 실패: {e}")
        return False


def _save_auth_state(page: Page) -> None:
    """브라우저 쿠키를 저장하여 세션을 재사용한다."""
    try:
        cookies = page.context.cookies()
        AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTH_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"[auth] 세션 저장: {AUTH_STATE_PATH}")
    except Exception as e:
        print(f"[warn] 세션 저장 실패: {e}")


# ── 게시글 목록 수집 ──

def collect_post_links(page: Page, board_url: str) -> list[str]:
    """게시판에서 모든 게시글 링크를 수집한다 (페이지네이션 포함)."""
    urls: list[str] = []

    page.goto(board_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    page_num = 1
    while True:
        print(f"  [page {page_num}] 게시글 링크 수집 중...")

        # 게시글 링크 셀렉터: 여러 패턴 시도
        link_selectors = [
            ".block_list a[href*='/article/']",
            "a[href*='/article/']",
            ".list_item a[href*='/article/']",
            ".board_list a[href*='/article/']",
            "tr a[href*='/article/']",
        ]

        found = False
        for selector in link_selectors:
            links = page.query_selector_all(selector)
            if links:
                for link in links:
                    href = link.get_attribute("href")
                    if href:
                        full_url = urljoin(page.url, href)
                        if full_url not in urls:
                            urls.append(full_url)
                found = True
                break

        if not found:
            # JavaScript로 링크 수집 폴백
            hrefs = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href).filter(h => h.includes('/article/'))",
            )
            for href in hrefs:
                if href not in urls:
                    urls.append(href)

        # 다음 페이지 탐색
        next_btn = page.query_selector(
            ".pagination .next:not(.disabled), "
            "a.btn_next:not(.disabled), "
            ".paging a.next, "
            "button.next_page"
        )
        if next_btn:
            try:
                next_btn.click()
                page.wait_for_timeout(2000)
                page_num += 1
            except Exception:
                break
        else:
            break

        time.sleep(PAGE_DELAY)

    print(f"  → {len(urls)}개 게시글 링크 수집")
    return urls


# ── 게시글 본문 추출 ──

def _clean_title(title: str) -> str:
    """제목에서 LINE WORKS UI 노이즈를 제거한다."""
    # "toggle important post", "toggle important" 등 제거
    title = re.sub(r"toggle\s*(important\s*)?(post)?$", "", title).strip()
    # "필독 노출 기간  :" 접두사 제거
    title = re.sub(r"^필독 노출 기간\s*:\s*", "", title).strip()
    # "공지" 접두사 제거 (이미 category로 구분됨)
    title = re.sub(r"^공지\s*", "", title).strip()
    # 날짜 범위 접두사 제거 (예: "2025. 7. 11. - 2025. 7. 18.")
    title = re.sub(r"^\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*-\s*\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*", "", title).strip()
    return title


def scrape_post(page: Page, url: str) -> dict | None:
    """단일 게시글의 제목, 본문, 인라인 이미지를 추출한다."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  [error] 페이지 로드 실패: {url} - {e}")
        return None

    html = page.content()
    soup = BeautifulSoup(html, "lxml")

    # 제목 추출
    title = ""
    for sel in [".board_view .subject", ".subject", "h3.txt", ".view_subject", ".article_subject"]:
        tag = soup.select_one(sel)
        if tag:
            title = tag.get_text(strip=True)
            break
    if not title:
        title_tag = soup.select_one("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
    title = _clean_title(title)

    # 본문 추출
    content = ""
    for sel in [".board_view .cont", ".board_view .content", ".view_content", ".article_content", ".se-main-container"]:
        tag = soup.select_one(sel)
        if tag:
            content = tag.get_text(separator="\n", strip=True)
            break

    if not title and not content:
        print(f"  [skip] 콘텐츠 없음: {url}")
        return None

    # 인라인 이미지 URL 수집
    inline_image_urls: list[str] = []
    content_area = None
    for sel in [".board_view .cont", ".board_view .content", ".view_content", ".article_content", ".se-main-container"]:
        content_area = soup.select_one(sel)
        if content_area:
            break

    if content_area:
        for img in content_area.find_all("img"):
            src = img.get("src", "")
            if src and not src.startswith("data:"):
                full_src = urljoin(url, src)
                if full_src not in inline_image_urls:
                    inline_image_urls.append(full_src)

    return {
        "url": url,
        "title": title,
        "content": content,
        "inline_image_urls": inline_image_urls,
    }


# ── 첨부파일 다운로드 ──

def download_attachments(page: Page, post_url: str) -> list[dict]:
    """게시글의 첨부파일을 다운로드하고 텍스트+이미지를 추출한다."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scraper.file_extractor import extract_content, SUPPORTED_EXTENSIONS

    attachments = []

    # 다운로드 버튼 셀렉터
    btn_selectors = [
        ".lw_file_attach_view button.btn_down_pc",
        ".file_attach button.btn_down",
        ".attach_list a.download",
        ".file_list a",
        "a[download]",
    ]

    for selector in btn_selectors:
        buttons = page.query_selector_all(selector)
        if buttons:
            for btn in buttons:
                try:
                    with page.expect_download(timeout=30000) as dl_info:
                        btn.click()
                    download = dl_info.value

                    # 임시 파일로 저장
                    tmp_dir = Path(tempfile.mkdtemp())
                    filename = download.suggested_filename or "attachment"
                    file_path = tmp_dir / filename
                    download.save_as(str(file_path))

                    ext = file_path.suffix.lower()
                    if ext not in SUPPORTED_EXTENSIONS:
                        print(f"    [skip] 지원하지 않는 첨부파일: {filename}")
                        file_path.unlink(missing_ok=True)
                        tmp_dir.rmdir()
                        continue

                    # 텍스트 + 이미지 추출
                    result = extract_content(file_path)
                    attachment = {
                        "filename": filename,
                        "extracted_text": result.text,
                        "images": [],  # 이미지 바이트 데이터 (후처리에서 R2 URL로 대체)
                        "_raw_images": result.images,  # 임시: 후처리용
                    }
                    attachments.append(attachment)
                    print(f"    [attach] {filename}: 텍스트 {len(result.text)}자, 이미지 {len(result.images)}장")

                    # 임시 파일 정리
                    file_path.unlink(missing_ok=True)
                    try:
                        tmp_dir.rmdir()
                    except OSError:
                        pass

                except Exception as e:
                    print(f"    [warn] 첨부파일 다운로드 실패: {e}")
            break  # 첫 번째 매칭 셀렉터만 사용

    return attachments


# ── 인라인 이미지 다운로드 ──

def _download_inline_images(page: Page, image_urls: list[str]) -> list[tuple[str, bytes]]:
    """인라인 이미지 URL에서 바이트 데이터를 다운로드한다."""
    results: list[tuple[str, bytes]] = []
    for img_url in image_urls:
        try:
            resp = page.request.get(img_url, timeout=15000)
            if resp.ok:
                data = resp.body()
                if len(data) >= 2000:  # 작은 이미지(아이콘 등) 스킵
                    filename = urlparse(img_url).path.split("/")[-1] or "inline_img.png"
                    results.append((filename, data))
        except Exception as e:
            print(f"    [warn] 인라인 이미지 다운로드 실패: {img_url[:80]} - {e}")
    return results


# ── 이미지 후처리 (R2 업로드 + 설명 생성 + 벡터화) ──

def _process_images(
    post_data: dict,
    inline_images: list[tuple[str, bytes]],
    raw_attachment_images: list,
) -> tuple[list[str], list[str]]:
    """이미지를 R2에 업로드하고 경로를 반환한다.

    Returns:
        (inline_image_r2_urls, attachment_image_r2_urls)
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    try:
        from storage.supabase_storage import upload_image
        from storage.supabase_client import is_configured as supabase_configured
    except ImportError:
        print("    [warn] supabase_storage 모듈을 로드할 수 없습니다.")
        return [], []

    if not supabase_configured():
        print("    [info] Supabase 미설정 — 이미지 업로드 건너뜀")
        return [], []

    url_hash = hashlib.md5(post_data["url"].encode("utf-8")).hexdigest()[:12]
    safe_title = re.sub(r"[^\w가-힣-]", "_", post_data.get("title", ""))[:30]
    r2_prefix = f"board_images/{url_hash}_{safe_title}"

    inline_urls: list[str] = []
    attach_urls: list[str] = []

    # 인라인 이미지 업로드
    for i, (filename, data) in enumerate(inline_images):
        try:
            safe_name = re.sub(r"[^\w.-]", "_", filename)
            key = f"{r2_prefix}/inline_{i}_{safe_name}"
            img_url = upload_image(data, key)
            inline_urls.append(img_url)
        except Exception as e:
            print(f"    [warn] 인라인 이미지 업로드 실패: {e}")

    # 첨부파일 이미지 업로드
    for img in raw_attachment_images:
        try:
            key = f"{r2_prefix}/{img.filename}"
            img_url = upload_image(img.data, key)
            attach_urls.append(img_url)
        except Exception as e:
            print(f"    [warn] 첨부 이미지 업로드 실패: {e}")

    return inline_urls, attach_urls


def _generate_descriptions_and_vectorize(
    post_data: dict,
    image_urls: list[str],
    image_bytes_map: dict[str, tuple[bytes, str]],
) -> None:
    """이미지 설명을 생성하고 벡터화하여 Supabase에 업로드한다."""
    if not image_urls:
        return

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    try:
        from graph.image_describer import describe_image_bytes
        from graph.supabase_vector import init_db
        from graph.ingest import ingest_images
    except ImportError as e:
        print(f"    [warn] 벡터화 모듈 로드 실패: {e}")
        return

    descriptions: dict[str, str] = {}
    for i, img_url in enumerate(image_urls):
        if img_url in image_bytes_map:
            img_data, media_type = image_bytes_map[img_url]
            try:
                desc = describe_image_bytes(img_data, media_type=media_type)
                descriptions[img_url] = desc
                if (i + 1) % 3 == 0:
                    time.sleep(0.5)
            except Exception as e:
                print(f"    [warn] 이미지 설명 생성 실패: {e}")
                descriptions[img_url] = ""
        else:
            descriptions[img_url] = ""

    # 벡터화
    try:
        supabase_client = init_db()
        count = ingest_images(
            image_paths=image_urls,
            title=post_data.get("title", ""),
            url=post_data.get("url", ""),
            source="board",
            supabase_client=supabase_client,
            descriptions=descriptions,
        )
        print(f"    [vector] {count}개 이미지 벡터 업로드")
    except Exception as e:
        print(f"    [warn] 이미지 벡터화 실패: {e}")


# ── 메인 크롤링 로직 ──

def scrape_board(board_name: str, board_url: str, page: Page) -> list[dict]:
    """단일 게시판의 모든 게시글을 크롤링한다."""
    print(f"\n{'=' * 60}")
    print(f"[게시판] {board_name}")
    print(f"{'=' * 60}")

    # 1. 게시글 링크 수집
    post_urls = collect_post_links(page, board_url)
    if not post_urls:
        print("  게시글이 없습니다.")
        return []

    results: list[dict] = []

    for i, post_url in enumerate(post_urls, 1):
        print(f"\n[{i}/{len(post_urls)}] {post_url}")

        # 2. 게시글 본문 추출
        post = scrape_post(page, post_url)
        if not post:
            continue

        post["category"] = board_name
        post["source"] = "board"
        post["scraped_at"] = datetime.now(timezone.utc).isoformat()

        # 3. 첨부파일 다운로드 + 텍스트 추출
        attachments = download_attachments(page, post_url)

        # 첨부파일 텍스트를 본문에 추가
        for att in attachments:
            if att["extracted_text"]:
                post["content"] += f"\n\n[첨부: {att['filename']}]\n{att['extracted_text']}"

        # 4. 인라인 이미지 다운로드
        inline_images = _download_inline_images(page, post.get("inline_image_urls", []))

        # 5. 이미지 R2 업로드
        raw_att_images = []
        for att in attachments:
            raw_att_images.extend(att.pop("_raw_images", []))

        inline_r2_urls, attach_r2_urls = _process_images(post, inline_images, raw_att_images)

        # 첨부파일 메타데이터 정리
        for att in attachments:
            att["images"] = []  # 초기화
        if attach_r2_urls:
            # 첨부 이미지를 각 첨부파일에 분배
            idx = 0
            for att in attachments:
                att_img_count = len([x for x in raw_att_images])  # 이미 pop됨
                att["images"] = attach_r2_urls[idx:idx + att_img_count]
                idx += att_img_count

        post["attachments"] = [
            {
                "filename": att["filename"],
                "extracted_text": att["extracted_text"],
                "images": att["images"],
            }
            for att in attachments
        ]
        post["inline_images"] = inline_r2_urls

        # 6. 이미지 설명 생성 + 벡터화
        all_image_urls = inline_r2_urls + attach_r2_urls
        if all_image_urls:
            # 바이트 → URL 매핑 생성
            image_bytes_map: dict[str, tuple[bytes, str]] = {}
            for url_str, (filename, data) in zip(inline_r2_urls, inline_images):
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
                media_type = {
                    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp",
                }.get(ext, "image/png")
                image_bytes_map[url_str] = (data, media_type)
            for url_str, img_obj in zip(attach_r2_urls, raw_att_images):
                ext = img_obj.filename.rsplit(".", 1)[-1].lower() if "." in img_obj.filename else "png"
                media_type = {
                    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp",
                }.get(ext, "image/png")
                image_bytes_map[url_str] = (img_obj.data, media_type)

            _generate_descriptions_and_vectorize(post, all_image_urls, image_bytes_map)

        # inline_image_urls 임시 필드 제거
        post.pop("inline_image_urls", None)

        results.append(post)
        print(f"  ✓ {post['title'][:50]}")
        time.sleep(PAGE_DELAY)

    return results


def scrape_all() -> list[dict]:
    """모든 게시판을 크롤링한다."""
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            # 로그인
            if not _login(page):
                print("[error] 로그인 실패. 크롤링을 중단합니다.")
                return []

            # 각 게시판 크롤링
            for board_name, board_url in BOARDS.items():
                board_results = scrape_board(board_name, board_url, page)
                results.extend(board_results)

        finally:
            browser.close()

    return results


def save_results(results: list[dict], output_path: Path = OUTPUT_PATH) -> None:
    """결과를 JSON 파일로 저장한다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[done] {len(results)}개 게시글을 {output_path}에 저장 완료")


def main():
    print("=" * 60)
    print("LINE WORKS 사내게시판 크롤러")
    print("=" * 60)

    results = scrape_all()
    if results:
        save_results(results)
    else:
        print("[warn] 크롤링된 게시글이 없습니다.")


if __name__ == "__main__":
    main()
