"""OpenGraph 메타데이터 fetch + 카드 렌더링."""

import html as html_mod
import re
from urllib.parse import urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_og_metadata(url: str) -> dict | None:
    """URL에서 OpenGraph 메타데이터를 가져온다."""
    try:
        resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        og_image = soup.find("meta", property="og:image")
        title = og_title["content"] if og_title and og_title.get("content") else soup.title.string if soup.title else ""
        desc = og_desc["content"] if og_desc and og_desc.get("content") else ""
        image = og_image["content"] if og_image and og_image.get("content") else ""
        domain = urlparse(url).netloc
        return {"title": title or domain, "description": desc, "image": image, "domain": domain, "url": url}
    except Exception:
        domain = urlparse(url).netloc
        return {"title": domain, "description": "", "image": "", "domain": domain, "url": url}


def extract_urls(text: str) -> list[str]:
    """텍스트에서 URL을 추출한다."""
    return list(dict.fromkeys(re.findall(r'https?://[^\s\)\]<>"]+', text)))


def render_og_cards(text: str, og_cache: list[dict] | None = None):
    """텍스트 내 URL을 OpenGraph 카드로 렌더링한다."""
    if og_cache is not None:
        cards = og_cache
    else:
        urls = extract_urls(text)
        if not urls:
            return []
        cards = [fetch_og_metadata(u) for u in urls[:5]]
        cards = [c for c in cards if c]

    for card in cards:
        esc = html_mod.escape
        img_html = f'<img class="og-card-img" src="{esc(card["image"])}" alt="" />' if card.get("image") else ""
        st.html(f"""
        <style>
        .og-card {{
            display: flex;
            border: 1px solid #E5E7EB;
            border-radius: 8px;
            overflow: hidden;
            margin: 10px 0;
            background: #FFFFFF;
            text-decoration: none;
            color: inherit;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            max-height: 120px;
        }}
        .og-card:hover {{
            border-color: rgba(0, 0, 127, 0.15);
            box-shadow: 0 4px 12px rgba(0, 0, 127, 0.06);
            transform: translateY(-1px);
        }}
        .og-card-img {{
            width: 120px;
            min-width: 120px;
            object-fit: cover;
            background: #F0F0F0;
        }}
        .og-card-body {{
            padding: 12px 16px;
            overflow: hidden;
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            font-family: 'Pretendard Variable', 'Pretendard', -apple-system, sans-serif;
        }}
        .og-card-title {{
            font-size: 0.88rem;
            font-weight: 600;
            color: #1A1A2E;
            margin: 0 0 4px 0;
            display: -webkit-box;
            -webkit-line-clamp: 1;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .og-card-desc {{
            font-size: 0.78rem;
            color: #6B7280;
            margin: 0 0 6px 0;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            line-height: 1.5;
        }}
        .og-card-url {{
            font-size: 0.68rem;
            color: #9CA3AF;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        </style>
        <a class="og-card" href="{esc(card['url'])}" target="_blank" rel="noopener">
            {img_html}
            <div class="og-card-body">
                <div class="og-card-title">{esc(card.get('title', ''))}</div>
                <div class="og-card-desc">{esc(card.get('description', ''))}</div>
                <div class="og-card-url">{esc(card.get('domain', ''))}</div>
            </div>
        </a>
        """)
    return cards
