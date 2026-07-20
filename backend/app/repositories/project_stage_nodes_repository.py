"""Data access for per-project workflow node instances (mig 380, M1 PR-B).

A project's workflow is a copy of a team template's nodes, taken once at
project creation and independent thereafter (editing the template never touches
a live project). ORM-backed (read_scope / write_scope); Snowflake BIGINT ids
ride as strings at the API boundary (bigIntSafeFetch discipline), so
``_node_row`` stringifies ids/UUIDs and ISO-formats the DATE schedule columns.

Discipline:
  * ``update_node`` ASSERTS that ``planned_start`` / ``planned_due`` are real
    ``datetime.date`` objects, never ISO strings — feeding asyncpg an ISO string
    for a DATE column is the recurring isoformat-bind footgun.
  * ``set_node_status`` is the ONE writer of ``status`` and is called only by
    the issue→node sync hook; business code never PATCHes ``status`` directly
    (status is a projection of the mirror issue, per spec §6).
  * ``instantiate_from_template`` is idempotent: a project that already owns any
    node is left untouched (returns its existing nodes).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from app.db.session import read_scope, write_scope
from app.models import (
    AgentRuns,
    ProjectStageNodeMembers,
    ProjectStageNodes,
    ProjectStages,
    Projects,
    WorkflowTemplateNodeMembers,
    WorkflowTemplateNodes,
)

# Node-bank slugs whose skip state the method shortcut overrides (spec §2/§3):
# Canvas is always on (常驻不可关); Shooting is on for live/hybrid, off for ai.
_SLUG_CANVAS = "canvas"
_SLUG_SHOOTING = "shooting"

_VALID_STATUSES = frozenset({"pending", "in_progress", "in_review", "done", "skipped"})


def _as_uuid(val: Any) -> Optional[uuid.UUID]:
    """Coerce a str/UUID/None owner id to uuid.UUID (or None)."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _s(val: Any) -> Any:
    """Stringify ids/UUIDs; ISO-format dates/datetimes; pass the rest through."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    return val


def _require_date(val: Any, field: str) -> Optional[datetime.date]:
    """Return ``val`` when it is a real ``date``/``None``; raise on an ISO string.

    Guards the asyncpg DATE-bind footgun: an ISO string silently accepted here
    would bind wrong (or DataError) against the DATE column. Callers upstream
    (Pydantic ``date`` fields) already hand us ``datetime.date`` — this asserts
    it stayed one."""
    if val is None:
        return None
    if isinstance(val, str):
        raise TypeError(f"{field} must be a datetime.date, got an ISO string ({val!r})")
    if isinstance(val, datetime.date):
        return val
    raise TypeError(f"{field} must be a datetime.date, got {type(val)!r}")


def _member_row(obj: ProjectStageNodeMembers) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "node_id": str(obj.node_id),
        "user_id": _s(obj.user_id),
        "agent_id": _s(obj.agent_id),
    }


def _node_row(obj: ProjectStageNodes, members: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "project_id": str(obj.project_id),
        "source_template_node_id": (
            str(obj.source_template_node_id)
            if obj.source_template_node_id is not None
            else None
        ),
        "legacy_stage_id": (
            str(obj.legacy_stage_id) if obj.legacy_stage_id is not None else None
        ),
        "name": obj.name,
        "sort_order": obj.sort_order,
        "parallel_group": obj.parallel_group,
        "status": obj.status,
        "owner_user_id": _s(obj.owner_user_id),
        "owner_agent_id": _s(obj.owner_agent_id),
        "planned_start": _s(obj.planned_start),
        "planned_due": _s(obj.planned_due),
        "review_required": obj.review_required,
        "deliverable_required": obj.deliverable_required,
        "deliverable_label": obj.deliverable_label,
        "skipped": obj.skipped,
        "members": members,
    }


def _resolve_skip(
    slug: Optional[str], skip_default: bool, method: Optional[str]
) -> bool:
    """Method shortcut over a template node's ``skip_default`` (team-lead B1).

    Canvas is never skipped (常驻不可关); Shooting is forced on for live/hybrid
    and off for ai; every other node keeps its template default.
    """
    if slug == _SLUG_CANVAS:
        return False
    if slug == _SLUG_SHOOTING:
        if method in ("live", "hybrid"):
            return False
        if method == "ai":
            return True
    return bool(skip_default)


class ProjectStageNodesRepository:
    async def _load_slug_map(self, session) -> Dict[int, str]:
        """{project_stages.id → slug} for the node bank, so the method shortcut
        can key off a node's ``source_stage_id`` rather than its display name."""
        rows = (
            await session.execute(select(ProjectStages.id, ProjectStages.slug))
        ).all()
        return {r[0]: r[1] for r in rows}

    async def instantiate_from_template(
        self,
        project_id: str,
        template_id: str,
        *,
        method: Optional[str] = None,
        overrides: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Copy a template's nodes into ``project_stage_nodes`` (idempotent).

        A project that already owns any node is left untouched (returns its
        existing nodes). ``method`` (live/ai/hybrid) flips the Shooting/Canvas
        skip state and, for hybrid, drops Shooting+Canvas into one parallel
        group. ``overrides`` (keyed by ``source_template_node_id``) let the
        create dialog tweak the instance without touching the template.
        """
        pid = int(str(project_id))
        overrides_by_src: Dict[str, Dict[str, Any]] = {}
        for ov in overrides or []:
            key = ov.get("source_template_node_id")
            if key is not None:
                overrides_by_src[str(key)] = ov

        async with write_scope() as session:
            # Idempotency: never re-instantiate over an existing workflow.
            existing = (
                (
                    await session.execute(
                        select(ProjectStageNodes.id).where(
                            ProjectStageNodes.project_id == pid
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return await self._list_nodes_in_session(session, pid)

            tpl_nodes = (
                (
                    await session.execute(
                        select(WorkflowTemplateNodes)
                        .where(
                            WorkflowTemplateNodes.template_id == int(str(template_id))
                        )
                        .order_by(WorkflowTemplateNodes.sort_order)
                    )
                )
                .scalars()
                .all()
            )
            if not tpl_nodes:
                return []

            tpl_ids = [n.id for n in tpl_nodes]
            tpl_members = (
                (
                    await session.execute(
                        select(WorkflowTemplateNodeMembers).where(
                            WorkflowTemplateNodeMembers.node_id.in_(tpl_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            members_by_tpl: Dict[int, List[WorkflowTemplateNodeMembers]] = {}
            for m in tpl_members:
                members_by_tpl.setdefault(m.node_id, []).append(m)

            slug_map = await self._load_slug_map(session)

            # Build instance rows (skip/parallel resolved) before inserting so we
            # can compute the hybrid parallel group over the full set.
            planned: List[Dict[str, Any]] = []
            for tn in tpl_nodes:
                slug = (
                    slug_map.get(tn.source_stage_id)
                    if tn.source_stage_id is not None
                    else None
                )
                ov = overrides_by_src.get(str(tn.id), {})
                skipped = _resolve_skip(slug, tn.skip_default, method)
                if "skipped" in ov:
                    skipped = bool(ov["skipped"])
                planned.append(
                    {
                        "tpl_node": tn,
                        "slug": slug,
                        "override": ov,
                        "parallel_group": tn.parallel_group,
                        "skipped": skipped,
                    }
                )

            if method == "hybrid":
                groups = [
                    p["parallel_group"]
                    for p in planned
                    if p["parallel_group"] is not None
                ]
                fresh_group = (max(groups) if groups else 0) + 1
                for p in planned:
                    if p["slug"] in (_SLUG_SHOOTING, _SLUG_CANVAS):
                        p["parallel_group"] = fresh_group

            for p in planned:
                tn = p["tpl_node"]
                ov = p["override"]
                owner_user = ov.get("owner_user_id", tn.default_owner_user_id)
                owner_agent = ov.get("owner_agent_id", tn.default_owner_agent_id)
                # An override that sets a user owner clears any agent owner (XOR).
                if "owner_user_id" in ov and ov.get("owner_user_id") is not None:
                    owner_agent = None
                elif "owner_agent_id" in ov and ov.get("owner_agent_id") is not None:
                    owner_user = None

                node = ProjectStageNodes(
                    project_id=pid,
                    source_template_node_id=tn.id,
                    legacy_stage_id=tn.source_stage_id,
                    name=tn.name,
                    sort_order=tn.sort_order,
                    parallel_group=p["parallel_group"],
                    status="skipped" if p["skipped"] else "pending",
                    owner_user_id=_as_uuid(owner_user),
                    owner_agent_id=_as_uuid(owner_agent),
                    planned_start=_require_date(
                        ov.get("planned_start"), "planned_start"
                    ),
                    planned_due=_require_date(ov.get("planned_due"), "planned_due"),
                    review_required=tn.review_required,
                    deliverable_required=tn.deliverable_required,
                    deliverable_label=tn.deliverable_label,
                    skipped=p["skipped"],
                )
                session.add(node)
                await session.flush()

                # Members: template defaults, unless the override supplies a list.
                if "members" in ov:
                    member_pairs = [
                        (m.get("user_id"), m.get("agent_id")) for m in ov["members"]
                    ]
                else:
                    member_pairs = [
                        (m.user_id, m.agent_id) for m in members_by_tpl.get(tn.id, [])
                    ]
                for user_id, agent_id in member_pairs:
                    session.add(
                        ProjectStageNodeMembers(
                            node_id=node.id,
                            user_id=_as_uuid(user_id),
                            agent_id=_as_uuid(agent_id),
                        )
                    )

            return await self._list_nodes_in_session(session, pid)

    async def _list_nodes_in_session(self, session, pid: int) -> List[Dict[str, Any]]:
        nodes = (
            (
                await session.execute(
                    select(ProjectStageNodes)
                    .where(ProjectStageNodes.project_id == pid)
                    .order_by(ProjectStageNodes.sort_order)
                )
            )
            .scalars()
            .all()
        )
        node_ids = [n.id for n in nodes]
        members_by_node: Dict[int, List[Dict[str, Any]]] = {n.id: [] for n in nodes}
        if node_ids:
            mrows = (
                (
                    await session.execute(
                        select(ProjectStageNodeMembers).where(
                            ProjectStageNodeMembers.node_id.in_(node_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for m in mrows:
                members_by_node.setdefault(m.node_id, []).append(_member_row(m))
        return [_node_row(n, members_by_node.get(n.id, [])) for n in nodes]

    async def has_nodes(self, project_id: str) -> bool:
        """Whether the project owns any workflow node instance (cheap EXISTS).

        The single signal the SOP-mirror suppression guard keys off (W2-1): a
        project with live ``project_stage_nodes`` rows mirrors its node chain,
        not the global SOP stage.
        """
        pid = int(str(project_id))
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(ProjectStageNodes.id)
                    .where(ProjectStageNodes.project_id == pid)
                    .limit(1)
                )
            ).first()
        return row is not None

    async def list_nodes(self, project_id: str) -> List[Dict[str, Any]]:
        """All of a project's nodes (+ members), ordered by sort_order."""
        async with read_scope() as session:
            return await self._list_nodes_in_session(session, int(str(project_id)))

    async def get_node(
        self, node_id: str, project_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """One node (+ members), optionally scoped to a project."""
        async with read_scope() as session:
            stmt = select(ProjectStageNodes).where(
                ProjectStageNodes.id == int(str(node_id))
            )
            if project_id is not None:
                stmt = stmt.where(ProjectStageNodes.project_id == int(str(project_id)))
            node = (await session.execute(stmt.limit(1))).scalars().first()
            if node is None:
                return None
            mrows = (
                (
                    await session.execute(
                        select(ProjectStageNodeMembers).where(
                            ProjectStageNodeMembers.node_id == node.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return _node_row(node, [_member_row(m) for m in mrows])

    async def update_node(
        self,
        node_id: str,
        project_id: str,
        *,
        owner_user_id: Any = None,
        owner_agent_id: Any = None,
        members: Optional[List[Dict[str, Any]]] = None,
        planned_start: Any = None,
        planned_due: Any = None,
        skipped: Optional[bool] = None,
        _set_owner: bool = False,
        _set_schedule_start: bool = False,
        _set_schedule_due: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """In-place tweak of a live node (owner / members / schedule / skipped).

        Writes only the instance row — never the template. ``planned_start`` /
        ``planned_due`` are asserted to be ``datetime.date`` objects. The
        ``_set_*`` flags separate "assign None" from "leave unchanged" for the
        nullable owner/schedule fields (a bare None default cannot). Returns the
        updated node, or None when it isn't in this project.
        """
        pid = int(str(project_id))
        nid = int(str(node_id))
        async with write_scope() as session:
            node = (
                (
                    await session.execute(
                        select(ProjectStageNodes)
                        .where(ProjectStageNodes.id == nid)
                        .where(ProjectStageNodes.project_id == pid)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if node is None:
                return None

            if _set_owner:
                node.owner_user_id = _as_uuid(owner_user_id)
                node.owner_agent_id = _as_uuid(owner_agent_id)
            if _set_schedule_start:
                node.planned_start = _require_date(planned_start, "planned_start")
            if _set_schedule_due:
                node.planned_due = _require_date(planned_due, "planned_due")
            if skipped is not None:
                node.skipped = bool(skipped)
                # Reflect a skip toggle into status so the projection stays sane:
                # skipping parks the node; un-skipping returns it to pending.
                if skipped:
                    node.status = "skipped"
                elif node.status == "skipped":
                    node.status = "pending"
            node.updated_at = datetime.datetime.now(datetime.timezone.utc)

            if members is not None:
                await session.execute(
                    ProjectStageNodeMembers.__table__.delete().where(
                        ProjectStageNodeMembers.node_id == nid
                    )
                )
                for m in members:
                    session.add(
                        ProjectStageNodeMembers(
                            node_id=nid,
                            user_id=_as_uuid(m.get("user_id")),
                            agent_id=_as_uuid(m.get("agent_id")),
                        )
                    )

        return await self.get_node(node_id, project_id)

    async def set_node_status(self, node_id: str, status: str) -> Optional[str]:
        """Set a node's ``status`` — the ONLY status writer (issue→node hook).

        No-op returning None when the node is missing (e.g. the origin id points
        at a legacy SOP stage, not a workflow node) or the status is unknown.
        """
        if status not in _VALID_STATUSES:
            return None
        async with write_scope() as session:
            result = await session.execute(
                update(ProjectStageNodes)
                .where(ProjectStageNodes.id == int(str(node_id)))
                .values(
                    status=status,
                    updated_at=datetime.datetime.now(datetime.timezone.utc),
                )
                .returning(ProjectStageNodes.id)
            )
            row = result.first()
        return status if row is not None else None

    async def get_active_group(self, project_id: str) -> List[Dict[str, Any]]:
        """The node(s) forming the project's active group.

        The cursor ``projects.current_node_id`` names one node; the active group
        is every non-skipped node sharing its ``parallel_group`` (or just that
        node when it has none). Empty when no cursor is set.
        """
        pid = int(str(project_id))
        async with read_scope() as session:
            cursor = (
                await session.execute(
                    select(Projects.current_node_id).where(Projects.id == pid).limit(1)
                )
            ).first()
            if cursor is None or cursor[0] is None:
                return []
            current = (
                (
                    await session.execute(
                        select(ProjectStageNodes)
                        .where(ProjectStageNodes.id == cursor[0])
                        .where(ProjectStageNodes.project_id == pid)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if current is None:
                return []
            if current.parallel_group is None:
                group = [current]
            else:
                group = (
                    (
                        await session.execute(
                            select(ProjectStageNodes)
                            .where(ProjectStageNodes.project_id == pid)
                            .where(
                                ProjectStageNodes.parallel_group
                                == current.parallel_group
                            )
                            .where(ProjectStageNodes.skipped.is_(False))
                            .order_by(ProjectStageNodes.sort_order)
                        )
                    )
                    .scalars()
                    .all()
                )
            group_ids = [n.id for n in group]
            members_by_node: Dict[int, List[Dict[str, Any]]] = {
                nid: [] for nid in group_ids
            }
            if group_ids:
                mrows = (
                    (
                        await session.execute(
                            select(ProjectStageNodeMembers).where(
                                ProjectStageNodeMembers.node_id.in_(group_ids)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for m in mrows:
                    members_by_node.setdefault(m.node_id, []).append(_member_row(m))
            return [_node_row(n, members_by_node.get(n.id, [])) for n in group]

    async def set_current_node_id(
        self, project_id: str, node_id: Optional[str]
    ) -> None:
        """Move the ``projects.current_node_id`` cursor."""
        async with write_scope() as session:
            await session.execute(
                update(Projects)
                .where(Projects.id == int(str(project_id)))
                .values(
                    current_node_id=(int(str(node_id)) if node_id is not None else None)
                )
            )

    async def count_running_agent_runs(self, project_id: str) -> int:
        """Number of ``agent_runs`` for the project with ``status='running'``
        (the header "N agents active" chip, spec §7)."""
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(AgentRuns.id)
                    .where(AgentRuns.project_id == int(str(project_id)))
                    .where(AgentRuns.status == "running")
                )
            ).all()
        return len(rows)


_repo: Optional[ProjectStageNodesRepository] = None


def get_project_stage_nodes_repository() -> ProjectStageNodesRepository:
    global _repo
    if _repo is None:
        _repo = ProjectStageNodesRepository()
    return _repo
