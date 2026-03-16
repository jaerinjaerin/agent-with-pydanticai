"""
단건 문서 인제스트 파이프라인.

어드민 UI에서 문서를 첨부/제출하면 이 모듈이 호출되어
해당 문서만 청킹 → 임베딩 → Pinecone upsert를 수행한다.
그래프 업데이트도 포함.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.embedding_index import (
    chunk_text,
    delete_doc_vectors,
    embed_documents,
    make_doc_id,
    upsert_vectors,
)
from graph.image_describer import DESCRIPTIONS_PATH


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GRAPH_PATH = DATA_DIR / "knowledge_graph.json"


def _load_image_descriptions() -> dict[str, str]:
    """캐시된 이미지 설명을 로드한다."""
    if DESCRIPTIONS_PATH.exists():
        import json
        with open(DESCRIPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def ingest_images(
    image_paths: list[str],
    title: str,
    url: str,
    source: str,
    embed_model,
    pinecone_index,
    descriptions: dict[str, str] | None = None,
    namespace: str = "",
) -> int:
    """이미지 설명을 임베딩하여 Pinecone에 업로드한다.

    Returns:
        업로드된 이미지 청크 수.
    """
    if not image_paths:
        return 0

    if descriptions is None:
        descriptions = _load_image_descriptions()

    ids = []
    texts = []
    metadata_list = []

    for i, img_path in enumerate(image_paths):
        desc = descriptions.get(img_path, "")
        if not desc:
            continue

        text = f"{title} - 이미지 {i + 1}: {desc}"
        import hashlib
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        vec_id = f"doc_{url_hash}_img_{i}"

        ids.append(vec_id)
        texts.append(text)
        metadata_list.append({
            "title": title[:200],
            "url": url,
            "source": source,
            "type": "image",
            "image_path": img_path,
            "content_preview": desc[:500],
            "chunk_index": i,
        })

    if not ids:
        return 0

    vectors = embed_documents(embed_model, texts)
    upsert_vectors(pinecone_index, ids, vectors, metadata_list, namespace=namespace)
    return len(ids)


def ingest_document(
    title: str,
    content: str,
    source: str,
    url: str,
    embed_model,
    pinecone_index,
    namespace: str = "",
) -> dict:
    """단일 문서를 청킹 → 임베딩 → Pinecone에 업로드한다.

    기존에 같은 URL의 문서가 있으면 삭제 후 재업로드.

    Returns:
        {"chunks": 청크 수, "deleted": 삭제된 기존 청크 수}
    """
    # 1) 기존 벡터 삭제 (같은 URL로 이미 등록된 경우)
    deleted = delete_doc_vectors(pinecone_index, url, namespace=namespace)

    # 2) 청킹
    full_text = f"{title}\n{content}" if title else content
    chunks = chunk_text(full_text, max_chars=1000, overlap=200)

    # 3) ID, 메타데이터 준비
    ids = [make_doc_id(url, i) for i in range(len(chunks))]
    metadata_list = [
        {
            "title": title[:200],
            "url": url,
            "source": source,
            "content_preview": chunk[:500],
            "chunk_index": i,
            "total_chunks": len(chunks),
        }
        for i, chunk in enumerate(chunks)
    ]

    # 4) 임베딩
    vectors = embed_documents(embed_model, chunks)

    # 5) Pinecone upsert
    upsert_vectors(pinecone_index, ids, vectors, metadata_list, namespace=namespace)

    return {"chunks": len(chunks), "deleted": deleted}


def delete_document(url: str, pinecone_index, namespace: str = "") -> int:
    """문서를 Pinecone에서 삭제한다. 삭제된 벡터 수를 반환."""
    return delete_doc_vectors(pinecone_index, url, namespace=namespace)


async def ingest_with_graph(
    title: str,
    content: str,
    source: str,
    url: str,
    embed_model,
    pinecone_index,
    update_graph: bool = True,
    namespace: str = "",
) -> dict:
    """문서 인제스트 + 지식그래프 업데이트를 수행한다.

    Returns:
        {"chunks": int, "deleted": int, "graph_updated": bool}
    """
    # Pinecone 인제스트
    result = ingest_document(
        title=title, content=content, source=source, url=url,
        embed_model=embed_model, pinecone_index=pinecone_index,
        namespace=namespace,
    )

    graph_updated = False
    if update_graph and GRAPH_PATH.exists():
        try:
            from graph.graph_builder import load_graph, save_graph, add_doc_to_graph

            graph = load_graph(GRAPH_PATH)
            doc = {"title": title, "content": content, "url": url, "source": source}
            graph = await add_doc_to_graph(graph, doc, embed_model)
            save_graph(graph, GRAPH_PATH)
            graph_updated = True
        except Exception as e:
            print(f"[warn] 그래프 업데이트 실패: {e}")

    return {**result, "graph_updated": graph_updated}


async def delete_with_graph(
    url: str, pinecone_index, namespace: str = ""
) -> dict:
    """문서를 Pinecone + 지식그래프에서 삭제한다.

    Returns:
        {"deleted": int, "graph_updated": bool}
    """
    deleted = delete_document(url, pinecone_index, namespace=namespace)

    graph_updated = False
    if GRAPH_PATH.exists():
        try:
            from graph.graph_builder import load_graph, save_graph, remove_doc_from_graph

            graph = load_graph(GRAPH_PATH)
            graph = remove_doc_from_graph(graph, url)
            save_graph(graph, GRAPH_PATH)
            graph_updated = True
        except Exception as e:
            print(f"[warn] 그래프 삭제 실패: {e}")

    return {"deleted": deleted, "graph_updated": graph_updated}
