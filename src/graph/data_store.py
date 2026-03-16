"""
JSON 데이터 CRUD 모듈.

어드민에서 등록한 문서를 JSON 파일에 영속 저장한다.
"""

import json
from pathlib import Path
from filelock import FileLock


def _lock_path(path: Path) -> Path:
    return path.with_suffix(".lock")


def load_items(path: Path) -> list[dict]:
    """JSON 파일에서 아이템 목록을 로드한다."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_items(path: Path, items: list[dict]) -> None:
    """아이템 목록을 JSON 파일에 저장한다. filelock으로 동시 쓰기 방지."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_lock_path(path)), timeout=10)
    with lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)


def add_item(path: Path, item: dict) -> None:
    """아이템을 JSON 파일에 추가한다."""
    items = load_items(path)
    items.append(item)
    save_items(path, items)


def update_item(path: Path, url: str, updated_fields: dict) -> bool:
    """URL로 아이템을 찾아 필드를 업데이트한다. 성공 여부를 반환."""
    items = load_items(path)
    for item in items:
        if item.get("url") == url:
            item.update(updated_fields)
            save_items(path, items)
            return True
    return False


def delete_item(path: Path, url: str) -> bool:
    """URL로 아이템을 찾아 삭제한다. 성공 여부를 반환."""
    items = load_items(path)
    new_items = [item for item in items if item.get("url") != url]
    if len(new_items) == len(items):
        return False
    save_items(path, new_items)
    return True


def find_item_by_url(path: Path, url: str) -> dict | None:
    """URL로 아이템을 찾는다."""
    items = load_items(path)
    for item in items:
        if item.get("url") == url:
            return item
    return None
