"""Who may read or steer a DBOS workflow by id.

``/api/v1/workflows/{workflow_id}/…`` (status, steps, events, cancel, resume,
restart) talks to DBOS directly, and DBOS knows nothing about users: before
this guard any signed-in caller holding an id could read another user's
workflow inputs and outputs, cancel it, or fork it.

A workflow id belongs to the caller when ANY of the records that name it
grants access:

- ``task_tracking`` (``dbos_workflow_id``): the row's ``user_id`` is the
  caller. This is the Task Center's own rule (``/task-manager/tasks`` lists
  by ``user_id``).
- ``agent_runs`` (``task_id``): the run's ``user_id`` is the caller. An agent
  run's workflow can outlive its ``task_tracking`` row (the FK was dropped in
  migration 200), and the Task Center falls back to ``/restart`` exactly when
  that row is gone.
- ``issues`` (``dbos_workflow_id``): the issue is visible to the caller under
  the shared issue rule (creator / assignee / team member). The Issues page
  streams ``/events`` for an issue's current dispatch.

Anything else is a typed 404 ``not_found_or_out_of_scope``, the same answer
an unknown id gets, so existence never leaks across users.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from app.db.session import read_scope
from app.models import AgentRuns, Issues, TaskTracking
from app.services.issues.issue_visibility import is_issue_visible


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


async def caller_owns_workflow(workflow_id: str, user_id: str) -> bool:
    """True when some record naming ``workflow_id`` grants ``user_id`` access."""
    uid = str(user_id)
    async with read_scope() as session:
        tracked = await session.execute(
            select(TaskTracking.user_id).where(
                TaskTracking.dbos_workflow_id == workflow_id
            )
        )
        if any(_str_or_none(owner) == uid for owner in tracked.scalars().all()):
            return True

        runs = await session.execute(
            select(AgentRuns.user_id).where(AgentRuns.task_id == workflow_id)
        )
        if any(_str_or_none(owner) == uid for owner in runs.scalars().all()):
            return True

        issues = await session.execute(
            select(
                Issues.created_by_user_id,
                Issues.assignee_user_id,
                Issues.team_id,
            ).where(Issues.dbos_workflow_id == workflow_id)
        )
        issue_rows = [
            {
                "created_by_user_id": _str_or_none(r["created_by_user_id"]),
                "assignee_user_id": _str_or_none(r["assignee_user_id"]),
                "team_id": r["team_id"],
            }
            for r in issues.mappings().all()
        ]
    for row in issue_rows:
        if await is_issue_visible(row, uid):
            return True
    return False


def workflow_not_found() -> HTTPException:
    """The one answer for "unknown id" and "not yours"."""
    return HTTPException(
        status_code=404,
        detail={
            "code": NOT_FOUND_OR_OUT_OF_SCOPE,
            "message": "The workflow does not exist or is outside your scope.",
        },
    )


async def require_workflow_access(workflow_id: str, user_id: str) -> None:
    """Raise the typed 404 unless the caller may see ``workflow_id``."""
    if not await caller_owns_workflow(workflow_id, user_id):
        raise workflow_not_found()


__all__ = [
    "caller_owns_workflow",
    "require_workflow_access",
    "workflow_not_found",
]
