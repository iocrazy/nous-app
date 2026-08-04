"""Unified Conversations REST endpoints."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.schemas.conversation import (
    AgentAdd,
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    MarkReadIn,
    MemberAdd,
    MemberOut,
    MemberRoleSet,
    MessageCreate,
    MessageOut,
    OwnerTransfer,
)
from app.services.chat.chat_attachment_service import save_chat_image
from app.services.conversation_service import get_conversation_service
from app.services.library.promote_generated_media_service import (
    PromoteGeneratedMediaService,
)
from app.services.modules.gate import require_module

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"],
    dependencies=[Depends(require_module("ai-library"))],
)


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


@router.get("", response_model=list[ConversationOut])
async def list_my_conversations(auth: AuthDep):
    svc = get_conversation_service()
    try:
        rows = await svc.list_my_conversations(user_id=auth.user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return rows


@router.post("", response_model=ConversationOut)
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


@router.get("/{conversation_id}/members", response_model=list[MemberOut])
async def list_members(conversation_id: int, auth: AuthDep):
    svc = get_conversation_service()
    try:
        return await svc.list_members(
            conversation_id=conversation_id, user_id=auth.user_id
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.delete("/{conversation_id}/members/{member_user_id}")
async def remove_member(conversation_id: int, member_user_id: str, auth: AuthDep):
    """Remove a member (admin/owner) or leave the group (self-target)."""
    svc = get_conversation_service()
    try:
        return await svc.remove_member(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            target_user_id=member_user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.patch("/{conversation_id}/members/{member_user_id}/role")
async def set_member_role(
    conversation_id: int, member_user_id: str, payload: MemberRoleSet, auth: AuthDep
):
    svc = get_conversation_service()
    try:
        return await svc.set_member_role(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            target_user_id=member_user_id,
            role=payload.role,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{conversation_id}/transfer-owner")
async def transfer_owner(conversation_id: int, payload: OwnerTransfer, auth: AuthDep):
    svc = get_conversation_service()
    try:
        return await svc.transfer_ownership(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            to_user_id=payload.to_user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/{conversation_id}/agents/{agent_id}")
async def remove_agent(conversation_id: int, agent_id: str, auth: AuthDep):
    svc = get_conversation_service()
    try:
        return await svc.remove_agent(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            agent_id=agent_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: int, payload: ConversationUpdate, auth: AuthDep
):
    svc = get_conversation_service()
    try:
        row = await svc.update_conversation(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            name=payload.name,
            type=payload.type,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {**row, "unread": 0}


@router.delete("/{conversation_id}")
async def dissolve_conversation(conversation_id: int, auth: AuthDep):
    """Dissolve (archive) a group — owner only."""
    svc = get_conversation_service()
    try:
        return await svc.dissolve_conversation(
            conversation_id=conversation_id, user_id=auth.user_id
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


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


class PromoteAttachmentBody(BaseModel):
    scope_id: int


@router.post("/{conversation_id}/attachments")
async def upload_attachment(
    conversation_id: int,
    auth: AuthDep,
    file: UploadFile,
) -> dict:
    """Upload an image into the staged generated_media store for this conversation.

    Returns: {id, mime, file_size_bytes, url}
    Errors:  400 for non-image mime or unknown conversation; 403 for non-members.
    """
    mime = file.content_type or ""
    if not mime.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only image/* uploads are supported; got {mime!r}",
        )
    file_bytes = await file.read()
    try:
        row = await save_chat_image(
            conversation_id=conversation_id,
            user_id=auth.user_id,
            file_bytes=file_bytes,
            filename=file.filename or "upload",
            mime=mime,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {
        "id": str(row["id"]),
        "mime": row["mime"],
        "file_size_bytes": row["file_size_bytes"],
        "url": f"/api/v1/generated-media/{row['id']}/cover",
    }


@router.post("/attachments/{attachment_id}/promote")
async def promote_attachment(
    attachment_id: int,
    body: PromoteAttachmentBody,
    auth: AuthDep,
) -> dict:
    """Promote a staged chat attachment into a first-class resource.

    Returns: {promoted_resource_id}
    Errors:  400 if generation not found or missing file; 403 for auth failures.
    """
    try:
        resource = await PromoteGeneratedMediaService().promote(
            gen_id=attachment_id,
            user_id=auth.user_id,
            target_scope_id=body.scope_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"promoted_resource_id": str(resource["id"])}
