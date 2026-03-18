"""
임베딩 + Pinecone 벡터 인덱스 모듈.

Pinecone Integrated Index (multilingual-e5-large)를 사용하여
서버사이드 임베딩 + 검색을 제공한다.
"""

import hashlib
import os
from typing import Any

from pinecone import Pinecone

PINECONE_INDEX_NAME = "eluocnc-faq-v2"
INTEGRATED_MODEL = "multilingual-e5-large"
INTEGRATED_TEXT_FIELD = "chunk_text"


def init_pinecone(api_key: str | None = None, index_name: str = PINECONE_INDEX_NAME) -> Any:
    """Integrated Index를 생성/연결한다. 인덱스가 없으면 생성."""
    api_key = api_key or os.environ.get("PINECONE_API_KEY", "")
    pc = Pinecone(api_key=api_key)

    if index_name not in [idx.name for idx in pc.list_indexes()]:
        pc.create_index_for_model(
            name=index_name,
            cloud="aws",
            region="us-east-1",
            embed={
                "model": INTEGRATED_MODEL,
                "field_map": {"text": INTEGRATED_TEXT_FIELD},
            },
        )

    return pc.Index(index_name)


def upsert_records(
    index: Any,
    ids: list[str],
    texts: list[str],
    metadata_list: list[dict],
    batch_size: int = 96,
    namespace: str = "",
) -> None:
    """텍스트 레코드를 Pinecone에 업로드한다. (서버사이드 임베딩)"""
    for i in range(0, len(ids), batch_size):
        records = []
        for j in range(i, min(i + batch_size, len(ids))):
            record = {"_id": ids[j], INTEGRATED_TEXT_FIELD: texts[j]}
            record.update(metadata_list[j])
            records.append(record)
        index.upsert_records(namespace=namespace or "__default__", records=records)


def search_records(
    index: Any,
    query_text: str,
    top_k: int = 5,
    filter: dict | None = None,
    namespace: str = "",
) -> list[dict]:
    """텍스트 쿼리로 Pinecone 검색한다. (서버사이드 임베딩)

    Returns:
        [{"id": str, "score": float, "metadata": dict}, ...]
    """
    query: dict[str, Any] = {
        "inputs": {"text": query_text},
        "top_k": top_k,
    }
    if filter:
        query["filter"] = filter
    results = index.search_records(
        namespace=namespace or "__default__",
        query=query,
    )

    # 기존 search_pinecone()와 동일한 반환 형식으로 정규화
    # SDK 반환 형식: {"result": {"hits": [...]}} 또는 직접 리스트
    if hasattr(results, "result"):
        hits = results.result.hits if hasattr(results.result, "hits") else []
    elif isinstance(results, dict):
        hits = results.get("result", {}).get("hits", results.get("records", []))
    else:
        hits = []
    normalized = []
    for hit in hits:
        d = hit.to_dict() if hasattr(hit, "to_dict") else (hit if isinstance(hit, dict) else {})
        hit_id = d.get("_id", "")
        hit_score = d.get("_score", 0)
        # Integrated Index: 메타데이터가 fields 안에 있음
        fields = d.get("fields", {})
        if fields:
            hit_meta = {k: v for k, v in fields.items() if k != INTEGRATED_TEXT_FIELD}
        else:
            hit_meta = {k: v for k, v in d.items() if k not in ("_id", "_score", "fields")}
        normalized.append({"id": hit_id, "score": hit_score, "metadata": hit_meta})
    return normalized


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
            # 단락 자체가 max_chars 초과 시 하드컷
            while len(para) > max_chars:
                chunks.append(para[:max_chars])
                para = para[max_chars - overlap:]
            current = para

    if current:
        chunks.append(current)

    # overlap 적용: 이전 청크의 끝부분을 다음 청크 앞에 추가
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


def delete_doc_vectors(pinecone_index, url: str, namespace: str = "") -> int:
    """URL에 해당하는 모든 벡터를 Pinecone에서 삭제한다. 삭제된 벡터 수를 반환."""
    prefix = _doc_prefix(url)
    deleted = 0
    try:
        listed = pinecone_index.list(prefix=prefix, namespace=namespace)
        all_ids = []
        # list()는 페이지네이션된 결과를 반환할 수 있음
        if hasattr(listed, '__iter__'):
            for page in listed:
                if isinstance(page, list):
                    all_ids.extend(page)
                elif isinstance(page, str):
                    all_ids.append(page)
        if all_ids:
            pinecone_index.delete(ids=all_ids, namespace=namespace)
            deleted = len(all_ids)
    except Exception as e:
        print(f"[warn] 벡터 삭제 중 오류: {e}")
    return deleted


def get_or_create_index(pc, index_name: str = PINECONE_INDEX_NAME):
    """이미 초기화된 Pinecone 클라이언트로 인덱스를 가져오거나 생성한다."""
    if isinstance(pc, Pinecone):
        if index_name not in [idx.name for idx in pc.list_indexes()]:
            pc.create_index_for_model(
                name=index_name,
                cloud="aws",
                region="us-east-1",
                embed={
                    "model": INTEGRATED_MODEL,
                    "field_map": {"text": INTEGRATED_TEXT_FIELD},
                },
            )
        return pc.Index(index_name)
    # pc가 이미 Index 객체인 경우 그대로 반환
    return pc


def list_all_doc_ids(pinecone_index, namespace: str = "") -> list[dict]:
    """Pinecone에서 doc_ prefix로 전체 레코드를 조회하여 URL별로 그룹핑한다."""
    try:
        all_ids: list[str] = []
        listed = pinecone_index.list(prefix="doc_", namespace=namespace)
        if hasattr(listed, '__iter__'):
            for page in listed:
                if isinstance(page, list):
                    all_ids.extend(page)
                elif isinstance(page, str):
                    all_ids.append(page)

        if not all_ids:
            return []

        # ID에서 메타데이터를 가져오기 위해 fetch
        # 배치로 fetch (Pinecone은 한 번에 최대 1000개)
        url_groups: dict[str, dict] = {}
        for i in range(0, len(all_ids), 100):
            batch_ids = all_ids[i:i + 100]
            fetched = pinecone_index.fetch(ids=batch_ids, namespace=namespace)
            # Integrated Index fetch 반환 형식 정규화
            vectors = fetched.get("vectors", {}) if isinstance(fetched, dict) else {}
            if not vectors and hasattr(fetched, "vectors"):
                vectors = fetched.vectors or {}
            for vid, vec_data in vectors.items():
                if isinstance(vec_data, dict):
                    meta = vec_data.get("metadata", {})
                else:
                    meta = getattr(vec_data, "metadata", {}) or {}
                url = meta.get("url", "")
                prefix = "_".join(vid.split("_")[:3]) + "_"  # doc_{hash}_
                if url not in url_groups:
                    url_groups[url] = {
                        "title": meta.get("title", ""),
                        "url": url,
                        "source": meta.get("source", ""),
                        "doc_prefix": prefix,
                        "chunk_count": 0,
                    }
                url_groups[url]["chunk_count"] += 1

        return sorted(url_groups.values(), key=lambda x: x["title"])
    except Exception as e:
        print(f"[warn] 문서 목록 조회 실패: {e}")
        return []


def rerank_results(
    pc: Any,
    query: str,
    documents: list[dict],
    top_n: int = 5,
    model: str = "bge-reranker-v2-m3",
) -> list[dict]:
    """Pinecone Inference API로 결과를 리랭킹한다.

    Args:
        pc: Pinecone 클라이언트 인스턴스.
        query: 원본 검색 쿼리.
        documents: vector_search에서 반환된 결과 리스트.
        top_n: 리랭킹 후 반환할 최대 결과 수.
        model: 리랭킹 모델명.

    Returns:
        리랭킹된 결과 리스트 (rerank_score 포함).
    """
    if not documents:
        return []

    doc_texts = [d.get("content", "") for d in documents]
    response = pc.inference.rerank(
        model=model,
        query=query,
        documents=doc_texts,
        top_n=top_n,
        return_documents=False,
    )
    reranked = []
    for item in response.data:
        doc = documents[item.index].copy()
        doc["rerank_score"] = item.score
        reranked.append(doc)
    return reranked
