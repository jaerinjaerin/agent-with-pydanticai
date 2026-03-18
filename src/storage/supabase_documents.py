"""
Supabase 문서 CRUD 모듈.

JSON 파일 기반 데이터 저장을 Supabase documents 테이블로 대체한다.
"""

from datetime import datetime
from typing import Any

from storage.supabase_client import get_client


def list_documents(source: str = "") -> list[dict]:
    """문서 목록을 조회한다.

    Args:
        source: 소스 필터 ("eluocnc", "admin", "board", "" 전체).

    Returns:
        문서 딕셔너리 리스트.
    """
    client = get_client()
    query = client.table("documents").select("*").order("created_at", desc=True)
    if source:
        query = query.eq("source", source)
    result = query.execute()
    return result.data or []


def get_document(url: str) -> dict | None:
    """URL로 문서를 조회한다."""
    client = get_client()
    result = (
        client.table("documents")
        .select("*")
        .eq("url", url)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def upsert_document(doc: dict) -> dict:
    """문서를 삽입 또는 업데이트한다.

    Args:
        doc: 문서 딕셔너리. "url" 필드가 반드시 포함되어야 한다.
             선택적 필드: title, content, source, category, metadata.

    Returns:
        삽입/업데이트된 문서 딕셔너리.
    """
    client = get_client()
    row = {
        "url": doc["url"],
        "title": doc.get("title", ""),
        "content": doc.get("content", ""),
        "source": doc.get("source", "eluocnc"),
        "category": doc.get("category", ""),
        "metadata": doc.get("metadata", {}),
        "updated_at": datetime.now().isoformat(),
    }

    # 메타데이터에 attachments, inline_images 등 추가 필드 저장
    extra_meta = {}
    for key in ("attachments", "inline_images", "scraped_at"):
        if key in doc:
            extra_meta[key] = doc[key]
    if extra_meta:
        row["metadata"] = {**row["metadata"], **extra_meta}

    result = (
        client.table("documents")
        .upsert(row, on_conflict="url")
        .execute()
    )
    return result.data[0] if result.data else row


def delete_document(url: str) -> bool:
    """URL로 문서를 삭제한다. CASCADE로 관련 chunks도 함께 삭제된다."""
    client = get_client()
    result = (
        client.table("documents")
        .delete()
        .eq("url", url)
        .execute()
    )
    return bool(result.data)


def get_stats() -> dict:
    """source별 문서 수 통계를 반환한다."""
    client = get_client()
    result = client.table("documents").select("source").execute()
    stats: dict[str, int] = {}
    for row in result.data or []:
        src = row.get("source", "")
        stats[src] = stats.get(src, 0) + 1
    stats["total"] = sum(stats.values())
    return stats
