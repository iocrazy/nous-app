"""Publish issue-chat events to Redis `issue:{id}` for the WS forwarder.
Best-effort: a publish failure must never break the agent turn."""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from loguru import logger


async def _get_redis():
    from app.core.redis import get_async_redis

    return await get_async_redis()


def _channel(issue_id: int) -> str:
    return f"issue:{issue_id}"


async def _publish(issue_id: int, payload: dict[str, Any]) -> None:
    try:
        r = await _get_redis()
        await r.publish(_channel(issue_id), json.dumps(payload, default=str))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] publish failed (issue={issue_id}): {e}")


async def publish_chunk(issue_id: int, delta: str) -> None:
    await _publish(issue_id, {"type": "chunk", "delta": delta})


async def publish_status(issue_id: int, phase: str) -> None:
    await _publish(issue_id, {"type": "status", "phase": phase})


async def publish_message(
    issue_id: int, ai_message_row: dict[str, Any], *, session_user_id: Optional[UUID]
) -> None:
    if not ai_message_row or not ai_message_row.get("id"):
        logger.warning(
            f"[issue_chat_stream] skip publish_message — empty assistant row (issue={issue_id})"
        )
        return

    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    msg = map_ai_message_to_issue_message(
        ai_message_row, issue_id=issue_id, session_user_id=session_user_id
    )
    await _publish(
        issue_id, {"type": "message", "message": msg.model_dump(mode="json")}
    )
