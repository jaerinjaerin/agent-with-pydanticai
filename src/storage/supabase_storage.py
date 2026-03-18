"""
Supabase Storage 이미지 저장 모듈.

AWS S3를 대체하여 Supabase Storage에 이미지를 업로드/삭제한다.
"""

import os
from typing import Any

from storage.supabase_client import get_client, is_configured

BUCKET_NAME = "doc-images"


def _guess_content_type(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def _get_public_url(key: str) -> str:
    """Supabase Storage 공개 URL을 반환한다."""
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    return f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{key}"


def upload_image(image_data: bytes, key: str) -> str:
    """이미지를 Supabase Storage에 업로드하고 공개 URL을 반환한다.

    Args:
        image_data: 이미지 바이너리 데이터.
        key: 오브젝트 키 (예: "doc_images/report_page1_img0.png").

    Returns:
        공개 접근 가능한 URL.
    """
    client = get_client()
    content_type = _guess_content_type(key)

    client.storage.from_(BUCKET_NAME).upload(
        path=key,
        file=image_data,
        file_options={"content-type": content_type, "upsert": "true"},
    )

    return _get_public_url(key)


def delete_images(prefix: str) -> int:
    """prefix로 시작하는 이미지를 일괄 삭제한다.

    Args:
        prefix: 오브젝트 키 접두사 (예: "doc_images/report_").

    Returns:
        삭제된 오브젝트 수.
    """
    client = get_client()
    storage = client.storage.from_(BUCKET_NAME)

    # prefix로 파일 목록 조회
    try:
        # Supabase Storage list는 폴더 기반이므로 prefix를 폴더/파일로 분리
        folder = "/".join(prefix.split("/")[:-1]) if "/" in prefix else ""
        file_prefix = prefix.split("/")[-1] if "/" in prefix else prefix

        files = storage.list(folder)
        matching = [
            f"{folder}/{f['name']}" if folder else f["name"]
            for f in files
            if f["name"].startswith(file_prefix)
        ]

        if not matching:
            return 0

        storage.remove(matching)
        return len(matching)
    except Exception as e:
        print(f"[warn] 이미지 삭제 중 오류: {e}")
        return 0
