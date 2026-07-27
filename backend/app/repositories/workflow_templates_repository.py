"""Data access for team workflow templates + the node bank (mig 380 / 381).

ORM-backed (read_scope / write_scope). Snowflake BIGINT ids ride as strings at
the API boundary (bigIntSafeFetch discipline); ``_serialize`` renders ids and
UUIDs as str and timestamps as ISO strings. No scope mixin — ownership is a
service-role model gated by the explicit ``team_id`` predicate in every method
and by the router's ``resolve_effective_role`` guard.

``update_template`` replaces the node list wholesale (delete + insert) inside a
single write transaction; CASCADE removes the old nodes' members.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update

from app.db.session import read_scope, write_scope
from app.models import (
    ProjectStages,
    WorkflowTemplateNodeMembers,
    WorkflowTemplateNodes,
    WorkflowTemplates,
)


def _as_uuid(val: Any) -> Optional[uuid.UUID]:
    """Coerce a str/UUID/None owner id to uuid.UUID (or None)."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _s(val: Any) -> Any:
    """Stringify ids/UUIDs; ISO-format datetimes; pass the rest through."""
    if val is None:
        return None
    if isinstance(val, (uuid.UUID,)):
        return str(val)
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    return val


def _template_row(obj: WorkflowTemplates, node_count: int) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "team_id": str(obj.team_id),
        "name": obj.name,
        "is_default": obj.is_default,
        "created_by": _s(obj.created_by),
        "created_at": _s(obj.created_at),
        "updated_at": _s(obj.updated_at),
        "node_count": node_count,
    }


def _node_row(
    obj: WorkflowTemplateNodes, members: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "template_id": str(obj.template_id),
        "name": obj.name,
        "sort_order": obj.sort_order,
        "parallel_group": obj.parallel_group,
        "default_owner_user_id": _s(obj.default_owner_user_id),
        "default_owner_agent_id": _s(obj.default_owner_agent_id),
        "skip_default": obj.skip_default,
        "review_required": obj.review_required,
        "deliverable_required": obj.deliverable_required,
        "deliverable_label": obj.deliverable_label,
        "source_stage_id": (
            str(obj.source_stage_id) if obj.source_stage_id is not None else None
        ),
        "duration_days": obj.duration_days,
        "completion_policy": obj.completion_policy,
        "events": obj.events,
        "members": members,
    }


def _member_row(obj: WorkflowTemplateNodeMembers) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "node_id": str(obj.node_id),
        "user_id": _s(obj.user_id),
        "agent_id": _s(obj.agent_id),
    }


class WorkflowTemplatesRepository:
    async def list_templates(self, team_id: str) -> List[Dict[str, Any]]:
        """Templates for a team, newest first, each with its node count."""
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(WorkflowTemplates)
                        .where(WorkflowTemplates.team_id == int(str(team_id)))
                        .order_by(WorkflowTemplates.created_at)
                    )
                )
                .scalars()
                .all()
            )
            out: List[Dict[str, Any]] = []
            for tpl in rows:
                count = (
                    await session.execute(
                        select(WorkflowTemplateNodes.id).where(
                            WorkflowTemplateNodes.template_id == tpl.id
                        )
                    )
                ).all()
                out.append(_template_row(tpl, len(count)))
            return out

    async def get_template(
        self, template_id: str, team_id: str
    ) -> Optional[Dict[str, Any]]:
        """One template (scoped to team) with its ordered nodes + members."""
        async with read_scope() as session:
            tpl = (
                (
                    await session.execute(
                        select(WorkflowTemplates)
                        .where(WorkflowTemplates.id == int(str(template_id)))
                        .where(WorkflowTemplates.team_id == int(str(team_id)))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if tpl is None:
                return None

            nodes = (
                (
                    await session.execute(
                        select(WorkflowTemplateNodes)
                        .where(WorkflowTemplateNodes.template_id == tpl.id)
                        .order_by(WorkflowTemplateNodes.sort_order)
                    )
                )
                .scalars()
                .all()
            )
            node_ids = [n.id for n in nodes]
            members_by_node: Dict[int, List[Dict[str, Any]]] = {
                nid: [] for nid in node_ids
            }
            if node_ids:
                mrows = (
                    (
                        await session.execute(
                            select(WorkflowTemplateNodeMembers).where(
                                WorkflowTemplateNodeMembers.node_id.in_(node_ids)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for m in mrows:
                    members_by_node.setdefault(m.node_id, []).append(_member_row(m))

            result = _template_row(tpl, len(nodes))
            result["nodes"] = [
                _node_row(n, members_by_node.get(n.id, [])) for n in nodes
            ]
            return result

    async def get_template_team_id(self, template_id: str) -> Optional[str]:
        """The owning team_id (str) for a template, or None if it doesn't exist.

        Lets the router resolve authz from the id alone (no team_id query param)
        while still 404-ing before it leaks existence to a non-member."""
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(WorkflowTemplates.team_id)
                    .where(WorkflowTemplates.id == int(str(template_id)))
                    .limit(1)
                )
            ).first()
        return str(row[0]) if row is not None else None

    async def create_template(
        self, team_id: str, name: str, created_by: Optional[str]
    ) -> Dict[str, Any]:
        async with write_scope() as session:
            obj = WorkflowTemplates(
                team_id=int(str(team_id)),
                name=name,
                created_by=_as_uuid(created_by),
            )
            session.add(obj)
            await session.flush()
            await session.refresh(obj)
            return _template_row(obj, 0)

    async def update_template(
        self,
        template_id: str,
        team_id: str,
        *,
        name: Optional[str] = None,
        is_default: Optional[bool] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Patch a template; None when it isn't in this team.

        ``nodes`` (when present) fully replaces the node list. ``is_default=True``
        first clears any sibling default so the partial-unique index holds.
        """
        async with write_scope() as session:
            tpl = (
                (
                    await session.execute(
                        select(WorkflowTemplates)
                        .where(WorkflowTemplates.id == int(str(template_id)))
                        .where(WorkflowTemplates.team_id == int(str(team_id)))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if tpl is None:
                return None

            if name is not None:
                tpl.name = name

            if is_default is not None:
                if is_default:
                    await session.execute(
                        update(WorkflowTemplates)
                        .where(WorkflowTemplates.team_id == tpl.team_id)
                        .where(WorkflowTemplates.id != tpl.id)
                        .where(WorkflowTemplates.is_default.is_(True))
                        .values(is_default=False)
                    )
                tpl.is_default = is_default

            if nodes is not None:
                await session.execute(
                    delete(WorkflowTemplateNodes).where(
                        WorkflowTemplateNodes.template_id == tpl.id
                    )
                )
                for nd in nodes:
                    # completion_policy / events (mig 386): only set when the
                    # caller supplied them — omitting the kwarg (rather than
                    # passing an explicit None) lets the NOT NULL column fall
                    # back to its DB server_default (template_seeder's hand-built
                    # node dicts carry neither key and rely on exactly this).
                    node_kwargs: Dict[str, Any] = dict(
                        template_id=tpl.id,
                        name=nd["name"],
                        sort_order=int(nd["sort_order"]),
                        parallel_group=nd.get("parallel_group"),
                        default_owner_user_id=_as_uuid(nd.get("default_owner_user_id")),
                        default_owner_agent_id=_as_uuid(
                            nd.get("default_owner_agent_id")
                        ),
                        skip_default=bool(nd.get("skip_default", False)),
                        review_required=bool(nd.get("review_required", False)),
                        deliverable_required=bool(
                            nd.get("deliverable_required", False)
                        ),
                        deliverable_label=nd.get("deliverable_label"),
                        source_stage_id=(
                            int(nd["source_stage_id"])
                            if nd.get("source_stage_id") is not None
                            else None
                        ),
                        duration_days=nd.get("duration_days"),
                    )
                    if nd.get("completion_policy") is not None:
                        node_kwargs["completion_policy"] = nd["completion_policy"]
                    if nd.get("events") is not None:
                        node_kwargs["events"] = nd["events"]
                    node = WorkflowTemplateNodes(**node_kwargs)
                    session.add(node)
                    await session.flush()
                    for m in nd.get("members", []):
                        session.add(
                            WorkflowTemplateNodeMembers(
                                node_id=node.id,
                                user_id=_as_uuid(m.get("user_id")),
                                agent_id=_as_uuid(m.get("agent_id")),
                            )
                        )

            tpl.updated_at = datetime.datetime.now(datetime.timezone.utc)

        return await self.get_template(template_id, team_id)

    async def delete_template(self, template_id: str, team_id: str) -> bool:
        async with write_scope() as session:
            tpl = (
                (
                    await session.execute(
                        select(WorkflowTemplates)
                        .where(WorkflowTemplates.id == int(str(template_id)))
                        .where(WorkflowTemplates.team_id == int(str(team_id)))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if tpl is None:
                return False
            await session.delete(tpl)
            return True

    async def count_templates(self, team_id: str) -> int:
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(WorkflowTemplates.id).where(
                        WorkflowTemplates.team_id == int(str(team_id))
                    )
                )
            ).all()
            return len(rows)

    async def list_stage_library(self) -> List[Dict[str, Any]]:
        """The 11 workflow node-bank rows (phase IS NOT NULL), by sort order.

        Legacy project_stages rows (planning/generation/review/delivery) keep
        phase = NULL and are excluded, so the picker shows only the curated set.
        """
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ProjectStages)
                        .where(ProjectStages.phase.is_not(None))
                        .order_by(ProjectStages.sort_order)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": str(r.id),
                    "slug": r.slug,
                    "name": r.name,
                    "sort_order": r.sort_order,
                    "phase": r.phase,
                    "default_role_label": r.default_role_label,
                    "deliverable_label": r.deliverable_label,
                    "review_required": r.review_required,
                }
                for r in rows
            ]


_repo: Optional[WorkflowTemplatesRepository] = None


def get_workflow_templates_repository() -> WorkflowTemplatesRepository:
    global _repo
    if _repo is None:
        _repo = WorkflowTemplatesRepository()
    return _repo
