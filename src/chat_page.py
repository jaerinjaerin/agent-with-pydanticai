"""
Ask Eluo — 채팅 페이지.

PydanticAI 에이전트 기반 사내 지식 검색 챗봇 UI.
st.navigation에서 호출되는 페이지 파일.
"""

import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# sniffio 패치 + 백그라운드 루프 — 반드시 첫 번째 import
from ui.async_runtime import run_async  # noqa: E402

import streamlit as st
import streamlit.components.v1 as components

# src 디렉토리를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic_ai.models.anthropic import AnthropicModel

from agent.faq_agent import AgentDeps, GraphRAGDatabase, faq_agent, get_graph_db
from storage.supabase_chat import (
    get_or_create_conversation,
    is_configured as supabase_configured,
    load_messages,
    restore_pydantic_history,
    save_message,
    upload_chat_image,
)

_RELATED_TOPIC_RE = re.compile(r"\[관련\s*주제:\s*(.+?)\]")
_IMAGE_RE = re.compile(r"\[IMAGE:\s*(.+?)\]")
_EXPENSE_RESULT_RE = re.compile(r"\[EXPENSE_RESULT:\s*(\{.+?\})\]")
# 스트리밍 중 아직 닫히지 않은 미완성 패턴 (문자열 끝까지 매치)
_PARTIAL_PATTERN_RE = re.compile(r"\[(?:관련\s*주제|IMAGE|EXPENSE_RESULT):[^\]]*$")


def _strip_display_patterns(text: str) -> str:
    """스트리밍/최종 표시용 텍스트에서 메타 패턴([IMAGE], [관련 주제], [EXPENSE_RESULT])을 제거한다."""
    text = _IMAGE_RE.sub("", text)
    text = _RELATED_TOPIC_RE.sub("", text)
    text = _EXPENSE_RESULT_RE.sub("", text)
    text = _PARTIAL_PATTERN_RE.sub("", text)
    return text


def _parse_related_topics(text: str) -> tuple[str, list[str]]:
    """답변에서 [관련 주제: ...] 패턴을 추출하고 본문에서 제거한다."""
    match = _RELATED_TOPIC_RE.search(text)
    if not match:
        return text, []
    topics = [t.strip() for t in match.group(1).split(",") if t.strip()]
    clean_text = text[:match.start()].rstrip() + text[match.end():]
    return clean_text.strip(), topics


def _parse_expense_result(text: str) -> tuple[str, dict | None]:
    """답변에서 [EXPENSE_RESULT: {...}] 패턴을 추출하고 본문에서 제거한다."""
    match = _EXPENSE_RESULT_RE.search(text)
    if not match:
        return text, None
    try:
        expense_data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text, None
    clean_text = text[:match.start()].rstrip() + text[match.end():]
    return clean_text.strip(), expense_data


def _img_html(src: str, max_width: str = "100%") -> str:
    """Layout shift 방지를 위해 min-height placeholder가 포함된 <img> HTML을 반환한다."""
    return (
        f'<div class="chat-img-wrap">'
        f'<img src="{src}" loading="lazy" style="max-width:{max_width}" '
        f'onload="this.parentElement.style.minHeight=\'auto\'" />'
        f'</div>'
    )


def _render_message_with_images(text: str) -> None:
    """[IMAGE: 경로 또는 URL] 패턴을 파싱하여 텍스트와 이미지를 인라인으로 렌더링한다."""
    parts = _IMAGE_RE.split(text)
    for i, part in enumerate(parts):
        if i % 2 == 0:
            stripped = part.strip()
            if stripped:
                st.markdown(stripped)
        else:
            img_ref = part.strip()
            if img_ref.startswith(("http://", "https://")):
                st.markdown(_img_html(img_ref), unsafe_allow_html=True)
            else:
                img_path = PROJECT_ROOT / img_ref
                if img_path.exists():
                    st.markdown(_img_html(str(img_path)), unsafe_allow_html=True)
                else:
                    st.caption(f"(이미지를 찾을 수 없습니다: {img_ref})")


# 자체 완결형 스크롤 트리거 (iframe 내에서 parent document 직접 조작, 1회성)
_SCROLL_TRIGGER_JS = """<script>
(function(){
    var c=window.parent.document.querySelector('[data-testid="stAppScrollToBottomContainer"]');
    if(c) c.scrollTop=c.scrollHeight;
})();
</script>"""

_STREAMING_MARKER = '<div id="eluo-streaming" style="display:none"></div>'

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(__file__).resolve().parent / "ui" / "static"
_AVATAR_BOT = str(_STATIC / "avatar_bot.svg")
_AVATAR_USER = str(_STATIC / "avatar_user.svg")

# ── 모델 선택 ──
MODEL_OPTIONS = {
    "Claude Sonnet 4": ("anthropic", "claude-sonnet-4-6"),
    "Claude Haiku 4.5": ("anthropic", "claude-haiku-4-5"),
}

def _build_model(provider: str, model_id: str):
    return AnthropicModel(model_id)

# ── CSS / JS 로드 ──
st.markdown(f"<style>\n{_STATIC.joinpath('style.css').read_text()}\n</style>", unsafe_allow_html=True)

# ── 로딩 오버레이 (CSS/JS 적용 전 FOUC 방지) ──
st.markdown("""
<div id="eluo-loading-overlay">
    <div class="loading-logo">
        <span class="loading-text">Ask Eluo</span>
        <div class="loading-bar"><div class="loading-bar-fill"></div></div>
    </div>
</div>
""", unsafe_allow_html=True)

# JS 로드 (components.html은 iframe 생성 → script 실행 보장)
components.html(
    f"<script>\n{_STATIC.joinpath('scroll_lock.js').read_text()}\n</script>"
    f"<script>\n{_STATIC.joinpath('input_layout.js').read_text()}\n</script>",
    height=0,
)


def _format_timestamp() -> str:
    """현재 시각을 '오후 05:17' 형식으로 반환한다."""
    now = datetime.now()
    hour = now.hour
    period = "오전" if hour < 12 else "오후"
    display_hour = hour if hour <= 12 else hour - 12
    if display_hour == 0:
        display_hour = 12
    return f"{period} {display_hour:02d}:{now.minute:02d}"


def _db_row_to_ui(row: dict) -> dict:
    """Supabase messages 행을 st.session_state.messages 형식으로 변환한다."""
    meta = row.get("metadata") or {}
    msg: dict = {
        "role": row["role"],
        "content": row["content"],
        "timestamp": meta.get("display_timestamp", ""),
        "related_topics": row.get("related_topics") or [],
    }
    if meta.get("image_url"):
        msg["image"] = meta["image_url"]
    if meta.get("expense_data"):
        msg["expense_data"] = meta["expense_data"]
    return msg


@st.cache_resource
def load_faq_db() -> GraphRAGDatabase:
    """FAQ 데이터베이스를 로드하고 캐시한다."""
    return get_graph_db()


# DB 로드
try:
    faq_db = load_faq_db()
    pinecone_ok = getattr(faq_db, "pinecone_index", None) is not None
    badge_class = "connected" if pinecone_ok else "disconnected"
    badge_text = "Pinecone" if pinecone_ok else "Pinecone 연결 안됨"
    st.markdown(f"""
    <div class="pinecone-badge {badge_class}">
        <span class="pinecone-dot"></span>
        {badge_text}
    </div>
    """, unsafe_allow_html=True)
except FileNotFoundError:
    st.error(
        "데이터가 없습니다. 먼저 크롤러를 실행해주세요:\n\n"
        "`python src/scraper/eluocnc_scraper.py`"
    )
    st.stop()
except ValueError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    # 캐시된 객체가 오래된 클래스 구조일 수 있음 → 캐시 클리어 후 재시도
    load_faq_db.clear()
    try:
        faq_db = load_faq_db()
        pinecone_ok = getattr(faq_db, "pinecone_index", None) is not None
        badge_class = "connected" if pinecone_ok else "disconnected"
        badge_text = "Pinecone" if pinecone_ok else "Pinecone 연결 안됨"
        st.markdown(f"""
        <div class="pinecone-badge {badge_class}">
            <span class="pinecone-dot"></span>
            {badge_text}
        </div>
        """, unsafe_allow_html=True)
    except Exception as e2:
        st.error(f"데이터 로드 실패: {e2}")
        st.stop()

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pydantic_history" not in st.session_state:
    st.session_state.pydantic_history = []
if "pending_input" not in st.session_state:
    st.session_state.pending_input = None
if "scroll_to_bottom" not in st.session_state:
    st.session_state.scroll_to_bottom = False
if "model_choice" not in st.session_state:
    st.session_state.model_choice = list(MODEL_OPTIONS.keys())[0]
if "is_streaming" not in st.session_state:
    st.session_state.is_streaming = False
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

# ── Supabase 대화 복원 ──
if "session_token" not in st.session_state:
    token = st.query_params.get("session", str(uuid4()))
    st.query_params["session"] = token
    st.session_state.session_token = token

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
    st.session_state._pydantic_history_loaded = False
    try:
        if supabase_configured():
            conv = get_or_create_conversation(
                st.session_state.session_token, st.session_state.model_choice
            )
            st.session_state.conversation_id = conv["id"]
            if not st.session_state.messages:
                db_msgs = load_messages(conv["id"])
                if db_msgs:
                    st.session_state.messages = [
                        _db_row_to_ui(r) for r in db_msgs
                    ]
    except Exception as e:
        print(f"[Supabase] 대화 복원 실패: {e}")

# ── 사용자 비용처리 프로필 (기본값 — 추후 어드민/설정에서 변경 가능) ──
_DEFAULT_EXPENSE_PROFILE = {
    "expenseCategory": "AI",
    "project": "노비타 브랜드 유지운영",
    "detail": "AI 전환 작업",
    "approver1": "안동균",
    "approver2": "임혜연",
}
if "expense_profile" not in st.session_state:
    st.session_state.expense_profile = dict(_DEFAULT_EXPENSE_PROFILE)

def _run_chat():
    # 이전 run이 스트리밍 중 중단되었는지 확인
    if st.session_state.is_streaming:
        st.session_state.is_streaming = False
        if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Claude의 응답이 중단되었습니다.",
                "related_topics": [],
                "timestamp": _format_timestamp(),
            })

    # 버튼 클릭 후 rerun 시 스크롤 트리거
    if st.session_state.scroll_to_bottom:
        st.session_state.scroll_to_bottom = False
        components.html(_SCROLL_TRIGGER_JS, height=0)

    # ── 영수증 업로드 (숨김 — JS가 + 버튼으로 트리거) ──
    receipt_file = st.file_uploader(
        "영수증 이미지",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"receipt_uploader_{st.session_state.uploader_key}",
        label_visibility="collapsed",
    )
    if receipt_file:
        st.session_state.receipt_data = {
            "bytes": receipt_file.getvalue(),
            "mime": receipt_file.type,
            "name": receipt_file.name,
        }
        # 미리보기용 base64 데이터를 숨겨진 div로 전달 (JS가 읽어 썸네일 렌더링)
        _b64 = base64.standard_b64encode(receipt_file.getvalue()).decode()
        st.markdown(
            f'<div id="receipt-preview-data" data-src="data:{receipt_file.type};base64,{_b64}" '
            f'data-name="{receipt_file.name}" style="display:none"></div>',
            unsafe_allow_html=True,
        )
    else:
        st.session_state.receipt_data = None

    # ── 모델 선택 드롭다운 (입력창 바로 위) ──
    def _on_model_change():
        st.session_state.model_choice = st.session_state._model_select_widget

    selected_model_label = st.selectbox(
        "모델 선택",
        options=list(MODEL_OPTIONS.keys()),
        index=list(MODEL_OPTIONS.keys()).index(st.session_state.model_choice),
        key="_model_select_widget",
        on_change=_on_model_change,
        label_visibility="collapsed",
    )
    _provider, _model_id = MODEL_OPTIONS[st.session_state.model_choice]
    selected_model = _build_model(_provider, _model_id)

    # 숨겨진 정지 트리거 — 항상 렌더링하여 위젯 트리 일관성 유지
    # JS(input_layout.js)가 이 버튼을 찾아 숨기고, 정지 버튼 클릭 시 프로그래밍 방식으로 클릭
    st.button("중지", key="_eluo_stop_trigger")

    # 숨겨진 새채팅 트리거 — JS가 커스텀 버튼으로 대체
    if st.button("새채팅", key="_new_chat_trigger"):
        st.session_state.messages = []
        st.session_state.pydantic_history = []
        st.session_state.session_token = str(uuid4())
        st.query_params["session"] = st.session_state.session_token
        if "conversation_id" in st.session_state:
            del st.session_state["conversation_id"]
        st.rerun()

    # 웰컴 — st.empty()로 DOM 위치만 확보. 실제 렌더링은 prompt 확인 후 결정.
    # 이렇게 하면 첫 제출 사이클에서 웰컴이 아예 그려지지 않아 레이아웃 점프가 없다.
    welcome_slot = st.empty()

    # 마지막 assistant 메시지 인덱스 계산 (관련 주제 버튼은 마지막 답변에만 표시)
    last_assistant_idx = None
    for i in range(len(st.session_state.messages) - 1, -1, -1):
        if st.session_state.messages[i]["role"] == "assistant":
            last_assistant_idx = i
            break

    # 대화 히스토리 표시
    for idx, msg in enumerate(st.session_state.messages):
        avatar = _AVATAR_BOT if msg["role"] == "assistant" else _AVATAR_USER
        marker_cls = "msg-marker-assistant" if msg["role"] == "assistant" else "msg-marker-user"
        ts = msg.get("timestamp", "")
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(f'<span class="{marker_cls}" style="display:none"></span>', unsafe_allow_html=True)
            # 사용자 첨부 이미지 표시
            if msg["role"] == "user" and msg.get("image"):
                st.markdown(_img_html(msg["image"], max_width="200px"), unsafe_allow_html=True)
            if msg["role"] == "assistant" and _IMAGE_RE.search(msg["content"]):
                try:
                    _render_message_with_images(msg["content"])
                except Exception:
                    st.markdown(_IMAGE_RE.sub("", msg["content"]))
            else:
                st.markdown(msg["content"])
            # 타임스탬프를 버블 내부에 렌더링
            if ts:
                align = "right" if msg["role"] == "user" else "left"
                st.markdown(
                    f'<div class="msg-timestamp-inline" style="text-align:{align}">{ts}</div>',
                    unsafe_allow_html=True,
                )
        # 비용처리 결과 메시지: NaverWorks 버튼 (rerun 후에도 유지)
        # #action-payload는 버튼 클릭 시에만 렌더링 — Extension이 DOM 등장 즉시 처리하므로
        if msg.get("expense_data") and idx == last_assistant_idx:
            if st.button("▶ NaverWorks에 입력", key=f"expense_exec_{idx}"):
                expense = msg["expense_data"]
                import html as _html
                full_payload = {
                    "data": {
                        "date": expense.get("date", ""),
                        "amount": expense.get("amount", ""),
                        "item": expense.get("item", ""),
                        "place": expense.get("place", ""),
                        "receiptImage": expense.get("receiptImage", ""),
                    },
                    "profile": st.session_state.expense_profile,
                }
                action_json = json.dumps(full_payload, ensure_ascii=False)
                escaped = _html.escape(action_json, quote=True)
                st.markdown(
                    f'<div id="action-payload" data-payload="{escaped}" style="display:none"></div>',
                    unsafe_allow_html=True,
                )
                st.success("NaverWorks 확장 프로그램으로 전달 중...")

        if msg["role"] == "assistant" and msg.get("related_topics"):
            if idx == last_assistant_idx:
                with st.container():
                    st.markdown('<div class="related-topics-row">', unsafe_allow_html=True)
                    cols = st.columns(len(msg["related_topics"]))
                    for col, topic in zip(cols, msg["related_topics"]):
                        safe_key = re.sub(r"[^\w가-힣]", "_", topic)
                        if col.button(f"💡 {topic}", key=f"topic_{idx}_{safe_key}"):
                            st.session_state.pending_input = topic
                            st.session_state.scroll_to_bottom = True
                            st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div class="related-topics-placeholder"></div>',
                    unsafe_allow_html=True,
                )

    # pending_input이 있으면 자동 실행
    _pending = st.session_state.pending_input
    st.session_state.pending_input = None
    prompt = st.chat_input("질문을 입력하세요...") or _pending

    # 웰컴 렌더링 결정: prompt가 없고 메시지도 없을 때만 웰컴 표시.
    # prompt 확인 후 결정하므로, 첫 제출 사이클에서 웰컴이 아예 그려지지 않는다.
    if not st.session_state.messages and not prompt:
        welcome_slot.markdown("""
        <div class="welcome-card">
            <div class="welcome-greeting">Hello, I'm Eluo :)</div>
        </div>
        <div id="eluo-welcome-state" style="display:none"></div>
        """, unsafe_allow_html=True)

    if prompt:
        user_ts = _format_timestamp()

        # 첨부 이미지가 있으면 메시지에 포함 (file_uploader에 파일이 실제 존재할 때만)
        _attached_image = None
        if receipt_file and st.session_state.get("receipt_data"):
            rd = st.session_state.receipt_data
            _attached_image = f"data:{rd['mime']};base64,{base64.standard_b64encode(rd['bytes']).decode()}"

        user_msg = {"role": "user", "content": prompt, "timestamp": user_ts}
        if _attached_image:
            user_msg["image"] = _attached_image
        st.session_state.messages.append(user_msg)

        # ── Supabase: 유저 메시지 저장 ──
        if st.session_state.get("conversation_id"):
            try:
                _img_url = None
                if _attached_image and st.session_state.get("receipt_data"):
                    rd = st.session_state.receipt_data
                    _img_url = upload_chat_image(
                        rd["bytes"], rd["mime"],
                        st.session_state.conversation_id,
                    )
                    if _img_url:
                        user_msg["image"] = _img_url
                _user_meta = {}
                if _img_url:
                    _user_meta["image_url"] = _img_url
                save_message(
                    st.session_state.conversation_id,
                    "user", prompt, user_ts,
                    metadata=_user_meta if _user_meta else None,
                )
            except Exception as e:
                print(f"[Supabase] 유저 메시지 저장 실패: {e}")

        with st.chat_message("user", avatar=_AVATAR_USER):
            st.markdown('<span class="msg-marker-user" style="display:none"></span>', unsafe_allow_html=True)
            if _attached_image:
                st.markdown(_img_html(_attached_image, max_width="200px"), unsafe_allow_html=True)
            st.markdown(prompt)

        # 첨부 이미지 미리보기 DOM 즉시 제거 (receipt_data는 에이전트 응답 후 정리)
        if _attached_image:
            components.html("""<script>
            (function(){
                var doc=window.parent.document;
                var p=doc.querySelector('.receipt-preview'); if(p) p.remove();
                var d=doc.getElementById('receipt-preview-data'); if(d) d.remove();
                var b=doc.querySelector('.receipt-file-badge'); if(b) b.remove();
            })();
            </script>""", height=0)
        st.markdown(
            f'<div class="msg-timestamp" style="text-align:right">{user_ts}</div>',
            unsafe_allow_html=True,
        )

        # 하단 스크롤 트리거 (components.html → iframe 내 JS 실행)
        components.html(_SCROLL_TRIGGER_JS, height=0)

        # ── RAG 챗봇 로직 (비용처리도 에이전트 도구로 통합) ──
        st.session_state.is_streaming = True
        st.markdown(_STREAMING_MARKER, unsafe_allow_html=True)

        related_topics = []
        expense_data = None
        with st.chat_message("assistant", avatar=_AVATAR_BOT):
            st.markdown('<span class="msg-marker-assistant" style="display:none"></span>', unsafe_allow_html=True)
            placeholder = st.empty()

            async def _stream(user_prompt, deps, history):
                """PydanticAI run_stream_events — 캐릭터 버퍼 드레인으로 자연스러운 타이핑 효과."""
                import asyncio

                from pydantic_ai import (
                    AgentRunResultEvent,
                    FunctionToolCallEvent,
                    FunctionToolResultEvent,
                    PartDeltaEvent,
                    PartStartEvent,
                    TextPartDelta,
                )
                from pydantic_ai.messages import TextPart

                REVEAL_INTERVAL = 0.03  # 30ms
                BASE_CHARS = 1

                displayed = ""
                pending = []  # list of chars
                final_output = ""
                all_msgs = []
                done = asyncio.Event()
                lock = asyncio.Lock()

                _tool_labels = {
                    "search_faq": "🔍 검색 중...",
                    "list_titles": "📋 목록 조회 중...",
                    "get_item_detail": "📄 상세 조회 중...",
                    "get_data_stats": "📊 통계 조회 중...",
                    "process_expense": "🧾 영수증 분석 중...",
                }
                _loading_html = """<style>
@keyframes pulse{0%,100%{opacity:.3}50%{opacity:1}}
@keyframes bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
.dots{display:inline-flex;gap:5px;padding:8px 0;min-height:1.5em}
.dots span{width:7px;height:7px;border-radius:50%;background:#00007F;
animation:pulse 1.4s ease-in-out infinite,bounce 1.4s ease-in-out infinite}
.dots span:nth-child(2){animation-delay:.2s}
.dots span:nth-child(3){animation-delay:.4s}
</style><div class="dots"><span></span><span></span><span></span></div>"""
                placeholder.markdown(_loading_html, unsafe_allow_html=True)
                print(f"[Model] {_model_id} (provider: {_provider})")

                async def _reveal():
                    """20ms 간격으로 버퍼에서 글자를 꺼내 화면에 표시한다."""
                    nonlocal displayed
                    while not done.is_set() or pending:
                        async with lock:
                            if pending:
                                n = BASE_CHARS
                                if len(pending) > 300:
                                    n = 6
                                elif len(pending) > 150:
                                    n = 3
                                elif len(pending) > 60:
                                    n = 2
                                chunk = pending[:n]
                                del pending[:n]
                                displayed += "".join(chunk)
                                placeholder.markdown(_strip_display_patterns(displayed).rstrip() + " ▌")
                        await asyncio.sleep(REVEAL_INTERVAL)
                    # 최종 렌더 (커서 제거)
                    if displayed:
                        placeholder.markdown(_strip_display_patterns(displayed).rstrip())

                reveal_task = asyncio.create_task(_reveal())

                async for event in faq_agent.run_stream_events(
                    user_prompt=user_prompt,
                    deps=deps,
                    message_history=history or None,
                    model=selected_model,
                ):
                    if isinstance(event, AgentRunResultEvent):
                        all_msgs = event.result.all_messages()
                        final_output = event.result.output
                    elif isinstance(event, FunctionToolCallEvent):
                        # 버퍼 즉시 flush
                        async with lock:
                            if pending:
                                displayed += "".join(pending)
                                pending.clear()
                        placeholder.markdown(
                            _tool_labels.get(event.part.tool_name, f"⏳ {event.part.tool_name} 실행 중...")
                        )
                    elif isinstance(event, PartStartEvent):
                        if isinstance(event.part, TextPart) and event.part.content:
                            async with lock:
                                pending.extend(event.part.content)
                    elif isinstance(event, PartDeltaEvent):
                        if isinstance(event.delta, TextPartDelta):
                            async with lock:
                                pending.extend(event.delta.content_delta)

                done.set()
                await reveal_task

                answer = final_output or displayed or "답변을 생성하지 못했습니다."
                if answer != displayed:
                    placeholder.markdown(_strip_display_patterns(answer).rstrip())
                return answer, all_msgs

            # pydantic_history 지연 로딩 (첫 질문 시 1회만 DB에서 복원)
            if (
                not st.session_state.get("_pydantic_history_loaded")
                and st.session_state.get("conversation_id")
                and not st.session_state.pydantic_history
            ):
                try:
                    pydantic_hist = restore_pydantic_history(st.session_state.conversation_id)
                    if pydantic_hist:
                        st.session_state.pydantic_history = pydantic_hist
                except Exception as e:
                    print(f"[Supabase] pydantic history 복원 실패: {e}")
                st.session_state._pydantic_history_loaded = True

            deps = AgentDeps(graph_db=faq_db, receipt_data=st.session_state.get("receipt_data"))
            try:
                answer, all_messages = run_async(
                    _stream(prompt, deps, st.session_state.pydantic_history)
                )
                st.session_state.pydantic_history = all_messages
            except (st.runtime.scriptrunner.StopException, KeyboardInterrupt):
                raise
            except Exception:
                try:
                    answer, all_messages = run_async(
                        _stream(prompt, deps, None)
                    )
                    st.session_state.pydantic_history = all_messages
                except (st.runtime.scriptrunner.StopException, KeyboardInterrupt):
                    raise
                except Exception as e:
                    st.session_state.pydantic_history = []
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "rate" in err_str.lower():
                        answer = (
                            "⚠️ 현재 선택된 모델의 API 호출 한도에 도달했습니다.\n\n"
                            "다른 모델을 선택하거나 잠시 후 다시 시도해주세요."
                        )
                    else:
                        answer = f"죄송합니다. 오류가 발생했습니다: {e}"
                    placeholder.markdown(answer)

            # 관련 주제 파싱 + answer 정리 (세션 저장용)
            clean_answer, related_topics = _parse_related_topics(answer)
            clean_answer, expense_data = _parse_expense_result(clean_answer)
            if clean_answer != answer:
                answer = clean_answer

            # receiptImage 첨부
            if expense_data and st.session_state.get("receipt_data"):
                rd = st.session_state.receipt_data
                b64_img = base64.standard_b64encode(rd["bytes"]).decode()
                expense_data["receiptImage"] = f"data:{rd['mime']};base64,{b64_img}"

        # 세션에 저장 → 즉시 rerun (중간 렌더링 생략, 히스토리 루프에서 1회만 렌더링)
        bot_ts = _format_timestamp()
        msg_dict = {
            "role": "assistant",
            "content": answer,
            "related_topics": related_topics if related_topics else [],
            "timestamp": bot_ts,
        }
        if expense_data:
            msg_dict["expense_data"] = expense_data
        st.session_state.messages.append(msg_dict)

        # receipt_data 정리 (비용처리 여부 무관)
        if st.session_state.get("receipt_data"):
            st.session_state.receipt_data = None
            st.session_state.uploader_key += 1

        # ── Supabase: 어시스턴트 메시지 저장 ──
        if st.session_state.get("conversation_id"):
            try:
                _save_meta = {}
                if expense_data:
                    _save_meta["expense_data"] = expense_data
                save_message(
                    st.session_state.conversation_id,
                    "assistant", answer, bot_ts,
                    related_topics=related_topics if related_topics else None,
                    pydantic_messages=st.session_state.pydantic_history or None,
                    metadata=_save_meta if _save_meta else None,
                )
            except Exception as e:
                print(f"[Supabase] 어시스턴트 메시지 저장 실패: {e}")

        st.session_state.is_streaming = False
        st.rerun()


try:
    _run_chat()
except Exception as e:
    st.error(f"예기치 않은 오류가 발생했습니다: {e}")
    if st.button("대화 초기화"):
        st.session_state.messages = []
        st.session_state.pydantic_history = []
        st.session_state.session_token = str(uuid4())
        st.query_params["session"] = st.session_state.session_token
        if "conversation_id" in st.session_state:
            del st.session_state["conversation_id"]
        st.rerun()
