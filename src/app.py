"""
LINE WORKS FAQ 챗봇 Streamlit UI.

PydanticAI 에이전트를 활용한 대화형 FAQ 챗봇 인터페이스.
노션 스타일 도식화 적용.
"""

import asyncio
import re
import sys
import threading
from pathlib import Path

# ── sniffio 패치 (모든 import보다 먼저 실행) ──
# Streamlit Cloud(uvloop)에서 sniffio가 비동기 백엔드를 감지하지 못하는 문제를 해결.
# 이 패치는 어떤 라이브러리가 import되기 전에 적용되어야 한다.
import sniffio
_sniffio_original = sniffio.current_async_library
def _patched_sniffio():
    try:
        return _sniffio_original()
    except sniffio.AsyncLibraryNotFoundError:
        return "asyncio"
sniffio.current_async_library = _patched_sniffio

import streamlit as st

# 표준 asyncio 루프를 백그라운드 스레드에서 운영한다.
# uvloop(Streamlit Cloud)과 완전히 분리된 별도 루프이므로 nest_asyncio 불필요.
_bg_loop = asyncio.DefaultEventLoopPolicy().new_event_loop()


def _run_loop():
    asyncio.set_event_loop(_bg_loop)
    _bg_loop.run_forever()


_bg_thread = threading.Thread(target=_run_loop, daemon=True)
_bg_thread.start()


def run_async(coro):
    """백그라운드 표준 asyncio 루프에서 코루틴을 실행한다."""
    future = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    return future.result()

# src 디렉토리를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.faq_agent import FAQDatabase, faq_agent, get_faq_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]

st.set_page_config(page_title="엘루오 도우미", page_icon="💬", layout="wide")

# ── 노션 스타일 CSS ──
st.markdown("""
<style>
/* 전역 폰트 */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif; }

/* 사이드바 숨김 */
section[data-testid="stSidebar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }

/* 데이터 로드 상태 배너 */
.data-status {
    background: #f7f6f3;
    border: 1px solid #e9e9e7;
    border-radius: 8px;
    padding: 8px 16px;
    margin-bottom: 16px;
    font-size: 0.85rem;
    color: #37352f;
    display: flex;
    gap: 16px;
    align-items: center;
}
.data-status .status-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.data-status .status-count {
    font-weight: 600;
}

/* 채팅 메시지 */
.stChatMessage { border-radius: 12px; }

/* 참고자료 카드 */
.ref-card {
    background: #ffffff;
    border: 1px solid #e9e9e7;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 10px;
    transition: box-shadow 0.2s;
}
.ref-card:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}

/* 출처 뱃지 */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    margin-right: 6px;
    vertical-align: middle;
}
.badge-faq {
    background: #e8f5e9;
    color: #2e7d32;
}
.badge-board {
    background: #e3f2fd;
    color: #1565c0;
}
.badge-eluocnc {
    background: #fce4ec;
    color: #c62828;
}

/* 유사도 바 */
.score-bar-bg {
    background: #f0f0ef;
    border-radius: 4px;
    height: 6px;
    width: 100%;
    margin-top: 8px;
}
.score-bar-fill {
    height: 6px;
    border-radius: 4px;
    background: linear-gradient(90deg, #6c5ce7, #a29bfe);
}

/* 카드 제목 */
.ref-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: #37352f;
    margin: 4px 0;
    line-height: 1.4;
}

/* 카드 본문 미리보기 */
.ref-content {
    font-size: 0.85rem;
    color: #6b6b6b;
    margin-top: 6px;
    line-height: 1.6;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

/* 카드 URL */
.ref-url {
    font-size: 0.75rem;
    color: #9b9a97;
    margin-top: 8px;
    word-break: break-all;
}
.ref-url a { color: #2383e2; text-decoration: none; }
.ref-url a:hover { text-decoration: underline; }

/* callout 박스 */
.callout {
    background: #f7f6f3;
    border-radius: 8px;
    padding: 14px 18px;
    margin: 10px 0;
    border-left: 3px solid #e9e9e7;
    font-size: 0.9rem;
    color: #37352f;
}
.callout-icon {
    margin-right: 8px;
    font-size: 1.1rem;
}

/* 구분선 */
.notion-divider {
    border: none;
    border-top: 1px solid #e9e9e7;
    margin: 16px 0;
}

/* 섹션 헤더 */
.section-header {
    font-size: 0.8rem;
    font-weight: 600;
    color: #9b9a97;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 10px;
}

/* 자동 스크롤 방지 */
[data-testid="ScrollToBottomContainer"] { display: none !important; }
[data-testid="stChatMessageContainer"] {
    overflow-anchor: none !important;
    scroll-behavior: auto !important;
}
.stApp, .stApp * {
    scroll-behavior: auto !important;
    scroll-margin: 0 !important;
    scroll-padding: 0 !important;
}
/* expander 열릴 때 스크롤 이동 방지 */
[data-testid="stExpander"],
[data-testid="stExpander"] * {
    scroll-margin-top: 0 !important;
    scroll-margin-bottom: 0 !important;
    scroll-snap-align: none !important;
}
[data-testid="stExpanderDetails"] {
    overflow-anchor: none !important;
}

/* 참고자료 expander 컴팩트 스타일 */
.ref-expander [data-testid="stExpander"] {
    border: 1px solid #e9e9e7;
    border-radius: 8px;
    margin-top: 8px;
}
.ref-expander [data-testid="stExpanderDetails"] {
    max-height: 300px;
    overflow-y: auto;
}

/* 참고자료 카드 컴팩트 */
.ref-card {
    padding: 10px 14px !important;
    margin-bottom: 6px !important;
}
.ref-title { font-size: 0.85rem !important; }
.ref-content {
    font-size: 0.8rem !important;
    -webkit-line-clamp: 2 !important;
}
</style>
""", unsafe_allow_html=True)

st.title("💬 엘루오 도우미")
st.caption("FAQ 및 사내 규정/업무가이드 관련 질문을 입력해주세요.")

# 자동 스크롤 방지 JS — 프로그래밍적 스크롤을 완전 차단하고 사용자 입력만 허용
st.markdown("""
<script>
(function() {
    if (window.__scrollLockApplied) return;
    window.__scrollLockApplied = true;

    // 사용자가 직접 스크롤할 때만 허용 플래그
    let userInitiated = false;
    ['wheel', 'touchmove', 'touchstart', 'keydown', 'mousedown'].forEach(evt => {
        window.addEventListener(evt, () => {
            userInitiated = true;
            setTimeout(() => { userInitiated = false; }, 300);
        }, { capture: true, passive: true });
    });

    // window.scrollTo / window.scroll 차단
    const _scrollTo = window.scrollTo.bind(window);
    window.scrollTo = function(...args) {
        if (userInitiated) _scrollTo(...args);
    };
    window.scroll = function(...args) {
        if (userInitiated) _scrollTo(...args);
    };

    // Element.scrollIntoView 차단
    Element.prototype.scrollIntoView = function() {};

    // ScrollToBottom 버튼 제거
    new MutationObserver(function() {
        document.querySelectorAll('[data-testid="ScrollToBottomContainer"]')
            .forEach(el => el.remove());
    }).observe(document.body, { childList: true, subtree: true });
})();
</script>
""", unsafe_allow_html=True)


@st.cache_resource
def load_faq_db() -> FAQDatabase:
    """FAQ 데이터베이스를 로드하고 캐시한다."""
    return get_faq_db()


# FAQ DB 로드
try:
    faq_db = load_faq_db()
    faq_count = sum(1 for item in faq_db.items if item.get("source", "faq") == "faq")
    board_count = sum(1 for item in faq_db.items if item.get("source") == "board")
    eluocnc_count = sum(1 for item in faq_db.items if item.get("source") == "eluocnc")
    st.markdown(f"""
    <div class="data-status">
        <span>✅ 데이터 로드 완료</span>
        <span class="status-item">📄 FAQ <span class="status-count">{faq_count}</span>개</span>
        <span class="status-item">📋 게시판 <span class="status-count">{board_count}</span>개</span>
        <span class="status-item">🏢 홈페이지 <span class="status-count">{eluocnc_count}</span>개</span>
        <span class="status-item">총 <span class="status-count">{len(faq_db.items)}</span>개</span>
    </div>
    """, unsafe_allow_html=True)
except FileNotFoundError:
    st.error(
        "데이터가 없습니다. 먼저 크롤러를 실행해주세요:\n\n"
        "`python src/scraper/faq_scraper.py`\n\n"
        "`python src/scraper/board_scraper.py`"
    )
    st.stop()
except ValueError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pydantic_history" not in st.session_state:
    st.session_state.pydantic_history = []
if "pending_selection" not in st.session_state:
    st.session_state.pending_selection = None


def parse_choices(text: str) -> list[str]:
    """에이전트 답변에서 번호 목록 선택지를 추출한다.

    '1. 항목', '1) 항목', '- 항목' 패턴을 인식하며,
    선택을 요청하는 문맥이 있을 때만 추출한다.
    """
    # 선택을 유도하는 키워드가 없으면 무시
    choice_keywords = ["선택", "어떤", "골라", "알려주세요", "원하시", "궁금"]
    if not any(kw in text for kw in choice_keywords):
        return []

    # 번호 목록 추출: "1. ...", "1) ...", "- ..." 등
    patterns = [
        r'^\s*\d+[.)]\s*(.+)$',       # 1. 또는 1)
        r'^\s*[-*]\s*\*?\*?(.+?)(?:\*?\*?)$',  # - 또는 * (볼드 마크다운 제거)
    ]
    choices = []
    for line in text.split("\n"):
        for pat in patterns:
            m = re.match(pat, line.strip())
            if m:
                item = m.group(1).strip().strip("*").strip()
                if item and len(item) > 2:
                    choices.append(item)
                break

    return choices if len(choices) >= 2 else []


def resolve_image_path(img_path: str) -> Path:
    """이미지 경로를 절대 경로로 해석한다."""
    p = Path(img_path)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


def render_ref_card(r: dict):
    """검색 결과를 노션 스타일 카드로 렌더링한다."""
    source = r.get("source", "faq")
    badge_map = {
        "faq": ("badge-faq", "FAQ"),
        "board": ("badge-board", "게시판"),
        "eluocnc": ("badge-eluocnc", "홈페이지"),
    }
    badge_class, badge_label = badge_map.get(source, ("badge-faq", source))
    score = r.get("score", 0)
    score_pct = min(int(score * 100), 100)
    content_preview = r.get("content", "")[:200].replace("\n", " ")
    url = r.get("url", "")
    url_html = f'<a href="{url}" target="_blank">{url}</a>' if url else "—"

    st.markdown(f"""
    <div class="ref-card">
        <div>
            <span class="badge {badge_class}">{badge_label}</span>
            <span style="font-size:0.75rem; color:#9b9a97;">유사도 {score:.0%}</span>
        </div>
        <div class="ref-title">{r.get('title', '')}</div>
        <div class="ref-content">{content_preview}</div>
        <div class="score-bar-bg"><div class="score-bar-fill" style="width:{score_pct}%"></div></div>
        <div class="ref-url">{url_html}</div>
    </div>
    """, unsafe_allow_html=True)


def render_ref_panel(search_results: list[dict]):
    """검색 결과 패널을 접을 수 있는 expander로 렌더링한다."""
    valid = [r for r in search_results if r.get("score", 0) > 0]
    if not valid:
        return

    with st.expander(f"📚 참고 자료 ({len(valid)}건)", expanded=False):
        for r in valid:
            render_ref_card(r)

            # 첨부파일 이미지
            images = r.get("images", [])
            existing = [p for p in images if resolve_image_path(p).exists()]
            if existing:
                with st.expander(f"📎 첨부파일 이미지 ({len(existing)}장)"):
                    for img_path in existing:
                        st.image(str(resolve_image_path(img_path)))


def render_images_from_history(image_groups: list[dict]):
    """히스토리에 저장된 이미지 그룹을 표시한다."""
    if not image_groups:
        return
    for img_group in image_groups:
        with st.expander(f"📎 {img_group['title']} — 첨부파일 이미지"):
            for img_path in img_group["paths"]:
                resolved = resolve_image_path(img_path)
                if resolved.exists():
                    st.image(str(resolved))


# 대화 히스토리 표시
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            # 참고자료 패널 재표시
            if msg.get("references"):
                render_ref_panel(msg["references"])
            elif msg.get("images"):
                render_images_from_history(msg["images"])
            # 선택지가 있었던 메시지 (마지막 assistant 메시지만 selectbox 표시)
            if msg.get("choices") and idx == len(st.session_state.messages) - 1:
                choices = msg["choices"]
                selected = st.selectbox(
                    "항목을 선택해주세요:",
                    choices,
                    key=f"select_{idx}",
                )
                if st.button("선택 완료", key=f"btn_{idx}"):
                    st.session_state.pending_selection = selected
                    st.rerun()

# 셀렉트박스 선택 처리
if st.session_state.pending_selection:
    prompt = f"{st.session_state.pending_selection}에 대해 자세히 알려줘"
    st.session_state.pending_selection = None
    # 이전 메시지의 choices 제거 (selectbox 숨김)
    if st.session_state.messages and st.session_state.messages[-1].get("choices"):
        st.session_state.messages[-1]["choices"] = None
    st.session_state.messages.append({"role": "user", "content": prompt})
    _has_input = True
elif prompt := st.chat_input("질문을 입력하세요..."):
    _has_input = True
else:
    _has_input = False

if _has_input:
    # pending_selection 경로에서는 이미 append 했으므로 chat_input 경로만 추가
    if not any(
        m["role"] == "user" and m["content"] == prompt
        for m in st.session_state.messages[-1:]
    ):
        st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()

        async def _stream(user_prompt, deps, history):
            """PydanticAI 스트리밍으로 답변을 생성한다."""
            async with faq_agent.run_stream(
                user_prompt=user_prompt,
                deps=deps,
                message_history=history or None,
            ) as result:
                full_text = ""
                async for chunk in result.stream_text(delta=True):
                    full_text += chunk
                    placeholder.markdown(full_text + "▌")
                placeholder.markdown(full_text)
                return full_text, result.all_messages()

        try:
            answer, all_messages = run_async(
                _stream(prompt, faq_db, st.session_state.pydantic_history)
            )
            st.session_state.pydantic_history = all_messages
        except Exception:
            try:
                answer, all_messages = run_async(
                    _stream(prompt, faq_db, None)
                )
                st.session_state.pydantic_history = all_messages
            except Exception as e:
                answer = f"죄송합니다. 오류가 발생했습니다: {e}"
                placeholder.markdown(answer)

        # 참고자료 패널
        search_results = faq_db.search(prompt, top_k=3)
        render_ref_panel(search_results)

        # 이미지 경로 수집 (히스토리 저장용)
        image_groups = []
        for r in search_results:
            images = r.get("images", [])
            existing = [p for p in images if resolve_image_path(p).exists()]
            if existing:
                image_groups.append({"title": r["title"], "paths": existing})

        # 선택지 감지
        choices = parse_choices(answer)

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "references": search_results,
        "images": image_groups,
        "choices": choices,
    })
    # 선택지가 있으면 rerun하여 히스토리 루프에서 selectbox 렌더링
    if choices:
        st.rerun()
