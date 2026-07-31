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
  * ``set_node_metadata`` is the ONE writer of ``metadata`` (mig 389) — a
    shallow JSONB merge, never touching ``status``/``events``/schedule
    columns. The stage-hook workflow (M3 PR-H2) uses it to stamp
    ``run_prepared_at`` for idempotency.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update

from app.db.session import read_scope, write_scope
from app.models import (
    AgentRuns,
    ProjectFiles,
    Projects,
    ProjectStageNodeDeps,
    ProjectStageNodeMembers,
    ProjectStageNodes,
    ProjectStages,
    WorkflowTemplateNodeDeps,
    WorkflowTemplateNodeMembers,
    WorkflowTemplateNodes,
)
from app.schemas.workflow import DepsBackwardOnly

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


async def _deps_by_node(session, node_ids: List[int]) -> Dict[int, List[str]]:
    """{node_id -> [depends_on instance ids as str]} for a set of live nodes
    (mig 391, M3 PR-J). Mirrors the members-lookup shape used everywhere else
    in this module. Empty dict (no query) when ``node_ids`` is empty."""
    out: Dict[int, List[str]] = {nid: [] for nid in node_ids}
    if not node_ids:
        return out
    drows = (
        (
            await session.execute(
                select(ProjectStageNodeDeps).where(
                    ProjectStageNodeDeps.node_id.in_(node_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    for d in drows:
        out.setdefault(d.node_id, []).append(str(d.depends_on_node_id))
    return out


def _member_row(obj: ProjectStageNodeMembers) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "node_id": str(obj.node_id),
        "user_id": _s(obj.user_id),
        "agent_id": _s(obj.agent_id),
    }


def _node_row(
    obj: ProjectStageNodes,
    members: List[Dict[str, Any]],
    depends_on: Optional[List[str]] = None,
) -> Dict[str, Any]:
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
        "folder_id": (str(obj.folder_id) if obj.folder_id is not None else None),
        "completion_policy": obj.completion_policy,
        "events": obj.events,
        "metadata": obj.metadata_,
        "form_schema": obj.form_schema,
        "form_data": obj.form_data,
        # Runtime "heads up" text (mig 395, M4 Autopilot §1) — instance-only,
        # same shape as form_data/metadata_ above.
        "brief": obj.brief,
        "members": members,
        # Dependency edges (mig 391, M3 PR-J): the OTHER live nodes in this
        # project this node depends on (real instance ids). A node removed by
        # CASCADE (project_stage_node_deps FKs both ON DELETE CASCADE) simply
        # stops appearing here — no dangling reference ever surfaces.
        "depends_on": depends_on or [],
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

            # Dependency edges (mig 391, M3 PR-J): loaded once here, resolved
            # to instance ids AFTER the instance nodes below are created (the
            # tpl-id -> instance-id map only exists once every node has been
            # flushed).
            tpl_deps = (
                (
                    await session.execute(
                        select(WorkflowTemplateNodeDeps).where(
                            WorkflowTemplateNodeDeps.node_id.in_(tpl_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )

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

            # Dependency edges (mig 391, M3 PR-J): template node id -> the
            # instance node id created for it below, so the copied edges below
            # can be re-pointed at the new rows (same idiom as
            # ``source_template_node_id`` on the node itself).
            tpl_to_instance: Dict[int, int] = {}
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
                    # Flow Rules / Events (mig 386) — template-layer config,
                    # copied verbatim at instantiation; instances don't open
                    # these for in-place tweaks (spec §5).
                    completion_policy=tn.completion_policy,
                    events=tn.events,
                    # Form-based deliverables (mig 390, M3 PR-I): form_schema
                    # copied verbatim, same instantiate-then-freeze idiom as
                    # completion_policy/events above. form_data is left unset
                    # here (DB server_default '{}'::jsonb) — an instance
                    # starts with no entered values.
                    form_schema=tn.form_schema,
                )
                session.add(node)
                await session.flush()
                tpl_to_instance[tn.id] = node.id

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

            # Copy dependency edges last, now that every template node id has
            # a resolved instance id. A dep whose endpoint fell outside this
            # template's own node set (should never happen — edges are
            # created FK-scoped to one template) is skipped defensively
            # rather than raising: instantiation must never fail because of a
            # data shape a repo-level guard already prevents.
            for d in tpl_deps:
                inst_node_id = tpl_to_instance.get(d.node_id)
                inst_dep_id = tpl_to_instance.get(d.depends_on_node_id)
                if inst_node_id is not None and inst_dep_id is not None:
                    session.add(
                        ProjectStageNodeDeps(
                            node_id=inst_node_id,
                            depends_on_node_id=inst_dep_id,
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
        deps_by_node = await _deps_by_node(session, node_ids)
        return [
            _node_row(n, members_by_node.get(n.id, []), deps_by_node.get(n.id, []))
            for n in nodes
        ]

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
            deps_by_node = await _deps_by_node(session, [node.id])
            return _node_row(
                node, [_member_row(m) for m in mrows], deps_by_node.get(node.id, [])
            )

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
        form_data: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[str]] = None,
        brief: Optional[str] = None,
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

        ``form_data`` (mig 390, M3 PR-I) is merge-with-whitelist: any key not
        declared in THIS node's own ``form_schema`` is dropped silently before
        merging into the existing ``form_data`` (previously-set keys not
        touched by this call are preserved) — the server never trusts a
        client-supplied key set, and a node's form fields are frozen at
        instantiation (spec §2).

        ``depends_on`` (mig 391, M3 PR-J) is FULL-REPLACE, same shape as
        ``members`` — dependencies are instance STRUCTURE, not frozen
        template config. Each id must belong to THIS project and have a
        strictly smaller ``sort_order`` than this node (backward-only,
        self-deps included — spec §3); any violation raises
        ``DepsBackwardOnly`` and the whole write rolls back (validated before
        the delete+insert, and the surrounding ``write_scope`` transaction
        would roll back the rest of this call's edits too either way).
        Duplicate ids in the payload are deduped (order-preserving) before
        validation/insert (M3 final review #3) — a repeated id is not a
        backward-only violation, so without the dedupe it would reach the
        insert loop and hit the composite-PK IntegrityError instead.

        ``brief`` (mig 395, M4 Autopilot §1) is a plain overwrite — runtime
        free text, no whitelist to enforce (unlike ``form_data``'s per-key
        ``form_schema`` filter), writable any time before/while the node is
        open.
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

            # Dependency edges (mig 391, M3 PR-J): validate BEFORE any mutation
            # below actually persists (the write_scope transaction would roll
            # everything back on a raise regardless, but failing fast here
            # keeps the intent obvious). ``dep_ids`` is resolved once and reused
            # by the delete+insert pass further down.
            dep_ids: Optional[List[int]] = None
            if depends_on is not None:
                # Dedupe first, preserving first-seen order (M3 final review
                # #3): a payload like ``["30", "30"]`` would otherwise survive
                # the backward-only check below (duplicates aren't a backward
                # violation) and reach the insert loop further down, adding
                # two ``ProjectStageNodeDeps`` rows sharing the same
                # composite PK (node_id, depends_on_node_id) — an
                # IntegrityError the router surfaces as a bare 500 instead of
                # the 422 ``DepsBackwardOnly`` callers expect.
                raw_dep_ids: List[int] = []
                for dep in depends_on:
                    try:
                        raw_dep_ids.append(int(str(dep)))
                    except (TypeError, ValueError):
                        raise DepsBackwardOnly() from None
                dep_ids = list(dict.fromkeys(raw_dep_ids))
                sort_order_by_id: Dict[int, int] = {}
                if dep_ids:
                    srows = (
                        await session.execute(
                            select(ProjectStageNodes.id, ProjectStageNodes.sort_order)
                            .where(ProjectStageNodes.project_id == pid)
                            .where(ProjectStageNodes.id.in_(dep_ids))
                        )
                    ).all()
                    sort_order_by_id = {r[0]: r[1] for r in srows}
                for dep_id in dep_ids:
                    dep_sort = sort_order_by_id.get(dep_id)
                    if dep_sort is None or dep_sort >= node.sort_order:
                        raise DepsBackwardOnly()

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
            if form_data is not None:
                allowed_keys = {
                    f.get("key") for f in (node.form_schema or []) if f.get("key")
                }
                filtered = {k: v for k, v in form_data.items() if k in allowed_keys}
                merged = dict(node.form_data or {})
                merged.update(filtered)
                node.form_data = merged
            if brief is not None:
                # Runtime data (mig 395, M4 Autopilot §1) — no whitelist beyond
                # NodePatch itself (same treatment as form_data, minus the
                # per-key form_schema filter since brief is free text, not a
                # keyed field set).
                node.brief = brief
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

            if dep_ids is not None:
                await session.execute(
                    ProjectStageNodeDeps.__table__.delete().where(
                        ProjectStageNodeDeps.node_id == nid
                    )
                )
                for dep_id in dep_ids:
                    session.add(
                        ProjectStageNodeDeps(node_id=nid, depends_on_node_id=dep_id)
                    )

        return await self.get_node(node_id, project_id)

    async def add_node(
        self,
        project_id: str,
        *,
        source_stage_id: Optional[str] = None,
        name: Optional[str] = None,
        sort_order: int,
        parallel_group: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Insert one node into a live instance (W3-1).

        From the node bank (``source_stage_id`` → inherit name / deliverable
        label / review flag) or blank (``name`` with those defaults off). The
        insert shifts every existing node at or after ``sort_order`` down by one
        so the new node lands at that position. Writes the instance only.
        """
        pid = int(str(project_id))
        async with write_scope() as session:
            inherited_name = name
            legacy_stage_id: Optional[int] = None
            review_required = False
            deliverable_required = False
            deliverable_label: Optional[str] = None

            if source_stage_id is not None:
                bank = (
                    (
                        await session.execute(
                            select(ProjectStages).where(
                                ProjectStages.id == int(str(source_stage_id))
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if bank is None:
                    raise ValueError(f"unknown node-bank stage {source_stage_id!r}")
                inherited_name = bank.name
                legacy_stage_id = bank.id
                review_required = bool(bank.review_required)
                deliverable_label = bank.deliverable_label

            # Make room: bump existing rows at or past the insert position.
            await session.execute(
                update(ProjectStageNodes)
                .where(ProjectStageNodes.project_id == pid)
                .where(ProjectStageNodes.sort_order >= sort_order)
                .values(sort_order=ProjectStageNodes.sort_order + 1)
            )

            node = ProjectStageNodes(
                project_id=pid,
                source_template_node_id=None,
                legacy_stage_id=legacy_stage_id,
                name=inherited_name or "New stage",
                sort_order=sort_order,
                parallel_group=parallel_group,
                status="pending",
                owner_user_id=None,
                owner_agent_id=None,
                planned_start=None,
                planned_due=None,
                review_required=review_required,
                deliverable_required=deliverable_required,
                deliverable_label=deliverable_label,
                skipped=False,
            )
            session.add(node)
            await session.flush()
            nid = node.id

        created = await self.get_node(str(nid), str(project_id))
        assert created is not None  # just written in this repo
        return created

    async def delete_node(self, node_id: str, project_id: str) -> bool:
        """Hard-delete a node instance (+ its members via CASCADE).

        Pure delete — the removal guards (pending / no mirror issue / not in the
        active group) live in the ``node_mutations`` service so they can be unit
        tested without a DB. Returns whether a row was removed.
        """
        pid = int(str(project_id))
        nid = int(str(node_id))
        async with write_scope() as session:
            result = await session.execute(
                ProjectStageNodes.__table__.delete()
                .where(ProjectStageNodes.id == nid)
                .where(ProjectStageNodes.project_id == pid)
                .returning(ProjectStageNodes.id)
            )
            return result.first() is not None

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

    async def set_node_folder_id(
        self, node_id: str, folder_id: Optional[str]
    ) -> Optional[str]:
        """Backfill a node's ``folder_id`` (the deliverable folder link, mig
        383). Returns the stored id, or None when the node is missing. Only the
        lazy folder-materializer calls this — business PATCH never touches it."""
        async with write_scope() as session:
            result = await session.execute(
                update(ProjectStageNodes)
                .where(ProjectStageNodes.id == int(str(node_id)))
                .values(
                    folder_id=(int(str(folder_id)) if folder_id is not None else None),
                    updated_at=datetime.datetime.now(datetime.timezone.utc),
                )
                .returning(ProjectStageNodes.folder_id)
            )
            row = result.first()
        if row is None:
            return None
        return str(row[0]) if row[0] is not None else None

    async def set_node_metadata(
        self, node_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Shallow-merge ``patch`` into a node's ``metadata`` JSONB (mig 389,
        M3 PR-H2). Free-form business decoration — never touches
        ``status``/``events``/schedule or any other trigger-/template-owned
        column. Existing keys not present in ``patch`` are preserved (one
        level deep — a nested dict value is replaced wholesale, not merged
        recursively). Returns the merged metadata dict, or None when the node
        is missing.
        """
        nid = int(str(node_id))
        async with write_scope() as session:
            node = (
                (
                    await session.execute(
                        select(ProjectStageNodes)
                        .where(ProjectStageNodes.id == nid)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if node is None:
                return None
            merged = dict(node.metadata_ or {})
            merged.update(patch)
            node.metadata_ = merged
            node.updated_at = datetime.datetime.now(datetime.timezone.utc)
            await session.flush()
            return dict(merged)

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
            deps_by_node = await _deps_by_node(session, group_ids)
            return [
                _node_row(n, members_by_node.get(n.id, []), deps_by_node.get(n.id, []))
                for n in group
            ]

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

    async def list_folder_files(
        self, folder_id: str, *, include_trashed: bool = False
    ) -> List[Dict[str, Any]]:
        """Files filed into one folder (bigint ``folder_id`` equality).

        Extracted so the SAME query backs both ``_deliverable_present``
        (advance_service.py — "has a file been filed?") and the Stage Board
        endpoint's ``files`` section ("which files are filed?") — the two can
        never drift about what counts as filed. Non-trashed only by default,
        newest first (matches ``ProjectsRepository.get_project_files``'
        ordering).
        """
        from app.repositories.projects_repository import _FILES_N2A, _row

        async with read_scope() as session:
            stmt = select(ProjectFiles).where(
                ProjectFiles.folder_id == int(str(folder_id))
            )
            if not include_trashed:
                stmt = stmt.where(ProjectFiles.is_trashed.is_(False))
            stmt = stmt.order_by(ProjectFiles.created_at.desc())
            result = await session.execute(stmt)
            return [_row(r, _FILES_N2A) for r in result.scalars().all()]

    async def workflow_badges_for_projects(
        self, project_ids: List[Any]
    ) -> Dict[str, Dict[str, Any]]:
        """Per-project workflow badge data for the list page in THREE queries
        (W3-3, no N+1 regardless of the number of projects).

        Returns ``{str(project_id): {current_node_name, workflow_total,
        workflow_position, agents_active}}``. Only projects that own workflow
        nodes are present (a No-workflow project renders no badge);
        ``current_node_name`` / ``workflow_position`` are null when the cursor
        is unset. Never raises — enrichment must not sink the list.
        """
        if not project_ids:
            return {}
        pids = [int(p) for p in project_ids]
        try:
            async with read_scope() as session:
                cursors = {
                    r[0]: r[1]
                    for r in (
                        await session.execute(
                            select(Projects.id, Projects.current_node_id).where(
                                Projects.id.in_(pids)
                            )
                        )
                    ).all()
                }
                node_rows = (
                    await session.execute(
                        select(
                            ProjectStageNodes.project_id,
                            ProjectStageNodes.id,
                            ProjectStageNodes.name,
                            ProjectStageNodes.sort_order,
                            ProjectStageNodes.skipped,
                        )
                        .where(ProjectStageNodes.project_id.in_(pids))
                        .order_by(
                            ProjectStageNodes.project_id, ProjectStageNodes.sort_order
                        )
                    )
                ).all()
                agent_counts = {
                    r[0]: r[1]
                    for r in (
                        await session.execute(
                            select(
                                AgentRuns.project_id,
                                func.count(AgentRuns.id),
                            )
                            .where(AgentRuns.project_id.in_(pids))
                            .where(AgentRuns.status == "running")
                            .group_by(AgentRuns.project_id)
                        )
                    ).all()
                }
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            from loguru import logger

            logger.error(f"[project_stage_nodes] batch badge lookup failed: {e}")
            return {}

        # Group non-skipped nodes per project (already ordered by sort_order).
        active_by_pid: Dict[int, List[tuple]] = {}
        for pid, nid, nname, _so, skipped in node_rows:
            if skipped:
                continue
            active_by_pid.setdefault(pid, []).append((nid, nname))

        out: Dict[str, Dict[str, Any]] = {}
        for pid in pids:
            active = active_by_pid.get(pid)
            if not active:
                continue  # No-workflow (or all-skipped) project → no badge
            cursor = cursors.get(pid)
            current_name: Optional[str] = None
            position: Optional[int] = None
            if cursor is not None:
                for i, (nid, nname) in enumerate(active):
                    if nid == cursor:
                        current_name = nname
                        position = i + 1
                        break
            out[str(pid)] = {
                "current_node_name": current_name,
                "workflow_total": len(active),
                "workflow_position": position,
                "agents_active": int(agent_counts.get(pid, 0)),
            }
        return out


_repo: Optional[ProjectStageNodesRepository] = None


def get_project_stage_nodes_repository() -> ProjectStageNodesRepository:
    global _repo
    if _repo is None:
        _repo = ProjectStageNodesRepository()
    return _repo
