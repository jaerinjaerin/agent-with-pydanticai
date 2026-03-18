"""
VectorRAG 인덱스 빌드 스크립트.

Supabase documents 테이블 → 청킹 → Gemini 임베딩 → Supabase pgvector 업로드.

사용법:
    python src/graph/build_index.py
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# src를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.supabase_vector import (
    init_db,
    upsert_records,
    chunk_text,
    make_doc_id,
)
from graph.image_describer import (
    collect_all_image_paths,
    describe_images_batch,
)
from graph.ingest import ingest_images
from storage.supabase_documents import list_documents


def load_documents() -> list[dict]:
    """Supabase에서 문서를 로드한다."""
    all_items = list_documents()

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
    """문서를 청킹하고 Supabase pgvector에 업로드한다."""
    print(f"\n[1/2] 청킹 + Supabase 업로드 중... ({len(documents)}개 문서)")

    # Supabase 초기화
    print("  Supabase 클라이언트 초기화 중...")
    client = init_db()

    # 기존 청크 전체 삭제
    print("  기존 청크 전체 삭제 중...")
    try:
        client.table("document_chunks").delete().neq("id", "").execute()
    except Exception as e:
        print(f"  [info] 기존 청크 삭제 건너뜀: {e}")

    # 문서별 청킹
    all_chunks = []
    all_ids = []
    all_metadata = []

    for doc in documents:
        url = doc.get("url", "")
        title = doc.get("title", "")
        source = doc.get("source", "eluocnc")
        doc_id = doc.get("id", "")
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
                "document_id": doc_id,
            })

    print(f"  총 {len(all_chunks)}개 청크 생성 (문서 {len(documents)}개)")

    # Supabase 업로드 (Gemini 임베딩)
    print(f"  Supabase에 {len(all_ids)}개 레코드 업로드 중...")
    upsert_records(client, all_ids, all_chunks, all_metadata)
    print("  업로드 완료!")

    return client


def build_image_embeddings(documents: list[dict], supabase_client):
    """이미지 설명을 생성하고 Supabase에 업로드한다."""
    image_paths = collect_all_image_paths(documents)
    if not image_paths:
        print("\n[2/2] 이미지 없음 — 건너뜀")
        return

    print(f"\n[2/2] 이미지 설명 생성 중... ({len(image_paths)}장)")
    descriptions = describe_images_batch(image_paths)

    # 이미지 설명 Supabase 업로드
    total_uploaded = 0
    for doc in documents:
        # metadata에서 attachments 확인
        metadata = doc.get("metadata", {})
        doc_images = []
        for att in metadata.get("attachments", []):
            doc_images.extend(att.get("images", []))
        # 최상위 attachments도 확인
        for att in doc.get("attachments", []):
            doc_images.extend(att.get("images", []))
        if not doc_images:
            continue

        count = ingest_images(
            image_paths=doc_images,
            title=doc.get("title", ""),
            url=doc.get("url", ""),
            source=doc.get("source", ""),
            supabase_client=supabase_client,
            document_id=doc.get("id", ""),
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
    for source in ("eluocnc", "admin", "board"):
        count = sum(1 for d in documents if d.get("source") == source)
        if count > 0:
            print(f"  - {source}: {count}건")

    # 1. 텍스트 청킹 + Supabase 업로드
    supabase_client = build_embeddings(documents)

    # 2. 이미지 설명 생성 + Supabase 업로드
    build_image_embeddings(documents, supabase_client)

    print("\n" + "=" * 60)
    print("빌드 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
