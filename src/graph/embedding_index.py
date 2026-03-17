"""
임베딩 + Pinecone 벡터 인덱스 모듈.

sentence-transformers로 임베딩을 생성하고,
Pinecone에 저장/검색하는 기능을 제공한다.
"""

import hashlib
import os
from typing import Any

from sentence_transformers import SentenceTransformer
from pinecone import Pinecone, ServerlessSpec

EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
PINECONE_INDEX_NAME = "eluocnc-faq"
EMBED_DIM = 384


def get_embed_model() -> SentenceTransformer:
    """sentence-transformers 임베딩 모델을 로드한다."""
    return SentenceTransformer(EMBED_MODEL_NAME)


def embed_documents(model: SentenceTransformer, texts: list[str]) -> list[list[float]]:
    """문서 리스트를 임베딩 벡터 리스트로 변환한다."""
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=64)
    return embeddings.tolist()


def embed_query(model: SentenceTransformer, query: str) -> list[float]:
    """단일 쿼리를 임베딩 벡터로 변환한다."""
    return model.encode(query).tolist()


def init_pinecone(api_key: str | None = None, index_name: str = PINECONE_INDEX_NAME) -> Any:
    """Pinecone 인덱스를 초기화하고 반환한다. 인덱스가 없으면 생성."""
    api_key = api_key or os.environ.get("PINECONE_API_KEY", "")
    pc = Pinecone(api_key=api_key)

    if index_name not in [idx.name for idx in pc.list_indexes()]:
        pc.create_index(
            name=index_name,
            dimension=EMBED_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    return pc.Index(index_name)


def upsert_vectors(
    index: Any,
    ids: list[str],
    vectors: list[list[float]],
    metadata_list: list[dict],
    batch_size: int = 100,
    namespace: str = "",
) -> None:
    """벡터 + 메타데이터를 Pinecone에 업로드한다."""
    for i in range(0, len(ids), batch_size):
        batch = list(zip(
            ids[i:i + batch_size],
            vectors[i:i + batch_size],
            metadata_list[i:i + batch_size],
        ))
        kwargs = {"vectors": batch}
        if namespace:
            kwargs["namespace"] = namespace
        index.upsert(**kwargs)


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
            pc.create_index(
                name=index_name,
                dimension=EMBED_DIM,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
        return pc.Index(index_name)
    # pc가 이미 Index 객체인 경우 그대로 반환
    return pc


def list_all_doc_ids(pinecone_index, namespace: str = "") -> list[dict]:
    """Pinecone에서 doc_ prefix로 전체 벡터를 조회하여 URL별로 그룹핑한다."""
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
            for vid, vec_data in fetched.get("vectors", {}).items():
                meta = vec_data.get("metadata", {})
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


def search_pinecone(
    index: Any,
    query_vector: list[float],
    top_k: int = 5,
    filter: dict | None = None,
) -> list[dict]:
    """Pinecone에서 유사 벡터를 검색한다.

    Args:
        filter: Pinecone 메타데이터 필터 (예: {"source": {"$eq": "admin"}}).

    Returns:
        [{"id": str, "score": float, "metadata": dict}, ...]
    """
    kwargs: dict[str, Any] = {
        "vector": query_vector,
        "top_k": top_k,
        "include_metadata": True,
    }
    if filter:
        kwargs["filter"] = filter
    results = index.query(**kwargs)
    return [
        {
            "id": match["id"],
            "score": match["score"],
            "metadata": match.get("metadata", {}),
        }
        for match in results["matches"]
    ]


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
