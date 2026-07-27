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
import re
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update

from app.db.session import read_scope, write_scope
from app.models import (
    ProjectStages,
    WorkflowTemplateNodeDeps,
    WorkflowTemplateNodeMembers,
    WorkflowTemplateNodes,
    WorkflowTemplates,
)
from app.schemas.workflow import DepsBackwardOnly


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
    obj: WorkflowTemplateNodes,
    members: List[Dict[str, Any]],
    depends_on: Optional[List[str]] = None,
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
        "form_schema": obj.form_schema,
        "members": members,
        # Dependency edges (mig 391, M3 PR-J): the OTHER template nodes (real
        # ids, stable until the next full-replace save) this node depends on.
        "depends_on": depends_on or [],
    }


def _slugify_label(label: str) -> str:
    """kebab-case a form field's label for use as its ``key``.

    Lowercases, collapses any run of non-alphanumeric characters to a single
    hyphen, and trims leading/trailing hyphens. A label with no alphanumeric
    content at all (paranoia — ``FormFieldDef.label`` already rejects a
    blank/whitespace-only label) falls back to ``"field"`` so a key is never
    empty.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug or "field"


def _slugify_field_keys(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate each field's ``key`` from its ``label`` (mig 390, M3 PR-I).

    Slugify-then-dedupe WITHIN one node's field list: the first field to claim
    a slug keeps it bare; each subsequent collision gets a ``-2``, ``-3``, ...
    suffix (spec: "重复加 -2 序号"). Only the repo write path can guarantee
    uniqueness across the whole list, so this always regenerates the key from
    the label rather than trusting whatever (possibly blank) ``key`` rode in
    on the request.
    """
    seen: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for field in fields:
        base = _slugify_label(str(field.get("label", "")))
        count = seen.get(base, 0) + 1
        seen[base] = count
        key = base if count == 1 else f"{base}-{count}"
        out.append({**field, "key": key})
    return out


def _dedupe_depends_on(nodes: List[Dict[str, Any]]) -> None:
    """De-duplicate each node's ``depends_on`` list IN PLACE, preserving
    first-seen order (M3 final review #3).

    A payload like ``depends_on: ["0", "0"]`` would otherwise survive
    ``_validate_deps_backward`` (duplicates don't violate backward-only) and
    reach the second insert pass in ``update_template``, which would add two
    ``WorkflowTemplateNodeDeps`` rows sharing the same composite PK
    ``(node_id, depends_on_node_id)`` — an IntegrityError the router surfaces
    as a bare 500, not the 422 ``DepsBackwardOnly`` callers expect. Mutating
    ``nodes`` here (before validation) means both the validation pass and the
    later insert pass see the same deduped list, so a duplicate collapses to
    a single edge instead of erroring.
    """
    for node in nodes:
        deps = node.get("depends_on")
        if deps:
            node["depends_on"] = list(dict.fromkeys(str(d) for d in deps))


def _validate_deps_backward(nodes: List[Dict[str, Any]]) -> None:
    """Enforce backward-only dependency edges within one full-replace payload
    (mig 391, M3 PR-J).

    Each node's ``depends_on`` is a list of stringified 0-based INDICES into
    THIS SAME ``nodes`` list — never a node id (see the payload-index
    contract documented on ``TemplateNodeIn.depends_on``: ``update_template``
    deletes and reinserts every node on every save that supplies ``nodes``, so
    no id survives across saves for a caller to reference in the first
    place). A target must exist (index in range) and have a strictly smaller
    ``sort_order`` than the dependent node; a self-reference (index == its
    own position) always fails this because a node's own sort_order is never
    smaller than itself — same violation, same code (spec §3: "自依赖同罪").
    Pure/no DB access — runs before any write so a bad payload never touches
    the table. Callers should run ``_dedupe_depends_on`` first so a
    duplicate-index payload never reaches the insert pass either.
    """
    n = len(nodes)
    for i, node in enumerate(nodes):
        for dep in node.get("depends_on") or []:
            try:
                dep_idx = int(dep)
            except (TypeError, ValueError):
                raise DepsBackwardOnly() from None
            if dep_idx < 0 or dep_idx >= n:
                raise DepsBackwardOnly()
            if nodes[dep_idx]["sort_order"] >= node["sort_order"]:
                raise DepsBackwardOnly()


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
            deps_by_node: Dict[int, List[str]] = {nid: [] for nid in node_ids}
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

                drows = (
                    (
                        await session.execute(
                            select(WorkflowTemplateNodeDeps).where(
                                WorkflowTemplateNodeDeps.node_id.in_(node_ids)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for d in drows:
                    deps_by_node.setdefault(d.node_id, []).append(
                        str(d.depends_on_node_id)
                    )

            result = _template_row(tpl, len(nodes))
            result["nodes"] = [
                _node_row(n, members_by_node.get(n.id, []), deps_by_node.get(n.id, []))
                for n in nodes
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
                # Dependency edges (mig 391, M3 PR-J): dedupe THEN validate,
                # both BEFORE touching any row — a bad payload must never
                # partially clobber the existing node list. Dedupe first (M3
                # final review #3) so a duplicate-index payload (e.g.
                # ["0","0"]) collapses to one edge instead of reaching the
                # insert pass and hitting the composite-PK IntegrityError.
                # Both helpers are pure/no DB access (see their docstrings).
                _dedupe_depends_on(nodes)
                _validate_deps_backward(nodes)

                await session.execute(
                    delete(WorkflowTemplateNodes).where(
                        WorkflowTemplateNodes.template_id == tpl.id
                    )
                )
                # Position -> freshly-created node id, built as each node is
                # flushed below. depends_on entries in ``nodes`` are indices
                # into THIS list, resolved against this map in the second pass
                # once every node has a real id.
                id_by_index: List[int] = []
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
                    if nd.get("form_schema") is not None:
                        node_kwargs["form_schema"] = _slugify_field_keys(
                            nd["form_schema"]
                        )
                    node = WorkflowTemplateNodes(**node_kwargs)
                    session.add(node)
                    await session.flush()
                    id_by_index.append(node.id)
                    for m in nd.get("members", []):
                        session.add(
                            WorkflowTemplateNodeMembers(
                                node_id=node.id,
                                user_id=_as_uuid(m.get("user_id")),
                                agent_id=_as_uuid(m.get("agent_id")),
                            )
                        )

                # Second pass: every node now has a real id, so depends_on
                # indices (already validated above) resolve unambiguously.
                for i, nd in enumerate(nodes):
                    for dep in nd.get("depends_on") or []:
                        session.add(
                            WorkflowTemplateNodeDeps(
                                node_id=id_by_index[i],
                                depends_on_node_id=id_by_index[int(dep)],
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
