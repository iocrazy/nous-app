"""The one merge-writer for ``issues.execution_state`` decorations.

Every writer of this column merges (``||``) its own keys and leaves the rest
alone — see ``issue_lifecycle.set_status`` for the history of the assignment
that used to drop other writers' keys. This module is the reusable form of
that rule: ``merge_execution_state(issue_id, {...})``. The column is
service_role-only under the mig-170 allowlist trigger, so the write runs as
``service_role`` for the transaction (``SET LOCAL`` — reverts at COMMIT).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import cast, func, literal, text, update
from sqlalchemy.dialects.postgresql import JSONB

from app.models import Issues

# A role identifier cannot be a bound parameter; this fixed fragment is the
# same carve-out mark_turn_progress / set_status rely on.
_AS_SERVICE_ROLE = text("SET LOCAL ROLE service_role")


def is_parked_on_input(issue: Mapping[str, Any]) -> bool:
    """Is a workflow suspended on the needs_input gate for this issue?

    Parked = the turn lock is still held (``execution_locked_at``) AND
    ``execution_state.awaiting_input`` names a question nobody has answered
    yet (no ``answered_at``). ``needs_followup`` alone cannot tell this apart
    from an issue that is merely waiting with nothing suspended (empty output,
    an expired gate), which is why both readers — the resume decision table
    and the wake-up fire — ask this one predicate instead of the status.

    ``execution_state`` may arrive as a JSON string (asyncpg can hand jsonb
    back as ``str``); an unreadable value reads as "not parked"."""
    if not issue.get("execution_locked_at"):
        return False
    state = issue.get("execution_state")
    if isinstance(state, str):
        try:
            state = json.loads(state) if state.strip() else {}
        except ValueError:
            return False
    marker = state.get("awaiting_input") if isinstance(state, dict) else None
    return isinstance(marker, dict) and bool(marker) and not marker.get("answered_at")


def merge_stmt(issue_id: int, patch: dict[str, Any]):
    """``UPDATE issues SET execution_state = COALESCE(execution_state,'{}') || :patch``."""
    merged = func.coalesce(Issues.execution_state, cast(literal("{}"), JSONB)).op(
        "||", return_type=JSONB
    )(cast(literal(json.dumps(patch, default=str)), JSONB))
    return (
        update(Issues).where(Issues.id == int(issue_id)).values(execution_state=merged)
    )


async def merge_execution_state(issue_id: int, patch: dict[str, Any]) -> None:
    if not patch:
        return
    # Function-scope import, same idiom as issue_lifecycle: the scope helper is
    # resolved per call so tests patch the SOURCE module (app.db.session) and
    # every caller sees it. A module-level binding made which fake a test saw
    # depend on whether this module happened to be imported before or after the
    # patch — an order-dependent test is worse than no test.
    from app.db.session import write_scope

    async with write_scope() as session:
        await session.execute(_AS_SERVICE_ROLE)
        await session.execute(merge_stmt(issue_id, patch))


def increment_step_attempt_stmt(issue_id: int, step_key: str):
    """Atomic per-step attempt counter (fh2 T3):

        UPDATE issues SET execution_state = COALESCE(execution_state,'{}') ||
            jsonb_build_object('step_attempts', jsonb_build_object(
                :key, COALESCE((execution_state->'step_attempts'->>:key)::int, 0) + 1))
        WHERE id = :issue
        RETURNING execution_state->'step_attempts'->>:key

    The read and the write are one statement under the row lock, so two
    executions of the same step cannot both read ``n``. ``step_attempts`` keeps
    ONLY the current key: a new step replaces it (count back to 1). Recovery
    re-executes one step at a time per issue (the dispatch lock), so older keys
    carry no information and the column stays bounded."""
    from sqlalchemy import Integer, Text

    key = cast(literal(step_key), Text)
    state = func.coalesce(Issues.execution_state, cast(literal("{}"), JSONB))
    previous = state.op("->", return_type=JSONB)(
        cast(literal("step_attempts"), Text)
    ).op("->>", return_type=Text)(key)
    attempts = func.coalesce(cast(previous, Integer), 0) + 1
    merged = state.op("||", return_type=JSONB)(
        func.jsonb_build_object(
            cast(literal("step_attempts"), Text), func.jsonb_build_object(key, attempts)
        )
    )
    stored = Issues.execution_state.op("->", return_type=JSONB)(
        cast(literal("step_attempts"), Text)
    ).op("->>", return_type=Text)(key)
    return (
        update(Issues)
        .where(Issues.id == int(issue_id))
        .values(execution_state=merged)
        .returning(stored)
    )


async def increment_step_attempt(issue_id: int, step_key: str) -> int | None:
    """Count one more execution of ``step_key``; returns the new count, or None
    when the issue row does not exist."""
    from app.db.session import write_scope

    async with write_scope() as session:
        await session.execute(_AS_SERVICE_ROLE)
        row = (
            await session.execute(increment_step_attempt_stmt(issue_id, step_key))
        ).first()
    return int(row[0]) if row is not None and row[0] is not None else None


def claim_budget_wrap_up_stmt(issue_id: int, run_id: Any):
    """Conditional claim of the one-run budget grace (phase 2a §3):

        UPDATE issues SET execution_state = execution_state ||
            jsonb_build_object('budget_wrap_up',
                               (execution_state->'budget_wrap_up') || {"consumed_by": run})
        WHERE id = :issue AND execution_state ? 'budget_wrap_up'
          AND (execution_state->'budget_wrap_up'->>'consumed_by' IS NULL
               OR execution_state->'budget_wrap_up'->>'consumed_by' = :run)

    One row updated == this run holds the grace. Two root runs racing for it
    get one winner (the WHERE is evaluated under the row lock); a DBOS retry
    of the SAME run matches again via the second disjunct."""
    from sqlalchemy import Text, or_

    run = str(run_id)
    flag = Issues.execution_state.op("->", return_type=JSONB)(
        cast(literal("budget_wrap_up"), Text)
    )
    consumed = flag.op("->>", return_type=Text)(cast(literal("consumed_by"), Text))
    stamped = flag.op("||", return_type=JSONB)(
        cast(literal(json.dumps({"consumed_by": run})), JSONB)
    )
    merged = Issues.execution_state.op("||", return_type=JSONB)(
        func.jsonb_build_object("budget_wrap_up", stamped)
    )
    return (
        update(Issues)
        .where(Issues.id == int(issue_id))
        .where(Issues.execution_state.has_key("budget_wrap_up"))
        .where(or_(consumed.is_(None), consumed == run))
        .values(execution_state=merged)
    )


async def claim_budget_wrap_up(issue_id: int, run_id: Any) -> bool:
    """True iff this run now holds (or already held) the wrap-up grace."""
    from app.db.session import write_scope

    async with write_scope() as session:
        await session.execute(_AS_SERVICE_ROLE)
        result = await session.execute(claim_budget_wrap_up_stmt(issue_id, run_id))
        return (getattr(result, "rowcount", 0) or 0) > 0


__all__ = [
    "claim_budget_wrap_up",
    "claim_budget_wrap_up_stmt",
    "increment_step_attempt",
    "increment_step_attempt_stmt",
    "merge_execution_state",
    "merge_stmt",
]
