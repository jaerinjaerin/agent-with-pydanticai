"""
Gemini 임베딩 모듈.

google-genai SDK를 사용하여 텍스트 임베딩을 생성한다.
모델: text-embedding-004 (768차원).
"""

import os

from google import genai

_client = None

EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIM = 768


def _get_client():
    """Gemini 클라이언트를 싱글턴으로 반환한다."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY 환경변수가 설정되지 않았습니다.")
    _client = genai.Client(api_key=api_key)
    return _client


def embed_texts(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
    batch_size: int = 100,
) -> list[list[float]]:
    """텍스트 리스트를 임베딩 벡터로 변환한다.

    Args:
        texts: 임베딩할 텍스트 리스트.
        task_type: "RETRIEVAL_DOCUMENT" (인덱싱) 또는 "RETRIEVAL_QUERY" (검색).
        batch_size: 한 번에 처리할 텍스트 수.

    Returns:
        임베딩 벡터 리스트 (각 768차원).
    """
    client = _get_client()
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={"task_type": task_type},
        )
        for emb in response.embeddings:
            all_embeddings.append(emb.values)

    return all_embeddings
