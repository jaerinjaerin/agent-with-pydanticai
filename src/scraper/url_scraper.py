"""
범용 URL 콘텐츠 추출 유틸리티.

URL을 입력받아 웹 페이지 또는 PDF에서 제목과 본문을 추출한다.
정적 크롤링(requests)으로 콘텐츠가 부족하면 Playwright 헤드리스 브라우저로 폴백.
"""

import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 정적 크롤링 결과가 이 글자수 미만이면 Playwright 폴백 시도
MIN_CONTENT_LENGTH = 100


def _is_notion_url(url: str) -> bool:
    """Notion 도메인 여부를 판별한다."""
    host = urlparse(url).hostname or ""
    return host.endswith("notion.so") or host.endswith("notion.site")


def _detect_encoding(resp: requests.Response) -> str:
    """HTTP 응답의 인코딩을 정확하게 감지한다.

    우선순위:
    1. HTTP Content-Type charset (ISO-8859-1은 requests 기본값이므로 무시)
    2. HTML <meta charset> / <meta http-equiv> 태그
    3. UTF-8 BOM
    4. UTF-8 strict 디코딩 시도
    5. CP949 디코딩 시도
    6. apparent_encoding 폴백
    """
    raw = resp.content

    # 1) HTTP Content-Type charset (ISO-8859-1은 requests가 붙인 기본값이므로 무시)
    ct_charset = resp.encoding
    if ct_charset and ct_charset.lower() not in ("iso-8859-1", "latin-1", "latin1"):
        return ct_charset

    # 2) HTML meta 태그에서 charset 추출
    # raw bytes의 앞부분만 검사 (성능)
    head = raw[:4096]
    meta_match = re.search(
        rb'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9_-]+)', head, re.IGNORECASE
    )
    if not meta_match:
        meta_match = re.search(
            rb'<meta[^>]+http-equiv=["\']?Content-Type["\']?[^>]+content=["\']?[^"\']*charset=([a-zA-Z0-9_-]+)',
            head,
            re.IGNORECASE,
        )
    if meta_match:
        return meta_match.group(1).decode("ascii", errors="ignore")

    # 3) UTF-8 BOM
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"

    # 4) UTF-8 strict 디코딩 시도
    try:
        raw.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # 5) CP949 (EUC-KR 상위호환) 시도
    try:
        raw.decode("cp949", errors="strict")
        return "cp949"
    except UnicodeDecodeError:
        pass

    # 6) 최후 수단
    return resp.apparent_encoding or "utf-8"


def validate_url(url: str) -> str:
    """URL 유효성 검증 및 정규화. 스킴이 없으면 https:// 추가."""
    url = url.strip()
    if not url:
        raise ValueError("URL이 비어 있습니다.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"유효하지 않은 URL입니다: {url}")
    return url


def scrape_url(url: str) -> dict:
    """메인 진입점. URL에서 제목과 본문을 추출한다.

    1) PDF URL이면 다운로드 후 텍스트 추출
    2) HTML: requests로 정적 크롤링 시도
    3) 콘텐츠가 부족하면 Playwright 헤드리스로 재시도

    Returns:
        {"title": str, "content": str, "url": str}
    """
    url = validate_url(url)
    if urlparse(url).path.lower().endswith(".pdf"):
        return _scrape_pdf_url(url)

    # 정적 크롤링 시도
    result = _scrape_html_static(url)
    if result and len(result["content"]) >= MIN_CONTENT_LENGTH:
        return result

    # 콘텐츠 부족 → Playwright 폴백
    pw_result = _scrape_html_playwright(url)
    if pw_result and len(pw_result["content"]) >= len(result.get("content", "") if result else ""):
        return pw_result

    # Playwright도 실패하면 정적 결과라도 반환
    if result and result["content"]:
        return result

    raise ValueError("페이지에서 콘텐츠를 추출할 수 없습니다.")


def _extract_from_soup(soup: BeautifulSoup, url: str) -> dict:
    """BeautifulSoup 객체에서 제목과 본문을 추출하는 공통 로직."""
    # 제목 추출: h1 → h2 → title 순서
    title = ""
    for selector in ["h1", "h2", "title"]:
        tag = soup.select_one(selector)
        if tag and tag.get_text(strip=True):
            title = tag.get_text(strip=True)
            if selector == "title":
                title = re.sub(r"\s*[|\-–—]\s*.*$", "", title).strip()
            break

    # 불필요한 요소 제거
    for tag in soup.select(
        "script, style, nav, header, footer, "
        ".notion-topbar, .notion-sidebar"
    ):
        tag.decompose()

    # 본문 영역 셀렉터 탐색
    content = ""
    content_tag = soup.select_one(
        ".notion-page-content, .super-content, .layout-content, "
        ".contents, .content-wrap, .sub-content, .page-content, "
        "main, article, #content, .container .content, .post-content, "
        ".entry-content, .article-body"
    )
    if content_tag:
        content = content_tag.get_text(separator="\n", strip=True)
    else:
        body = soup.find("body")
        if body:
            content = body.get_text(separator="\n", strip=True)

    return {"title": title, "content": content, "url": url}


def _scrape_html_static(url: str) -> dict | None:
    """requests + BeautifulSoup으로 정적 HTML을 크롤링한다."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    # 인코딩 자동 감지
    resp.encoding = _detect_encoding(resp)

    soup = BeautifulSoup(resp.text, "lxml")
    return _extract_from_soup(soup, url)


def _scrape_html_playwright(url: str) -> dict | None:
    """Playwright 헤드리스 브라우저로 JS 렌더링 후 콘텐츠를 추출한다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        notion = _is_notion_url(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            if notion:
                # Notion은 지속적 네트워크 요청으로 networkidle 타임아웃 발생
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_selector(
                        ".notion-page-content, .notion-selectable, .super-content",
                        timeout=15000,
                    )
                except Exception:
                    page.wait_for_timeout(5000)
            else:
                page.goto(url, wait_until="networkidle", timeout=30000)

            html = page.content()
            browser.close()
    except Exception:
        return None

    soup = BeautifulSoup(html, "lxml")
    return _extract_from_soup(soup, url)


def _scrape_pdf_url(url: str) -> dict:
    """PDF URL에서 텍스트를 추출한다."""
    from scraper.file_extractor import extract_text

    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()

    filename = urlparse(url).path.split("/")[-1]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = Path(tmp.name)

    try:
        content = extract_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not content:
        raise ValueError("PDF에서 텍스트를 추출할 수 없습니다.")

    title = filename.replace(".pdf", "") if filename else ""
    return {"title": title, "content": content, "url": url}
