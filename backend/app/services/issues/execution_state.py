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
from typing import Any

from sqlalchemy import cast, func, literal, text, update
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import write_scope
from app.models import Issues

# A role identifier cannot be a bound parameter; this fixed fragment is the
# same carve-out mark_turn_progress / set_status rely on.
_AS_SERVICE_ROLE = text("SET LOCAL ROLE service_role")


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
    async with write_scope() as session:
        await session.execute(_AS_SERVICE_ROLE)
        await session.execute(merge_stmt(issue_id, patch))


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
    async with write_scope() as session:
        await session.execute(_AS_SERVICE_ROLE)
        result = await session.execute(claim_budget_wrap_up_stmt(issue_id, run_id))
        return (getattr(result, "rowcount", 0) or 0) > 0


__all__ = [
    "claim_budget_wrap_up",
    "claim_budget_wrap_up_stmt",
    "merge_execution_state",
    "merge_stmt",
]
