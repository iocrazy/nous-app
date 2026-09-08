"""``/ai-library/inbox`` — deliver into / read a running agent's inbox (spec §1-③).

Not the user-notification inbox (``inbox_router``). A delivery is a row on
``agent_run_inbox`` keyed by its target; the target's root run claims it at
its next step boundary (``InboxClaimHook``). Nothing here talks to a run.

Errors are typed: 404 when the target is not visible to the caller (never
403 — existence must not leak), 409 ``target_ended`` when the issue is
done / cancelled / closed or the conversation is archived, 400
``answer_shape`` when an ``answer`` lacks ``question_id`` / ``value``.
Option matching for answers lands with typed questions (phase 3); until
then an answer is free text bound to a question id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.repositories.agent_run_inbox_repository import get_agent_run_inbox_repository
from app.repositories.conversation_repository import get_conversation_repository
from app.services.issues.issue_visibility import assert_issue_visible
from app.workflows.issue_lifecycle import PREEMPT_STATUSES

router = APIRouter(prefix="/ai-library/inbox", tags=["AI Library"])

TargetKind = Literal["conversation", "issue"]
DeliverableKind = Literal["steer", "answer", "budget_reply"]


class InboxPost(BaseModel):
    target_kind: TargetKind
    target_id: int
    kind: DeliverableKind = "steer"
    content: dict[str, Any] = Field(default_factory=dict)


class InboxItemOut(BaseModel):
    """Snowflake ids as strings — the JS side cannot hold them as numbers."""

    id: str
    target_kind: str
    target_id: str
    user_id: str
    kind: str
    content: dict[str, Any]
    created_at: datetime
    claimed_at: Optional[datetime] = None
    claimed_run_id: Optional[str] = None
    claimed_turn: Optional[int] = None
    claimed_step: Optional[int] = None
    expired_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "InboxItemOut":
        return cls(
            id=str(row["id"]),
            target_kind=row["target_kind"],
            target_id=str(row["target_id"]),
            user_id=str(row["user_id"]),
            kind=row["kind"],
            content=dict(row.get("content") or {}),
            created_at=row["created_at"],
            claimed_at=row.get("claimed_at"),
            claimed_run_id=(
                str(row["claimed_run_id"])
                if row.get("claimed_run_id") is not None
                else None
            ),
            claimed_turn=row.get("claimed_turn"),
            claimed_step=row.get("claimed_step"),
            expired_at=row.get("expired_at"),
        )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
    )


async def assert_target_open(target_kind: str, target_id: int, auth: Any) -> None:
    """404 if the caller cannot see the target, 409 ``target_ended`` if it
    can no longer take a message. Shared by POST and GET."""
    if target_kind == "issue":
        row = await assert_issue_visible(int(target_id), auth)
        if row.get("status") in PREEMPT_STATUSES:
            raise _conflict("target_ended", f"issue is {row.get('status')}")
        return
    conv = await get_agent_run_inbox_repository().conversation_target(int(target_id))
    user_id = str(auth.user_id)
    if conv is None or not (
        conv["created_by"] == user_id
        or await get_conversation_repository().is_member(
            conversation_id=int(target_id), user_id=user_id
        )
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if conv.get("archived_at") is not None:
        raise _conflict("target_ended", "conversation is archived")


def validate_content(kind: str, content: dict[str, Any]) -> None:
    if kind == "answer" and not (
        isinstance(content.get("question_id"), str) and "value" in content
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "answer_shape",
                "message": "answer needs question_id and value",
            },
        )
    if kind == "steer" and not isinstance(content.get("body"), str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "steer_shape", "message": "steer needs a text body"},
        )


async def persist_conversation_steer(
    conversation_id: int, user_id: str, body: str
) -> None:
    """Keep the steer on the conversation thread as a user message, the same
    way an issue comment keeps its row when diverted (issue_messages_router).
    The runner injects the inbox item into THIS turn; the row is what history
    shows afterwards. Best-effort — the inbox row is the contract."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    try:
        await ConversationsAiStore().append_user_message(
            session_id=int(conversation_id), user_id=user_id, content=body
        )
    except Exception as err:  # noqa: BLE001
        logger.warning(
            f"[agent_inbox] steer row not persisted (conversation={conversation_id}): {err}"
        )


@router.post("", response_model=InboxItemOut, status_code=status.HTTP_201_CREATED)
async def deliver(payload: InboxPost, auth: AuthDep) -> InboxItemOut:
    await assert_target_open(payload.target_kind, payload.target_id, auth)
    validate_content(payload.kind, payload.content)
    row = await get_agent_run_inbox_repository().enqueue(
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        user_id=str(auth.user_id),
        kind=payload.kind,
        content=payload.content,
    )
    if payload.target_kind == "conversation" and payload.kind == "steer":
        await persist_conversation_steer(
            payload.target_id, str(auth.user_id), str(payload.content.get("body", ""))
        )
    return InboxItemOut.from_row(row)


class PendingSummaryOut(BaseModel):
    """One visible issue with unclaimed steers. ``target_id`` str (Snowflake)."""

    target_id: str
    count: int
    oldest_at: datetime


@router.get("/pending-summary", response_model=list[PendingSummaryOut])
async def pending_summary(
    auth: AuthDep,
    target_kind: Literal["issue"] = Query(
        ..., description="only issue targets are summarised (phase 2a §4)"
    ),
) -> list[PendingSummaryOut]:
    """Queued-comment counts per issue for the list / board / Task Center
    chips. Visibility is folded into the query (issue_repository
    .visibility_predicate), so a count never names an issue the caller
    cannot open. ``target_kind`` is required and only ``issue`` is accepted:
    conversation inboxes have no list surface."""
    del target_kind  # validated by the Literal; one value today
    rows = await get_agent_run_inbox_repository().pending_summary(str(auth.user_id))
    return [
        PendingSummaryOut(
            target_id=str(r["target_id"]),
            count=int(r["count"]),
            oldest_at=r["oldest_at"],
        )
        for r in rows
    ]


@router.get("", response_model=list[InboxItemOut])
async def list_items(
    auth: AuthDep,
    target_kind: TargetKind = Query(...),
    target_id: int = Query(...),
    pending: bool = Query(True, description="only unclaimed, unexpired items"),
) -> list[InboxItemOut]:
    try:
        await assert_target_open(target_kind, target_id, auth)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:  # an ended target still lists
            raise
    rows = await get_agent_run_inbox_repository().list_for_target(
        target_kind=target_kind, target_id=target_id, pending_only=pending
    )
    return [InboxItemOut.from_row(r) for r in rows]


__all__ = ["router", "assert_target_open", "validate_content"]
