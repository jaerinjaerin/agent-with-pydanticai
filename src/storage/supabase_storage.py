"""
Supabase Storage 문서 이미지 저장 모듈.

AWS S3 대신 Supabase Storage에 이미지를 업로드/삭제한다.
기존 s3_storage.py와 동일한 인터페이스(upload_image, delete_images, is_configured)를 제공.
"""

import os
from typing import Any

_client: Any = None

BUCKET_NAME = "doc-images"


def _get_client():
    """Supabase 클라이언트를 싱글턴으로 반환한다."""
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "")
    key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY", "")

    if not all([url, key]):
        raise ValueError(
            "Supabase 환경변수가 설정되지 않았습니다. "
            "NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY를 확인하세요."
        )

    from supabase import create_client
    _client = create_client(url, key)
    return _client


def _guess_content_type(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def upload_image(image_data: bytes, key: str) -> str:
    """이미지를 Supabase Storage에 업로드하고 공개 URL을 반환한다.

    Args:
        image_data: 이미지 바이너리 데이터.
        key: 오브젝트 경로 (예: "doc_images/report_page1_img0.png").

    Returns:
        공개 접근 가능한 URL.
    """
    sb = _get_client()

    sb.storage.from_(BUCKET_NAME).upload(
        path=key,
        file=image_data,
        file_options={
            "content-type": _guess_content_type(key),
            "upsert": "true",
        },
    )

    return sb.storage.from_(BUCKET_NAME).get_public_url(key)


def delete_images(prefix: str) -> int:
    """prefix로 시작하는 이미지를 일괄 삭제한다.

    Args:
        prefix: 오브젝트 경로 접두사 (예: "doc_images/report_").

    Returns:
        삭제된 오브젝트 수.
    """
    sb = _get_client()
    bucket = sb.storage.from_(BUCKET_NAME)

    # prefix에서 폴더와 검색어 분리
    parts = prefix.rstrip("/").rsplit("/", 1)
    folder = parts[0] if len(parts) > 1 else ""
    search_prefix = parts[1] if len(parts) > 1 else parts[0]

    try:
        files = bucket.list(folder, {"limit": 1000, "search": search_prefix})
    except Exception:
        return 0

    if not files:
        return 0

    paths = [f"{folder}/{f['name']}" if folder else f["name"] for f in files]
    if not paths:
        return 0

    bucket.remove(paths)
    return len(paths)


def is_configured() -> bool:
    """Supabase Storage 환경변수가 설정되어 있는지 확인한다."""
    return bool(
        os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        and os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY")
    )
