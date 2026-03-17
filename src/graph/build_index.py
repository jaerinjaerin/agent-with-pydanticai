"""
VectorRAG 인덱스 빌드 스크립트.

JSON 데이터 → 청킹 → 임베딩 + Pinecone 업로드.

사용법:
    python src/graph/build_index.py
"""

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
    chunk_text,
    make_doc_id,
)
from graph.image_describer import (
    collect_all_image_paths,
    describe_images_batch,
)
from graph.ingest import ingest_images


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ELUOCNC_DATA_PATH = DATA_DIR / "eluocnc.json"
ADMIN_DATA_PATH = DATA_DIR / "admin_documents.json"
BOARD_DATA_PATH = DATA_DIR / "board_documents.json"


def load_documents() -> list[dict]:
    """JSON 데이터 파일을 로드하고 병합한다."""
    all_items = []
    source_map = {
        ELUOCNC_DATA_PATH: "eluocnc",
        ADMIN_DATA_PATH: "admin",
        BOARD_DATA_PATH: "FAQ",
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
        content = item.get("content", "").strip()
        if len(content) < 50:
            continue
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        unique.append(item)

    return unique


def build_embeddings(documents: list[dict]):
    """문서를 청킹하고 임베딩을 생성하여 Pinecone에 업로드한다."""
    print(f"\n[1/2] 청킹 + 임베딩 생성 중... ({len(documents)}개 문서)")
    model = get_embed_model()

    # Pinecone 초기화 + 기존 벡터 전체 삭제
    print("  Pinecone 인덱스 초기화 중...")
    index = init_pinecone()
    print("  기존 벡터 전체 삭제 중...")
    try:
        index.delete(delete_all=True)
    except Exception as e:
        print(f"  [info] 기존 벡터 삭제 건너뜀 (빈 인덱스): {e}")

    # 문서별 청킹
    all_chunks = []
    all_ids = []
    all_metadata = []

    for doc in documents:
        url = doc.get("url", "")
        title = doc.get("title", "")
        source = doc.get("source", "faq")
        full_text = f"{title}\n{doc.get('content', '')}" if title else doc.get("content", "")
        chunks = chunk_text(full_text, max_chars=1000, overlap=200)

        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_ids.append(make_doc_id(url, i))
            all_metadata.append({
                "title": title[:200],
                "url": url,
                "source": source,
                "content_preview": chunk[:500],
                "chunk_index": i,
                "total_chunks": len(chunks),
            })

    print(f"  총 {len(all_chunks)}개 청크 생성 (문서 {len(documents)}개)")

    # 임베딩
    vectors = embed_documents(model, all_chunks)
    print(f"  임베딩 완료: {len(vectors)}개 벡터 (차원: {len(vectors[0])})")

    # Pinecone 업로드
    print(f"  Pinecone에 {len(all_ids)}개 벡터 업로드 중...")
    upsert_vectors(index, all_ids, vectors, all_metadata)
    print("  업로드 완료!")

    return model, index


def build_image_embeddings(documents: list[dict], embed_model, pinecone_index):
    """이미지 설명을 생성하고 임베딩 → Pinecone에 업로드한다."""
    image_paths = collect_all_image_paths(documents)
    if not image_paths:
        print("\n[2/2] 이미지 없음 — 건너뜀")
        return

    print(f"\n[2/2] 이미지 설명 생성 중... ({len(image_paths)}장)")
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


def main():
    print("=" * 60)
    print("VectorRAG 인덱스 빌드 시작")
    print("=" * 60)

    documents = load_documents()
    print(f"\n로드된 문서: {len(documents)}개")
    for source in ("eluocnc", "admin", "FAQ"):
        count = sum(1 for d in documents if d.get("source") == source)
        if count > 0:
            print(f"  - {source}: {count}건")

    # 1. 텍스트 청킹 + 임베딩 + Pinecone
    embed_model, pinecone_index = build_embeddings(documents)

    # 2. 이미지 설명 생성 + 임베딩 + Pinecone
    build_image_embeddings(documents, embed_model, pinecone_index)

    print("\n" + "=" * 60)
    print("빌드 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
