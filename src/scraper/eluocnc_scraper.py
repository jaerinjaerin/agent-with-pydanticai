"""
엘루오씨앤씨 웹사이트 크롤러.

https://www.eluocnc.com 의 sitemap + AJAX 엔드포인트에서 URL을 수집하고,
각 페이지의 제목/본문을 추출하여 data/eluocnc.json에 저장한다.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.eluocnc.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "eluocnc.json"
REQUEST_DELAY = 1.5
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


def fetch_sitemap_urls() -> list[str]:
    """sitemap.xml에서 /ko/ URL 목록을 추출한다."""
    try:
        resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[warn] sitemap.xml 요청 실패: {e}")
        return []

    root = ET.fromstring(resp.content)
    # sitemap XML namespace
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        if loc.text and "/ko/" in loc.text:
            urls.append(loc.text)
    print(f"[sitemap] {len(urls)}개 /ko/ URL 수집 완료")
    return urls


def fetch_ajax_works_urls() -> list[str]:
    """AJAX works_list 엔드포인트에서 프로젝트 상세 페이지 URL을 수집한다."""
    urls = []
    ajax_url = f"{BASE_URL}/ko/ajax.works_list.asp"
    try:
        resp = requests.post(
            ajax_url,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            data={"pageIndex": "1"},
            timeout=30,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/ko/works/" in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in urls:
                    urls.append(full_url)
    except requests.RequestException as e:
        print(f"[warn] works AJAX 요청 실패: {e}")
    print(f"[ajax:works] {len(urls)}개 프로젝트 URL 수집")
    return urls


def fetch_ajax_idea_urls() -> list[str]:
    """AJAX idea_list 엔드포인트에서 블로그 글 URL을 수집한다."""
    urls = []
    ajax_url = f"{BASE_URL}/ko/ajax.idea_list.asp"
    for page in range(1, 6):
        try:
            resp = requests.post(
                ajax_url,
                headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
                data={"bid": "idea", "pageIndex": str(page)},
                timeout=30,
            )
            resp.raise_for_status()
            if not resp.text.strip():
                break
            # frmView('160') 형태에서 ID 추출
            ids = re.findall(r"frmView\('(\d+)'\)", resp.text)
            found = 0
            for idx in ids:
                full_url = f"{BASE_URL}/ko/idea_view.asp?bid=idea&idx={idx}"
                if full_url not in urls:
                    urls.append(full_url)
                    found += 1
            if found == 0:
                break
        except requests.RequestException as e:
            print(f"[warn] idea AJAX 페이지 {page} 요청 실패: {e}")
            break
        time.sleep(REQUEST_DELAY)
    print(f"[ajax:idea] {len(urls)}개 블로그 URL 수집")
    return urls


def detect_category(url: str) -> str:
    """URL 경로에서 카테고리를 추출한다."""
    path = urlparse(url).path.lower()
    for key, cat in CATEGORY_MAP.items():
        if key in path:
            return cat
    return "general"


def extract_page_content(soup: BeautifulSoup) -> str:
    """페이지에서 불필요한 요소를 제거하고 본문 텍스트를 추출한다."""
    # 불필요한 요소 제거
    for tag in soup.select("script, style, nav, header, footer, .gnb, .fnb, .popup"):
        tag.decompose()

    # 본문 영역 우선 탐색
    content_tag = soup.select_one(
        ".contents, .content-wrap, .sub-content, .page-content, "
        ".idea-view, .works-view, main, article, "
        "#content, .container .content"
    )
    if content_tag:
        return content_tag.get_text(separator="\n", strip=True)

    # fallback: body 전체
    body = soup.find("body")
    if body:
        return body.get_text(separator="\n", strip=True)
    return ""


def scrape_page(url: str) -> dict | None:
    """웹 페이지에서 제목과 본문을 추출한다."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[error] {url} 요청 실패: {e}")
        return None

    # 인코딩 자동 감지
    if resp.encoding and "euc" in resp.encoding.lower():
        resp.encoding = "euc-kr"
    elif resp.apparent_encoding:
        resp.encoding = resp.apparent_encoding

    soup = BeautifulSoup(resp.text, "lxml")

    # 제목 추출
    title_tag = soup.select_one(
        "h1, h2.page-title, .sub-title h2, .idea-view h2, "
        ".works-view h2, .contents h2, title, .content-wrap h2"
    )
    title = title_tag.get_text(strip=True) if title_tag else ""

    # <title> 태그에서 사이트명 제거
    if title_tag and title_tag.name == "title":
        title = re.sub(r"\s*[|\-–—]\s*.*$", "", title).strip()

    content = extract_page_content(soup)

    if not title and not content:
        print(f"[skip] {url} - 콘텐츠 없음")
        return None

    return {
        "url": url,
        "title": title,
        "content": content,
        "category": detect_category(url),
        "source": "eluocnc",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_pdf(url: str) -> dict | None:
    """PDF 파일을 다운로드하고 텍스트를 추출한다."""
    try:
        from scraper.file_extractor import extract_text
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scraper.file_extractor import extract_text

    import tempfile

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


def collect_all_urls() -> list[str]:
    """모든 URL을 수집하고 중복을 제거한다."""
    urls = set()

    # 1) sitemap
    for url in fetch_sitemap_urls():
        urls.add(url)

    time.sleep(REQUEST_DELAY)

    # 2) AJAX works
    for url in fetch_ajax_works_urls():
        urls.add(url)

    time.sleep(REQUEST_DELAY)

    # 3) AJAX ideas
    for url in fetch_ajax_idea_urls():
        urls.add(url)

    result = sorted(urls)
    print(f"[total] 중복 제거 후 {len(result)}개 URL")
    return result


def scrape_all() -> list[dict]:
    """모든 페이지와 PDF를 크롤링한다."""
    urls = collect_all_urls()
    results = []

    # 웹 페이지 크롤링
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}")
        item = scrape_page(url)
        if item:
            results.append(item)
        time.sleep(REQUEST_DELAY)

    # PDF 크롤링
    for i, url in enumerate(PDF_URLS, 1):
        print(f"[PDF {i}/{len(PDF_URLS)}] {url}")
        item = scrape_pdf(url)
        if item:
            results.append(item)
        time.sleep(REQUEST_DELAY)

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
