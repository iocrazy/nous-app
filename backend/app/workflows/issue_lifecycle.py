"""execute_issue parent workflow — design doc Protocol 2.

Lifecycle: issue(todo) → atomic_checkout → set_status(in_progress) →
[agent/leaf runs] → set_status(done|in_review|blocked) → clear_lock.

DB access goes through the SQLAlchemy engine (app.db.engine) over asyncpg
→ Supavisor — same privileged connection the other migrated workflows use,
so the legacy `SET ROLE service_role` from the raw-psycopg version is gone.
Steps + workflow are async because the engine helpers are async.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

# Spec-2: how many bounded continuation turns an agent may auto-run on one issue
# before it is handed to a human (in_review). Mirrors the liveness MAX, but is a
# distinct issue-level axis (process-liveness lives on agent_runs).
try:
    ISSUE_MAX_CONTINUATIONS = max(0, int(os.getenv("ISSUE_MAX_CONTINUATIONS", "2")))
except ValueError:
    ISSUE_MAX_CONTINUATIONS = 2

from dbos import DBOS
from loguru import logger

# noqa: F401 — module-level handle for run_issue_reply_step + tests
from app.services.ai.chat.ai_library_chat_service import (  # noqa: F401
    AILibraryChatService,
)
from app.services.issues.issue_chat_stream import (  # noqa: F401
    publish_chunk,
    publish_message,
    publish_status,
)


def _engine():
    from app.db import engine as db_engine

    return db_engine


@DBOS.step()
async def atomic_checkout(issue_id: int, dbos_workflow_id: str) -> bool:
    """Atomically claim an issue. False if someone else already holds the lock."""
    from app.db import engine as db_engine

    # execution fields are service_role-only (issues_update_allowlist, mig 170)
    locked = await db_engine.execute_as_service_role(
        "UPDATE public.issues SET execution_locked_at = now(), "
        "dbos_workflow_id = :wid "
        "WHERE id = :id AND execution_locked_at IS NULL",
        {"wid": dbos_workflow_id, "id": issue_id},
    )
    return locked > 0


@DBOS.step()
async def set_status(
    issue_id: int,
    status: str,
    *,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    agent_outcome: Optional[str] = None,
    outcome_reason: Optional[str] = None,
) -> None:
    """Transition issue.status with side-effect timestamps (design Protocol 5).

    Spec-2: ``agent_outcome`` / ``outcome_reason`` record the agent's FinishIssue
    self-report into execution_state so the UI can distinguish "agent reports
    done" from "agent merely stopped"."""
    from app.db import engine as db_engine

    now_dt = datetime.now(timezone.utc)
    cols = ["status = :status"]
    params: dict[str, Any] = {"status": status}
    if status == "in_progress":
        cols.append("started_at = :ts")
        params["ts"] = now_dt
    elif status == "done":
        cols.append("completed_at = :ts")
        params["ts"] = now_dt
    elif status == "cancelled":
        cols.append("cancelled_at = :ts")
        params["ts"] = now_dt
    state: dict[str, Any] = {}
    if error_code or error_message:
        state["error_code"] = error_code
        state["error_message"] = error_message
    if agent_outcome:
        state["agent_outcome"] = agent_outcome
    if outcome_reason:
        state["outcome_reason"] = outcome_reason
    if state:
        cols.append("execution_state = CAST(:state AS jsonb)")
        params["state"] = json.dumps(state)
    params["id"] = issue_id
    # may write execution_state (service_role-only via issues_update_allowlist)
    await db_engine.execute_as_service_role(
        f"UPDATE public.issues SET {', '.join(cols)} WHERE id = :id", params
    )


@DBOS.step()
async def load_auto_close_flag() -> bool:
    """Spec-2 slice 2a: read the platform ``issue_agent_auto_close`` toggle.
    Checkpointed as a step so a workflow replay uses the value seen at dispatch.
    Defaults to False (never auto-close) on any read failure / unset key."""
    from app.db import engine as db_engine

    try:
        val = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings "
            "WHERE key = 'issue_agent_auto_close'"
        )
    except Exception:  # noqa: BLE001 — a settings read must never break dispatch
        logger.warning("[execute_issue] auto_close flag read failed; defaulting off")
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"} if val else False


@DBOS.step()
async def clear_lock(issue_id: int) -> None:
    """Release the execution lock so the issue can be retried later."""
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        "UPDATE public.issues SET execution_locked_at = NULL WHERE id = :id",
        {"id": issue_id},
    )


@DBOS.step()
async def acquire_turn_lock(issue_id: int) -> bool:
    """Claim the per-issue turn lock for a reply turn. Reuses
    issues.execution_locked_at (shared with execute_issue dispatch) but does
    NOT touch dbos_workflow_id — the dispatch-status UI subscribes to that.
    Returns True if acquired, False if a turn is already in flight."""
    # execution_locked_at is service_role-only (issues_update_allowlist, mig 170)
    locked = await _engine().execute_as_service_role(
        "UPDATE public.issues SET execution_locked_at = now() "
        "WHERE id = :id AND execution_locked_at IS NULL",
        {"id": issue_id},
    )
    return locked > 0


@DBOS.step()
async def ensure_issue_session_step(issue_id: int) -> str:
    """Get-or-create the issue's ai_session; raise if the issue has no
    assignable agent (nothing to respond with)."""
    from app.services.issues.issue_session import get_or_create_issue_session

    session_id = await get_or_create_issue_session(issue_id)
    if not session_id:
        raise RuntimeError(f"issue {issue_id} has no assignable agent session")
    return session_id


@DBOS.step()
# no step retry: run_session_turn is non-idempotent (appends user msg + charges);
# it has its own internal LLM fallback chain.
async def run_issue_reply_step(
    *,
    issue_id: int,
    session_id: str,
    user_id: str,
    reply_text: str,
    attachments: Optional[list[dict]] = None,
) -> Optional[str]:
    """Run one reply turn, streaming token deltas + the final message to Redis.

    ``attachments`` is a list of serialised AttachmentRequest dicts (from the
    router's model_dump() call). They are deserialised back to AttachmentRequest
    objects here before being passed to run_session_turn, which follows the same
    resolve_attachments path used by the chat router (sub-plan 1, G2).
    """
    from uuid import UUID

    from app.schemas.ai_library_chat import AttachmentRequest

    async def _cb(delta: str) -> None:
        await publish_chunk(issue_id, delta)

    attachment_objects = (
        [AttachmentRequest(**a) for a in attachments] if attachments else None
    )

    result = await AILibraryChatService().run_session_turn(
        # session_id is ai_sessions.id = BIGINT Snowflake (mig 231), a numeric
        # string. Pass it through as-is; UUID() would raise ValueError.
        session_id,
        user_id=UUID(user_id),
        content=reply_text,
        trigger="issue_reply",
        chunk_callback=_cb,
        attachments=attachment_objects,
    )
    assistant = result.get("assistant_message") or {}
    await publish_message(issue_id, assistant, session_user_id=None)
    return assistant.get("content") or ""


# Spec-1b: bounded wait for the per-issue turn lock. Replies are human-paced,
# so a turn almost always frees the lock within seconds; the cap only bounds
# the pathological "reply lands during a multi-minute turn" case.
REPLY_LOCK_MAX_ATTEMPTS = 60
REPLY_LOCK_WAIT_SECONDS = 10


async def _run_reply_turns(
    issue_id: int,
    user_id: str,
    reply_text: str,
    *,
    session_id: str,
    acquire,
    run_turn,
    release,
    sleep,
    max_attempts: int = REPLY_LOCK_MAX_ATTEMPTS,
    wait_seconds: int = REPLY_LOCK_WAIT_SECONDS,
    attachments: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Acquire the per-issue turn lock (waiting if a turn is in flight), run
    exactly one reply turn, then release. No status change (Spec-1b)."""
    acquired = False
    for _ in range(max_attempts):
        if await acquire(issue_id):
            acquired = True
            break
        await sleep(wait_seconds)

    if not acquired:
        logger.warning(
            f"[issue_reply] issue {issue_id}: turn lock busy for ~10min"
            f" ({max_attempts} attempts); reply NOT processed — user must"
            " re-send. (Coalescing is the planned fix.)"
        )
        return {"issue_id": issue_id, "deferred": True}

    try:
        await run_turn(
            issue_id=issue_id,
            session_id=session_id,
            user_id=user_id,
            reply_text=reply_text,
            attachments=attachments,
        )
        return {"issue_id": issue_id, "executed": True}
    finally:
        await release(issue_id)


@DBOS.workflow()
async def respond_to_issue_reply(
    issue_id: int,
    user_id: str,
    reply_text: str,
    attachments: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Spec-1b: run one agent turn in response to a human reply on an issue.
    Serialized per issue via the turn lock; does NOT change issue status.

    ``attachments`` (added in sub-plan 3, Task 5) is a list of serialised
    AttachmentRequest dicts forwarded to run_session_turn so the agent turn
    can process images/PDFs pasted or dragged into the reply box.
    """
    session_id = await ensure_issue_session_step(issue_id)
    await publish_status(issue_id, "running")
    try:
        return await _run_reply_turns(
            issue_id,
            user_id,
            reply_text,
            session_id=session_id,
            acquire=acquire_turn_lock,
            run_turn=run_issue_reply_step,
            release=clear_lock,
            sleep=DBOS.sleep_async,
            attachments=attachments,
        )
    finally:
        await publish_status(issue_id, "done")


@DBOS.step()
async def load_issue(issue_id: int) -> dict[str, Any]:
    """Read issue row as a plain dict (serializes through DBOS step memo).

    The engine returns datetime/UUID as objects (normalized to str below) and
    jsonb as a string; no current consumer reads a jsonb column off this dict."""
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT * FROM public.issues WHERE id = :id", {"id": issue_id}
    )
    if not row:
        raise RuntimeError(f"issue id={issue_id} not found")
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):  # UUID has .hex
            out[k] = str(v)
        else:
            out[k] = v
    return out


@DBOS.step()
async def run_issue_agent_step(
    issue: dict[str, Any],
    agent_id: str,
    user_id: str,
    is_continuation: bool = False,
) -> dict[str, Any]:
    """Run the assigned agent on the issue. The RunRecorder (issue_id-linked)
    + mig-208 triggers write the result into the issue chat; we return the
    agent's FinishIssue declaration ``{content, outcome, reason}`` so the
    workflow can route status + continuation. No retry: run_issue_agent calls
    run_session_turn which is non-idempotent (appends user msg + charges) and
    streams per-token chunks — a retry would re-emit the whole stream (double
    bubble) and re-charge the user."""
    from app.services.issues.issue_agent_executor import run_issue_agent

    return await run_issue_agent(
        issue=issue,
        agent_id=agent_id,
        user_id=user_id,
        is_continuation=is_continuation,
    )


async def _run_dispatch_with_continuation(
    issue_id: int,
    issue_row: dict[str, Any],
    agent_id: str,
    user_id: str,
    *,
    run_turn: Callable[..., Awaitable[dict[str, Any]]],
    set_status: Callable[..., Awaitable[None]],
    max_continuations: int = ISSUE_MAX_CONTINUATIONS,
    auto_close: bool = False,
) -> dict[str, Any]:
    """Spec-2 core: run the agent, then route the issue by the agent's declared
    FinishIssue outcome. ``continue`` auto-runs another bounded turn; everything
    else terminates the dispatch. Deps are injected so this is unit-testable
    without DBOS/DB (mirrors _run_reply_turns).

    Routing:
      completed       → done if ``auto_close`` (slice 2a platform toggle) else
                        in_review (human confirms)
      needs_input     → needs_followup (slice 2b: a deliberate hand-off, distinct
                        from blocked=errored; carries the agent's reason)
      continue (capped)→ in_review (handed to a human after the cap; never
                        auto-closes — the agent never said it finished)
      none declared   → in_review (default — unchanged legacy behavior)
    """
    attempt = 0
    outcome: Optional[str] = None
    reason: Optional[str] = None
    while True:
        res = await run_turn(
            issue_row, agent_id, user_id, is_continuation=(attempt > 0)
        )
        outcome = (res or {}).get("outcome")
        reason = (res or {}).get("reason")
        if outcome == "continue" and attempt < max_continuations:
            attempt += 1
            continue
        break

    if outcome == "needs_input":
        await set_status(
            issue_id,
            "needs_followup",
            agent_outcome="needs_input",
            outcome_reason=reason,
        )
    elif outcome == "completed":
        # slice 2a: self-close only when the platform toggle trusts agents to.
        await set_status(
            issue_id,
            "done" if auto_close else "in_review",
            agent_outcome="completed",
            outcome_reason=reason,
        )
    elif outcome == "continue":
        # Asked for more turns past the cap — stop and hand to a human.
        await set_status(
            issue_id,
            "in_review",
            agent_outcome="continue_capped",
            outcome_reason=reason,
        )
    else:
        # No declaration → preserve legacy behavior (park for human review).
        await set_status(issue_id, "in_review")
    return {"outcome": outcome, "attempts": attempt}


@DBOS.workflow()
async def execute_issue(issue_id: int) -> dict[str, Any]:
    """Parent workflow — owns the issue lifecycle."""
    await load_issue(issue_id)

    workflow_id = DBOS.workflow_id
    locked = await atomic_checkout(issue_id, workflow_id)
    if not locked:
        logger.info(f"[execute_issue] issue {issue_id} already locked, skipping")
        return {"skipped": True, "issue_id": issue_id, "reason": "already_locked"}

    await set_status(issue_id, "in_progress")

    try:
        issue_row = await load_issue(issue_id)
        agent_id = issue_row.get("assignee_agent_id")
        user_id = issue_row.get("created_by_user_id") or issue_row.get(
            "assignee_user_id"
        )
        if agent_id and user_id:
            # Spec-2: route status + bounded continuation by the agent's
            # FinishIssue declaration (agent output already bridged to chat).
            auto_close = await load_auto_close_flag()
            routed = await _run_dispatch_with_continuation(
                issue_id,
                issue_row,
                agent_id,
                user_id,
                run_turn=run_issue_agent_step,
                set_status=set_status,
                auto_close=auto_close,
            )
            return {"issue_id": issue_id, "executed": True, **routed}
        # No agent assigned → nothing to run; close it out.
        await set_status(issue_id, "done")
        return {"issue_id": issue_id, "executed": False}

    except Exception as exc:  # noqa: BLE001
        await set_status(
            issue_id,
            "blocked",
            error_code="execute_issue_failed",
            error_message=str(exc)[:500],
        )
        raise
    finally:
        await clear_lock(issue_id)
