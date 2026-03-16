"""
Hybrid RAG 인덱스 빌드 스크립트.

JSON 데이터 → 임베딩 + Pinecone 업로드 + 엔티티/관계 추출 + 지식그래프 구축.

사용법:
    python src/graph/build_index.py
"""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# src를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.embedding_index import (
    get_embed_model,
    embed_documents,
    init_pinecone,
    upsert_vectors,
)
from graph.graph_builder import (
    extract_entities_from_doc,
    resolve_duplicate_entities,
    build_networkx_graph,
    save_graph,
)
from graph.image_describer import (
    collect_all_image_paths,
    describe_images_batch,
)
from graph.ingest import ingest_images


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FAQ_DATA_PATH = DATA_DIR / "faq_lineworks.json"
BOARD_DATA_PATH = DATA_DIR / "board_lineworks.json"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"
GRAPH_PATH = DATA_DIR / "knowledge_graph.json"


def load_documents() -> list[dict]:
    """3개의 JSON 데이터 파일을 로드하고 병합한다."""
    all_items = []
    source_map = {
        FAQ_DATA_PATH: "faq",
        BOARD_DATA_PATH: "board",
        ELUOCNC_DATA_PATH: "eluocnc",
    }

    for path, default_source in source_map.items():
        if not path.exists():
            print(f"[warn] 데이터 파일 없음 (건너뜀): {path}")
            continue
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            item.setdefault("source", default_source)
        all_items.extend(items)

    # 중복 제거 + 짧은 콘텐츠 제외
    seen_urls: set[str] = set()
    unique = []
    for item in all_items:
        url = item.get("url", "")
        base_url = url.split("?")[0] if url else ""
        content = item.get("content", "").strip()
        if len(content) < 50:
            continue
        if base_url and base_url in seen_urls:
            continue
        if base_url:
            seen_urls.add(base_url)
        unique.append(item)

    return unique


async def build_graph(documents: list[dict]):
    """엔티티/관계를 추출하고 지식그래프를 구축한다."""
    print(f"\n[3/4] 엔티티/관계 추출 중... ({len(documents)}개 문서)")
    extractions = []
    all_entities = []

    for i, doc in enumerate(documents):
        title = doc.get("title", "")
        content = doc.get("content", "")
        try:
            extraction = await extract_entities_from_doc(title, content)
            extractions.append(extraction)
            all_entities.extend([e.model_dump() for e in extraction.entities])
            if (i + 1) % 50 == 0 or i == len(documents) - 1:
                print(f"  ... {i + 1}/{len(documents)} 완료")
        except Exception as e:
            print(f"  [error] 문서 {i} ({title[:30]}): {e}")
            from graph.graph_builder import DocumentGraphExtraction
            extractions.append(DocumentGraphExtraction(entities=[], relationships=[]))

    print(f"  추출된 엔티티: {len(all_entities)}개")

    # 엔티티 중복 해결
    print("\n  엔티티 중복 해결 중...")
    canonical_map = resolve_duplicate_entities(all_entities)
    unique_entities = len(set(canonical_map.values()))
    print(f"  병합 후 고유 엔티티: {unique_entities}개")

    # 그래프 구축
    G = build_networkx_graph(documents, extractions, canonical_map)
    entity_nodes = sum(1 for _, d in G.nodes(data=True) if d.get("node_type") == "ENTITY")
    doc_nodes = sum(1 for _, d in G.nodes(data=True) if d.get("node_type") == "DOCUMENT")
    print(f"  그래프: {entity_nodes} 엔티티, {doc_nodes} 문서, {G.number_of_edges()} 엣지")

    return G


def build_embeddings(documents: list[dict]):
    """임베딩을 생성하고 Pinecone에 업로드한다."""
    print(f"\n[1/4] 임베딩 생성 중... ({len(documents)}개 문서)")
    model = get_embed_model()
    texts = [f"{doc.get('title', '')} {doc.get('content', '')}" for doc in documents]
    vectors = embed_documents(model, texts)
    print(f"  임베딩 완료: {len(vectors)}개 벡터 (차원: {len(vectors[0])})")

    # Pinecone 업로드
    print("\n  Pinecone 인덱스 초기화 중...")
    index = init_pinecone()

    ids = [f"doc_{i}" for i in range(len(documents))]
    metadata_list = [
        {
            "title": doc.get("title", "")[:200],
            "url": doc.get("url", ""),
            "source": doc.get("source", "faq"),
            "content_preview": doc.get("content", "")[:500],
        }
        for doc in documents
    ]

    print(f"  Pinecone에 {len(ids)}개 벡터 업로드 중...")
    upsert_vectors(index, ids, vectors, metadata_list)
    print("  업로드 완료!")

    return model, index


def build_image_embeddings(documents: list[dict], embed_model, pinecone_index):
    """이미지 설명을 생성하고 임베딩 → Pinecone에 업로드한다."""
    image_paths = collect_all_image_paths(documents)
    if not image_paths:
        print("\n[2/4] 이미지 없음 — 건너뜀")
        return

    print(f"\n[2/4] 이미지 설명 생성 중... ({len(image_paths)}장)")
    descriptions = describe_images_batch(image_paths)

    # 이미지 설명 임베딩 + Pinecone 업로드
    total_uploaded = 0
    for doc in documents:
        doc_images = []
        for att in doc.get("attachments", []):
            doc_images.extend(att.get("images", []))
        if not doc_images:
            continue

        count = ingest_images(
            image_paths=doc_images,
            title=doc.get("title", ""),
            url=doc.get("url", ""),
            source=doc.get("source", ""),
            embed_model=embed_model,
            pinecone_index=pinecone_index,
            descriptions=descriptions,
        )
        total_uploaded += count

    print(f"  이미지 벡터 업로드 완료: {total_uploaded}개")


async def main():
    print("=" * 60)
    print("Hybrid RAG 인덱스 빌드 시작")
    print("=" * 60)

    documents = load_documents()
    print(f"\n로드된 문서: {len(documents)}개")
    for source in ("faq", "board", "eluocnc"):
        count = sum(1 for d in documents if d.get("source") == source)
        print(f"  - {source}: {count}건")

    # 1. 텍스트 임베딩 + Pinecone
    embed_model, pinecone_index = build_embeddings(documents)

    # 2. 이미지 설명 생성 + 임베딩 + Pinecone
    build_image_embeddings(documents, embed_model, pinecone_index)

    # 3. 엔티티/관계 추출 + 지식그래프
    G = await build_graph(documents)

    # 4. 그래프 저장
    print(f"\n[4/4] 지식그래프 저장: {GRAPH_PATH}")
    save_graph(G, GRAPH_PATH)

    print("\n" + "=" * 60)
    print("빌드 완료!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
