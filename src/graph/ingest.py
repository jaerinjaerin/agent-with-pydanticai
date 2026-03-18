"""
단건 문서 인제스트 파이프라인.

어드민 UI에서 문서를 첨부/제출하면 이 모듈이 호출되어
해당 문서만 청킹 → Supabase upsert를 수행한다.
그래프 업데이트도 포함.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.supabase_vector import (
    chunk_text,
    delete_doc_vectors,
    make_doc_id,
    upsert_records,
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
    pinecone_index=None,
    supabase_client=None,
    document_id: str = "",
    descriptions: dict[str, str] | None = None,
    namespace: str = "",
) -> int:
    """이미지 설명을 벡터 DB에 업로드한다.

    Args:
        pinecone_index: 하위호환용 (무시됨). supabase_client를 사용.
        supabase_client: Supabase 클라이언트.
        document_id: documents 테이블의 UUID.

    Returns:
        업로드된 이미지 청크 수.
    """
    # 하위호환: pinecone_index가 전달되면 supabase_client로 사용
    client = supabase_client or pinecone_index
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
            "document_id": document_id,
            "total_chunks": len(image_paths),
        })

    if not ids:
        return 0

    upsert_records(client, ids, texts, metadata_list, namespace=namespace)
    return len(ids)


def ingest_document(
    title: str,
    content: str,
    source: str,
    url: str,
    pinecone_index=None,
    supabase_client=None,
    document_id: str = "",
    namespace: str = "",
) -> dict:
    """단일 문서를 청킹 → Supabase에 업로드한다.

    기존에 같은 URL의 문서가 있으면 삭제 후 재업로드.

    Args:
        pinecone_index: 하위호환용 (무시됨). supabase_client를 사용.
        supabase_client: Supabase 클라이언트.
        document_id: documents 테이블의 UUID.

    Returns:
        {"chunks": 청크 수, "deleted": 삭제된 기존 청크 수}
    """
    client = supabase_client or pinecone_index

    # 1) 기존 벡터 삭제 (같은 URL로 이미 등록된 경우)
    deleted = delete_doc_vectors(client, url, namespace=namespace)

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
            "document_id": document_id,
        }
        for i, chunk in enumerate(chunks)
    ]

    # 4) Supabase upsert (Gemini 임베딩)
    upsert_records(client, ids, chunks, metadata_list, namespace=namespace)

    return {"chunks": len(chunks), "deleted": deleted}


def ingest_document_with_media(
    title: str,
    content: str,
    source: str,
    url: str,
    pinecone_index=None,
    supabase_client=None,
    document_id: str = "",
    images: list | None = None,
    namespace: str = "",
    progress_callback=None,
) -> dict:
    """텍스트 + 이미지를 포함한 전체 인제스트 파이프라인.

    Args:
        pinecone_index: 하위호환용 (무시됨). supabase_client를 사용.
        supabase_client: Supabase 클라이언트.
        document_id: documents 테이블의 UUID.
        images: ExtractionResult.images 리스트 (ExtractedImage 객체들).
        progress_callback: (step: str, detail: str) → None. UI 상태 표시용.

    Returns:
        {"chunks": N, "images": M, "deleted": D, "image_urls": [...]}
    """
    client = supabase_client or pinecone_index

    def _progress(step: str, detail: str = ""):
        if progress_callback:
            progress_callback(step, detail)

    image_urls: list[str] = []
    image_count = 0

    # 1. 이미지 처리 (Supabase Storage 업로드 + 설명 생성 + 벡터화)
    if images:
        from storage.supabase_storage import is_configured as storage_configured, upload_image
        from graph.image_describer import describe_image_bytes

        if not storage_configured():
            _progress("storage_skip", "Supabase 환경변수 미설정 — 이미지 건너뜀")
        else:
            import hashlib
            import re
            import time

            url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
            safe_title = re.sub(r"[^\w가-힣-]", "_", title)[:30]
            storage_prefix = f"doc_images/{url_hash}_{safe_title}"

            # 1a. Supabase Storage에 이미지 업로드
            _progress("image_upload", f"{len(images)}장 업로드 중...")
            for img in images:
                try:
                    key = f"{storage_prefix}/{img.filename}"
                    img_url = upload_image(img.data, key)
                    image_urls.append(img_url)
                except Exception as e:
                    print(f"  [warn] 이미지 업로드 실패 ({img.filename}): {e}")

            if image_urls:
                _progress("image_upload_done", f"{len(image_urls)}장 업로드 완료")

            # 1b. 이미지 설명 생성 (Claude Vision)
            if image_urls:
                _progress("image_describe", f"{len(image_urls)}장 설명 생성 중...")
                descriptions: dict[str, str] = {}
                for i, (img, img_url) in enumerate(zip(images, image_urls)):
                    try:
                        ext = img.filename.rsplit(".", 1)[-1].lower()
                        media_type = {
                            "png": "image/png",
                            "jpg": "image/jpeg",
                            "jpeg": "image/jpeg",
                            "gif": "image/gif",
                            "webp": "image/webp",
                        }.get(ext, "image/png")
                        desc = describe_image_bytes(img.data, media_type=media_type)
                        descriptions[img_url] = desc
                        if (i + 1) % 3 == 0:
                            time.sleep(0.5)
                    except Exception as e:
                        print(f"  [warn] 이미지 설명 생성 실패 ({img.filename}): {e}")
                        descriptions[img_url] = ""

                _progress("image_describe_done", f"{len(descriptions)}장 설명 완료")

                # 1c. 이미지 설명 벡터화
                _progress("image_vectorize", "이미지 벡터화 중...")
                image_count = ingest_images(
                    image_paths=image_urls,
                    title=title,
                    url=url,
                    source=source,
                    supabase_client=client,
                    document_id=document_id,
                    descriptions=descriptions,
                    namespace=namespace,
                )
                _progress("image_vectorize_done", f"{image_count}개 이미지 벡터 업로드")

    # 2. 텍스트 인제스트
    _progress("text_ingest", "텍스트 벡터화 중...")
    result = ingest_document(
        title=title,
        content=content,
        source=source,
        url=url,
        supabase_client=client,
        document_id=document_id,
        namespace=namespace,
    )
    _progress("text_ingest_done", f"{result['chunks']}개 텍스트 청크 업로드")

    return {
        "chunks": result["chunks"],
        "images": image_count,
        "deleted": result["deleted"],
        "image_urls": image_urls,
    }


def delete_document(url: str, pinecone_index=None, supabase_client=None, namespace: str = "") -> int:
    """문서를 벡터 DB에서 삭제한다. 삭제된 벡터 수를 반환."""
    client = supabase_client or pinecone_index
    return delete_doc_vectors(client, url, namespace=namespace)
