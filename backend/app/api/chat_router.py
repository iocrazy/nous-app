"""Team Chat REST endpoints (PHASE-1 backend foundation + Task 2 attachments)."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import AuthDep
from app.repositories.chat_attachment_repository import get_chat_attachment_repository
from app.schemas.chat import (
    AgentAdd,
    ChannelCreate,
    ChannelOut,
    MarkReadIn,
    MemberAdd,
    MessageCreate,
    MessageOut,
)
from app.services.chat.chat_attachment_service import save_chat_image
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
    text = payload.body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text required",
        )
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


# ── Task 2: Chat attachments (independent image store) ─────────────────────


@router.post("/channels/{channel_id}/attachments")
async def upload_chat_attachment(channel_id: int, file: UploadFile, auth: AuthDep):
    """Upload an image to the independent chat store.

    Returns ``{id, mime, file_size_bytes, url}`` where ``url`` is the public
    serve endpoint (no auth required — world-readable-by-snowflake-id).
    """
    file_bytes = await file.read()
    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "attachment"
    try:
        row = await save_chat_image(
            channel_id=channel_id,
            user_id=str(auth.user_id),
            file_bytes=file_bytes,
            filename=filename,
            mime=mime,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    att_id = str(row["id"])
    return {
        "id": att_id,
        "mime": row.get("mime"),
        "file_size_bytes": row.get("file_size_bytes"),
        "url": f"/api/v1/chat/attachments/{att_id}/file",
    }


@router.get("/attachments/{attachment_id}/file")
async def serve_chat_attachment(attachment_id: int):
    """Serve a chat image file (no auth — world-readable-by-snowflake-id).

    Security: realpath must stay under DOWNLOAD_PATH (traversal guard mirrors
    get_generation_cover in generated_media_router.py:62-65).
    Cache-Control: immutable — snowflake IDs are unguessable and files are
    never mutated in place.
    """
    repo = get_chat_attachment_repository()
    row = await repo.get(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(settings.DOWNLOAD_PATH, row["file_path"]))
    if not (real == base or real.startswith(base + os.sep)):
        raise HTTPException(status_code=404, detail="not found")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(
        real,
        media_type=row.get("mime") or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
