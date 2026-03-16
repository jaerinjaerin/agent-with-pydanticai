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

from graph.embedding_index import get_embed_model, init_pinecone
from graph.ingest import ingest_document, delete_document, ingest_with_graph, delete_with_graph
from graph.data_store import (
    load_items, add_item, update_item, delete_item, find_item_by_url,
)

st.set_page_config(page_title="문서 관리", page_icon="📄", layout="wide")
st.title("📄 문서 관리")
st.caption("문서를 등록/수정/삭제하여 챗봇 지식 베이스를 관리합니다.")

# ── 상수 ──
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ADMIN_DATA_PATH = DATA_DIR / "admin_documents.json"
BOARD_DATA_PATH = DATA_DIR / "board_lineworks.json"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"
FAQ_DATA_PATH = DATA_DIR / "faq_lineworks.json"

ITEMS_PER_PAGE = 15

SOURCE_LABELS = {
    "admin": "어드민",
    "board": "사내 게시판",
    "eluocnc": "회사 홈페이지",
    "faq": "FAQ",
}

# ── Streamlit Cloud secrets → 환경변수 주입 ──
for key in ("PINECONE_API_KEY", "ANTHROPIC_API_KEY"):
    if key in st.secrets and key not in os.environ:
        os.environ[key] = st.secrets[key]


# ── 리소스 로드 ──

@st.cache_resource
def load_resources():
    """임베딩 모델 + Pinecone 인덱스를 로드한다."""
    api_key = os.environ.get("PINECONE_API_KEY", "")
    if not api_key:
        return None, None
    embed_model = get_embed_model()
    pc_index = init_pinecone(api_key)
    return embed_model, pc_index


try:
    embed_model, pinecone_index = load_resources()
except Exception as e:
    embed_model, pinecone_index = None, None
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


# ── 전체 JSON 데이터 로드 헬퍼 ──

def load_all_documents() -> list[dict]:
    """모든 JSON 데이터 소스에서 문서를 로드한다."""
    all_items = []
    source_map = {
        ADMIN_DATA_PATH: "admin",
        BOARD_DATA_PATH: "board",
        ELUOCNC_DATA_PATH: "eluocnc",
        FAQ_DATA_PATH: "faq",
    }
    for path, default_source in source_map.items():
        if not path.exists():
            continue
        try:
            import json
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
            for item in items:
                item.setdefault("source", default_source)
                item["_data_path"] = str(path)
            all_items.extend(items)
        except Exception:
            continue
    return all_items


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
        edit_source = st.selectbox(
            "출처",
            ["admin", "board", "eluocnc", "faq"],
            index=["admin", "board", "eluocnc", "faq"].index(doc.get("source", "admin")),
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

                step1.write("⏳ JSON 업데이트 중...")
                updated_fields = {
                    "title": edit_title,
                    "content": edit_content,
                    "source": edit_source,
                    "category": edit_category,
                }
                update_item(ADMIN_DATA_PATH, url, updated_fields)
                step1.write("✅ JSON 업데이트 완료")

                if _pinecone_ok:
                    step2.write("⏳ 기존 벡터 삭제 & 재임베딩 중...")
                    result = run_async(ingest_with_graph(
                        title=edit_title, content=edit_content,
                        source=edit_source, url=url,
                        embed_model=embed_model, pinecone_index=pinecone_index,
                        update_graph=True,
                    ))
                    step2.write(f"✅ {result['chunks']}개 청크 재업로드 완료")
                    if result.get("graph_updated"):
                        step3.write("✅ 지식그래프 업데이트 완료")
                else:
                    step2.write("⚠️ Pinecone 미연결 — 벡터 인덱싱 건너뜀")

                st.cache_resource.clear()
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


# ── 탭 구성 (2탭: 문서 관리 → 새 문서 등록) ──
tab_list, tab_upload = st.tabs(["📋 문서 관리", "📤 새 문서 등록"])

# ══════════════════════════════════════════════════════════════
# 탭 1: 📋 문서 관리
# ══════════════════════════════════════════════════════════════
with tab_list:
    st.subheader("등록된 문서 목록")

    # 필터
    col_filter, col_search, col_refresh = st.columns([2, 3, 1])
    with col_filter:
        source_filter = st.selectbox(
            "출처 필터",
            ["전체", "admin", "board", "eluocnc", "faq"],
            format_func=lambda x: "전체" if x == "전체" else SOURCE_LABELS.get(x, x),
        )
    with col_search:
        keyword = st.text_input("키워드 검색", placeholder="제목 또는 내용 검색")
    with col_refresh:
        st.write("")  # spacer
        if st.button("🔄 새로고침"):
            st.rerun()

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

    # 필터 변경 감지 → 페이지 리셋
    current_filter_key = f"{source_filter}|{keyword}"
    if st.session_state.get("_last_filter_key") != current_filter_key:
        st.session_state["_last_filter_key"] = current_filter_key
        st.session_state["admin_page"] = 0

    # 통계
    st.metric("검색 결과", f"{len(filtered_docs)}건 / 전체 {len(all_docs)}건")

    if not filtered_docs:
        st.info("조건에 맞는 문서가 없습니다.")
    else:
        # ── 페이지네이션 계산 ──
        total_pages = max(1, (len(filtered_docs) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        current_page = st.session_state.get("admin_page", 0)
        # 삭제 후 페이지 범위 초과 방지
        if current_page >= total_pages:
            current_page = total_pages - 1
            st.session_state["admin_page"] = current_page

        start_idx = current_page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, len(filtered_docs))
        page_docs = filtered_docs[start_idx:end_idx]

        # ── 문서 목록 렌더링 ──
        for i, doc in enumerate(page_docs):
            global_idx = start_idx + i
            src = doc.get("source", "")
            src_label = SOURCE_LABELS.get(src, src)
            title = doc.get("title", "(제목 없음)")
            url = doc.get("url", "")
            data_path = doc.get("_data_path", "")

            col1, col2, col3 = st.columns([5, 1, 1])
            with col1:
                st.markdown(f"**[{src_label}]** {title}")
                if url and not url.startswith("admin://"):
                    st.caption(url)
                if doc.get("category"):
                    st.caption(f"카테고리: {doc['category']}")
            with col2:
                if st.button("👁️ 보기", key=f"view_{global_idx}_{url}"):
                    st.session_state["_dialog_doc"] = doc
                    st.session_state["_dialog_is_admin"] = src == "admin"
                    show_doc_detail_dialog()
            with col3:
                if st.button("🗑️", key=f"del_{global_idx}_{url}"):
                    st.session_state[f"confirm_delete_{url}"] = True

            # 삭제 확인
            if st.session_state.get(f"confirm_delete_{url}"):
                st.warning(f"'{title}' 문서를 삭제하시겠습니까?")
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("삭제 확인", key=f"yes_del_{global_idx}_{url}", type="primary"):
                        with st.spinner("삭제 중..."):
                            # JSON에서 삭제
                            if data_path:
                                delete_item(Path(data_path), url)
                            # Pinecone + 그래프에서 삭제
                            vec_deleted = 0
                            if _pinecone_ok:
                                result = run_async(delete_with_graph(url, pinecone_index))
                                vec_deleted = result['deleted']
                            st.cache_resource.clear()
                        del st.session_state[f"confirm_delete_{url}"]
                        st.toast(f"'{title}' 삭제 완료 (벡터 {vec_deleted}개)")
                        st.rerun()
                with col_no:
                    if st.button("취소", key=f"no_del_{global_idx}_{url}"):
                        del st.session_state[f"confirm_delete_{url}"]
                        st.rerun()

            st.divider()

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
# 탭 2: 📤 새 문서 등록
# ══════════════════════════════════════════════════════════════
with tab_upload:
    st.subheader("새 문서 등록")

    source = st.selectbox(
        "출처 분류",
        ["admin", "board", "eluocnc", "faq"],
        format_func=SOURCE_LABELS.get,
    )
    category = st.text_input("카테고리 (선택사항)", placeholder="예: 업무가이드, 인사규정")

    input_method = st.radio("입력 방식", ["파일 업로드", "직접 입력", "URL 수집"], horizontal=True)

    title = ""
    content = ""
    url = ""

    if input_method == "파일 업로드":
        uploaded = st.file_uploader(
            "파일 선택",
            type=["pdf", "docx", "xlsx", "pptx", "md", "txt"],
        )
        title = st.text_input("제목 (빈칸이면 파일명 사용)", key="upload_title")
        url = st.text_input("관련 URL (선택사항)", key="upload_url")

        if uploaded:
            if not title:
                title = uploaded.name
            content = extract_file_content(uploaded)
            if content:
                with st.expander("추출된 텍스트 미리보기"):
                    st.text(content[:2000] + ("..." if len(content) > 2000 else ""))
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
            existing = find_item_by_url(ADMIN_DATA_PATH, fetched_url)
            if existing:
                st.warning(f"이미 등록된 URL입니다: '{existing.get('title', '')}'")

            title = st.text_input("제목", value=st.session_state["url_fetched_title"], key="url_title")
            url = fetched_url
            content = st.text_area("본문 내용", value=st.session_state["url_fetched_content"], height=300, key="url_content")
        else:
            title = ""
            url = ""
            content = ""

    extract_entities = st.checkbox("엔티티 추출 (지식그래프 업데이트)", value=True)

    # URL 자동 생성
    if not url and title:
        url = generate_admin_url(title)

    # 제출 버튼
    if st.button("📥 등록", type="primary", disabled=not (title and content)):
        with st.status("문서 등록 중...", expanded=True) as status:
            step1 = st.empty()
            step2 = st.empty()
            step3 = st.empty()
            step4 = st.empty()

            # Step 1: 텍스트 준비
            step1.write("✅ 텍스트 준비 완료")

            # Step 2: JSON 저장
            step2.write("⏳ JSON 저장 중...")
            doc_data = {
                "title": title,
                "content": content,
                "url": url,
                "source": source,
                "category": category,
            }
            add_item(ADMIN_DATA_PATH, doc_data)
            step2.write("✅ JSON 저장 완료")

            if _pinecone_ok:
                if extract_entities:
                    # 그래프 포함 인제스트
                    step3.write("⏳ 임베딩 & Pinecone 업로드 중...")
                    result = run_async(ingest_with_graph(
                        title=title, content=content, source=source, url=url,
                        embed_model=embed_model, pinecone_index=pinecone_index,
                        update_graph=True,
                    ))
                    step3.write(f"✅ {result['chunks']}개 청크 업로드 완료")

                    if result.get("graph_updated"):
                        step4.write("✅ 지식그래프 업데이트 완료")
                    else:
                        step4.write("⚠️ 지식그래프 업데이트 건너뜀 (그래프 파일 없음 또는 오류)")
                else:
                    # Pinecone만 인제스트
                    step3.write("⏳ 임베딩 & Pinecone 업로드 중...")
                    result = ingest_document(
                        title=title, content=content, source=source, url=url,
                        embed_model=embed_model, pinecone_index=pinecone_index,
                    )
                    step3.write(f"✅ {result['chunks']}개 청크 업로드 완료")

                if result.get("deleted", 0) > 0:
                    status.write(f"ℹ️ 기존 문서 {result['deleted']}개 청크 삭제 후 재등록")
            else:
                step3.write("⚠️ Pinecone 미연결 — 벡터 인덱싱 건너뜀 (JSON만 저장)")

            # 캐시 갱신
            st.cache_resource.clear()
            status.update(label="등록 완료!", state="complete")

        st.success(f"'{title}' 등록 완료!")

    if not (title and content):
        st.info("제목과 내용을 입력하면 등록 버튼이 활성화됩니다.")
