"""
JSON → Supabase 데이터 마이그레이션 스크립트.

eluocnc.json, admin_documents.json, board_documents.json →
Supabase documents 테이블로 이전한다.

사용법:
    python scripts/migrate_to_supabase.py
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# src를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from storage.supabase_client import get_client, is_configured
from storage.supabase_documents import upsert_document


DATA_DIR = Path(__file__).resolve().parents[1] / "data"

SOURCE_MAP = {
    "eluocnc.json": "eluocnc",
    "admin_documents.json": "admin",
    "board_documents.json": "board",
}


def load_json(path: Path) -> list[dict]:
    """JSON 파일을 로드한다."""
    if not path.exists():
        print(f"  [skip] 파일 없음: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def migrate():
    """모든 JSON 데이터를 Supabase documents 테이블로 이전한다."""
    if not is_configured():
        print("[error] Supabase 환경변수가 설정되지 않았습니다.")
        print("  SUPABASE_URL, SUPABASE_SERVICE_KEY를 .env에 추가하세요.")
        sys.exit(1)

    print("=" * 60)
    print("JSON → Supabase 마이그레이션 시작")
    print("=" * 60)

    total_migrated = 0
    total_skipped = 0

    for filename, default_source in SOURCE_MAP.items():
        path = DATA_DIR / filename
        items = load_json(path)
        if not items:
            continue

        print(f"\n[{default_source}] {len(items)}개 문서 마이그레이션 중...")

        for i, item in enumerate(items, 1):
            url = item.get("url", "")
            if not url:
                print(f"  [{i}] URL 없음 — 건너뜀")
                total_skipped += 1
                continue

            content = item.get("content", "").strip()
            if len(content) < 50:
                total_skipped += 1
                continue

            doc = {
                "url": url,
                "title": item.get("title", ""),
                "content": content,
                "source": item.get("source", default_source),
                "category": item.get("category", ""),
                "metadata": {},
            }

            # 추가 메타데이터 보존
            for key in ("attachments", "inline_images", "scraped_at"):
                if key in item:
                    doc[key] = item[key]

            try:
                upsert_document(doc)
                total_migrated += 1
                if i % 10 == 0:
                    print(f"  {i}/{len(items)} 완료")
            except Exception as e:
                print(f"  [{i}] 오류: {e}")
                total_skipped += 1

        print(f"  → {default_source}: 완료")

    print(f"\n{'=' * 60}")
    print(f"마이그레이션 완료!")
    print(f"  이전 성공: {total_migrated}개")
    print(f"  건너뜀: {total_skipped}개")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    migrate()
