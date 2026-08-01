"""execute_issue parent workflow — design doc Protocol 2.

Lifecycle: issue(todo) → atomic_checkout → set_status(in_progress) →
[agent/leaf runs] → set_status(done|in_review|blocked) → clear_lock.

DB access goes through the SQLAlchemy engine (app.db.engine) over asyncpg
→ Supavisor — same privileged connection the other migrated workflows use,
so the legacy `SET ROLE service_role` from the raw-psycopg version is gone.
Steps + workflow are async because the engine helpers are async.
"""

from __future__ import annotations

import functools
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
from app.services.ai.tools.finish_issue_tool import extract_issue_outcome
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
    await _project_status_onto_stage_node(issue_id, status)


async def _project_status_onto_stage_node(issue_id: int, status: str) -> None:
    """Mirror the status just written above onto the issue's project_stage
    node, if it has one.

    ``set_status`` writes ``public.issues`` with raw SQL rather than through
    ``issue_repository.transition_status``, which is where the issue→node
    projection normally rides. Without this call the node kept whatever status
    it had before: an agent self-completing to ``in_review`` left its node on
    ``in_progress``, so the Stage Board showed work still running that was
    actually waiting for a manager's review.

    The ``done``-path autopilot tail enqueue is suppressed — this runs inside
    a ``@DBOS.step``, where starting a workflow raises a bare AssertionError
    (the trap ``_maybe_fire_subissue_barrier`` avoids by living in the
    workflow body). Those ticks still arrive via the arrival/advance triggers
    and the 5-minute sweep.
    """
    try:
        from app.db import engine as db_engine
        from app.repositories.issue_repository import (
            STAGE_NODE_SYNC_STATUSES,
            fire_stage_node_sync,
        )

        if status not in STAGE_NODE_SYNC_STATUSES:
            return
        row = await db_engine.fetch_one(
            "SELECT id, origin_kind, origin_id FROM public.issues WHERE id = :id",
            {"id": issue_id},
        )
        await fire_stage_node_sync(row, status, enqueue_autopilot=False)
    except Exception as exc:  # noqa: BLE001 — the status write is the primary op
        logger.warning(
            f"[execute_issue] stage-node projection failed for issue "
            f"{issue_id}: {exc!r}"
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
) -> dict[str, Any]:
    """Run one reply turn, streaming token deltas + the final message to Redis.

    ``attachments`` is a list of serialised AttachmentRequest dicts (from the
    router's model_dump() call). They are deserialised back to AttachmentRequest
    objects here before being passed to run_session_turn, which follows the same
    resolve_attachments path used by the chat router (sub-plan 1, G2).

    Returns ``{"content": str, "outcome": Optional[str], "reason": Optional[str]}``
    — mirrors ``run_issue_agent``'s FinishIssue extraction (Spec-4) so a reply
    that resumes a needs_input issue can be routed by ``route_finish_outcome``
    the same way the dispatch loop is.
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
    content = assistant.get("content") or ""
    outcome, reason = extract_issue_outcome(result.get("tool_calls"))
    return {
        "content": content,
        "outcome": outcome,
        "reason": reason,
        "run_id": result.get("run_id"),
    }


# Spec-1b: bounded wait for the per-issue turn lock. Replies are human-paced,
# so a turn almost always frees the lock within seconds; the cap only bounds
# the pathological "reply lands during a multi-minute turn" case.
REPLY_LOCK_MAX_ATTEMPTS = 60
REPLY_LOCK_WAIT_SECONDS = 10


def _pending_agent_outcome(issue: dict[str, Any]) -> Optional[str]:
    """Best-effort read of ``execution_state.agent_outcome`` off a ``load_issue``
    row. The engine returns jsonb columns as raw JSON strings (see
    ``load_issue``'s docstring — no consumer parsed them before this), so parse
    defensively; any shape mismatch just means "no pending outcome"."""
    state = issue.get("execution_state")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except (TypeError, ValueError):
            return None
    return state.get("agent_outcome") if isinstance(state, dict) else None


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
    load_issue: Optional[Callable[[int], Awaitable[dict[str, Any]]]] = None,
    set_status: Optional[Callable[..., Awaitable[None]]] = None,
    auto_close: bool = False,
    max_attempts: int = REPLY_LOCK_MAX_ATTEMPTS,
    wait_seconds: int = REPLY_LOCK_WAIT_SECONDS,
    attachments: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Acquire the per-issue turn lock (waiting if a turn is in flight), run
    exactly one reply turn, then release.

    Spec-4 (needs_input first-class): a reply on an issue parked at
    ``needs_followup`` with ``execution_state.agent_outcome`` in
    ``{"needs_input", "empty_output"}`` is treated as the human's answer to
    the agent's question (or nudge past a silent EMPTY_OUTPUT stall) — the
    issue is resumed (``in_progress``) before the turn and routed by the
    turn's own FinishIssue outcome (``route_finish_outcome``) afterward. Any
    other status is left untouched — Spec-1b's original "no status change"
    behavior for a plain reply on an in-flight/already-terminal issue.

    ``load_issue`` / ``set_status`` are optional so existing callers/tests
    that don't exercise the resume path (and predate it) keep working
    unchanged: without ``load_issue`` there is no way to detect the pending
    state, so this behaves exactly as before.

    A resuming turn that raises is caught and routed to ``blocked`` with a
    typed error before re-raising — otherwise the ``set_status(in_progress)``
    above would leave the issue stuck there forever with no terminal status
    ever written (mirrors ``execute_issue``'s own outer try/except). A
    non-resuming reply never touched status, so an exception there just
    propagates unchanged (Spec-1b).
    """
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
        resuming = False
        if load_issue is not None and set_status is not None:
            issue = await load_issue(issue_id)
            resuming = (
                (issue or {}).get("status") == "needs_followup"
                and _pending_agent_outcome(issue or {})
                in {"needs_input", "empty_output"}
            )
            if resuming:
                await set_status(issue_id, "in_progress")

        try:
            result = await run_turn(
                issue_id=issue_id,
                session_id=session_id,
                user_id=user_id,
                reply_text=reply_text,
                attachments=attachments,
            )
        except Exception as exc:
            # Parked finding (Task 1 review): resuming already flipped the
            # issue to in_progress above — without this, a turn that raises
            # here leaves it stuck in_progress forever (no terminal status
            # ever gets written). Mirrors execute_issue's own outer
            # try/except (blocked + typed error fields, then re-raise).
            # Non-resuming replies never touched status, so they stay
            # untouched here too — the exception just propagates.
            if resuming:
                await set_status(
                    issue_id,
                    "blocked",
                    error_code="issue_reply_resume_failed",
                    error_message=str(exc)[:500],
                )
            raise

        if resuming:
            outcome = (result or {}).get("outcome")
            reason = (result or {}).get("reason")
            content_len = len((result or {}).get("content") or "")
            await route_finish_outcome(
                issue_id,
                outcome,
                reason,
                auto_close=auto_close,
                set_status=set_status,
                content_len=content_len,
                run_id=(result or {}).get("run_id"),
            )
            # I4 (final review): mirrors execute_issue's own fan-in call — a
            # child issue resumed and completed via a reply-answer (rather
            # than the dispatch loop) must still wake its parent's barrier,
            # or a parent with issue_agent_auto_close ON hangs forever
            # waiting for a sibling that already finished. Called from the
            # workflow body (this function is never a @DBOS.step), same
            # constraint _maybe_fire_subissue_barrier's own docstring
            # explains. Non-resuming replies never change status, so there
            # is nothing for the barrier to react to — left untouched.
            await _maybe_fire_subissue_barrier(issue_id)
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
    Serialized per issue via the turn lock; does NOT change issue status —
    EXCEPT the Spec-4 needs_input-resume case: a reply on an issue parked at
    ``needs_followup`` with a pending ``needs_input`` FinishIssue declaration
    is treated as the human's answer, so it IS routed (in_progress before the
    turn, then re-routed by the turn's own outcome afterward). See
    ``_run_reply_turns`` for the exact condition and routing.

    ``attachments`` (added in sub-plan 3, Task 5) is a list of serialised
    AttachmentRequest dicts forwarded to run_session_turn so the agent turn
    can process images/PDFs pasted or dragged into the reply box.
    """
    session_id = await ensure_issue_session_step(issue_id)
    auto_close = await load_auto_close_flag()
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
            load_issue=load_issue,
            set_status=set_status,
            auto_close=auto_close,
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
    auto: bool = False,
) -> dict[str, Any]:
    """Run the assigned agent on the issue. The RunRecorder (issue_id-linked)
    + mig-208 triggers write the result into the issue chat; we return the
    agent's FinishIssue declaration ``{content, outcome, reason}`` so the
    workflow can route status + continuation. No retry: run_issue_agent calls
    run_session_turn which is non-idempotent (appends user msg + charges) and
    streams per-token chunks — a retry would re-emit the whole stream (double
    bubble) and re-charge the user.

    ``auto`` (M4 Autopilot, task O2) forwards straight through to
    ``run_issue_agent`` — see that function's docstring for what it changes
    (the quota-counted ``agent_runs.trigger`` value)."""
    from app.services.issues.issue_agent_executor import run_issue_agent

    return await run_issue_agent(
        issue=issue,
        agent_id=agent_id,
        user_id=user_id,
        is_continuation=is_continuation,
        auto=auto,
    )


# Issue statuses that mean "stop working this issue". If an external actor
# (a human, or another agent) moved the issue into one of these mid-dispatch,
# preempt the continuation loop instead of burning the remaining turn budget /
# LLM spend and writing a status the user already overrode. Mirrors Symphony's
# per-turn tracker reconciliation (SPEC §16.5: re-fetch state each turn, stop if
# no longer active). The agent's own completion stays in_progress through the
# loop (set_status runs after), so it is never mistaken for an external stop.
PREEMPT_STATUSES = frozenset({"cancelled", "done", "closed"})


async def _backfill_run_issue_id(run_id: str, issue_id: int) -> None:
    """Best-effort: stamp ``agent_runs.issue_id`` for the run that just
    executed this issue's turn. See ``AgentRunsRepository.backfill_issue_id``
    for why this is a post-hoc UPDATE rather than a RunRecorder constructor
    kwarg."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    await get_agent_runs_repository().backfill_issue_id(run_id, issue_id)


async def _mark_run_empty_output(run_id: str) -> None:
    """Best-effort: type the EMPTY_OUTPUT run row (error_code/error_message).
    Deliberately does NOT touch liveness_state — see
    ``AgentRunsRepository.mark_empty_output`` for why."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    await get_agent_runs_repository().mark_empty_output(
        run_id, error_message="Agent produced no output (EMPTY_OUTPUT)"
    )


async def route_finish_outcome(
    issue_id: int,
    outcome: Optional[str],
    reason: Optional[str],
    *,
    auto_close: bool,
    set_status: Callable[..., Awaitable[None]],
    content_len: int = 0,
    run_id: Optional[str] = None,
) -> None:
    """Route an agent's FinishIssue declaration (or the lack of one) to an
    issue status transition. Shared by the dispatch loop's terminal step
    (``_run_dispatch_with_continuation``) and the reply-driven needs_input
    resume path (``_run_reply_turns``, Spec-4) — one routing table instead of
    two copies drifting apart.

    Routing:
      0 chars + none    → needs_followup, agent_outcome="empty_output",
                          outcome_reason="Agent produced no output
                          (EMPTY_OUTPUT)". A2 (needs_input first-class design
                          §5.1): a silent provider failure was previously
                          whitewashed into in_review ("please review", when
                          the agent produced literally nothing) — typed
                          instead, and NEVER routed to in_review. I3 (final
                          review): ``agent_outcome`` is deliberately distinct
                          from ``"needs_input"`` — the needs-input endpoint
                          predicate only matches the literal string
                          ``"needs_input"``, so an EMPTY_OUTPUT stall never
                          appears in the Task Center "needs your answer" feed
                          (nothing was actually asked) — but it's still a
                          member of the resume gate's accepted set in
                          ``_run_reply_turns``, so a plain reply on the issue
                          can nudge it forward instead of parking it out of
                          reach forever.
      completed       → done if ``auto_close`` (slice 2a platform toggle) else
                        in_review (human confirms)
      needs_input     → needs_followup (slice 2b: a deliberate hand-off, distinct
                        from blocked=errored; carries the agent's reason)
      continue (capped)→ in_review (handed to a human after the cap; never
                        auto-closes — the agent never said it finished)
      none declared (but has content) → in_review (default — unchanged
                        legacy behavior)

    ``run_id``, when given, also drives per-run housekeeping every
    issue-linked turn should get regardless of outcome: ``agent_runs.issue_id``
    backfill (unconditional) and, for the EMPTY_OUTPUT branch specifically, a
    typed ``error_code``/``error_message`` (prod evidence: agent_runs
    333739667136736 sat at status=completed/error_code=NULL having produced 0
    chars with no declaration). Deliberately does NOT touch
    ``liveness_state`` — see ``AgentRunsRepository.mark_empty_output``.
    """
    if run_id is not None:
        await _backfill_run_issue_id(run_id, issue_id)

    if content_len == 0 and outcome is None:
        await set_status(
            issue_id,
            "needs_followup",
            agent_outcome="empty_output",
            outcome_reason="Agent produced no output (EMPTY_OUTPUT)",
        )
        if run_id is not None:
            await _mark_run_empty_output(run_id)
        return

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


async def _run_dispatch_with_continuation(
    issue_id: int,
    issue_row: dict[str, Any],
    agent_id: str,
    user_id: str,
    *,
    run_turn: Callable[..., Awaitable[dict[str, Any]]],
    set_status: Callable[..., Awaitable[None]],
    load_issue: Callable[[int], Awaitable[dict[str, Any]]],
    max_continuations: int = ISSUE_MAX_CONTINUATIONS,
    auto_close: bool = False,
) -> dict[str, Any]:
    """Spec-2 core: run the agent, then route the issue by the agent's declared
    FinishIssue outcome via ``route_finish_outcome`` (see its docstring for the
    routing table). ``continue`` auto-runs another bounded turn; everything
    else terminates the dispatch. Deps are injected so this is unit-testable
    without DBOS/DB (mirrors _run_reply_turns).

    Before every turn the issue is re-loaded and the dispatch is preempted if it
    was externally moved to a terminal/cancelled status (see ``PREEMPT_STATUSES``)
    — the external status is left untouched.
    """
    attempt = 0
    outcome: Optional[str] = None
    reason: Optional[str] = None
    while True:
        # Reconcile against external state before (re)running. A user or another
        # agent may have cancelled/closed the issue since dispatch; if so, stop
        # without overwriting their status.
        fresh = await load_issue(issue_id)
        fresh_status = (fresh or {}).get("status")
        if fresh_status in PREEMPT_STATUSES:
            logger.info(
                f"[execute_issue] issue {issue_id} externally set to "
                f"{fresh_status!r} mid-dispatch; preempting after "
                f"{attempt} continuation(s)"
            )
            return {
                "issue_id": issue_id,
                "preempted": True,
                "preempted_status": fresh_status,
                "outcome": outcome,
                "attempts": attempt,
            }
        res = await run_turn(
            issue_row, agent_id, user_id, is_continuation=(attempt > 0)
        )
        outcome = (res or {}).get("outcome")
        reason = (res or {}).get("reason")
        if outcome == "continue" and attempt < max_continuations:
            attempt += 1
            continue
        break

    content_len = len((res or {}).get("content") or "")
    await route_finish_outcome(
        issue_id,
        outcome,
        reason,
        auto_close=auto_close,
        set_status=set_status,
        content_len=content_len,
        run_id=(res or {}).get("run_id"),
    )
    return {"outcome": outcome, "attempts": attempt}


async def _maybe_fire_subissue_barrier(issue_id: int) -> None:
    """After the workflow lands this issue on its final status, check whether it
    just closed its parent's sub-issue barrier (and, if so, report + wake).

    Called from the WORKFLOW BODY, never a @DBOS.step: the barrier's wake path
    dispatches respond_to_issue_reply, and dispatching a workflow inside a
    @DBOS.step raises a bare AssertionError (bug_retry_failed_downloads_two_layer).
    The workflow set this issue to in_progress before running, so the pre-status
    is a definite non-terminal — pass "in_progress" as prev. The hook is
    best-effort and self-guarding, but wrap anyway so it can never abort the
    workflow's own completion."""
    try:
        from app.db import engine as db_engine
        from app.services.issues.subissue_barrier import on_child_issue_terminal

        row = await db_engine.fetch_one(
            "SELECT status FROM public.issues WHERE id = :id", {"id": issue_id}
        )
        new_status = (row or {}).get("status")
        if new_status:
            await on_child_issue_terminal(issue_id, "in_progress", new_status)
            # W2b: pipeline-step children advance their run from the SAME seam.
            # Same rationale as the barrier — fired from the workflow BODY, never
            # a @DBOS.step (start_pipeline_run's dispatch would raise inside one).
            from app.services.issues.pipeline_relay import on_pipeline_child_terminal

            await on_pipeline_child_terminal(issue_id, "in_progress", new_status)
    except Exception as exc:  # noqa: BLE001 — the dispatch is the primary op
        logger.warning(
            f"[execute_issue] sub-issue barrier hook failed for {issue_id}: {exc!r}"
        )


@DBOS.workflow()
async def execute_issue(issue_id: int, auto: bool = False) -> dict[str, Any]:
    """Parent workflow — owns the issue lifecycle.

    ``auto`` (M4 Autopilot, task O2): True when the autopilot engine started
    this dispatch (via ``node_start._dispatch_node`` /
    ``app.api.issues_router._dispatch_execute_issue(..., auto=True)``) rather
    than a human confirming "Run now". Bound onto ``run_issue_agent_step`` via
    ``functools.partial`` at the call site below so
    ``_run_dispatch_with_continuation``'s own signature — and its existing
    unit tests, which inject a bare ``run_turn`` fake — stay untouched;
    forwarded from there down to ``run_issue_agent`` (see its docstring for
    what it changes)."""
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
                run_turn=functools.partial(run_issue_agent_step, auto=auto),
                set_status=set_status,
                load_issue=load_issue,
                auto_close=auto_close,
            )
            result = {"issue_id": issue_id, "executed": True, **routed}
        else:
            # No agent assigned → nothing to run; close it out.
            await set_status(issue_id, "done")
            result = {"issue_id": issue_id, "executed": False}

        # Fan-in: this issue may be someone's sub-issue — if it just landed
        # terminal and was the last outstanding sibling, wake the parent.
        await _maybe_fire_subissue_barrier(issue_id)
        return result

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
