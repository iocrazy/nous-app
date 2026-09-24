"""Link a run opened by a DBOS recovery re-execution to the run it replaces.

``run_issue_agent_step`` is not idempotent: when the worker dies mid-turn,
DBOS recovery re-executes the step and a second ``agent_runs`` row opens for
the same turn. Nothing used to connect the two (prod issue 352662630815921).
The issue turn now stamps ``metadata_json.dbos_step_key`` =
``<workflow_id>:<step_id>`` — identical across re-executions — and RunRecorder
uses these helpers at open:

- the new row gets ``recovered_from = <old id>``;
- the old row gets ``superseded_by = <new id>`` in its metadata. Its STATUS is
  not touched: it still closes through ``heartbeat_lost`` (worker shutdown or
  sweeper), and per ruling 1 both runs keep their real spend — this is a link
  and an explanation, not a refund.

Plain async, not DBOS steps. Both run inside the already-running turn step.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from sqlalchemy import Text, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.models import AgentRuns

STEP_KEY_FIELD = "dbos_step_key"


def prior_run_stmt(step_key: str, *, issue_id: Optional[int], user_id: Any):
    """Newest earlier row carrying ``step_key``. Scoped by ``issue_id``
    (``idx_agent_runs_issue``) when there is one, else by ``user_id``."""
    key = AgentRuns.metadata_json.op("->>", return_type=Text)(
        cast(literal(STEP_KEY_FIELD), Text)
    )
    stmt = select(AgentRuns.id, AgentRuns.status).where(key == step_key)
    if issue_id is not None:
        stmt = stmt.where(AgentRuns.issue_id == int(issue_id))
    else:
        stmt = stmt.where(AgentRuns.user_id == user_id)
    return stmt.order_by(AgentRuns.started_at.desc(), AgentRuns.id.desc()).limit(1)


async def find_prior_run(
    step_key: str, *, issue_id: Optional[int], user_id: Any
) -> Optional[dict[str, Any]]:
    """``{"id": int, "status": str}`` of the run this step already opened, or
    None on a first execution. Raises on a DB error (the caller logs)."""
    from app.db.session import read_scope

    async with read_scope() as session:
        row = (
            await session.execute(
                prior_run_stmt(step_key, issue_id=issue_id, user_id=user_id)
            )
        ).first()
    if row is None:
        return None
    return {"id": int(row[0]), "status": row[1]}


def supersede_stmt(old_run_id: Any, new_run_id: Any):
    """``metadata_json = COALESCE(metadata_json,'{}') || {"superseded_by": new}``
    — a merge, so the old row's ``view`` / ``cost`` mirrors survive."""
    patch = json.dumps({"superseded_by": str(new_run_id)})
    merged = func.coalesce(AgentRuns.metadata_json, cast(literal("{}"), JSONB)).op(
        "||", return_type=JSONB
    )(cast(literal(patch), JSONB))
    return (
        update(AgentRuns)
        .where(AgentRuns.id == int(old_run_id))
        .values(metadata_json=merged)
    )


async def stamp_superseded(old_run_id: Any, new_run_id: Any) -> None:
    """Best-effort backlink; a failure is logged and leaves only the forward
    ``recovered_from`` link."""
    from app.db.session import write_scope

    try:
        async with write_scope() as session:
            await session.execute(supersede_stmt(old_run_id, new_run_id))
    except Exception as exc:  # noqa: BLE001 — decoration, never break the run
        logger.error(
            f"[step_recovery] could not mark run {old_run_id} superseded_by "
            f"{new_run_id}: {exc!r}"
        )


__all__ = [
    "STEP_KEY_FIELD",
    "find_prior_run",
    "prior_run_stmt",
    "stamp_superseded",
    "supersede_stmt",
]
