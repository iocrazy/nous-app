"""Unified Conversations REST endpoints (Phase 1 — Task 7).

Port of api/chat_router.py with the Rename Map applied:
  /chat/channels           → /conversations
  channel_id               → conversation_id
  content_type             → type
  reply_to_id              → parent_id
  get_chat_service         → get_conversation_service
  dispatch_summons(channel_id=...) → dispatch_summons(conversation_id=...)

PermissionError  → HTTP 403
ValueError       → HTTP 400

Attachment upload/promote endpoints are Task 9 — not included here.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.schemas.conversation import (
    AgentAdd,
    ConversationCreate,
    ConversationOut,
    MarkReadIn,
    MemberAdd,
    MessageCreate,
    MessageOut,
)
from app.services.conversation_service import get_conversation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversations"])


class MessageEdit(BaseModel):
    body: dict[str, Any]


async def _summon_runner(
    conversation_id: int,
    user_id: str,
    message: dict[str, Any],
) -> None:
    """Background task: dispatch agent summons after a human message is posted.

    Must NEVER raise — swallows all exceptions and logs them instead so the
    background-task failure is never surfaced in the HTTP response.
    """
    try:
        await get_conversation_service().dispatch_summons(
            conversation_id=conversation_id,
            summoner_user_id=user_id,
            message=message,
        )
    except Exception as exc:
        logger.error(f"[conversations] summon dispatch failed: {exc}")


@router.get("/", response_model=list[ConversationOut])
async def list_my_conversations(auth: AuthDep):
    svc = get_conversation_service()
    try:
        rows = await svc.list_my_conversations(user_id=auth.user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return rows


@router.post("/", response_model=ConversationOut)
async def create_conversation(payload: ConversationCreate, auth: AuthDep):
    svc = get_conversation_service()
    try:
        conv = await svc.create_conversation(
            user_id=auth.user_id,
            scope_id=payload.scope_id,
            type=payload.type,
            name=payload.name,
            history_mode=payload.history_mode,
            member_ids=payload.member_ids,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {**conv, "unread": 0}


@router.post("/{conversation_id}/members")
async def add_members(conversation_id: int, payload: MemberAdd, auth: AuthDep):
    svc = get_conversation_service()
    try:
        added = await svc.add_members(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            user_ids=payload.user_ids,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"added": added}


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: int,
    auth: AuthDep,
    before_seq: Optional[int] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
):
    svc = get_conversation_service()
    try:
        return await svc.get_messages(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            before_seq=before_seq,
            limit=limit,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{conversation_id}/agents")
async def add_agent(conversation_id: int, payload: AgentAdd, auth: AuthDep):
    svc = get_conversation_service()
    try:
        return await svc.add_agent(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            agent_slug=payload.agent_slug,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{conversation_id}/messages", response_model=MessageOut)
async def post_message(
    conversation_id: int,
    payload: MessageCreate,
    auth: AuthDep,
    background_tasks: BackgroundTasks,
):
    svc = get_conversation_service()
    try:
        msg = await svc.post_message(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            type=payload.type,
            body=payload.body,
            parent_id=payload.parent_id,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    background_tasks.add_task(_summon_runner, conversation_id, auth.user_id, msg)
    return msg


@router.post("/{conversation_id}/read")
async def mark_read(conversation_id: int, payload: MarkReadIn, auth: AuthDep):
    svc = get_conversation_service()
    try:
        await svc.mark_read(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            last_read_seq=payload.last_read_seq,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"ok": True}


@router.patch("/{conversation_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    conversation_id: int, message_id: int, payload: MessageEdit, auth: AuthDep
):
    svc = get_conversation_service()
    text = payload.body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text required",
        )
    try:
        row = await svc.edit_message(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            message_id=message_id,
            body=payload.body,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return row


@router.delete("/{conversation_id}/messages/{message_id}", response_model=MessageOut)
async def delete_message(conversation_id: int, message_id: int, auth: AuthDep):
    svc = get_conversation_service()
    try:
        row = await svc.delete_message(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            message_id=message_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return row
