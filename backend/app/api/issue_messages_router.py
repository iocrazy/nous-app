"""Issue Messages REST API — paperclip-style chat thread per issue (A8).

Endpoints (mounted at /api/v1/issues/{issue_id}/messages):
  GET    /                  — list the thread, oldest first
  POST   /                  — post a comment; optional agent_id triggers
                              a dispatch (kind='agent_run' placeholder)

The status-change side of the timeline is auto-emitted by the database
trigger trg_issue_status_change_message — no explicit endpoint needed.

RLS at the DB layer enforces visibility cascades through `issues`. The
service-role admin client bypasses RLS, so this router re-checks
visibility in Python via the same pattern as issues_router.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.issue_repository import issue_repository
from app.schemas.issue_message import (
    IssueMessage,
    IssueMessageKind,
    IssueMessageList,
    IssueMessagePost,
    IssueMessagePostResponse,
)


router = APIRouter(prefix="/issues", tags=["Issue Messages"])


async def _assert_issue_visible(issue_id: int, auth) -> dict:
    """Return the issue row if the caller can see it; 404 otherwise.

    Mirrors issues_router._assert_visibility. We use service_role for
    storage so RLS doesn't block the SELECT — re-enforce at app layer.
    """
    row = await issue_repository.get_by_id(issue_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    user_id = str(auth.user_id)
    if (
        row.get("created_by_user_id") == user_id
        or row.get("assignee_user_id") == user_id
    ):
        return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


@router.get("/{issue_id}/messages", response_model=IssueMessageList)
async def list_issue_messages(issue_id: int, auth: AuthDep) -> IssueMessageList:
    """Fetch the chat thread for an issue, oldest first."""
    await _assert_issue_visible(issue_id, auth)

    sb = await get_async_supabase_admin()
    try:
        result = (
            await sb.table("issue_messages")
            .select("*", count="exact")
            .eq("issue_id", issue_id)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:
        logger.exception(f"list issue_messages failed (issue_id={issue_id}): {exc}")
        raise HTTPException(500, "failed to list messages")

    rows = result.data or []
    return IssueMessageList(
        messages=[IssueMessage.model_validate(r) for r in rows],
        total=result.count or len(rows),
    )


@router.post(
    "/{issue_id}/messages",
    response_model=IssueMessagePostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_issue_message(
    issue_id: int, payload: IssueMessagePost, auth: AuthDep
) -> IssueMessagePostResponse:
    """Post a comment on an issue. If `agent_id` is set, also record an
    agent_run placeholder in the same thread (the actual run is dispatched
    via the existing /api/v1/issues/{id}/dispatch path or the workforce
    runtime — A8.4 wires the dispatch trigger end-to-end)."""
    await _assert_issue_visible(issue_id, auth)

    sb = await get_async_supabase_admin()

    # 1. Insert the user's comment.
    comment_row = {
        "issue_id": issue_id,
        "kind": IssueMessageKind.COMMENT.value,
        "author_user_id": str(auth.user_id),
        "body": payload.body,
        "meta": {},
    }
    try:
        comment_resp = await sb.table("issue_messages").insert(comment_row).execute()
    except Exception as exc:
        logger.exception(f"insert comment failed (issue_id={issue_id}): {exc}")
        raise HTTPException(500, "comment insert failed")

    if not comment_resp.data:
        raise HTTPException(500, "comment insert returned no row")

    comment = IssueMessage.model_validate(comment_resp.data[0])

    # 2. If agent_id provided, record an agent_run placeholder. The actual
    #    agent execution stack (workforce / AgentRunner) ingests this row
    #    and writes its own update with the run summary + duration.
    agent_run: Optional[IssueMessage] = None
    if payload.agent_id is not None:
        run_row = {
            "issue_id": issue_id,
            "kind": IssueMessageKind.AGENT_RUN.value,
            "author_agent_id": str(payload.agent_id),
            "body": "(dispatched — agent picking up shortly)",
            "meta": {
                "trigger": "issue_reply",
                "triggered_by_user_id": str(auth.user_id),
                "comment_id": str(comment.id),
            },
        }
        try:
            run_resp = await sb.table("issue_messages").insert(run_row).execute()
        except Exception as exc:
            logger.exception(
                f"insert agent_run placeholder failed (issue_id={issue_id}): {exc}"
            )
            # Don't fail the whole request — comment is already posted.
            run_resp = None

        if run_resp and run_resp.data:
            agent_run = IssueMessage.model_validate(run_resp.data[0])

    return IssueMessagePostResponse(comment=comment, agent_run=agent_run)


__all__ = ["router"]
