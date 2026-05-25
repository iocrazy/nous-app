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

# noqa: F401 — module-level handle for run_issue_reply_step + tests
from app.services.ai.chat.ai_library_chat_service import (  # noqa: F401
    AILibraryChatService,
)


def _engine():
    from app.db import engine as db_engine

    return db_engine


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
async def acquire_turn_lock(issue_id: int) -> bool:
    """Claim the per-issue turn lock for a reply turn. Reuses
    issues.execution_locked_at (shared with execute_issue dispatch) but does
    NOT touch dbos_workflow_id — the dispatch-status UI subscribes to that.
    Returns True if acquired, False if a turn is already in flight."""
    locked = await _engine().execute(
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


@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_issue_reply_step(
    *, session_id: str, user_id: str, reply_text: str
) -> Optional[str]:
    """Run one reply turn on the issue's session via the full chat runtime."""
    from uuid import UUID

    result = await AILibraryChatService().run_session_turn(
        UUID(session_id),
        user_id=UUID(user_id),
        content=reply_text,
        trigger="issue_reply",
    )
    return (result.get("assistant_message") or {}).get("content") or ""


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


@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_issue_agent_step(
    issue: dict[str, Any], agent_id: str, user_id: str
) -> Optional[str]:
    """Run the assigned agent on the issue. The RunRecorder (issue_id-linked)
    + mig-208 triggers write the result into the issue chat; we just return the
    text. Retryable: each attempt is a fresh agent_runs row + LLM call."""
    from app.services.issues.issue_agent_executor import run_issue_agent

    return await run_issue_agent(issue=issue, agent_id=agent_id, user_id=user_id)


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
            await run_issue_agent_step(issue_row, agent_id, user_id)
            # Agent output is already in the chat (mig-208 bridge). Move to
            # in_review so a human confirms — agents don't self-close yet.
            await set_status(issue_id, "in_review")
            return {"issue_id": issue_id, "executed": True}
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
