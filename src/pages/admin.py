"""
문서 관리 어드민 페이지.

문서 등록/수정/삭제 → Supabase DB + pgvector 벡터 인덱스 관리.
"""

import asyncio
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import streamlit as st

# src 디렉토리를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.supabase_vector import init_db
from graph.ingest import ingest_document, ingest_document_with_media, delete_document
from storage.supabase_documents import (
    list_documents as db_list_documents,
    get_document as db_get_document,
    upsert_document as db_upsert_document,
    delete_document as db_delete_document,
)
from storage.supabase_client import is_configured as supabase_configured

st.set_page_config(page_title="RAG 문서 관리", page_icon="📄", layout="wide")

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
ITEMS_PER_PAGE = 15

SOURCE_LABELS = {
    "admin": "사내문서",
    "eluocnc": "회사 홈페이지",
    "board": "사내 게시판",
}

# ── Streamlit Cloud secrets → 환경변수 주입 ──
try:
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        if key in st.secrets and key not in os.environ:
            os.environ[key] = st.secrets[key]
except FileNotFoundError:
    pass  # secrets.toml 없으면 .env에서 로드


# ── 리소스 로드 ──

@st.cache_resource
def load_resources():
    """Supabase 클라이언트를 로드한다."""
    if not supabase_configured():
        return None
    return init_db()


try:
    supabase_client = load_resources()
except Exception as e:
    supabase_client = None
    err_msg = str(e)
    st.error(f"Supabase 연결 실패: {err_msg[:200]}")

if supabase_client is None:
    st.warning("Supabase에 연결되지 않았습니다. 환경변수를 확인해주세요. 문서 관리만 가능합니다.")
    _db_ok = False
else:
    _db_ok = True


# ── 문서 로드 헬퍼 ──

def load_all_documents() -> list[dict]:
    """Supabase에서 모든 문서를 로드한다."""
    try:
        return db_list_documents()
    except Exception as e:
        st.error(f"문서 로드 실패: {e}")
        return []


def generate_admin_url(title: str) -> str:
    """어드민 문서용 고유 URL을 생성한다."""
    sanitized = re.sub(r"[^\w가-힣-]", "_", title)[:50]
    return f"admin://{int(time.time())}_{sanitized}"


def extract_file_content(uploaded_file) -> str:
    """업로드된 파일에서 텍스트를 추출한다."""
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix in (".txt", ".md"):
        return uploaded_file.read().decode("utf-8", errors="ignore")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = Path(tmp.name)
    try:
        from scraper.file_extractor import extract_text
        return extract_text(tmp_path)
    except Exception as e:
        st.error(f"파일 텍스트 추출 실패: {e}")
        return ""
    finally:
        tmp_path.unlink(missing_ok=True)


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
        _source_options = ["admin", "eluocnc", "board"]
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

                step1.write("⏳ DB 업데이트 중...")
                updated_doc = {
                    "url": url,
                    "title": edit_title,
                    "content": edit_content,
                    "source": edit_source,
                    "category": edit_category,
                }
                saved = db_upsert_document(updated_doc)
                step1.write("✅ DB 업데이트 완료")

                if _db_ok:
                    step2.write("⏳ 기존 벡터 삭제 & 재임베딩 중...")
                    result = ingest_document(
                        title=edit_title, content=edit_content,
                        source=edit_source, url=url,
                        supabase_client=supabase_client,
                        document_id=saved.get("id", ""),
                    )
                    step2.write(f"✅ {result['chunks']}개 청크 재업로드 완료")
                else:
                    step2.write("⚠️ Supabase 미연결 — 벡터 인덱싱 건너뜀")

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


# ── 사이드바 내비게이션 ──
with st.sidebar:
    st.markdown("### 📄 문서 관리")
    _nav_options = ["📋 문서 관리", "📤 새 문서 등록"]
    _default_nav = st.session_state.get("admin_nav", _nav_options[0])
    _default_idx = _nav_options.index(_default_nav) if _default_nav in _nav_options else 0
    nav = st.radio("메뉴", _nav_options, index=_default_idx, label_visibility="collapsed")
    st.session_state["admin_nav"] = nav
    st.markdown("---")
    st.page_link("app.py", label="💬 챗봇으로 돌아가기")

# ══════════════════════════════════════════════════════════════
# 📋 문서 관리
# ══════════════════════════════════════════════════════════════
if nav == "📋 문서 관리":

    def _format_date(date_str: str) -> str:
        """ISO 또는 'YYYY-MM-DD' → '2026. 3. 18.' 포맷."""
        if not date_str:
            return "-"
        try:
            # ISO 형식 (Supabase timestamptz)
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return f"{dt.year}. {dt.month}. {dt.day}."
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
                return f"{dt.year}. {dt.month}. {dt.day}."
            except (ValueError, TypeError):
                return "-"

    def _doc_type_icon(source: str) -> str:
        """출처별 문서 아이콘."""
        icons = {"admin": "", "eluocnc": "", "board": ""}
        return icons.get(source, "")

    # ── 상단 필터 영역 ──
    col_search, col_filter, col_count = st.columns([3, 2, 2])
    with col_search:
        keyword = st.text_input(
            "검색",
            placeholder="문서 검색...",
            label_visibility="collapsed",
        )
    with col_filter:
        source_filter = st.selectbox(
            "출처 필터",
            ["전체", "admin", "eluocnc", "board"],
            format_func=lambda x: "전체" if x == "전체" else SOURCE_LABELS.get(x, x),
            label_visibility="collapsed",
        )

    # 문서 로드
    all_docs = load_all_documents()

    # 필터 적용
    filtered_docs = all_docs
    if source_filter != "전체":
        filtered_docs = [d for d in filtered_docs if d.get("source") == source_filter]
    if keyword:
        keyword_lower = keyword.lower()
        filtered_docs = [
            d for d in filtered_docs
            if keyword_lower in d.get("title", "").lower()
            or keyword_lower in d.get("content", "")[:500].lower()
        ]

    with col_count:
        st.markdown(
            f"<div style='padding-top:8px; text-align:right; color:#888; font-size:0.9rem;'>"
            f"총 <b>{len(filtered_docs)}</b>개 문서</div>",
            unsafe_allow_html=True,
        )

    # 필터 변경 감지 → 페이지 리셋
    current_filter_key = f"{source_filter}|{keyword}"
    if st.session_state.get("_last_filter_key") != current_filter_key:
        st.session_state["_last_filter_key"] = current_filter_key
        st.session_state["admin_page"] = 0

    if not filtered_docs:
        st.info("조건에 맞는 문서가 없습니다.")
    else:
        # ── 페이지네이션 계산 ──
        total_pages = max(1, (len(filtered_docs) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        current_page = st.session_state.get("admin_page", 0)
        if current_page >= total_pages:
            current_page = total_pages - 1
            st.session_state["admin_page"] = current_page

        start_idx = current_page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, len(filtered_docs))
        page_docs = filtered_docs[start_idx:end_idx]

        # ── 테이블 헤더 ──
        st.markdown(
            '<div class="admin-table-header">'
            '<span class="col-title">문서명</span>'
            '<span class="col-source">출처</span>'
            '<span class="col-date">업로드 날짜</span>'
            '<span class="col-status">상태</span>'
            '<span class="col-action">작업</span>'
            '</div>',
            unsafe_allow_html=True,
        )

        # ── 테이블 행 렌더링 ──
        for i, doc in enumerate(page_docs):
            global_idx = start_idx + i
            src = doc.get("source", "")
            src_label = SOURCE_LABELS.get(src, src)
            title = doc.get("title", "(제목 없음)")
            url = doc.get("url", "")
            date_str = _format_date(doc.get("created_at", ""))
            icon = _doc_type_icon(src)

            col_title, col_src, col_date, col_status, col_del = st.columns(
                [4, 1.5, 1.5, 1, 0.7]
            )
            with col_title:
                if st.button(
                    f"{icon}  {title}",
                    key=f"view_{global_idx}_{url}",
                    use_container_width=True,
                ):
                    st.session_state["_dialog_doc"] = doc
                    st.session_state["_dialog_is_admin"] = src == "admin"
                    show_doc_detail_dialog()
            with col_src:
                st.markdown(
                    f"<div class='admin-table-cell'>{src_label}</div>",
                    unsafe_allow_html=True,
                )
            with col_date:
                st.markdown(
                    f"<div class='admin-table-cell'>{date_str}</div>",
                    unsafe_allow_html=True,
                )
            with col_status:
                st.markdown(
                    '<div class="admin-table-cell">'
                    '<span class="status-badge">활성</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
            with col_del:
                if st.button(" ", key=f"del_{global_idx}_{url}"):
                    st.session_state[f"confirm_delete_{url}"] = True

            # 삭제 확인
            if st.session_state.get(f"confirm_delete_{url}"):
                st.warning(f"'{title}' 문서를 삭제하시겠습니까?")
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("삭제 확인", key=f"yes_del_{global_idx}_{url}", type="primary"):
                        with st.spinner("삭제 중..."):
                            # Supabase Storage에서 이미지 삭제
                            storage_deleted = 0
                            meta = doc.get("metadata", {})
                            if meta.get("attachments") or doc.get("attachments"):
                                try:
                                    from storage.supabase_storage import is_configured as storage_ok, delete_images
                                    import hashlib
                                    if storage_ok():
                                        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
                                        storage_deleted = delete_images(f"doc_images/{url_hash}_")
                                except Exception as e:
                                    print(f"[warn] Storage 이미지 삭제 실패: {e}")
                            # DB에서 삭제 (CASCADE로 chunks도 삭제됨)
                            db_delete_document(url)
                            # 벡터도 명시적 삭제 (안전장치)
                            vec_deleted = 0
                            if _db_ok:
                                vec_deleted = delete_document(url, supabase_client=supabase_client)
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


# ══════════════════════════════════════════════════════════════
# 📤 새 문서 등록
# ══════════════════════════════════════════════════════════════
elif nav == "📤 새 문서 등록":
    st.subheader("새 문서 등록")

    source = st.selectbox(
        "출처 분류",
        ["admin", "eluocnc", "board"],
        format_func=SOURCE_LABELS.get,
    )
    category = st.text_input("카테고리 (선택사항)", placeholder="예: 업무가이드, 인사규정")

    input_method = st.radio("입력 방식", ["파일 업로드", "직접 입력", "URL 수집"], horizontal=True)

    title = ""
    content = ""
    url = ""
    _extraction_result = None

    if input_method == "파일 업로드":
        uploaded = st.file_uploader(
            "파일 선택",
            type=["pdf", "docx", "xlsx", "pptx", "md", "txt"],
        )
        title = st.text_input("제목 (빈칸이면 파일명 사용)", key="upload_title")
        url = st.text_input("관련 URL (선택사항)", key="upload_url")

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
        title = st.text_input("제목", key="manual_title")
        url = st.text_input("관련 URL (선택사항)", key="manual_url")
        content = st.text_area("본문 내용", height=300)
    else:
        # URL 수집
        from scraper.url_scraper import scrape_url, validate_url

        url_input = st.text_input("URL 입력", placeholder="https://example.com/page", key="url_input")

        col_preview, col_warn = st.columns([1, 3])
        with col_preview:
            preview_clicked = st.button("🔍 미리보기")

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
            existing = db_get_document(fetched_url)
            if existing:
                st.warning(f"이미 등록된 URL입니다: '{existing.get('title', '')}'")

            title = st.text_input("제목", value=st.session_state["url_fetched_title"], key="url_title")
            url = fetched_url
            content = st.text_area("본문 내용", value=st.session_state["url_fetched_content"], height=300, key="url_content")
        else:
            title = ""
            url = ""
            content = ""

    # URL 자동 생성
    if not url and title:
        url = generate_admin_url(title)

    # 제출 버튼
    if st.button("📥 등록", type="primary", disabled=not (title and content)):
        # 파일 업로드 모드에서 추출된 이미지 가져오기
        extracted_images = None
        if input_method == "파일 업로드" and _extraction_result and _extraction_result.images:
            extracted_images = _extraction_result.images

        with st.status("문서 등록 중...", expanded=True) as status:
            steps = st.container()

            # Step 1: DB에 문서 저장
            steps.write("⏳ Step 1: DB 저장 중...")
            doc_data = {
                "url": url,
                "title": title,
                "content": content,
                "source": source,
                "category": category,
            }
            saved = db_upsert_document(doc_data)
            doc_id = saved.get("id", "")
            steps.write("✅ Step 1: DB 저장 완료")

            if _db_ok and extracted_images:
                # 이미지 포함 미디어 파이프라인
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
                    supabase_client=supabase_client,
                    document_id=doc_id,
                    images=extracted_images,
                    progress_callback=_on_progress,
                )

                # attachments 메타데이터 업데이트
                if result.get("image_urls"):
                    doc_data["attachments"] = [{
                        "filename": uploaded.name if uploaded else "file",
                        "images": result["image_urls"],
                    }]
                    db_upsert_document(doc_data)

                summary = f"텍스트 {result['chunks']}개 청크 + 이미지 {result['images']}개"
                if result.get("deleted", 0) > 0:
                    summary += f" (기존 {result['deleted']}개 삭제 후 재등록)"
            else:
                # 텍스트 전용 파이프라인
                if _db_ok:
                    steps.write("⏳ Step 2: 임베딩 & 업로드 중...")
                    result = ingest_document(
                        title=title, content=content, source=source, url=url,
                        supabase_client=supabase_client,
                        document_id=doc_id,
                    )
                    steps.write(f"✅ Step 2: {result['chunks']}개 청크 업로드 완료")
                    summary = f"{result['chunks']}개 청크"
                    if result.get("deleted", 0) > 0:
                        summary += f" (기존 {result['deleted']}개 삭제 후 재등록)"
                else:
                    steps.write("⚠️ Supabase 미연결 — 벡터 인덱싱 건너뜀 (DB만 저장)")
                    summary = "DB만 저장"

            status.update(label="등록 완료!", state="complete")

        st.success(f"'{title}' 등록 완료! ({summary})")
        st.session_state["admin_nav"] = "📋 문서 관리"
        st.rerun()

    if not (title and content):
        st.info("제목과 내용을 입력하면 등록 버튼이 활성화됩니다.")
