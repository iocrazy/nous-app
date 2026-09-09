"""One way to start ``execute_issue`` for an existing issue.

Moved out of ``issues_router`` (phase 2b-1 Task 3) so the fork service and
the two endpoints (``/dispatch``, ``/resume``) share it. The DBOS dispatcher
and the workflow-id persist helper still live on the router module and are
resolved at call time — their tests monkeypatch them THERE.
"""

from __future__ import annotations

import importlib
import uuid

from loguru import logger


class DispatchFailed(RuntimeError):
    """DBOS refused the dispatch; the router maps it to a 500."""


async def start_execute_issue(issue_id: int) -> str:
    """Dispatch ``execute_issue`` under a fresh workflow_id and persist it.

    Unique per dispatch so an issue can be re-dispatched after a prior run
    finished or errored — a fixed ``issue-{id}`` id would dedup in DBOS and
    the re-dispatch would become a silent no-op. The atomic_checkout CAS lock
    (``execution_locked_at``) still prevents concurrent double-runs. A
    duplicate workflow_id is a soft success — DBOS already has it."""
    # importlib, not ``from app.api import issues_router``: the package
    # re-exports the APIRouter under that very name and would shadow the module.
    router_mod = importlib.import_module("app.api.issues_router")

    workflow_id = f"issue-{issue_id}-{uuid.uuid4().hex[:12]}"
    try:
        router_mod._dispatch_execute_issue(issue_id, workflow_id)
    except Exception as e:
        if (
            "already exists" not in repr(e).lower()
            and "duplicate" not in repr(e).lower()
        ):
            logger.warning(f"[issues] dispatch {issue_id} failed: {e}")
            raise DispatchFailed(f"DBOS dispatch failed: {e}") from e
    await router_mod._persist_workflow_id(issue_id, workflow_id)
    return workflow_id


__all__ = ["DispatchFailed", "start_execute_issue"]
