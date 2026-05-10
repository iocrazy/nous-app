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

from typing import Optional
from uuid import UUID

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

    # 2. If agent_id provided, dispatch a real agent_run. mig 208's
    #    INSERT trigger emits the "Agent picking up…" chat row, the
    #    terminal trigger updates it with the summary on completion,
    #    and the liveness scanner updates the pill color while running.
    #
    #    NOTE: the agent_run row is created in 'running' state with the
    #    issue linkage. The actual execution stack (workforce /
    #    AgentRunner / DBOS) is responsible for filling in
    #    output_summary / cost / tokens / status='completed' when the
    #    run finishes. Until that wiring lands, runs sit in 'running'
    #    forever — the liveness scanner will eventually mark them dead
    #    via the 5-minute stuck threshold, which is the correct
    #    visible behaviour.
    agent_run: Optional[IssueMessage] = None
    if payload.agent_id is not None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        run_payload = {
            "agent_id": str(payload.agent_id),
            "user_id": str(auth.user_id),
            "issue_id": issue_id,
            "status": "running",
            "trigger": "issue_reply",
            "started_at": now,
            "heartbeat_at": now,
            "last_useful_action_at": now,
            "input_summary": payload.body[:500],
        }
        try:
            await sb.table("agent_runs").insert(run_payload).execute()
        except Exception as exc:
            logger.exception(f"agent_run insert failed (issue_id={issue_id}): {exc}")
            # Don't fail the whole request — comment is already posted.

        # The dispatch trigger emitted the chat row; fetch it so the
        # frontend can show it immediately without waiting for Realtime.
        try:
            chat_resp = (
                await sb.table("issue_messages")
                .select("*")
                .eq("issue_id", issue_id)
                .eq("kind", IssueMessageKind.AGENT_RUN.value)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if chat_resp.data:
                agent_run = IssueMessage.model_validate(chat_resp.data[0])
        except Exception as exc:
            logger.exception(f"fetch agent_run chat row after dispatch failed: {exc}")

    return IssueMessagePostResponse(comment=comment, agent_run=agent_run)


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
