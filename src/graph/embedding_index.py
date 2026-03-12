"""
임베딩 + Pinecone 벡터 인덱스 모듈.

sentence-transformers로 임베딩을 생성하고,
Pinecone에 저장/검색하는 기능을 제공한다.
"""

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
) -> None:
    """벡터 + 메타데이터를 Pinecone에 업로드한다."""
    for i in range(0, len(ids), batch_size):
        batch = list(zip(
            ids[i:i + batch_size],
            vectors[i:i + batch_size],
            metadata_list[i:i + batch_size],
        ))
        index.upsert(vectors=batch)


def search_pinecone(
    index: Any,
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Pinecone에서 유사 벡터를 검색한다.

    Returns:
        [{"id": str, "score": float, "metadata": dict}, ...]
    """
    results = index.query(vector=query_vector, top_k=top_k, include_metadata=True)
    return [
        {
            "id": match["id"],
            "score": match["score"],
            "metadata": match.get("metadata", {}),
        }
        for match in results["matches"]
    ]
