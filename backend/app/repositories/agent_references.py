"""Live routing references to an agent — what blocks a delete (mig 501).

Deleting an agent is a soft delete, so history is never at risk. What IS at
risk is routing that would keep sending work to an agent that no longer
accepts it: an open issue assigned to it, a pipeline step, a project stage it
owns, a workflow template that would stamp it onto new projects, or a run in
flight right now. ``DELETE /ai-library/agents/{slug}`` refuses with 409
``agent_in_use`` while any of these is non-zero; the user reassigns first.

What counts as "live" per kind:

* ``issues`` — assigned, not ``done`` / ``cancelled``, not hidden (a hidden
  issue is one the user already threw away).
* ``pipeline_steps`` — every step. A pipeline is a definition; a disabled one
  still names the agent and would route to it the day it is re-enabled.
* ``stage_nodes`` — project stage nodes the agent owns or is a member of,
  not ``done`` / ``skipped``. Distinct nodes.
* ``template_nodes`` — template nodes naming the agent as default owner or
  member. Templates are definitions, so no status filter. Distinct nodes.
* ``running_runs`` — ``agent_runs.status = 'running'``.

Errors propagate: a refusal guard that cannot count must not read as "no
references" and let the delete through.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict
from uuid import UUID

from loguru import logger
from sqlalchemy import Select, func, or_, select

from app.db.session import read_scope
from app.models import (
    AgentRuns,
    IssuePipelineSteps,
    Issues,
    ProjectStageNodeMembers,
    ProjectStageNodes,
    WorkflowTemplateNodeMembers,
    WorkflowTemplateNodes,
)
from app.repositories.agent_repository import get_agent_repository

TERMINAL_ISSUE_STATUSES = ("done", "cancelled")
FINISHED_STAGE_STATUSES = ("done", "skipped")


@dataclass(frozen=True)
class AgentLiveReferences:
    issues: int = 0
    pipeline_steps: int = 0
    stage_nodes: int = 0
    template_nodes: int = 0
    running_runs: int = 0

    @property
    def in_use(self) -> bool:
        return any(self.as_counts().values())

    def as_counts(self) -> Dict[str, int]:
        return asdict(self)


def _issues_stmt(agent_id: UUID) -> Select:
    return select(func.count()).where(
        Issues.assignee_agent_id == agent_id,
        Issues.status.not_in(TERMINAL_ISSUE_STATUSES),
        Issues.hidden_at.is_(None),
    )


def _pipeline_steps_stmt(agent_id: UUID) -> Select:
    return select(func.count()).where(IssuePipelineSteps.agent_id == agent_id)


def _stage_nodes_stmt(agent_id: UUID) -> Select:
    member_nodes = select(ProjectStageNodeMembers.node_id).where(
        ProjectStageNodeMembers.agent_id == agent_id
    )
    return select(func.count(func.distinct(ProjectStageNodes.id))).where(
        or_(
            ProjectStageNodes.owner_agent_id == agent_id,
            ProjectStageNodes.id.in_(member_nodes),
        ),
        ProjectStageNodes.status.not_in(FINISHED_STAGE_STATUSES),
        ProjectStageNodes.skipped.is_(False),
    )


def _template_nodes_stmt(agent_id: UUID) -> Select:
    member_nodes = select(WorkflowTemplateNodeMembers.node_id).where(
        WorkflowTemplateNodeMembers.agent_id == agent_id
    )
    return select(func.count(func.distinct(WorkflowTemplateNodes.id))).where(
        or_(
            WorkflowTemplateNodes.default_owner_agent_id == agent_id,
            WorkflowTemplateNodes.id.in_(member_nodes),
        )
    )


def _running_runs_stmt(agent_id: UUID) -> Select:
    return select(func.count()).where(
        AgentRuns.agent_id == agent_id, AgentRuns.status == "running"
    )


_COUNTERS = {
    "issues": _issues_stmt,
    "pipeline_steps": _pipeline_steps_stmt,
    "stage_nodes": _stage_nodes_stmt,
    "template_nodes": _template_nodes_stmt,
    "running_runs": _running_runs_stmt,
}


async def count_live_agent_references(agent_id: UUID) -> AgentLiveReferences:
    """Count every live routing reference to ``agent_id``. Raises on error."""
    counts: Dict[str, int] = {}
    try:
        async with read_scope() as session:
            for kind, build in _COUNTERS.items():
                counts[kind] = int(
                    (await session.execute(build(agent_id))).scalar_one()
                )
    except Exception as exc:
        logger.error(f"[agent_references] count failed for agent {agent_id}: {exc}")
        raise
    return AgentLiveReferences(**counts)


async def is_agent_soft_deleted(agent_id: object) -> bool:
    """True iff ``agent_id`` names a soft-deleted agent (mig 501).

    For callers that hold an agent id from routing (an issue's assignee, a
    queued task) and must refuse to use a deleted agent. A missing row or a
    failed read is ``False`` — those callers already handle "not found" their
    own way, and this check must not invent a second meaning for it.
    """
    try:
        row = await get_agent_repository().get_by_id(UUID(str(agent_id)))
    except ValueError:
        logger.warning(f"[agent_references] not an agent uuid: {agent_id!r}")
        return False
    return isinstance(row, dict) and bool(row.get("deleted_at"))
