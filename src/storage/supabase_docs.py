"""
Supabase 문서 저장 모듈.

어드민 문서를 Supabase documents 테이블에 CRUD한다.
환경변수 미설정 시 모든 함수가 graceful하게 스킵된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.supabase_chat import is_configured, _get_client  # noqa: F401 (re-export)


def _row_to_dict(row: dict) -> dict:
    """DB row → 기존 JSON 형식으로 변환한다."""
    item: dict[str, Any] = {
        "title": row.get("title", ""),
        "content": row.get("content", ""),
        "url": row.get("url", ""),
        "source": row.get("source", "admin"),
        "category": row.get("category", ""),
    }
    # metadata → top-level 필드 복원
    meta = row.get("metadata") or {}
    if meta.get("attachments"):
        item["attachments"] = meta["attachments"]
    if meta.get("inline_images"):
        item["inline_images"] = meta["inline_images"]
    # DB 메타 필드 유지
    for k in ("id", "created_at", "updated_at"):
        if row.get(k):
            item[k] = row[k]
    return item


def _dict_to_row(item: dict) -> dict:
    """JSON dict → DB row로 변환한다."""
    import json as _json

    metadata: dict[str, Any] = {}

    # attachments: 문자열이면 파싱, 리스트면 그대로
    attachments = item.get("attachments")
    if isinstance(attachments, str):
        try:
            attachments = _json.loads(attachments)
        except (ValueError, TypeError):
            attachments = None
    if attachments:
        metadata["attachments"] = attachments

    # inline_images 보존
    inline_images = item.get("inline_images")
    if isinstance(inline_images, str):
        try:
            inline_images = _json.loads(inline_images)
        except (ValueError, TypeError):
            inline_images = None
    if inline_images:
        metadata["inline_images"] = inline_images

    # scraped_at 보존
    if item.get("scraped_at"):
        metadata["scraped_at"] = item["scraped_at"]

    row = {
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "source": item.get("source", "admin"),
        "category": item.get("category", ""),
        "metadata": metadata or None,
    }
    return row


def load_items(source: str = "") -> list[dict]:
    """Supabase에서 문서 목록을 로드한다."""
    sb = _get_client()
    query = sb.table("documents").select("*")
    if source:
        query = query.eq("source", source)
    resp = query.order("created_at", desc=True).execute()
    return [_row_to_dict(r) for r in (resp.data or [])]


def load_items_page(
    source: str = "",
    keyword: str = "",
    offset: int = 0,
    limit: int = 15,
) -> tuple[list[dict], int]:
    """서버사이드 필터/검색/페이지네이션. (items, total_count) 반환."""
    sb = _get_client()
    query = sb.table("documents").select("*", count="exact")

    if source:
        query = query.eq("source", source)
    if keyword:
        query = query.or_(f"title.ilike.%{keyword}%,content.ilike.%{keyword}%")

    query = query.order("created_at", desc=True)
    query = query.range(offset, offset + limit - 1)

    resp = query.execute()
    items = [_row_to_dict(r) for r in (resp.data or [])]
    total = resp.count or 0
    return items, total


def add_item(item: dict) -> dict | None:
    """문서를 Supabase에 추가한다."""
    sb = _get_client()
    row = _dict_to_row(item)
    resp = sb.table("documents").insert(row).execute()
    return _row_to_dict(resp.data[0]) if resp.data else None


def update_item(url: str, updated_fields: dict) -> bool:
    """URL로 문서를 찾아 필드를 업데이트한다."""
    sb = _get_client()
    # 업데이트 가능한 컬럼만 추출
    row_update: dict[str, Any] = {}
    for k in ("title", "content", "source", "category"):
        if k in updated_fields:
            row_update[k] = updated_fields[k]

    # attachments → metadata
    if "attachments" in updated_fields:
        row_update["metadata"] = {"attachments": updated_fields["attachments"]}

    if not row_update:
        return False

    resp = sb.table("documents").update(row_update).eq("url", url).execute()
    return bool(resp.data)


def delete_item(url: str) -> bool:
    """URL로 문서를 삭제한다."""
    sb = _get_client()
    resp = sb.table("documents").delete().eq("url", url).execute()
    return bool(resp.data)


def find_item_by_url(url: str) -> dict | None:
    """URL로 문서를 조회한다."""
    sb = _get_client()
    resp = sb.table("documents").select("*").eq("url", url).limit(1).execute()
    if resp.data:
        return _row_to_dict(resp.data[0])
    return None


def migrate_from_json(json_path: Path, default_source: str = "admin") -> int:
    """기존 JSON 파일의 문서를 Supabase로 일괄 이관한다. upsert 사용."""
    import json

    if not json_path.exists():
        print(f"[migrate] 파일 없음: {json_path}")
        return 0

    with open(json_path, encoding="utf-8") as f:
        items = json.load(f)

    if not items:
        print("[migrate] 이관할 문서가 없습니다.")
        return 0

    sb = _get_client()
    count = 0
    for item in items:
        row = _dict_to_row(item)
        row.setdefault("source", default_source)
        sb.table("documents").upsert(row, on_conflict="url").execute()
        count += 1

    print(f"[migrate] {json_path.name} → {count}건 이관 완료 (source={default_source})")
    return count


if __name__ == "__main__":
    from pathlib import Path as _P
    from dotenv import load_dotenv

    load_dotenv()

    _data_dir = _P(__file__).resolve().parents[2] / "data"
    _sources = {
        "admin_documents.json": "admin",
        "eluocnc.json": "eluocnc",
        "board_documents.json": "board",
    }
    total = 0
    for filename, src in _sources.items():
        path = _data_dir / filename
        print(f"\n마이그레이션: {path}")
        total += migrate_from_json(path, default_source=src)
    print(f"\n전체 완료: {total}건")
