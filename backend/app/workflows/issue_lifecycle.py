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
from datetime import datetime, timezone
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def atomic_checkout(issue_id: int, dbos_workflow_id: str) -> bool:
    """Atomically claim an issue. False if someone else already holds the lock."""
    from app.db import engine as db_engine

    locked = await db_engine.execute(
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
) -> None:
    """Transition issue.status with side-effect timestamps (design Protocol 5)."""
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
    if error_code or error_message:
        cols.append("execution_state = CAST(:state AS jsonb)")
        params["state"] = json.dumps(
            {"error_code": error_code, "error_message": error_message}
        )
    params["id"] = issue_id
    await db_engine.execute(
        f"UPDATE public.issues SET {', '.join(cols)} WHERE id = :id", params
    )


@DBOS.step()
async def clear_lock(issue_id: int) -> None:
    """Release the execution lock so the issue can be retried later."""
    from app.db import engine as db_engine

    await db_engine.execute(
        "UPDATE public.issues SET execution_locked_at = NULL WHERE id = :id",
        {"id": issue_id},
    )


@DBOS.step()
async def create_agent_run_for_issue(
    issue_id: int, agent_id: str, user_id: str, dbos_workflow_id: str
) -> Optional[str]:
    """Create an agent_runs row linked to the issue. Returns run id or None
    (never blocks the workflow on it)."""
    import uuid

    from app.db import engine as db_engine

    run_id = str(uuid.uuid4())
    now_dt = datetime.now(timezone.utc)
    try:
        await db_engine.execute(
            "INSERT INTO public.agent_runs (id, agent_id, user_id, issue_id, "
            "status, trigger, started_at, heartbeat_at, last_useful_action_at) "
            "VALUES (:id, :agent_id, :user_id, :issue_id, 'running', "
            "'issue_dispatch', :now, :now, :now)",
            {
                "id": run_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "issue_id": issue_id,
                "now": now_dt,
            },
        )
        logger.info(
            f"[execute_issue] created agent_run {run_id} for issue {issue_id} "
            f"(agent={agent_id}, dbos_wf={dbos_workflow_id})"
        )
        return run_id
    except Exception as exc:
        logger.warning(
            f"[execute_issue] agent_run insert failed for issue {issue_id}: {exc}"
        )
        return None


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
        agent_run_id: Optional[str] = None
        if agent_id and user_id:
            agent_run_id = await create_agent_run_for_issue(
                issue_id, agent_id, user_id, workflow_id
            )

        result: dict[str, Any] = {"issue_id": issue_id, "agent_run_id": agent_run_id}
        if agent_run_id is None:
            await set_status(issue_id, "done")
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
