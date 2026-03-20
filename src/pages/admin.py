"""
문서 관리 어드민 페이지.

문서 등록/수정/삭제 → JSON 저장 + Pinecone 벡터 인덱스 + 지식그래프 관리.
"""

import asyncio
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import streamlit as st

# src 디렉토리를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.embedding_index import init_pinecone
from graph.ingest import ingest_document, ingest_document_with_media, delete_document
from graph.data_store import (
    add_item, update_item, delete_item, find_item_by_url,
)
from storage import supabase_docs

_use_supabase = supabase_docs.is_configured()

# set_page_config는 엔트리포인트(app.py)에서 호출됨

# Admin 전용 CSS 로드
_STATIC = Path(__file__).resolve().parents[1] / "ui" / "static"
st.markdown(
    f"<style>\n{_STATIC.joinpath('admin_style.css').read_text()}\n</style>",
    unsafe_allow_html=True,
)

# selectbox input 타이핑 방지 + 자동 포커스 해제
import streamlit.components.v1 as components
components.html("""<script>
(function() {
    const doc = window.parent.document;
    function fixSelects() {
        doc.querySelectorAll('[data-baseweb="select"] input').forEach(el => {
            el.setAttribute('readonly', '');
            el.style.caretColor = 'transparent';
        });
    }
    fixSelects();
    doc.activeElement?.blur();
    setTimeout(fixSelects, 500);
    setTimeout(fixSelects, 1500);
    new MutationObserver(fixSelects).observe(doc.body, {childList: true, subtree: true});
})();
</script>""", height=0)

st.title("📄 RAG 문서 관리")
st.caption("AI 챗봇이 참조할 문서를 관리하세요")

# ── 상수 ──
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ADMIN_DATA_PATH = DATA_DIR / "admin_documents.json"

ITEMS_PER_PAGE = 15

SOURCE_LABELS = {
    "admin": "문서",
    "eluocnc": "엘루오씨앤씨",
    "FAQ": "WORKS게시판",
}

# ── Streamlit Cloud secrets → 환경변수 주입 ──
try:
    for key in ("PINECONE_API_KEY", "ANTHROPIC_API_KEY"):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except FileNotFoundError:
    pass  # secrets.toml 없으면 .env에서 로드


# ── 리소스 로드 ──

@st.cache_resource
def load_resources():
    """Pinecone 인덱스를 로드한다."""
    api_key = os.environ.get("PINECONE_API_KEY", "")
    if not api_key:
        return None
    pc_index = init_pinecone(api_key)
    return pc_index


try:
    pinecone_index = load_resources()
except Exception as e:
    pinecone_index = None
    err_msg = str(e)
    if "401" in err_msg or "Unauthorized" in err_msg or "Invalid API Key" in err_msg:
        st.error("Pinecone API 키가 유효하지 않습니다. `.env` 파일의 PINECONE_API_KEY를 확인해주세요.")
    else:
        st.error(f"Pinecone 연결 실패: {err_msg[:200]}")

if pinecone_index is None:
    st.warning("Pinecone에 연결되지 않았습니다. API 키를 확인해주세요. JSON 문서 관리만 가능합니다.")
    _pinecone_ok = False
else:
    _pinecone_ok = True


# source 필터 매핑 (UI key → DB source)
_SOURCE_DB_MAP = {"FAQ": "FAQ", "admin": "admin", "eluocnc": "eluocnc"}


def generate_admin_url(title: str) -> str:
    """어드민 문서용 고유 URL을 생성한다."""
    sanitized = re.sub(r"[^\w가-힣-]", "_", title)[:50]
    return f"admin://{int(time.time())}_{sanitized}"


def extract_file_content_with_media(uploaded_file):
    """업로드된 파일에서 텍스트 + 이미지를 모두 추출한다. ExtractionResult 반환."""
    from scraper.file_extractor import ExtractionResult, extract_content

    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix in (".txt",):
        text = uploaded_file.read().decode("utf-8", errors="ignore")
        return ExtractionResult(text=text)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = Path(tmp.name)
    try:
        return extract_content(tmp_path)
    except Exception as e:
        st.error(f"파일 콘텐츠 추출 실패: {e}")
        return ExtractionResult(text="")
    finally:
        tmp_path.unlink(missing_ok=True)


def run_async(coro):
    """비동기 코루틴을 동기적으로 실행한다."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)


# ── 문서 상세 모달 ──

@st.dialog("문서 상세", width="large")
def show_doc_detail_dialog():
    """문서 상세 정보를 모달 다이얼로그로 표시한다. session_state에서 문서 데이터를 읽는다."""
    doc = st.session_state.get("_dialog_doc")
    is_admin = st.session_state.get("_dialog_is_admin", False)
    if not doc:
        st.error("문서 데이터를 찾을 수 없습니다.")
        return
    title_display = doc.get("title", "(제목 없음)")
    url = doc.get("url", "")
    # 문서별 고유 key suffix로 위젯 값 캐싱 문제 방지
    k = str(hash(url))[:8]

    if is_admin:
        st.subheader(f"✏️ 문서 수정: {title_display}")
        st.caption(f"URL: {url}")

        edit_title = st.text_input("제목", value=doc.get("title", ""), key=f"dlg_title_{k}")
        _source_options = ["admin", "eluocnc", "FAQ"]
        _current_source = doc.get("source", "admin")
        _source_idx = _source_options.index(_current_source) if _current_source in _source_options else 0
        edit_source = st.selectbox(
            "출처",
            _source_options,
            index=_source_idx,
            format_func=SOURCE_LABELS.get,
            key=f"dlg_source_{k}",
        )
        edit_category = st.text_input(
            "카테고리",
            value=doc.get("category", ""),
            key=f"dlg_cat_{k}",
        )
        edit_content = st.text_area(
            "본문 내용",
            value=doc.get("content", ""),
            height=400,
            key=f"dlg_content_{k}",
        )

        if st.button("💾 저장", type="primary", key=f"dlg_save_{k}"):
            with st.status("문서 수정 중...", expanded=True) as status:
                step1 = st.empty()
                step2 = st.empty()
                step3 = st.empty()

                step1.write("⏳ 문서 업데이트 중...")
                updated_fields = {
                    "title": edit_title,
                    "content": edit_content,
                    "source": edit_source,
                    "category": edit_category,
                }
                if _use_supabase:
                    supabase_docs.update_item(url, updated_fields)
                else:
                    update_item(ADMIN_DATA_PATH, url, updated_fields)
                step1.write("✅ 문서 업데이트 완료")

                if _pinecone_ok:
                    step2.write("⏳ 기존 벡터 삭제 & 재임베딩 중...")
                    result = ingest_document(
                        title=edit_title, content=edit_content,
                        source=edit_source, url=url,
                        pinecone_index=pinecone_index,
                    )
                    step2.write(f"✅ {result['chunks']}개 청크 재업로드 완료")
                else:
                    step2.write("⚠️ Pinecone 미연결 — 벡터 인덱싱 건너뜀")

                status.update(label="수정 완료!", state="complete")

            st.success(f"'{edit_title}' 수정 완료!")
            st.rerun()
    else:
        st.subheader(f"📖 문서 상세: {title_display}")
        st.caption(f"URL: {url}")

        st.text_input("제목", value=doc.get("title", ""), disabled=True, key=f"dlg_title_{k}")
        st.text_input(
            "출처",
            value=SOURCE_LABELS.get(doc.get("source", ""), doc.get("source", "")),
            disabled=True,
            key=f"dlg_source_{k}",
        )
        if doc.get("category"):
            st.text_input("카테고리", value=doc.get("category", ""), disabled=True, key=f"dlg_cat_{k}")
        st.text_area(
            "본문 내용",
            value=doc.get("content", ""),
            height=400,
            disabled=True,
            key=f"dlg_content_{k}",
        )
        st.info("크롤링된 문서는 읽기 전용입니다. 삭제만 가능합니다.")


# ── 문서 등록 모달 ──

@st.dialog("새 문서 등록", width="large")
def show_register_dialog():
    """문서 등록 폼을 모달 다이얼로그로 표시한다."""
    source = st.selectbox(
        "출처 분류",
        ["admin", "eluocnc", "FAQ"],
        format_func=SOURCE_LABELS.get,
        key="reg_source",
    )
    category = st.text_input("카테고리 (선택사항)", placeholder="예: 업무가이드, 인사규정", key="reg_category")

    input_method = st.radio("입력 방식", ["파일 업로드", "직접 입력", "URL 수집"], horizontal=True, key="reg_method")

    title = ""
    content = ""
    url = ""
    _extraction_result = None

    if input_method == "파일 업로드":
        uploaded = st.file_uploader(
            "파일 선택",
            type=["pdf", "docx", "xlsx", "pptx", "md", "txt"],
            key="reg_file",
        )
        title = st.text_input("제목 (빈칸이면 파일명 사용)", key="reg_upload_title")
        url = st.text_input("관련 URL (선택사항)", key="reg_upload_url")

        _extraction_result = None
        if uploaded:
            if not title:
                title = uploaded.name
            _extraction_result = extract_file_content_with_media(uploaded)
            content = _extraction_result.text
            if content:
                with st.expander("추출된 텍스트 미리보기"):
                    st.text(content[:2000] + ("..." if len(content) > 2000 else ""))
            if _extraction_result.images:
                st.info(f"이미지 {len(_extraction_result.images)}장 감지됨 (등록 시 Supabase Storage에 업로드)")
    elif input_method == "직접 입력":
        title = st.text_input("제목", key="reg_manual_title")
        url = st.text_input("관련 URL (선택사항)", key="reg_manual_url")
        content = st.text_area("본문 내용", height=300, key="reg_manual_content")
    else:
        # URL 수집
        from scraper.url_scraper import scrape_url, validate_url

        url_input = st.text_input("URL 입력", placeholder="https://example.com/page", key="reg_url_input")

        col_preview, col_warn = st.columns([1, 3])
        with col_preview:
            preview_clicked = st.button("🔍 미리보기", key="reg_preview")

        if preview_clicked and url_input:
            try:
                with st.spinner("페이지 수집 중..."):
                    result = scrape_url(url_input)
                st.session_state["url_fetched_title"] = result["title"]
                st.session_state["url_fetched_content"] = result["content"]
                st.session_state["url_fetched_url"] = result["url"]
            except Exception as e:
                st.error(f"URL 수집 실패: {e}")
                st.session_state.pop("url_fetched_title", None)
                st.session_state.pop("url_fetched_content", None)
                st.session_state.pop("url_fetched_url", None)

        if "url_fetched_title" in st.session_state:
            fetched_url = st.session_state["url_fetched_url"]
            # 중복 URL 경고
            existing = supabase_docs.find_item_by_url(fetched_url) if _use_supabase else find_item_by_url(ADMIN_DATA_PATH, fetched_url)
            if existing:
                st.warning(f"이미 등록된 URL입니다: '{existing.get('title', '')}'")

            title = st.text_input("제목", value=st.session_state["url_fetched_title"], key="reg_url_title")
            url = fetched_url
            content = st.text_area("본문 내용", value=st.session_state["url_fetched_content"], height=300, key="reg_url_content")
        else:
            title = ""
            url = ""
            content = ""

    # URL 자동 생성
    if not url and title:
        url = generate_admin_url(title)

    # 제출 버튼
    if st.button("📥 등록", type="primary", disabled=not (title.strip() and content.strip()), key="reg_submit"):
        # 파일 업로드 모드에서 추출된 이미지 가져오기
        extracted_images = None
        if input_method == "파일 업로드" and _extraction_result and _extraction_result.images:
            extracted_images = _extraction_result.images

        with st.status("문서 등록 중...", expanded=True) as status:
            steps = st.container()

            # Step 1: 텍스트 준비
            steps.write("✅ Step 1: 텍스트 준비 완료")

            if _pinecone_ok and extracted_images:
                # 이미지 포함 미디어 파이프라인
                step_container = steps.empty()

                def _on_progress(step: str, detail: str):
                    label_map = {
                        "image_upload": "⏳ Step 2: 이미지 업로드 중...",
                        "image_upload_done": f"✅ Step 2: 이미지 업로드 완료 ({detail})",
                        "storage_skip": f"⚠️ Step 2: {detail}",
                        "image_describe": "⏳ Step 3: 이미지 설명 생성 중 (Claude Vision)...",
                        "image_describe_done": f"✅ Step 3: 이미지 설명 완료 ({detail})",
                        "image_vectorize": "⏳ Step 4: 이미지 벡터화 중...",
                        "image_vectorize_done": f"✅ Step 4: {detail}",
                        "text_ingest": "⏳ Step 5: 텍스트 벡터화 중...",
                        "text_ingest_done": f"✅ Step 5: {detail}",
                    }
                    msg = label_map.get(step, f"⏳ {step}: {detail}")
                    steps.write(msg)

                result = ingest_document_with_media(
                    title=title, content=content, source=source, url=url,
                    pinecone_index=pinecone_index,
                    images=extracted_images,
                    progress_callback=_on_progress,
                )

                # JSON에 attachments 포함
                doc_data = {
                    "title": title,
                    "content": content,
                    "url": url,
                    "source": source,
                    "category": category,
                }
                if result.get("image_urls"):
                    doc_data["attachments"] = [{
                        "filename": "file",
                        "images": result["image_urls"],
                    }]

                steps.write("⏳ Step 6: 문서 저장 중...")
                if _use_supabase:
                    supabase_docs.add_item(doc_data)
                else:
                    add_item(ADMIN_DATA_PATH, doc_data)
                steps.write("✅ Step 6: 문서 저장 완료")

                summary = f"텍스트 {result['chunks']}개 청크 + 이미지 {result['images']}개"
                if result.get("deleted", 0) > 0:
                    summary += f" (기존 {result['deleted']}개 삭제 후 재등록)"
            else:
                # 기존 텍스트 전용 파이프라인
                steps.write("⏳ Step 2: 문서 저장 중...")
                doc_data = {
                    "title": title,
                    "content": content,
                    "url": url,
                    "source": source,
                    "category": category,
                }
                if _use_supabase:
                    supabase_docs.add_item(doc_data)
                else:
                    add_item(ADMIN_DATA_PATH, doc_data)
                steps.write("✅ Step 2: 문서 저장 완료")

                if _pinecone_ok:
                    steps.write("⏳ Step 3: 임베딩 & Pinecone 업로드 중...")
                    result = ingest_document(
                        title=title, content=content, source=source, url=url,
                        pinecone_index=pinecone_index,
                    )
                    steps.write(f"✅ Step 3: {result['chunks']}개 청크 업로드 완료")
                    summary = f"{result['chunks']}개 청크"
                    if result.get("deleted", 0) > 0:
                        summary += f" (기존 {result['deleted']}개 삭제 후 재등록)"
                else:
                    steps.write("⚠️ Pinecone 미연결 — 벡터 인덱싱 건너뜀")
                    summary = "JSON만 저장"

            status.update(label="등록 완료!", state="complete")

        st.success(f"'{title}' 등록 완료! ({summary})")
        st.rerun()

    if not (title and content):
        st.info("제목과 내용을 입력하면 등록 버튼이 활성화됩니다.")


# ── 챗봇 복귀 링크 ──
if st.button("💬 챗봇으로 돌아가기", key="back_to_chat"):
    st.switch_page("chat_page.py")

# ══════════════════════════════════════════════════════════════
# 📋 문서 관리
# ══════════════════════════════════════════════════════════════
title_col, btn_col = st.columns([8, 2])
with title_col:
    st.subheader("등록된 문서 목록")
with btn_col:
    if st.button("📤 문서 등록", type="primary"):
        # 모달 데이터 초기화 — 이전에 닫힌 모달의 잔여 데이터 제거
        for k in [
            "url_fetched_title", "url_fetched_content", "url_fetched_url",
            "reg_source", "reg_category", "reg_method",
            "reg_file", "reg_upload_title", "reg_upload_url",
            "reg_manual_title", "reg_manual_url", "reg_manual_content",
            "reg_url_input", "reg_url_title", "reg_url_content",
        ]:
            st.session_state.pop(k, None)
        show_register_dialog()

# 필터 / 검색
col_search, col_filter, col_refresh = st.columns([4, 2, 0.7])
with col_search:
    keyword = st.text_input("검색", placeholder="문서 검색...", label_visibility="collapsed")
with col_filter:
    source_filter = st.selectbox(
        "출처 필터",
        ["전체", "FAQ", "admin", "eluocnc"],
        format_func=lambda x: "전체" if x == "전체" else SOURCE_LABELS.get(x, x),
        label_visibility="collapsed",
    )
with col_refresh:
    if st.button("🔄", help="새로고침"):
        st.rerun()

# 필터 변경 감지 → 페이지 리셋
current_filter_key = f"{source_filter}|{keyword}"
if st.session_state.get("_last_filter_key") != current_filter_key:
    st.session_state["_last_filter_key"] = current_filter_key
    st.session_state["admin_page"] = 0

# Supabase 서버사이드 쿼리
db_source = _SOURCE_DB_MAP.get(source_filter, source_filter) if source_filter != "전체" else ""
current_page = st.session_state.get("admin_page", 0)
offset = current_page * ITEMS_PER_PAGE
page_docs, total_count = supabase_docs.load_items_page(
    source=db_source, keyword=keyword,
    offset=offset, limit=ITEMS_PER_PAGE,
)
for item in page_docs:
    item["_data_path"] = "supabase"

# 통계
st.markdown(
    f"<div style='text-align:right; color:#6B7280; font-size:0.88rem; padding:0.2rem 0 0.5rem;'>"
    f"총 <b>{total_count}</b>개 문서"
    f"</div>",
    unsafe_allow_html=True,
)

if not page_docs and total_count == 0:
    st.info("조건에 맞는 문서가 없습니다.")
else:
    # ── 페이지네이션 계산 ──
    total_pages = max(1, (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    # 삭제 후 페이지 범위 초과 방지
    if current_page >= total_pages:
        current_page = total_pages - 1
        st.session_state["admin_page"] = current_page

    # ── 테이블 헤더 ──
    st.markdown(
        '<div class="admin-table-header">'
        "<span>문서명</span><span>출처</span><span>카테고리</span>"
        "<span>상태</span><span>작업</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 문서 목록 렌더링 ──
    for i, doc in enumerate(page_docs):
        global_idx = offset + i
        src = doc.get("source", "")
        src_label = SOURCE_LABELS.get(src, src)
        title = doc.get("title", "(제목 없음)")
        url = doc.get("url", "")
        data_path = doc.get("_data_path", "")
        category = doc.get("category", "") or "-"

        c_title, c_src, c_cat, c_status, c_del = st.columns([4, 1.5, 1.5, 1, 0.7])
        with c_title:
            if st.button(title, key=f"view_{global_idx}_{url}", type="secondary"):
                st.session_state["_dialog_doc"] = doc
                st.session_state["_dialog_is_admin"] = src == "admin"
                show_doc_detail_dialog()
        with c_src:
            st.markdown(f'<div class="admin-table-cell">{src_label}</div>', unsafe_allow_html=True)
        with c_cat:
            st.markdown(f'<div class="admin-table-cell">{category}</div>', unsafe_allow_html=True)
        with c_status:
            st.markdown('<div class="admin-table-cell"><span class="status-badge">활성</span></div>', unsafe_allow_html=True)
        with c_del:
            if st.button("삭제", key=f"del_{global_idx}_{url}"):
                st.session_state[f"confirm_delete_{url}"] = True

        # 삭제 확인
        if st.session_state.get(f"confirm_delete_{url}"):
            st.warning(f"'{title}' 문서를 삭제하시겠습니까?")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("삭제 확인", key=f"yes_del_{global_idx}_{url}", type="primary"):
                    with st.spinner("삭제 중..."):
                        # S3에서 이미지 삭제
                        s3_deleted = 0
                        if doc.get("attachments"):
                            try:
                                from storage.supabase_storage import is_configured, delete_images
                                import hashlib
                                if is_configured():
                                    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
                                    s3_deleted = delete_images(f"doc_images/{url_hash}_")
                            except Exception as e:
                                st.warning(f"S3 이미지 정리 실패 (문서는 삭제됨): {e}")
                        # DB/JSON에서 삭제
                        if data_path == "supabase":
                            supabase_docs.delete_item(url)
                        elif data_path:
                            delete_item(Path(data_path), url)
                        # Pinecone에서 삭제
                        vec_deleted = 0
                        if _pinecone_ok:
                            vec_deleted = delete_document(url, pinecone_index)
                    del st.session_state[f"confirm_delete_{url}"]
                    st.toast(f"'{title}' 삭제 완료 (벡터 {vec_deleted}개)")
                    st.rerun()
            with col_no:
                if st.button("취소", key=f"no_del_{global_idx}_{url}"):
                    del st.session_state[f"confirm_delete_{url}"]
                    st.rerun()

    # ── 페이지네이션 컨트롤 ──
    if total_pages > 1:
        col_prev, col_info, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.button("⬅️ 이전", disabled=current_page == 0):
                st.session_state["admin_page"] = current_page - 1
                st.rerun()
        with col_info:
            st.markdown(
                f"<div style='text-align:center; padding-top:8px;'>"
                f"{current_page + 1} / {total_pages} 페이지</div>",
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button("다음 ➡️", disabled=current_page >= total_pages - 1):
                st.session_state["admin_page"] = current_page + 1
                st.rerun()


