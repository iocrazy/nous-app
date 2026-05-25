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

Spec-1a (Task 5): GET /{issue_id}/messages has a dual read path.
  - Session path: when issues.ai_session_id is set, read ai_messages for that
    session and map each row to IssueMessage shape so the frontend needs no
    change.
  - Legacy path: when ai_session_id is NULL, fall back to the issue_messages
    table unchanged (covers issues predating the ai_session wiring).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from dbos import DBOS, SetWorkflowID
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
from app.services.issues.issue_session import get_or_create_issue_session
from app.workflows.issue_lifecycle import respond_to_issue_reply

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


def _map_ai_message_to_issue_message(
    row: dict[str, Any],
    *,
    issue_id: int,
    session_user_id: Optional[UUID],
) -> IssueMessage:
    """Map a single ai_messages row to the IssueMessage UI shape.

    Mapping rules (Spec-1a Task 5):
      role='user'      → kind='comment',       author_user_id=session's user
      role='assistant' → kind='agent_run',     author_agent_id=ai_message.agent_id,
                                               agent_run_id=metadata_json.run_id
      role='system'    → kind='system_status', from_status / to_status from
                                               metadata_json when kind=='status'
    """
    role: str = row.get("role", "")
    meta: dict[str, Any] = row.get("metadata_json") or {}
    raw_agent_id = row.get("agent_id")

    if role == "user":
        kind = IssueMessageKind.COMMENT
        author_user_id: Optional[UUID] = session_user_id
        author_agent_id: Optional[UUID] = None
        agent_run_id: Optional[UUID] = None
        from_status: Optional[str] = None
        to_status: Optional[str] = None
    elif role == "assistant":
        kind = IssueMessageKind.AGENT_RUN
        author_user_id = None
        author_agent_id = UUID(str(raw_agent_id)) if raw_agent_id else None
        raw_run_id = meta.get("run_id")
        agent_run_id = UUID(str(raw_run_id)) if raw_run_id else None
        from_status = None
        to_status = None
    else:
        # role='system' — treat as system_status; extract from/to if present
        kind = IssueMessageKind.SYSTEM_STATUS
        author_user_id = None
        author_agent_id = None
        agent_run_id = None
        if meta.get("kind") == "status":
            from_status = meta.get("from") or None
            to_status = meta.get("to") or None
        else:
            from_status = None
            to_status = None

    return IssueMessage(
        id=UUID(str(row["id"])),
        issue_id=issue_id,
        kind=kind,
        author_user_id=author_user_id,
        author_agent_id=author_agent_id,
        body=row.get("content"),
        meta=meta,
        agent_run_id=agent_run_id,
        from_status=from_status,
        to_status=to_status,
        created_at=row["created_at"],
    )


@router.get("/{issue_id}/messages", response_model=IssueMessageList)
async def list_issue_messages(issue_id: int, auth: AuthDep) -> IssueMessageList:
    """Fetch the chat thread for an issue, oldest first.

    Dual-path (Spec-1a Task 5):
    - Session path: when the issue has ai_session_id set, reads ai_messages for
      that session and maps each row to IssueMessage shape.
    - Legacy path: when ai_session_id is None, reads issue_messages directly
      (unchanged behaviour for issues predating the ai_session wiring).
    """
    issue_row = await _assert_issue_visible(issue_id, auth)
    ai_session_id: Optional[str] = issue_row.get("ai_session_id")

    sb = await get_async_supabase_admin()

    # ── Session path ──────────────────────────────────────────────────────
    if ai_session_id:
        # Fetch the session's user_id once (needed for user-role message mapping).
        try:
            session_resp = (
                await sb.table("ai_sessions")
                .select("id,user_id")
                .eq("id", ai_session_id)
                .maybe_single()
                .execute()
            )
        except Exception as exc:
            logger.exception(
                f"fetch ai_session failed (issue_id={issue_id}, "
                f"session_id={ai_session_id}): {exc}"
            )
            raise HTTPException(500, "failed to fetch session")

        session_row = session_resp.data if session_resp else None
        session_user_id: Optional[UUID] = None
        if session_row and session_row.get("user_id"):
            try:
                session_user_id = UUID(str(session_row["user_id"]))
            except (ValueError, AttributeError):
                pass

        # Read ai_messages for this session, oldest first.
        try:
            msgs_resp = (
                await sb.table("ai_messages")
                .select("*")
                .eq("session_id", ai_session_id)
                .order("created_at", desc=False)
                .execute()
            )
        except Exception as exc:
            logger.exception(
                f"fetch ai_messages failed (issue_id={issue_id}, "
                f"session_id={ai_session_id}): {exc}"
            )
            raise HTTPException(500, "failed to list messages")

        rows = msgs_resp.data or []
        messages = [
            _map_ai_message_to_issue_message(
                r, issue_id=issue_id, session_user_id=session_user_id
            )
            for r in rows
        ]
        return IssueMessageList(messages=messages, total=len(messages))

    # ── Legacy path (no ai_session) ───────────────────────────────────────
    # Unchanged: read issue_messages table so old issues render correctly.
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
    """Post a human comment on an issue.

    Session path (Spec-1b): when the issue has an assigned agent, the comment
    is treated as a reply that drives another agent turn on the issue's
    ai_session (respond_to_issue_reply workflow). The human message + the
    agent reply are persisted as ai_messages by run_session_turn and surface
    through GET /messages (Spec-1a). The returned comment is a synthesized
    optimistic row; the canonical thread comes from GET.

    Legacy path: issues with no assigned agent keep the issue_messages insert.
    """
    issue_row = await _assert_issue_visible(issue_id, auth)
    assignee_agent_id = issue_row.get("assignee_agent_id")

    # ── Session path (Spec-1b) ────────────────────────────────────────────
    if assignee_agent_id:
        session_id = await get_or_create_issue_session(issue_id)
        if not session_id:
            raise HTTPException(500, "issue has an agent but no resolvable session")

        # The turn runs as the issue OWNER (BYO-key/adapter context), mirroring
        # get_or_create_issue_session; the replying human's identity is not
        # separately threaded (single-owner-issue assumption).
        owner_id = issue_row.get("created_by_user_id") or issue_row.get(
            "assignee_user_id"
        )
        if not owner_id:
            raise HTTPException(500, "issue has no owner to run the turn as")

        wf_id = f"issue-reply-{issue_id}-{uuid.uuid4()}"
        try:
            with SetWorkflowID(wf_id):
                DBOS.start_workflow(
                    respond_to_issue_reply, issue_id, str(owner_id), payload.body
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"dispatch respond_to_issue_reply failed (issue_id={issue_id}): {exc}"
            )
            raise HTTPException(500, "failed to dispatch reply turn")

        # Optimistic comment for immediate render; GET (ai_messages) is canonical.
        comment = IssueMessage(
            id=uuid.uuid4(),
            issue_id=issue_id,
            kind=IssueMessageKind.COMMENT,
            author_user_id=auth.user_id,
            body=payload.body,
            meta={"optimistic": True},
            created_at=datetime.now(timezone.utc),
        )
        return IssueMessagePostResponse(comment=comment, agent_run=None)

    # ── Legacy path (no assigned agent) ───────────────────────────────────
    sb = await get_async_supabase_admin()
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
    return IssueMessagePostResponse(comment=comment, agent_run=None)


@router.post("/{issue_id}/agent-runs/{run_id}/simulate-complete")
async def simulate_agent_run_complete(
    issue_id: int,
    run_id: UUID,
    auth: AuthDep,
    output_summary: Optional[str] = None,
) -> dict:
    """Dev/demo helper: flip an issue-scoped agent_run from running →
    completed with a sample summary. The mig 208 terminal trigger then
    UPDATEs the chat row in place; Realtime delivers it to subscribers.

    Mediahub doesn't have a real agent runtime listening for
    issue_reply-triggered runs yet, so without this endpoint the chat
    row sits at "Agent picking up…" forever. This isn't gated to admin
    by intent — it's a dev tool that's safe in any environment because
    it only affects rows the user can already see (issue visibility +
    explicit run_id)."""
    from datetime import datetime, timezone

    await _assert_issue_visible(issue_id, auth)

    sb = await get_async_supabase_admin()

    # Confirm the run belongs to this issue and is still running.
    pre = (
        await sb.table("agent_runs")
        .select("id,status,issue_id,started_at,prompt_tokens,completion_tokens")
        .eq("id", str(run_id))
        .maybe_single()
        .execute()
    )
    row = pre.data if pre and pre.data else None
    if not row:
        raise HTTPException(404, "agent_run not found")
    if row.get("issue_id") != issue_id:
        raise HTTPException(400, "run does not belong to this issue")
    if row.get("status") != "running":
        raise HTTPException(
            400, f"run already in status={row.get('status')}; cannot simulate"
        )

    summary = output_summary or (
        "(simulated) 我已经看完上下文，上面这条 reply 涉及 mediahub 的 paperclip-style "
        "心跳模型 + workforce 调度。简短结论：当前实现已经把 4 维 liveness 接通到 chat row，"
        "下一步可以让 workforce 创建 agent_runs 时回填 issue_id。"
    )

    now = datetime.now(timezone.utc).isoformat()
    try:
        await (
            sb.table("agent_runs")
            .update(
                {
                    "status": "completed",
                    "ended_at": now,
                    "output_summary": summary,
                    "cost_cents": 4,
                    "prompt_tokens": int(row.get("prompt_tokens") or 0) + 240,
                    "completion_tokens": int(row.get("completion_tokens") or 0) + 180,
                }
            )
            .eq("id", str(run_id))
            .eq("status", "running")  # CAS guard
            .execute()
        )
    except Exception as exc:
        logger.exception(f"simulate-complete update failed (run_id={run_id}): {exc}")
        raise HTTPException(500, "simulate-complete update failed")

    return {
        "run_id": str(run_id),
        "status": "completed",
        "summary_preview": summary[:80],
    }


__all__ = ["router"]
