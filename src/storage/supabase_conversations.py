"""
Supabase 대화 지속성 모듈.

세션 기반 대화 기록을 Supabase에 저장/복원한다.
"""

import json
from typing import Any

from storage.supabase_client import get_client


def get_or_create_conversation(session_token: str) -> dict:
    """세션 토큰으로 대화를 조회하거나 새로 생성한다."""
    client = get_client()

    # 기존 대화 조회
    result = (
        client.table("conversations")
        .select("*")
        .eq("session_token", session_token)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]

    # 새 대화 생성
    result = (
        client.table("conversations")
        .insert({"session_token": session_token})
        .execute()
    )
    return result.data[0] if result.data else {"session_token": session_token}


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    related_topics: list[str] | None = None,
    pydantic_message: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    """대화에 메시지를 저장한다."""
    client = get_client()
    row = {
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "related_topics": related_topics or [],
        "metadata": metadata or {},
    }
    if pydantic_message:
        row["pydantic_message"] = pydantic_message

    result = client.table("messages").insert(row).execute()
    return result.data[0] if result.data else row


def load_messages(conversation_id: str) -> list[dict]:
    """대화의 모든 메시지를 시간순으로 로드한다."""
    client = get_client()
    result = (
        client.table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("timestamp")
        .execute()
    )
    return result.data or []


def load_pydantic_history(conversation_id: str) -> list:
    """대화에서 PydanticAI 메시지 히스토리를 복원한다."""
    messages = load_messages(conversation_id)
    history = []
    for msg in messages:
        pm = msg.get("pydantic_message")
        if pm:
            history.append(pm)
    return history


def delete_conversation(conversation_id: str) -> bool:
    """대화를 삭제한다. CASCADE로 관련 메시지도 함께 삭제된다."""
    client = get_client()
    result = (
        client.table("conversations")
        .delete()
        .eq("id", conversation_id)
        .execute()
    )
    return bool(result.data)


def update_model_choice(conversation_id: str, model_choice: str) -> None:
    """대화의 모델 선택을 업데이트한다."""
    client = get_client()
    client.table("conversations").update(
        {"model_choice": model_choice}
    ).eq("id", conversation_id).execute()
