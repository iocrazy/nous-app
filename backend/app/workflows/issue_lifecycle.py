"""execute_issue parent workflow — design doc Protocol 2.

Lifecycle:
    issue (status=todo, dbos_workflow_id=NULL)
        ↓
    atomic_checkout (set execution_locked_at + dbos_workflow_id)
        ↓
    set_status('in_progress')
        ↓
    [agent / leaf workflow runs] ← driven by issue.assignee_*_id + payload
        ↓
    on success: set_status('done' or 'in_review' depending on auto_complete)
    on failure: set_status('blocked', record error)
        ↓
    clear_lock (regardless of outcome)
"""

from __future__ import annotations

# Helper steps use psycopg directly because supabase-py async client is not
# safe to call from within a DBOS step (it spins its own event loop). For
# production we'll switch to the supabase-py admin client wrapped via
# `asyncio.to_thread`. Keeping it minimal here.
import os
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from dbos import DBOS
from loguru import logger


def _dsn() -> str:
    url = os.environ.get("DBOS_DATABASE_URL")
    if not url:
        raise RuntimeError("DBOS_DATABASE_URL not configured")
    return url + ("&" if "?" in url else "?") + "sslmode=disable"


@DBOS.step()
def atomic_checkout(issue_id: int, dbos_workflow_id: str) -> bool:
    """Atomically claim an issue for execution. Returns False if someone else
    already has it (execution_locked_at IS NOT NULL).
    """
    with psycopg.connect(_dsn()) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute(
            """UPDATE public.issues
               SET execution_locked_at = now(),
                   dbos_workflow_id   = %s
               WHERE id = %s
                 AND execution_locked_at IS NULL
               RETURNING id""",
            (dbos_workflow_id, issue_id),
        )
        locked = cur.fetchone() is not None
        conn.commit()
    return locked


@DBOS.step()
def set_status(
    issue_id: int,
    status: str,
    *,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Transition issue.status with side-effect timestamps per design Protocol 5."""
    now_iso = datetime.now(timezone.utc).isoformat()
    patch_cols = ["status = %s"]
    args: list[Any] = [status]

    if status == "in_progress":
        patch_cols.append("started_at = %s")
        args.append(now_iso)
    elif status == "done":
        patch_cols.append("completed_at = %s")
        args.append(now_iso)
    elif status == "cancelled":
        patch_cols.append("cancelled_at = %s")
        args.append(now_iso)

    if error_code or error_message:
        # execution_state stores transient error context; cleared on rerun.
        patch_cols.append("execution_state = %s::jsonb")
        import json

        args.append(
            json.dumps({"error_code": error_code, "error_message": error_message})
        )

    args.append(issue_id)
    sql = f"UPDATE public.issues SET {', '.join(patch_cols)} WHERE id = %s"

    with psycopg.connect(_dsn()) as conn:
        conn.execute("SET ROLE service_role")
        conn.execute(sql, args)
        conn.commit()


@DBOS.step()
def clear_lock(issue_id: int) -> None:
    """Release execution lock so the issue can be re-tried later if needed."""
    with psycopg.connect(_dsn()) as conn:
        conn.execute("SET ROLE service_role")
        conn.execute(
            "UPDATE public.issues SET execution_locked_at = NULL WHERE id = %s",
            (issue_id,),
        )
        conn.commit()


@DBOS.step()
def load_issue(issue_id: int) -> dict[str, Any]:
    """Read issue row as plain dict (so it serializes through DBOS step memo)."""
    with psycopg.connect(_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute("SELECT * FROM public.issues WHERE id = %s", (issue_id,))
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"issue id={issue_id} not found")
    # Normalize non-JSON-serializable types (UUID/datetime → str)
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
def execute_issue(issue_id: int) -> dict[str, Any]:
    """Parent workflow — owns the issue lifecycle. Child workflows (per
    assignee_agent_id / origin_kind) run inside via DBOS.start_workflow.
    """
    # Load to validate existence + cache row in DBOS step output. The
    # current scaffold doesn't yet branch on the loaded payload — D5
    # will use it to pick the right child workflow per origin_kind /
    # assignee. Keep the call so a future edit doesn't have to thread
    # the step in from scratch.
    load_issue(issue_id)

    workflow_id = DBOS.workflow_id  # the running workflow's id
    locked = atomic_checkout(issue_id, workflow_id)
    if not locked:
        logger.info(f"[execute_issue] issue {issue_id} already locked, skipping")
        return {"skipped": True, "issue_id": issue_id, "reason": "already_locked"}

    set_status(issue_id, "in_progress")

    try:
        # PR-D2.2 scaffold — actual agent dispatch wired in PR-D5.
        # For now: this parent workflow demonstrates the full lifecycle pattern
        # but doesn't dispatch real children. Leaf workflows (ai_summary etc)
        # are invoked directly by handlers, not via execute_issue, until D5.
        result: dict[str, Any] = {
            "issue_id": issue_id,
            "noop": "PR-D5 will wire agent dispatch",
        }

        # Default to in_review (user reviews agent output) unless skill marks auto_complete.
        # Until skill metadata wires up, default to 'done' for the no-op path.
        set_status(issue_id, "done")
        return result

    except Exception as exc:  # noqa: BLE001
        set_status(
            issue_id,
            "blocked",
            error_code="execute_issue_failed",
            error_message=str(exc)[:500],
        )
        raise
    finally:
        clear_lock(issue_id)
