"""Dependency edges — backward-only validation, copy, full-replace (mig 391,
M3 PR-J).

Two new tables (``workflow_template_node_deps`` / ``project_stage_node_deps``)
back a light DAG on top of the existing cursor-based workflow: a node may
declare other nodes (strictly earlier ``sort_order``, self-deps included as a
violation) it depends on. The core invariant — backward-only — is enforced in
the application layer, never the DB, so it is tested at that layer:

  * ``workflow_templates_repository._validate_deps_backward`` — pure, no DB.
    ``TemplateNodeIn.depends_on`` is a PAYLOAD-INDEX contract (0-based position
    within the SAME ``nodes`` list of one PATCH request), not a node id — see
    that field's docstring for why: ``update_template`` deletes and reinserts
    every node on every save that supplies ``nodes``, so no id survives across
    saves for a caller to reference in the first place.
  * ``update_template`` — full-replace write path: validates before touching
    any row, then resolves indices to freshly-created ids in a second pass.
  * ``ProjectStageNodesRepository.instantiate_from_template`` — copies edges
    template-node-id -> instance-node-id using the same map the node copy
    itself builds.
  * ``ProjectStageNodesRepository.update_node`` — instance PATCH full-replace,
    validated against the node's LIVE project siblings (real ids, not
    indices).
  * ``_list_nodes_in_session`` / ``get_node`` — surface ``depends_on`` per
    node; CASCADE means a removed node's edges are simply absent from the
    table, so no dangling id can ever leak through a listing.

FakeSession plumbing (house pattern per test_workflow_flow_rules.py /
test_workflow_instantiation.py): real statement construction / add / flush
runs; only SQL execution is faked. Duplicated (not imported) so this module
stays self-contained, per the established convention in this file family.
"""

from __future__ import annotations

import importlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from app.models import (
    ProjectStageNodeDeps,
    ProjectStageNodes,
    WorkflowTemplateNodeDeps,
    WorkflowTemplateNodes,
    WorkflowTemplates,
)
from app.repositories.project_stage_nodes_repository import ProjectStageNodesRepository
from app.repositories.workflow_templates_repository import (
    WorkflowTemplatesRepository,
)
from app.repositories.workflow_templates_repository import _node_row as _tpl_node_row
from app.repositories.workflow_templates_repository import (
    _dedupe_depends_on,
    _validate_deps_backward,
)
from app.schemas.workflow import (
    DEP_BACKWARD_ONLY,
    DepsBackwardOnly,
    NodeOut,
    NodePatch,
    TemplateNodeIn,
)

MIG = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/391_workflow_node_deps.sql"
)


# ── shared FakeSession plumbing ─────────────────────────────────────────────


class _Result:
    """Wraps a canned row list for both ``.scalars().all()/.first()`` and the
    bare ``.all()`` access patterns (same tiny helper duplicated across this
    file family)."""

    def __init__(self, rows: List[Any]):
        self._rows = list(rows)

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


def _write_scope_with(session: Any):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _read_scope_with(session: Any):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


# ── migration DDL: static content checks (house pattern per
# test_generated_media_migration.py) ────────────────────────────────────────


def test_migration_defines_both_edge_tables_with_cascade_and_composite_pk():
    sql = MIG.read_text()

    for table in ("workflow_template_node_deps", "project_stage_node_deps"):
        assert re.search(
            rf"\bCREATE TABLE.*{table}\b", sql, re.IGNORECASE
        ), f"missing table {table}"

    # Both columns present on both tables.
    for col in ("node_id", "depends_on_node_id"):
        assert sql.count(col) >= 4, f"expected {col} on both edge tables"

    # CASCADE cleanup: a deleted node's edges must vanish with it, never
    # dangle. Four FK clauses total (2 tables x 2 columns each).
    assert sql.count("ON DELETE CASCADE") >= 4

    # Composite PK, not a surrogate id (both columns NOT NULL, unlike the
    # nullable-XOR member tables).
    assert sql.count("PRIMARY KEY (node_id, depends_on_node_id)") == 2

    # RLS: service-role-only lockdown, mig 380 idiom.
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 2
    assert sql.count("auth.role() = 'service_role'") >= 2

    assert "NOTIFY pgrst, 'reload schema'" in sql


# ── _validate_deps_backward: pure, no DB ────────────────────────────────────


def _n(sort_order: int, depends_on: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "name": f"n{sort_order}",
        "sort_order": sort_order,
        "depends_on": depends_on or [],
    }


def test_validate_deps_backward_accepts_legit_backward_edge():
    nodes = [_n(1), _n(2, depends_on=["0"])]
    _validate_deps_backward(nodes)  # must not raise


def test_validate_deps_backward_rejects_self_dependency():
    nodes = [_n(1), _n(2, depends_on=["1"])]  # index 1 == its own position
    with pytest.raises(DepsBackwardOnly) as exc:
        _validate_deps_backward(nodes)
    assert exc.value.reason == DEP_BACKWARD_ONLY


def test_validate_deps_backward_rejects_forward_dependency():
    nodes = [_n(1, depends_on=["1"]), _n(2)]  # node 0 depends on node 1 (later)
    with pytest.raises(DepsBackwardOnly):
        _validate_deps_backward(nodes)


def test_validate_deps_backward_rejects_equal_sort_order():
    # Two different nodes sharing a sort_order: spec says the target must be
    # STRICTLY smaller, so equal is a violation even across different nodes.
    nodes = [_n(1), _n(1, depends_on=["0"])]
    with pytest.raises(DepsBackwardOnly):
        _validate_deps_backward(nodes)


def test_validate_deps_backward_rejects_out_of_range_index():
    nodes = [_n(1, depends_on=["5"])]
    with pytest.raises(DepsBackwardOnly):
        _validate_deps_backward(nodes)


def test_validate_deps_backward_rejects_non_numeric_index():
    nodes = [_n(1), _n(2, depends_on=["not-a-number"])]
    with pytest.raises(DepsBackwardOnly):
        _validate_deps_backward(nodes)


def test_validate_deps_backward_no_deps_is_a_noop():
    nodes = [_n(1), _n(2), _n(3)]
    _validate_deps_backward(nodes)  # must not raise


# ── _dedupe_depends_on: collapses duplicate ids before validation/insert
#    (M3 final review #3) ──────────────────────────────────────────────────


def test_dedupe_depends_on_collapses_duplicates_preserving_order():
    nodes = [_n(1), _n(2), _n(3, depends_on=["0", "1", "0", "1", "0"])]
    _dedupe_depends_on(nodes)
    assert nodes[2]["depends_on"] == ["0", "1"]


def test_dedupe_depends_on_leaves_no_dup_list_untouched():
    nodes = [_n(1), _n(2, depends_on=["0"])]
    _dedupe_depends_on(nodes)
    assert nodes[1]["depends_on"] == ["0"]


def test_dedupe_depends_on_is_a_noop_on_empty_lists():
    nodes = [_n(1), _n(2), _n(3)]
    _dedupe_depends_on(nodes)
    assert all(n["depends_on"] == [] for n in nodes)


def test_validate_deps_backward_accepts_duplicate_index_after_dedupe():
    """A duplicate-index payload (['0','0']) is legal backward-only content
    once deduped — proves the two helpers compose the way the repo wires
    them (dedupe THEN validate)."""
    nodes = [_n(1), _n(2, depends_on=["0", "0"])]
    _dedupe_depends_on(nodes)
    _validate_deps_backward(nodes)  # must not raise
    assert nodes[1]["depends_on"] == ["0"]


# ── update_template: full-replace writes resolved edges ─────────────────────


class _TemplateFakeSession:
    """Drives ``update_template``'s tpl fetch + node delete + insert. Only
    ``execute`` calls count — ``session.add()`` for members/deps never goes
    through ``execute`` in the real repo code, so this fake never needs to
    know about them beyond recording them in ``added``."""

    def __init__(self, tpl: WorkflowTemplates):
        self._tpl = tpl
        self.added: List[Any] = []
        self._next_id = 7000
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def execute(self, stmt: Any) -> _Result:
        await self.flush()
        self._calls += 1
        if self._calls == 1:
            return _Result([self._tpl])  # tpl select
        return _Result([])  # the node-list delete


def _fake_tpl() -> WorkflowTemplates:
    import datetime

    now = datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc)
    return WorkflowTemplates(
        id=1,
        team_id=2,
        name="Short-form",
        is_default=True,
        created_by=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_update_template_writes_deps_resolved_to_fresh_ids(monkeypatch):
    session = _TemplateFakeSession(_fake_tpl())

    import app.repositories.workflow_templates_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = WorkflowTemplatesRepository()

    async def _fake_get_template(template_id, team_id):
        return {"id": str(template_id), "sentinel": True}

    monkeypatch.setattr(repo, "get_template", _fake_get_template)

    nodes = [
        {"name": "Script", "sort_order": 1, "members": [], "depends_on": []},
        {
            "name": "Storyboard",
            "sort_order": 2,
            "members": [],
            "depends_on": ["0"],  # payload index 0 -> the Script node above
        },
    ]
    result = await repo.update_template("1", "2", nodes=nodes)

    assert result == {"id": "1", "sentinel": True}
    written_nodes = [o for o in session.added if isinstance(o, WorkflowTemplateNodes)]
    written_deps = [o for o in session.added if isinstance(o, WorkflowTemplateNodeDeps)]
    assert len(written_nodes) == 2
    assert len(written_deps) == 1

    script_id = written_nodes[0].id
    storyboard_id = written_nodes[1].id
    assert written_deps[0].node_id == storyboard_id
    assert written_deps[0].depends_on_node_id == script_id


@pytest.mark.asyncio
async def test_update_template_dedupes_duplicate_dep_indices_into_one_edge(
    monkeypatch,
):
    """A payload with a duplicate dep index (['0','0']) must collapse to a
    SINGLE edge row, not two rows colliding on the composite PK
    (node_id, depends_on_node_id) — the pre-fix behavior would have hit an
    IntegrityError here (M3 final review #3)."""
    session = _TemplateFakeSession(_fake_tpl())

    import app.repositories.workflow_templates_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = WorkflowTemplatesRepository()

    async def _fake_get_template(template_id, team_id):
        return {"id": str(template_id), "sentinel": True}

    monkeypatch.setattr(repo, "get_template", _fake_get_template)

    nodes = [
        {"name": "Script", "sort_order": 1, "members": [], "depends_on": []},
        {
            "name": "Storyboard",
            "sort_order": 2,
            "members": [],
            "depends_on": ["0", "0"],  # duplicate payload index
        },
    ]
    result = await repo.update_template("1", "2", nodes=nodes)

    assert result == {"id": "1", "sentinel": True}
    written_deps = [o for o in session.added if isinstance(o, WorkflowTemplateNodeDeps)]
    assert len(written_deps) == 1


@pytest.mark.asyncio
async def test_update_template_backward_violation_raises_before_any_write(
    monkeypatch,
):
    session = _TemplateFakeSession(_fake_tpl())

    import app.repositories.workflow_templates_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = WorkflowTemplatesRepository()

    nodes = [
        {"name": "Script", "sort_order": 1, "members": [], "depends_on": ["0"]},
    ]  # self-dependency

    with pytest.raises(DepsBackwardOnly) as exc:
        await repo.update_template("1", "2", nodes=nodes)
    assert exc.value.reason == DEP_BACKWARD_ONLY

    # Only the tpl select happened (call #1) -- the delete (call #2) never
    # ran, and nothing was added. A bad payload must never partially clobber
    # the existing node list.
    assert session._calls == 1
    assert session.added == []


# ── _node_row (template side): depends_on surfaces in the read shape ────────


def test_template_node_row_includes_depends_on():
    node = WorkflowTemplateNodes(
        id=10,
        template_id=1,
        name="Storyboard",
        sort_order=2,
        parallel_group=None,
        default_owner_user_id=None,
        default_owner_agent_id=None,
        skip_default=False,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        source_stage_id=None,
        duration_days=None,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=[],
    )
    row = _tpl_node_row(node, [], ["7"])
    assert row["depends_on"] == ["7"]


def test_template_node_row_defaults_depends_on_to_empty_list():
    node = WorkflowTemplateNodes(
        id=10,
        template_id=1,
        name="Storyboard",
        sort_order=2,
        parallel_group=None,
        default_owner_user_id=None,
        default_owner_agent_id=None,
        skip_default=False,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        source_stage_id=None,
        duration_days=None,
        completion_policy="owner",
        events={},
        form_schema=[],
    )
    row = _tpl_node_row(node, [])
    assert row["depends_on"] == []


# ── instantiate_from_template: copies edges tpl-id -> instance-id ───────────


class _InstantiateFakeSession:
    """8-call fake for one instantiate pass with non-empty deps (see the
    docstring on the sibling class in test_workflow_instantiation.py for the
    full call-order rationale)."""

    def __init__(
        self,
        tpl_nodes: List[WorkflowTemplateNodes],
        tpl_deps: List[WorkflowTemplateNodeDeps],
    ):
        self._tpl_nodes = tpl_nodes
        self._tpl_deps = tpl_deps
        self.added: List[Any] = []
        self._next_id = 9000
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def execute(self, stmt: Any) -> _Result:
        await self.flush()
        self._calls += 1
        if self._calls == 1:
            return _Result([])  # existing-nodes check
        if self._calls == 2:
            return _Result(self._tpl_nodes)  # template nodes select
        if self._calls == 3:
            return _Result([])  # template node-members select
        if self._calls == 4:
            return _Result(self._tpl_deps)  # template node-deps select
        if self._calls == 5:
            return _Result([])  # node-bank slug map select
        if self._calls == 6:
            nodes = [o for o in self.added if isinstance(o, ProjectStageNodes)]
            nodes.sort(key=lambda n: n.sort_order)
            return _Result(nodes)
        if self._calls == 7:
            return _Result([])  # final member listing (no members in fixture)
        if self._calls == 8:
            deps = [o for o in self.added if isinstance(o, ProjectStageNodeDeps)]
            return _Result(deps)
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


def _tpl_node(*, node_id: int, sort_order: int) -> WorkflowTemplateNodes:
    return WorkflowTemplateNodes(
        id=node_id,
        template_id=1,
        name=f"node-{node_id}",
        sort_order=sort_order,
        parallel_group=None,
        default_owner_user_id=None,
        default_owner_agent_id=None,
        skip_default=False,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        source_stage_id=None,
        duration_days=None,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=[],
    )


@pytest.mark.asyncio
async def test_instantiate_copies_deps_with_id_remapping(monkeypatch):
    tpl_nodes = [
        _tpl_node(node_id=1, sort_order=1),
        _tpl_node(node_id=2, sort_order=2),
    ]
    tpl_deps = [WorkflowTemplateNodeDeps(node_id=2, depends_on_node_id=1)]
    session = _InstantiateFakeSession(tpl_nodes, tpl_deps)

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.instantiate_from_template("50", "1")

    assert len(result) == 2
    by_sort = {n["sort_order"]: n for n in result}
    instance_id_1 = by_sort[1]["id"]
    instance_id_2 = by_sort[2]["id"]

    # The copied edge must point at the FRESH instance ids, not the template
    # ids (1, 2) that only ever existed on the template side.
    assert by_sort[2]["depends_on"] == [instance_id_1]
    assert by_sort[1]["depends_on"] == []
    assert instance_id_1 != "1" and instance_id_2 != "2"


@pytest.mark.asyncio
async def test_instantiate_with_no_deps_leaves_depends_on_empty(monkeypatch):
    tpl_nodes = [_tpl_node(node_id=1, sort_order=1)]
    session = _InstantiateFakeSession(tpl_nodes, [])

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.instantiate_from_template("50", "1")

    assert result[0]["depends_on"] == []


# ── ProjectStageNodesRepository.update_node: depends_on full-replace ───────


class _UpdateNodeFakeSession:
    """Drives ``update_node``'s node fetch + (conditionally) the sibling
    sort_order lookup + the deps delete/insert. Call order:
      1. node fetch
      2. sibling sort_order lookup (ONLY when depends_on is non-empty)
      3. deps delete (only when depends_on is not None at all)
    """

    def __init__(self, node: ProjectStageNodes, sibling_rows: List[tuple]):
        self._node = node
        self._sibling_rows = sibling_rows
        self.added: List[Any] = []
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: Any) -> _Result:
        self._calls += 1
        if self._calls == 1:
            return _Result([self._node])
        if self._calls == 2:
            return _Result(self._sibling_rows)
        return _Result([])  # the deps delete


def _live_node(*, node_id: int, sort_order: int) -> ProjectStageNodes:
    return ProjectStageNodes(
        id=node_id,
        project_id=50,
        source_template_node_id=None,
        legacy_stage_id=None,
        name="Storyboard",
        sort_order=sort_order,
        parallel_group=None,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=[],
        form_data={},
    )


def _install_update_node(monkeypatch, session: Any):
    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))
    repo = ProjectStageNodesRepository()

    async def _fake_get_node(node_id, project_id):
        return {"sentinel": True}

    monkeypatch.setattr(repo, "get_node", _fake_get_node)
    return repo


@pytest.mark.asyncio
async def test_update_node_depends_on_self_rejected(monkeypatch):
    node = _live_node(node_id=10, sort_order=5)
    # The node's own row is a valid "sibling" lookup result too.
    session = _UpdateNodeFakeSession(node, sibling_rows=[(10, 5)])
    repo = _install_update_node(monkeypatch, session)

    with pytest.raises(DepsBackwardOnly) as exc:
        await repo.update_node("10", "50", depends_on=["10"])
    assert exc.value.reason == DEP_BACKWARD_ONLY
    assert session.added == []  # nothing written on a rejected replace


@pytest.mark.asyncio
async def test_update_node_depends_on_forward_rejected(monkeypatch):
    node = _live_node(node_id=10, sort_order=5)
    # Target has a LARGER sort_order (7 >= 5) -> forward, rejected.
    session = _UpdateNodeFakeSession(node, sibling_rows=[(20, 7)])
    repo = _install_update_node(monkeypatch, session)

    with pytest.raises(DepsBackwardOnly):
        await repo.update_node("10", "50", depends_on=["20"])


@pytest.mark.asyncio
async def test_update_node_depends_on_missing_target_rejected(monkeypatch):
    node = _live_node(node_id=10, sort_order=5)
    # Sibling query finds nothing (id doesn't exist / wrong project).
    session = _UpdateNodeFakeSession(node, sibling_rows=[])
    repo = _install_update_node(monkeypatch, session)

    with pytest.raises(DepsBackwardOnly):
        await repo.update_node("10", "50", depends_on=["999"])


@pytest.mark.asyncio
async def test_update_node_depends_on_non_numeric_rejected(monkeypatch):
    node = _live_node(node_id=10, sort_order=5)
    session = _UpdateNodeFakeSession(node, sibling_rows=[])
    repo = _install_update_node(monkeypatch, session)

    with pytest.raises(DepsBackwardOnly):
        await repo.update_node("10", "50", depends_on=["not-a-number"])


@pytest.mark.asyncio
async def test_update_node_depends_on_legit_backward_writes_edge(monkeypatch):
    node = _live_node(node_id=10, sort_order=5)
    # Target has a SMALLER sort_order (2 < 5) -> legit.
    session = _UpdateNodeFakeSession(node, sibling_rows=[(30, 2)])
    repo = _install_update_node(monkeypatch, session)

    result = await repo.update_node("10", "50", depends_on=["30"])

    assert result == {"sentinel": True}  # proves get_node's return ships through
    written = [o for o in session.added if isinstance(o, ProjectStageNodeDeps)]
    assert len(written) == 1
    assert written[0].node_id == 10
    assert written[0].depends_on_node_id == 30


@pytest.mark.asyncio
async def test_update_node_depends_on_dedupes_duplicate_ids_into_one_edge(
    monkeypatch,
):
    """A payload with a duplicate dep id (['30','30']) must collapse to a
    SINGLE edge row — the pre-fix behavior would have hit an IntegrityError
    on the composite PK (node_id, depends_on_node_id) (M3 final review #3)."""
    node = _live_node(node_id=10, sort_order=5)
    # Target has a SMALLER sort_order (2 < 5) -> legit; sibling lookup only
    # needs to resolve the deduped id once.
    session = _UpdateNodeFakeSession(node, sibling_rows=[(30, 2)])
    repo = _install_update_node(monkeypatch, session)

    result = await repo.update_node("10", "50", depends_on=["30", "30"])

    assert result == {"sentinel": True}
    written = [o for o in session.added if isinstance(o, ProjectStageNodeDeps)]
    assert len(written) == 1
    assert written[0].node_id == 10
    assert written[0].depends_on_node_id == 30


@pytest.mark.asyncio
async def test_update_node_depends_on_empty_list_clears_without_querying_siblings(
    monkeypatch,
):
    node = _live_node(node_id=10, sort_order=5)
    session = _UpdateNodeFakeSession(node, sibling_rows=[])
    repo = _install_update_node(monkeypatch, session)

    result = await repo.update_node("10", "50", depends_on=[])

    assert result == {"sentinel": True}
    assert [o for o in session.added if isinstance(o, ProjectStageNodeDeps)] == []
    # Only the node fetch happened -- no sibling lookup for an empty list, but
    # the delete (clearing any prior edges) still ran as call #2.
    assert session._calls == 2


@pytest.mark.asyncio
async def test_update_node_without_depends_on_kwarg_leaves_deps_untouched(
    monkeypatch,
):
    """``depends_on=None`` (the default -- caller didn't touch it) must not
    run any deps query/delete at all, mirroring the ``form_data`` precedent."""
    node = _live_node(node_id=10, sort_order=5)
    session = _UpdateNodeFakeSession(node, sibling_rows=[])
    repo = _install_update_node(monkeypatch, session)

    await repo.update_node("10", "50", skipped=True)

    assert session._calls == 1  # only the node fetch
    assert session.added == []


# ── list_nodes / get_node: depends_on surfaces; CASCADE leaves no dangling ──


class _ListNodesFakeSession:
    def __init__(self, nodes, member_rows, dep_rows):
        self._nodes = nodes
        self._member_rows = member_rows
        self._dep_rows = dep_rows
        self._calls = 0

    async def execute(self, stmt: Any) -> _Result:
        self._calls += 1
        if self._calls == 1:
            return _Result(self._nodes)
        if self._calls == 2:
            return _Result(self._member_rows)
        if self._calls == 3:
            return _Result(self._dep_rows)
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


@pytest.mark.asyncio
async def test_list_nodes_returns_depends_on(monkeypatch):
    node_a = _live_node(node_id=1, sort_order=1)
    node_b = _live_node(node_id=2, sort_order=2)
    dep_row = ProjectStageNodeDeps(node_id=2, depends_on_node_id=1)
    session = _ListNodesFakeSession([node_a, node_b], [], [dep_row])

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "read_scope", _read_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.list_nodes("50")

    by_id = {n["id"]: n for n in result}
    assert by_id["2"]["depends_on"] == ["1"]
    assert by_id["1"]["depends_on"] == []


@pytest.mark.asyncio
async def test_list_nodes_cascade_cleanup_leaves_no_dangling_edge(monkeypatch):
    """Simulates the post-CASCADE state: node A was deleted (FK ON DELETE
    CASCADE also removed the project_stage_node_deps row that referenced it),
    so the live query set already excludes both A's node row AND the B->A
    edge row. B's depends_on must reflect that -- no code path re-derives a
    stale reference, it just never sees one."""
    node_b = _live_node(node_id=2, sort_order=2)
    # Node A (id=1) is gone; no deps rows survive referencing it either.
    session = _ListNodesFakeSession([node_b], [], [])

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "read_scope", _read_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.list_nodes("50")

    assert len(result) == 1
    assert result[0]["id"] == "2"
    assert result[0]["depends_on"] == []


# ── router error mapping: DepsBackwardOnly -> 422 {"code": ...} ────────────


class _Auth:
    user_id = "00000000-0000-0000-0000-000000000001"


_AUTH = _Auth()


@pytest.mark.asyncio
async def test_update_template_router_maps_deps_backward_only_to_422(monkeypatch):
    router_mod = importlib.import_module("app.api.workflow_templates_router")

    class _FakeRepo:
        async def get_template_team_id(self, template_id):
            return "777"

        async def update_template(self, template_id, team_id, **kwargs):
            raise DepsBackwardOnly()

    monkeypatch.setattr(
        router_mod, "get_workflow_templates_repository", lambda: _FakeRepo()
    )

    async def _role(user_id, *, project_id=None, team_id=None):
        return "manager"

    monkeypatch.setattr(router_mod, "resolve_effective_role", _role)

    from app.schemas.workflow import TemplateUpdate

    with pytest.raises(HTTPException) as exc:
        await router_mod.update_template(
            "500",
            TemplateUpdate(
                nodes=[TemplateNodeIn(name="A", sort_order=1, depends_on=["0"])]
            ),
            _AUTH,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == DEP_BACKWARD_ONLY


@pytest.mark.asyncio
async def test_patch_node_router_maps_deps_backward_only_to_422(monkeypatch):
    router_mod = importlib.import_module("app.api.projects_router")

    import app.core.workflow_roles as roles_mod

    async def _role(user_id, *, project_id=None, team_id=None):
        return "manager"

    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role)

    class _FakeNodesRepo:
        async def update_node(self, node_id, project_id, **kwargs):
            raise DepsBackwardOnly()

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _FakeNodesRepo(),
    )

    with pytest.raises(HTTPException) as exc:
        await router_mod.patch_workflow_node(
            "100", "900", NodePatch(depends_on=["900"]), _AUTH, None
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == DEP_BACKWARD_ONLY


# ── schema-level: defaults + the payload-index vs id contract documentation ─


def test_template_node_in_depends_on_defaults_empty():
    node = TemplateNodeIn(name="Script", sort_order=1)
    assert node.depends_on == []


def test_template_node_in_depends_on_round_trips_through_node_to_dict():
    workflow_templates_router = importlib.import_module(
        "app.api.workflow_templates_router"
    )
    node = TemplateNodeIn(name="Storyboard", sort_order=2, depends_on=["0"])
    payload = workflow_templates_router._node_to_dict(node)
    assert payload["depends_on"] == ["0"]


def test_node_out_depends_on_defaults_empty_for_pre_migration_row():
    # A bare dict predating mig 391 has no "depends_on" key at all -- NodeOut
    # must default to [] rather than raising (same precedent as metadata /
    # form_schema before it).
    out = NodeOut(
        id="1",
        project_id="100",
        name="Script",
        sort_order=1,
        status="pending",
        review_required=False,
        deliverable_required=False,
        skipped=False,
    )
    assert out.depends_on == []


def test_node_patch_depends_on_optional_and_none_by_default():
    patch = NodePatch()
    assert patch.depends_on is None
    assert "depends_on" not in patch.model_fields_set

    patch2 = NodePatch(depends_on=["5", "6"])
    assert patch2.depends_on == ["5", "6"]
    assert "depends_on" in patch2.model_fields_set
