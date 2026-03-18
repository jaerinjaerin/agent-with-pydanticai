"""
Supabase pgvector 벡터 검색 모듈.

Pinecone Integrated Index를 대체하여 Supabase + Gemini 임베딩으로
벡터 검색을 제공한다.
"""

import hashlib
from typing import Any

from graph.embeddings import embed_texts


# ── 순수 텍스트 유틸리티 (embedding_index.py에서 이관) ──

def chunk_text(text: str, max_chars: int = 1000, overlap: int = 200) -> list[str]:
    """텍스트를 청크로 분할한다. 단락(\\n\\n) 기준 우선 분할, 초과 시 하드컷."""
    if not text.strip():
        return [text.strip()] if text.strip() else [""]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            while len(para) > max_chars:
                chunks.append(para[:max_chars])
                para = para[max_chars - overlap:]
            current = para

    if current:
        chunks.append(current)

    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + "\n" + chunks[i])
        chunks = overlapped

    return chunks if chunks else [""]


def make_doc_id(url: str, chunk_index: int) -> str:
    """URL 기반 결정론적 문서 ID를 생성한다."""
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return f"doc_{url_hash}_{chunk_index}"


def _doc_prefix(url: str) -> str:
    """URL에서 doc prefix를 반환한다."""
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return f"doc_{url_hash}_"


# ── Supabase 벡터 DB 함수 ──

def init_db() -> Any:
    """Supabase 클라이언트를 초기화하고 반환한다."""
    from storage.supabase_client import get_client
    return get_client()


def upsert_records(
    client: Any,
    ids: list[str],
    texts: list[str],
    metadata_list: list[dict],
    batch_size: int = 96,
    namespace: str = "",
) -> None:
    """텍스트 레코드를 Supabase document_chunks에 업로드한다.

    Gemini로 임베딩을 생성한 뒤, document_chunks 테이블에 upsert한다.
    document_id는 metadata의 url로부터 documents 테이블에서 조회한다.
    """
    if not ids:
        return

    # 임베딩 생성
    embeddings = embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

    for i in range(0, len(ids), batch_size):
        batch_end = min(i + batch_size, len(ids))
        rows = []
        for j in range(i, batch_end):
            meta = metadata_list[j]
            row = {
                "id": ids[j],
                "document_id": meta.get("document_id", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "total_chunks": meta.get("total_chunks", 1),
                "chunk_text": texts[j],
                "content_preview": meta.get("content_preview", texts[j][:500]),
                "embedding": embeddings[j],
                "chunk_type": meta.get("type", "text"),
                "image_path": meta.get("image_path"),
                "source": meta.get("source", ""),
            }
            rows.append(row)

        client.table("document_chunks").upsert(rows).execute()


def search_records(
    client: Any,
    query_text: str,
    top_k: int = 5,
    filter: dict | None = None,
    namespace: str = "",
) -> list[dict]:
    """하이브리드 검색 (벡터 + FTS RRF).

    Returns:
        [{"id": str, "score": float, "metadata": dict}, ...]
    """
    # 쿼리 임베딩 생성
    query_embedding = embed_texts([query_text], task_type="RETRIEVAL_QUERY")[0]

    # 소스 필터 추출
    source_filter = None
    if filter and "$eq" in filter.get("source", {}):
        source_filter = filter["source"]["$eq"]
    elif filter and isinstance(filter.get("source"), str):
        source_filter = filter["source"]

    # hybrid_search RPC 호출
    result = client.rpc("hybrid_search", {
        "query_embedding": query_embedding,
        "query_text": query_text,
        "match_count": top_k,
        "source_filter": source_filter,
    }).execute()

    # 기존 Pinecone 반환 형식으로 정규화
    normalized = []
    for row in result.data or []:
        normalized.append({
            "id": row["id"],
            "score": float(row["score"]),
            "metadata": {
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "source": row.get("source", ""),
                "content_preview": row.get("content_preview", ""),
                "chunk_index": row.get("chunk_index", 0),
                "total_chunks": row.get("total_chunks", 1),
                "type": row.get("chunk_type", "text"),
                "image_path": row.get("image_path"),
            },
        })
    return normalized


def delete_doc_vectors(client: Any, url: str, namespace: str = "") -> int:
    """URL에 해당하는 모든 벡터를 삭제한다."""
    prefix = _doc_prefix(url)
    try:
        result = client.table("document_chunks").delete().like("id", f"{prefix}%").execute()
        return len(result.data) if result.data else 0
    except Exception as e:
        print(f"[warn] 벡터 삭제 중 오류: {e}")
        return 0


def list_all_doc_ids(client: Any, namespace: str = "") -> list[dict]:
    """document_chunks에서 URL별로 그룹핑한 문서 목록을 반환한다."""
    try:
        result = client.rpc("get_doc_summary", {}).execute()
        if result.data:
            return sorted(result.data, key=lambda x: x.get("title", ""))
    except Exception:
        pass

    # RPC 없으면 직접 쿼리
    try:
        result = (
            client.table("document_chunks")
            .select("id, source, document_id")
            .execute()
        )
        if not result.data:
            return []

        # document_id별 그룹핑
        doc_groups: dict[str, dict] = {}
        for row in result.data:
            doc_id = row.get("document_id", "")
            if doc_id not in doc_groups:
                doc_groups[doc_id] = {
                    "document_id": doc_id,
                    "source": row.get("source", ""),
                    "chunk_count": 0,
                }
            doc_groups[doc_id]["chunk_count"] += 1

        return sorted(doc_groups.values(), key=lambda x: x.get("document_id", ""))
    except Exception as e:
        print(f"[warn] 문서 목록 조회 실패: {e}")
        return []
