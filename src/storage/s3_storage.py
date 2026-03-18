"""
AWS S3 이미지 저장 모듈.

boto3로 S3에 이미지를 업로드/삭제한다.
"""

import os
from typing import Any

import boto3

_client: Any = None


def _get_client():
    """S3 클라이언트를 싱글턴으로 반환한다."""
    global _client
    if _client is not None:
        return _client

    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    region = os.environ.get("AWS_S3_REGION", "ap-northeast-2")

    if not all([access_key, secret_key]):
        raise ValueError(
            "AWS S3 환경변수가 설정되지 않았습니다. "
            "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY를 확인하세요."
        )

    _client = boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    return _client


def _get_bucket() -> str:
    return os.environ.get("AWS_S3_BUCKET_NAME", "eluo-docs")


def _get_public_url() -> str:
    """공개 URL 베이스를 반환한다.

    AWS_S3_PUBLIC_URL이 설정되면 그대로 사용 (CloudFront 등).
    미설정이면 S3 기본 URL을 생성한다.
    """
    custom = os.environ.get("AWS_S3_PUBLIC_URL", "").rstrip("/")
    if custom:
        return custom
    bucket = _get_bucket()
    region = os.environ.get("AWS_S3_REGION", "ap-northeast-2")
    return f"https://{bucket}.s3.{region}.amazonaws.com"


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
    """이미지를 S3에 업로드하고 공개 URL을 반환한다.

    Args:
        image_data: 이미지 바이너리 데이터.
        key: S3 오브젝트 키 (예: "doc_images/report_page1_img0.png").

    Returns:
        공개 접근 가능한 URL.
    """
    client = _get_client()
    bucket = _get_bucket()

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=image_data,
        ContentType=_guess_content_type(key),
    )

    public_url = _get_public_url()
    return f"{public_url}/{key}"


def delete_images(prefix: str) -> int:
    """prefix로 시작하는 이미지를 일괄 삭제한다.

    Args:
        prefix: S3 오브젝트 키 접두사 (예: "doc_images/report_").

    Returns:
        삭제된 오브젝트 수.
    """
    client = _get_client()
    bucket = _get_bucket()

    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    objects = response.get("Contents", [])
    if not objects:
        return 0

    delete_keys = [{"Key": obj["Key"]} for obj in objects]
    client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": delete_keys},
    )
    return len(delete_keys)


def is_configured() -> bool:
    """S3 환경변수가 모두 설정되어 있는지 확인한다."""
    return all([
        os.environ.get("AWS_ACCESS_KEY_ID"),
        os.environ.get("AWS_SECRET_ACCESS_KEY"),
        os.environ.get("AWS_S3_BUCKET_NAME"),
    ])
