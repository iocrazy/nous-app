"""One way to start ``execute_issue`` for an existing issue.

Moved out of ``issues_router`` (phase 2b-1 Task 3) so the fork service and
the two endpoints (``/dispatch``, ``/resume``) share it. The DBOS dispatcher
and the workflow-id persist helper still live on the router module and are
resolved at call time — their tests monkeypatch them THERE.

Phase 2b-2 §4.1: the dispatch WINDOW. ``_dispatch_execute_issue`` returns as
soon as DBOS accepts the enqueue, but the lock (``execution_locked_at``) and
the ``agent_runs`` row are both written INSIDE the workflow, by
``atomic_checkout``. For the seconds in between every pre-existing busy check
— fork's ``running_root_run_id``, resume's ``execution_locked_at``, the
comment→inbox decision — reads idle. A fork landing there swings
``ai_session_id`` and the workflow's own checkout then silently returns
``{"skipped": True}``. ``execution_state.dispatching`` is the marker that
closes the window; ``atomic_checkout`` removes it in the same UPDATE that
opens the lock.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.services.issues.execution_state import merge_execution_state

# The marker's shelf life. execute_issue's first act is atomic_checkout, so the
# real window is milliseconds; 60s is the "the workflow never came up" line,
# same order as the reaper's. An expired marker reads as absent — a failed
# dispatch must never pin an issue busy forever.
DISPATCH_MARKER_TTL_S = 60


class DispatchFailed(RuntimeError):
    """DBOS refused the dispatch; the router maps it to a 500."""


def is_dispatching(
    issue: dict, *, now: datetime | None = None, ttl_s: int = DISPATCH_MARKER_TTL_S
) -> bool:
    """True while a dispatch is in flight but the workflow has not checked out.

    ``_dispatch_execute_issue`` returns as soon as DBOS accepts the enqueue, but
    ``execution_locked_at`` is written by ``atomic_checkout`` INSIDE the
    workflow. In between there is no run row and no lock — every pre-existing
    busy check reads idle.

    ``merge_execution_state`` merges with ``||``, so clearing the marker on a
    failed dispatch writes JSON ``null`` rather than dropping the key; ``null``
    (and a marker carrying no ``at``) therefore has to read as NOT busy. The
    real key removal happens in ``atomic_checkout``.
    """
    marker = (issue.get("execution_state") or {}).get("dispatching") or {}
    at = marker.get("at") if isinstance(marker, dict) else None
    if not at:
        return False
    try:
        stamped = datetime.fromisoformat(str(at))
    except ValueError:
        return False  # unparseable = not a guard we can trust; never wedge on it
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - stamped < timedelta(seconds=ttl_s)


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
    # Marker BEFORE the enqueue: a marker written after it would leave exactly
    # the window it exists to close. Written even though the dispatch may fail
    # — the except branch clears it, and the TTL covers a crash in between.
    await merge_execution_state(
        issue_id,
        {
            "dispatching": {
                "workflow_id": workflow_id,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    try:
        router_mod._dispatch_execute_issue(issue_id, workflow_id)
    except Exception as e:
        if (
            "already exists" not in repr(e).lower()
            and "duplicate" not in repr(e).lower()
        ):
            # A duplicate is a soft success — DBOS already holds that workflow
            # and its atomic_checkout will clear the marker. Only a real
            # failure means nobody is coming, so only it clears here.
            await merge_execution_state(issue_id, {"dispatching": None})
            logger.warning(f"[issues] dispatch {issue_id} failed: {e}")
            raise DispatchFailed(f"DBOS dispatch failed: {e}") from e
    await router_mod._persist_workflow_id(issue_id, workflow_id)
    return workflow_id


__all__ = [
    "DISPATCH_MARKER_TTL_S",
    "DispatchFailed",
    "is_dispatching",
    "start_execute_issue",
]
