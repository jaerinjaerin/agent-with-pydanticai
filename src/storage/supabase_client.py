"""
Supabase 공유 클라이언트.

싱글턴 패턴으로 Supabase 클라이언트를 제공한다.
"""

import os
from typing import Any

_client: Any = None


def is_configured() -> bool:
    """Supabase 환경변수가 설정되어 있는지 확인한다."""
    return bool(
        os.environ.get("SUPABASE_URL")
        and os.environ.get("SUPABASE_SERVICE_KEY")
    )


def get_client():
    """Supabase 클라이언트를 싱글턴으로 반환한다."""
    global _client
    if _client is not None:
        return _client

    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if not url or not key:
        raise ValueError(
            "Supabase 환경변수가 설정되지 않았습니다. "
            "SUPABASE_URL, SUPABASE_SERVICE_KEY를 확인하세요."
        )

    _client = create_client(url, key)
    return _client
