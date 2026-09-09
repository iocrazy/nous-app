"""Run the agent assigned to an issue on the FULL chat runtime (Spec-1a).

The issue is backed by an ai_session (get_or_create_issue_session); execution
reuses AILibraryChatService.run_session_turn — the same turn flow chat() runs —
so memory, compaction, sub-agents, delegation, budget, fallback, and BYO-key
adapter resolution all apply. The agent's reply is persisted as an ai_message
by the turn flow; the issue chat surface reads ai_messages (Task 5).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
from app.services.ai.tools.ask_user_tool import awaiting_input_outcome
from app.services.ai.tools.finish_issue_tool import (
    extract_issue_options,
    extract_issue_outcome,
)
from app.services.ai.tools.forced_finish_declaration import (
    attempt_forced_finish_declaration,
)
from app.services.issues.execution_state import merge_execution_state
from app.services.issues.issue_chat_stream import (  # noqa: F401
    publish_chunk,
    publish_message,
    publish_status,
)
from app.services.issues.issue_session import get_or_create_issue_session


def _build_user_message(issue: dict[str, Any], brief: str | None = None) -> str:
    """Compose a user message from an issue's title, optional description,
    and (M4 Autopilot, task O2) an optional workflow-node ``brief`` — the
    "heads up" runtime text a manager can write onto a ``project_stage_nodes``
    row any time before/while it's open (design spec §1/§3). Appended
    regardless of how the run was dispatched (auto-start, confirm-gate
    manual, or start-early) — the brief is context for the WORK, not a
    dispatch-mechanism detail."""
    title = (issue.get("title") or "").strip()
    description = (issue.get("description") or "").strip()
    parts = [f"Task: {title}"] if title else []
    if description:
        parts.append(f"\nDetails:\n{description}")
    if brief:
        parts.append(f"\nContext:\n{brief}")
    return "\n".join(parts) or "Complete the assigned task."


async def _resolve_stage_brief(issue: dict[str, Any]) -> str | None:
    """Best-effort lookup of the workflow node's ``brief`` for a
    ``project_stage`` mirror issue — None for any other origin_kind, a
    missing/legacy origin, or a lookup failure (never raises; a brief is
    enrichment, not a dispatch precondition)."""
    if issue.get("origin_kind") != "project_stage" or not issue.get("origin_id"):
        return None
    try:
        from app.services.library.project_stage_issues import parse_stage_origin_id

        project_id, node_id = parse_stage_origin_id(str(issue["origin_id"]))
        if project_id is None:
            return None
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        node = await get_project_stage_nodes_repository().get_node(node_id, project_id)
        brief = (node or {}).get("brief")
        return brief.strip() if isinstance(brief, str) and brief.strip() else None
    except Exception as exc:  # noqa: BLE001 — a brief is enrichment only
        logger.warning(
            f"[issue_agent] stage-brief lookup failed for issue "
            f"{issue.get('id')}: {exc!r}"
        )
        return None


# Synthetic nudge for a continuation turn (Spec-2). The session already carries
# the full task history via memory, so we only need to prompt another turn.
CONTINUATION_NUDGE = (
    "Continue working on this issue. When you are finished, blocked, or need "
    "another turn, call the FinishIssue tool to declare the outcome."
)


def _read_forked_from(
    issue_id: Any, issue: dict[str, Any]
) -> tuple[Optional[tuple[int, int]], Optional[str]]:
    """``execution_state.forked_from`` → ``(fork_of, steer_text)``; ``(None,
    None)`` when absent, null, or malformed (logged — a bad stamp must not
    kill the run). ``steer_text`` is the human's "from here, do this" — it
    becomes the forked turn's user message (spec §2 实施记录: not the inbox,
    which would inject it on top of a history that already shows it)."""
    state = issue.get("execution_state")
    if isinstance(state, str):  # load_issue may hand jsonb back as raw JSON
        try:
            state = json.loads(state)
        except (TypeError, ValueError):
            state = None
    forked = state.get("forked_from") if isinstance(state, dict) else None
    if not forked:
        return None, None
    try:
        steer_text = str(forked.get("steer_text") or "").strip() or None
        return (int(forked["run_id"]), int(forked["at_seq"])), steer_text
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.warning(
            f"[issue_agent] issue={issue_id} malformed execution_state.forked_from "
            f"{forked!r}: {exc!r}; running as a plain (non-fork) run"
        )
        return None, None


async def run_issue_agent(
    *,
    issue: dict[str, Any],
    agent_id: str,
    user_id: str,
    is_continuation: bool = False,
    auto: bool = False,
) -> dict[str, Any]:
    """Run the assigned agent on the issue via the chat runtime.

    Streams token deltas + publishes the final message to Redis channel
    ``issue:{id}`` while the turn is in flight.

    Returns ``{"content": str, "outcome": Optional[str], "reason": Optional[str]}``
    where ``outcome`` is the agent's FinishIssue declaration (completed |
    needs_input | continue) or None if it never declared. The assistant text is
    also persisted as an ai_message by run_session_turn.

    ``is_continuation`` sends a short "keep going" nudge instead of the full
    task text (Spec-2 bounded continuation); the agent already has the history.

    ``auto`` (M4 Autopilot, task O2): True when this dispatch was started by
    the autopilot engine rather than a human confirming "Run now". Threaded
    all the way from ``execute_issue``'s own ``auto`` kwarg through
    ``run_issue_agent_step``. Flips the ``trigger`` value passed to
    ``run_session_turn`` from ``'issue_dispatch'`` to
    ``'issue_dispatch_auto'`` — a distinct ``agent_runs.trigger`` the daily
    quota counter (``agent_runs_repository.count_auto_dispatches_today``)
    filters on, so a MANUAL dispatch never counts against the autopilot
    budget even though it produces the exact same shape of run otherwise.

    Raises RuntimeError when the issue has no assignable agent session.

    ``agent_id`` is accepted for caller-signature compatibility
    (run_issue_agent_step passes it) but is unused here — the session already
    binds the agent.
    """
    _ = agent_id  # session already binds the agent; kept for caller compat

    iid = int(issue["id"])
    session_id = await get_or_create_issue_session(iid)
    if not session_id:
        raise RuntimeError(f"issue {issue['id']} has no assignable agent session")

    async def _cb(delta: str) -> None:
        await publish_chunk(iid, delta)

    trigger = "issue_dispatch_auto" if auto else "issue_dispatch"

    # Phase 2b-1 §2.3: the fork endpoint stamps execution_state.forked_from
    # = {run_id, at_seq, steer}; the new run opens with the fork columns and
    # a first `fork` event. Consumed here: the stamp is nulled before the
    # turn runs so a later resume / retry / reply on the same issue is not
    # mislabelled a fork. Malformed stamps are logged and treated as none.
    fork_of, steer_text = _read_forked_from(iid, issue)
    if fork_of is not None:
        try:
            await merge_execution_state(iid, {"forked_from": None})
        except Exception as exc:  # noqa: BLE001 — decoration, never break the turn
            logger.error(
                f"[issue_agent] issue={iid} could not clear forked_from: {exc!r}; "
                f"the next run on this issue would be recorded as a fork"
            )

    if fork_of is not None:
        # A forked run starts from the seeded history (task text included);
        # re-sending the full task would duplicate it. The human's steer, if
        # any, IS this turn's user message; otherwise the continuation nudge.
        content_in = steer_text or CONTINUATION_NUDGE
    elif is_continuation:
        content_in = CONTINUATION_NUDGE
    else:
        brief = await _resolve_stage_brief(issue)
        content_in = _build_user_message(issue, brief=brief)

    # W3c: classify spend by who ultimately caused it. A routine/pipeline issue
    # runs on the schedule/pipeline owner's behalf (rule_owner); anything else
    # is a human action (direct_human). Derived from the issue origin_kind —
    # no new flag threaded through the dispatch seam.
    from app.services.ai_usage import attribution_from_origin_kind

    attribution = attribution_from_origin_kind(issue.get("origin_kind"))

    await publish_status(iid, "running")
    try:
        result = await AILibraryChatService().run_session_turn(
            session_id,
            user_id=user_id,
            content=content_in,
            trigger=trigger,
            chunk_callback=_cb,
            attribution=attribution,
            fork_of=fork_of,
            fork_steer=steer_text is not None,
        )
        assistant = result.get("assistant_message") or {}
        await publish_message(iid, assistant, session_user_id=None)
        content = assistant.get("content") or ""
        outcome, reason = extract_issue_outcome(result.get("tool_calls"))
        question = None
        parked = awaiting_input_outcome(result)
        if parked is not None:
            outcome, reason, question = parked

        # Bounded fallback (production gap, 2026-08-01 E2E probe): agents
        # reliably produce content but almost never call FinishIssue on
        # their own, even when the tool is registered and the system prompt
        # commands it. When the turn produced content but declared nothing,
        # force exactly ONE short follow-up call with tool_choice pinned to
        # FinishIssue. Skipped entirely when an outcome was already declared
        # (zero added cost on the healthy path) or when content is empty
        # (that's the EMPTY_OUTPUT path — route_finish_outcome's own typed
        # branch, left untouched). Defense in depth: even though
        # attempt_forced_finish_declaration is itself fail-open, wrap it here
        # too so a bug in that module can never turn an already-succeeded
        # turn into an unhandled exception.
        if outcome is None and content:
            logger.info(
                f"[issue_agent] issue={iid} session={session_id} produced "
                f"content but declared no outcome; forcing FinishIssue "
                f"declaration"
            )
            try:
                outcome, reason = await attempt_forced_finish_declaration(
                    session_id=session_id,
                    user_id=user_id,
                    assistant_text=content,
                    issue_id=iid,
                    trigger=trigger,
                    # W3c: same two-level cost tag as the main turn's own
                    # RunRecorder — without this the forced-declare row
                    # would default to direct_human regardless of whether
                    # this issue is actually a routine/pipeline dispatch.
                    attribution=attribution,
                )
            except Exception as exc:  # noqa: BLE001 — decoration, never break the turn
                logger.warning(
                    f"[issue_agent] forced FinishIssue declaration raised "
                    f"for issue {iid}: {exc!r}"
                )
                outcome, reason = None, None

        logger.info(
            f"[issue_agent] issue={iid} session={session_id} "
            f"produced {len(content)} chars; outcome={outcome}"
        )
        return {
            "content": content,
            "outcome": outcome,
            "reason": reason,
            # A2 (needs_input first-class design §5.1): threaded up to
            # route_finish_outcome so it can backfill agent_runs.issue_id
            # and, for the zero-content/no-outcome case, type the row's
            # error_code/error_message.
            "run_id": result.get("run_id"),
            # Phase 2a: parked on a typed question (AskUser) → the workflow
            # parks the issue with it; FinishIssue options → built there.
            "awaiting_input": parked is not None,
            "question": question,
            "options": extract_issue_options(result.get("tool_calls")),
            # Phase 2a Task 5: a hook stop ("paused" / "cancelled") — the
            # workflow reads it BEFORE FinishIssue routing.
            "stop_reason": result.get("stop_reason"),
        }
    finally:
        await publish_status(iid, "done")
