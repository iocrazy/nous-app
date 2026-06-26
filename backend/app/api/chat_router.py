"""Team Chat REST endpoints (PHASE-1 backend foundation)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.schemas.chat import (
    AgentAdd,
    ChannelCreate,
    ChannelOut,
    MarkReadIn,
    MemberAdd,
    MessageCreate,
    MessageOut,
)
from app.services.chat_service import get_chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


class MessageEdit(BaseModel):
    body: dict[str, Any]


async def _summon_runner(
    channel_id: int,
    user_id: str,
    message: dict[str, Any],
) -> None:
    """Background task: dispatch agent summons after a human message is posted.

    Must NEVER raise — swallows all exceptions and logs them instead so the
    background-task failure is never surfaced in the HTTP response.
    """
    try:
        await get_chat_service().dispatch_summons(
            channel_id=channel_id,
            summoner_user_id=user_id,
            message=message,
        )
    except Exception as exc:
        logger.error(f"[chat] summon dispatch failed: {exc}")


@router.get("/channels", response_model=list[ChannelOut])
async def list_my_channels(auth: AuthDep):
    svc = get_chat_service()
    return await svc.list_my_channels(user_id=auth.user_id)


@router.post("/channels", response_model=ChannelOut)
async def create_channel(payload: ChannelCreate, auth: AuthDep):
    svc = get_chat_service()
    try:
        ch = await svc.create_channel(
            user_id=auth.user_id,
            team_id=payload.team_id,
            type=payload.type,
            name=payload.name,
            history_mode=payload.history_mode,
            member_ids=payload.member_ids,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    return {**ch, "unread": 0}


@router.post("/channels/{channel_id}/members")
async def add_members(channel_id: int, payload: MemberAdd, auth: AuthDep):
    svc = get_chat_service()
    try:
        added = await svc.add_members(
            channel_id=channel_id,
            user_id=auth.user_id,
            user_ids=payload.user_ids,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    return {"added": added}


@router.get("/channels/{channel_id}/messages", response_model=list[MessageOut])
async def list_messages(
    channel_id: int,
    auth: AuthDep,
    before_seq: Optional[int] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
):
    svc = get_chat_service()
    try:
        return await svc.get_messages(
            channel_id=channel_id,
            user_id=auth.user_id,
            before_seq=before_seq,
            limit=limit,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )


@router.post("/channels/{channel_id}/agents")
async def add_agent(channel_id: int, payload: AgentAdd, auth: AuthDep):
    svc = get_chat_service()
    try:
        return await svc.add_agent(
            channel_id=channel_id,
            user_id=auth.user_id,
            agent_slug=payload.agent_slug,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/channels/{channel_id}/messages", response_model=MessageOut)
async def post_message(
    channel_id: int,
    payload: MessageCreate,
    auth: AuthDep,
    background_tasks: BackgroundTasks,
):
    svc = get_chat_service()
    try:
        msg = await svc.post_message(
            channel_id=channel_id,
            user_id=auth.user_id,
            content_type=payload.content_type,
            body=payload.body,
            reply_to_id=payload.reply_to_id,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    background_tasks.add_task(_summon_runner, channel_id, auth.user_id, msg)
    return msg


@router.post("/channels/{channel_id}/read")
async def mark_read(channel_id: int, payload: MarkReadIn, auth: AuthDep):
    svc = get_chat_service()
    try:
        await svc.mark_read(
            channel_id=channel_id,
            user_id=auth.user_id,
            last_read_seq=payload.last_read_seq,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member"
        )
    return {"ok": True}


@router.patch("/channels/{channel_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    channel_id: int, message_id: int, payload: MessageEdit, auth: AuthDep
):
    svc = get_chat_service()
    try:
        row = await svc.edit_message(
            channel_id=channel_id,
            user_id=auth.user_id,
            message_id=message_id,
            body=payload.body,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    return row


@router.delete(
    "/channels/{channel_id}/messages/{message_id}", response_model=MessageOut
)
async def delete_message(channel_id: int, message_id: int, auth: AuthDep):
    svc = get_chat_service()
    try:
        row = await svc.delete_message(
            channel_id=channel_id,
            user_id=auth.user_id,
            message_id=message_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    return row
