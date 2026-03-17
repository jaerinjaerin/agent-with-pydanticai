"""
엘루오씨앤씨 웹사이트 크롤러 (Playwright BFS 방식).

https://www.eluocnc.com 을 Playwright로 BFS 크롤링하여
모든 /ko/ 페이지의 제목/본문을 추출하고 data/eluocnc.json에 저장한다.
"""

import json
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.eluocnc.com"
START_URL = f"{BASE_URL}/ko/main.asp"
OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "eluocnc.json"
MAX_PAGES = 200
PAGE_DELAY = 1.5

# requests용 헤더 (PDF 다운로드에서 사용)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# PDF 파일 URL 목록
PDF_URLS = [
    f"{BASE_URL}/ko/common/pdf/회사소개서_엘루오씨앤씨.pdf",
    f"{BASE_URL}/ko/common/pdf/ELUO Brand Story.pdf",
]

# 정적 페이지 카테고리 매핑
CATEGORY_MAP = {
    "about": "about",
    "careers": "careers",
    "contact": "contact",
    "eluocian": "about",
    "works": "works",
    "idea": "idea",
}

# BFS에서 제외할 확장자/패턴
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".webp",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".rar", ".exe", ".mp4", ".mp3",
}


def normalize_url(url: str) -> str:
    """URL에서 fragment를 제거하고 도메인을 통일한다."""
    from urllib.parse import unquote
    parsed = urlparse(url)
    # 도메인 통일: eluocnc.com → www.eluocnc.com, http → https
    netloc = parsed.netloc
    if netloc == "eluocnc.com":
        netloc = "www.eluocnc.com"
    scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme
    # fragment 제거, 후행 슬래시 정리
    normalized = parsed._replace(scheme=scheme, netloc=netloc, fragment="").geturl()
    return normalized.rstrip("/")


def is_valid_internal_url(url: str) -> bool:
    """/ko/ 내부 링크인지 확인한다."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc and parsed.netloc not in ("www.eluocnc.com", "eluocnc.com"):
        return False
    path = parsed.path.lower()
    if "/ko/" not in path and not path.startswith("/ko"):
        return False
    # 정적 리소스 제외
    ext = Path(path).suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return False
    # PDF는 별도 처리
    if ext == ".pdf":
        return False
    # javascript: void(0) 등 제외
    if url.startswith("javascript:") or url.startswith("mailto:"):
        return False
    return True


def extract_page_content_pw(page) -> tuple[str, str]:
    """Playwright 페이지에서 제목과 본문 텍스트를 추출한다."""
    html = page.content()
    soup = BeautifulSoup(html, "lxml")

    # 불필요한 요소 제거
    for tag in soup.select("script, style, nav, header, footer, .gnb, .fnb, .popup"):
        tag.decompose()

    # 제목 추출
    title_tag = soup.select_one(
        "h1, h2.page-title, .sub-title h2, .idea-view h2, "
        ".works-view h2, .contents h2, title, .content-wrap h2"
    )
    title = title_tag.get_text(strip=True) if title_tag else ""
    if title_tag and title_tag.name == "title":
        title = re.sub(r"\s*[|\-–—]\s*.*$", "", title).strip()

    # 본문 추출
    content_tag = soup.select_one(
        ".contents, .content-wrap, .sub-content, .page-content, "
        ".idea-view, .works-view, main, article, "
        "#content, .container .content"
    )
    if content_tag:
        content = content_tag.get_text(separator="\n", strip=True)
    else:
        body = soup.find("body")
        content = body.get_text(separator="\n", strip=True) if body else ""

    return title, content


def collect_links_from_page(page) -> list[str]:
    """페이지 내 모든 <a href> 링크를 수집한다."""
    hrefs = page.eval_on_selector_all(
        "a[href]",
        "elements => elements.map(el => el.href).filter(h => h)"
    )
    links = []
    for href in hrefs:
        full_url = urljoin(page.url, href)
        normalized = normalize_url(full_url)
        if is_valid_internal_url(normalized) and normalized not in links:
            links.append(normalized)
    return links


def scrape_page_pw(page, url: str) -> dict | None:
    """단일 페이지를 크롤링하여 데이터를 반환한다."""
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        # networkidle 타임아웃 시 domcontentloaded 폴백
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"  [error] 페이지 로드 실패: {url} - {e}")
            return None

    title, content = extract_page_content_pw(page)

    if not title and not content:
        print(f"  [skip] 콘텐츠 없음: {url}")
        return None

    return {
        "url": url,
        "title": title,
        "content": content,
        "category": detect_category(url),
        "source": "eluocnc",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def crawl_bfs(page) -> tuple[list[dict], list[str]]:
    """BFS로 /ko/ 내부 페이지를 탐색하며 콘텐츠를 수집한다."""
    visited: set[str] = set()
    queue: deque[str] = deque()
    results: list[dict] = []
    pdf_urls: list[str] = []

    # 시작 URL
    start = normalize_url(START_URL)
    queue.append(start)
    visited.add(start)

    while queue and len(visited) < MAX_PAGES:
        url = queue.popleft()
        print(f"[{len(results) + 1}/{len(visited)}] {url}")

        item = scrape_page_pw(page, url)
        if item:
            results.append(item)

        # 링크 수집
        try:
            links = collect_links_from_page(page)
            for link in links:
                if link not in visited and len(visited) < MAX_PAGES:
                    visited.add(link)
                    queue.append(link)

            # PDF 링크 별도 수집
            pdf_hrefs = page.eval_on_selector_all(
                'a[href$=".pdf"], a[href*=".pdf"]',
                "elements => elements.map(el => el.href).filter(h => h)"
            )
            for href in pdf_hrefs:
                full_url = urljoin(page.url, href)
                normalized = normalize_url(full_url)
                if normalized not in pdf_urls and "/ko/" in normalized:
                    pdf_urls.append(normalized)
        except Exception as e:
            print(f"  [warn] 링크 수집 실패: {e}")

        time.sleep(PAGE_DELAY)

    print(f"[bfs] {len(results)}개 페이지 수집, {len(visited)}개 URL 방문, {len(pdf_urls)}개 PDF 발견")
    return results, pdf_urls


def crawl_ajax_works(page) -> list[str]:
    """AJAX works_list 엔드포인트에서 프로젝트 상세 URL을 수집한다."""
    urls = []
    ajax_url = f"{BASE_URL}/ko/ajax.works_list.asp"
    for pg in range(1, 16):  # lastPage = 15
        try:
            resp = page.request.post(
                ajax_url,
                form={
                    "searchGb": "1",
                    "searchOrd": "1",
                    "searchType": "",
                    "searchCate": "",
                    "searchValue": "",
                    "searchAi": "",
                    "tp": "",
                    "pageIndex": str(pg),
                },
            )
            if not resp.ok or not resp.text().strip():
                break
            # frmView('NNN') 패턴에서 ID 추출
            ids = re.findall(r"frmView\('?(\d+)'?\)", resp.text())
            # 폴백: <a href> 링크도 수집
            soup = BeautifulSoup(resp.text(), "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/ko/works/" in href:
                    full_url = normalize_url(urljoin(BASE_URL, href))
                    if full_url not in urls:
                        urls.append(full_url)
            found = 0
            for idx in ids:
                full_url = f"{BASE_URL}/ko/works_detail.asp?idx={idx}"
                normalized = normalize_url(full_url)
                if normalized not in urls:
                    urls.append(normalized)
                    found += 1
            if found == 0 and not ids:
                break
        except Exception as e:
            print(f"[warn] works AJAX 페이지 {pg} 요청 실패: {e}")
            break
        time.sleep(PAGE_DELAY)
    print(f"[ajax:works] {len(urls)}개 프로젝트 URL 수집")
    return urls


def crawl_ajax_ideas(page) -> list[str]:
    """AJAX idea_list 엔드포인트에서 블로그 글 URL을 수집한다."""
    urls = []
    ajax_url = f"{BASE_URL}/ko/ajax.idea_list.asp"
    for pg in range(1, 6):
        try:
            resp = page.request.post(
                ajax_url,
                form={"bid": "idea", "pageIndex": str(pg)},
            )
            if not resp.ok or not resp.text().strip():
                break
            ids = re.findall(r"frmView\('(\d+)'\)", resp.text())
            found = 0
            for idx in ids:
                full_url = f"{BASE_URL}/ko/idea_view.asp?bid=idea&idx={idx}"
                normalized = normalize_url(full_url)
                if normalized not in urls:
                    urls.append(normalized)
                    found += 1
            if found == 0:
                break
        except Exception as e:
            print(f"[warn] idea AJAX 페이지 {pg} 요청 실패: {e}")
            break
        time.sleep(PAGE_DELAY)
    print(f"[ajax:idea] {len(urls)}개 블로그 URL 수집")
    return urls


def detect_category(url: str) -> str:
    """URL 경로에서 카테고리를 추출한다."""
    path = urlparse(url).path.lower()
    for key, cat in CATEGORY_MAP.items():
        if key in path:
            return cat
    return "general"


def scrape_pdf(url: str) -> dict | None:
    """PDF 파일을 다운로드하고 텍스트를 추출한다."""
    try:
        from scraper.file_extractor import extract_text
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scraper.file_extractor import extract_text

    import tempfile
    import requests

    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[error] PDF 다운로드 실패: {url} - {e}")
        return None

    filename = urlparse(url).path.split("/")[-1]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = Path(tmp.name)

    try:
        content = extract_text(tmp_path)
    except Exception as e:
        print(f"[error] PDF 텍스트 추출 실패: {filename} - {e}")
        content = ""
    finally:
        tmp_path.unlink(missing_ok=True)

    if not content:
        print(f"[skip] {filename} - 텍스트 추출 실패")
        return None

    return {
        "url": url,
        "title": filename.replace(".pdf", ""),
        "content": content,
        "category": "about",
        "source": "eluocnc",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_all() -> list[dict]:
    """Playwright BFS + AJAX + PDF로 전체 크롤링을 수행한다."""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Phase 1: BFS 크롤링
            print("=" * 60)
            print("[Phase 1] BFS 링크 탐색 + 콘텐츠 추출")
            print("=" * 60)
            bfs_results, discovered_pdfs = crawl_bfs(page)
            results.extend(bfs_results)
            visited_urls = {r["url"] for r in results}

            # Phase 2: AJAX 페이지네이션
            print("=" * 60)
            print("[Phase 2] AJAX 페이지네이션 (works + ideas)")
            print("=" * 60)
            ajax_urls = []
            ajax_urls.extend(crawl_ajax_works(page))
            ajax_urls.extend(crawl_ajax_ideas(page))

            # BFS에서 방문하지 않은 AJAX URL만 크롤링
            new_ajax = [u for u in ajax_urls if u not in visited_urls]
            print(f"[ajax] BFS 미방문 {len(new_ajax)}개 URL 추가 크롤링")
            for i, url in enumerate(new_ajax, 1):
                print(f"[ajax {i}/{len(new_ajax)}] {url}")
                item = scrape_page_pw(page, url)
                if item:
                    results.append(item)
                time.sleep(PAGE_DELAY)

        finally:
            browser.close()

    # Phase 3: PDF 다운로드 + 텍스트 추출
    print("=" * 60)
    print("[Phase 3] PDF 다운로드 + 텍스트 추출")
    print("=" * 60)
    from urllib.parse import unquote
    # URL decode 기반 중복 제거
    seen_decoded: set[str] = set()
    all_pdf_urls: list[str] = []
    for url in PDF_URLS + discovered_pdfs:
        decoded = unquote(url)
        if decoded not in seen_decoded:
            seen_decoded.add(decoded)
            all_pdf_urls.append(url)
    for i, url in enumerate(all_pdf_urls, 1):
        print(f"[PDF {i}/{len(all_pdf_urls)}] {url}")
        item = scrape_pdf(url)
        if item:
            results.append(item)
        time.sleep(PAGE_DELAY)

    return results


def save_results(results: list[dict], output_path: Path = OUTPUT_PATH) -> None:
    """결과를 JSON 파일로 저장한다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[done] {len(results)}개 항목을 {output_path}에 저장 완료")


def main():
    results = scrape_all()
    save_results(results)


if __name__ == "__main__":
    main()
