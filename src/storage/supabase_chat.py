"""
Supabase 대화 기록 저장 모듈.

대화(conversations)와 메시지(messages)를 Supabase에 저장/복원한다.
환경변수 미설정 시 모든 함수가 graceful하게 스킵된다.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

_client: Any = None
_ModelMessagesTA = TypeAdapter(list[ModelMessage])


def is_configured() -> bool:
    """Supabase 환경변수가 설정되어 있는지 확인한다."""
    return bool(
        os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        and os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY")
    )


def _get_client():
    """Supabase 클라이언트를 싱글턴으로 반환한다."""
    global _client
    if _client is not None:
        return _client

    from supabase import create_client

    url = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
    key = os.environ["NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY"]
    _client = create_client(url, key)
    return _client


def get_or_create_conversation(session_token: str, model_choice: str = "") -> dict:
    """session_token으로 대화를 조회하거나 새로 생성한다."""
    sb = _get_client()
    resp = (
        sb.table("conversations")
        .select("*")
        .eq("session_token", session_token)
        .execute()
    )
    if resp.data:
        return resp.data[0]

    row = {
        "session_token": session_token,
        "model_choice": model_choice,
    }
    resp = sb.table("conversations").insert(row).execute()
    return resp.data[0]


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    timestamp: str = "",
    related_topics: list[str] | None = None,
    pydantic_messages: list[ModelMessage] | None = None,
    metadata: dict | None = None,
) -> dict | None:
    """메시지를 저장하고 conversation.updated_at을 갱신한다."""
    sb = _get_client()

    pydantic_json = None
    if pydantic_messages:
        pydantic_json = _ModelMessagesTA.dump_python(pydantic_messages, mode="json")

    row = {
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "related_topics": related_topics or [],
        "pydantic_message": pydantic_json,
        "metadata": metadata or {},
    }
    # display_timestamp을 metadata에 저장 (한국어 포맷)
    if timestamp:
        row["metadata"] = {**(metadata or {}), "display_timestamp": timestamp}

    resp = sb.table("messages").insert(row).execute()

    # conversation updated_at 갱신
    sb.table("conversations").update(
        {"updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", conversation_id).execute()

    return resp.data[0] if resp.data else None


def load_messages(conversation_id: str) -> list[dict]:
    """대화의 전체 메시지를 시간순으로 로드한다 (표시용 컬럼만 조회)."""
    sb = _get_client()
    resp = (
        sb.table("messages")
        .select("role,content,related_topics,metadata,timestamp")
        .eq("conversation_id", conversation_id)
        .order("timestamp", desc=False)
        .execute()
    )
    return resp.data or []


def restore_pydantic_history(conversation_id: str) -> list[ModelMessage]:
    """마지막 assistant 메시지의 pydantic_message를 역직렬화한다."""
    sb = _get_client()
    resp = (
        sb.table("messages")
        .select("pydantic_message")
        .eq("conversation_id", conversation_id)
        .eq("role", "assistant")
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data or not resp.data[0].get("pydantic_message"):
        return []
    return _ModelMessagesTA.validate_python(resp.data[0]["pydantic_message"])


def upload_chat_image(
    image_bytes: bytes, mime_type: str, conversation_id: str
) -> str | None:
    """Supabase Storage에 이미지를 업로드하고 public URL을 반환한다."""
    sb = _get_client()
    ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
    path = f"{conversation_id}/{uuid.uuid4().hex}.{ext}"

    sb.storage.from_("chat-images").upload(
        path,
        image_bytes,
        file_options={"content-type": mime_type},
    )
    return sb.storage.from_("chat-images").get_public_url(path)
