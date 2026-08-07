"""Episode-scoped data entry point — repository layer (mig 402, B2 T0).

The workflow is descending from project level to episode level. Every episode
instantiated from the same template copies the template's ``parallel_group`` /
``sort_order`` values VERBATIM, so those values are *identical across episodes*.
That is the P0 trap (``docs/superpowers/plans/2026-08-04-p0-cursor-audit.md``):
a project-wide node listing collapses Ep1's and Ep3's same-named nodes into one
parallel group downstream, and the "advance" logic then advances every episode
at once. T0 closes this at the data entry point (the repository), which T1's
``advance_service`` refactor stands on:

  * ``list_nodes_by_episode(project_id, episode_id)`` — same return shape as
    ``list_nodes`` but confined to one episode.
  * ``get_active_group(project_id, episode_id=...)`` — the badge/board read
    path AND the "can't delete an active node" guard; without the episode
    filter it matches a sibling episode's template-cloned twin.

The bug is INVISIBLE with a single episode — every assertion below builds a
project with TWO episodes sharing one ``parallel_group`` value on purpose.

FakeSession plumbing follows the house pattern (test_workflow_deps.py /
test_workflow_flow_rules.py): real statement construction runs; only SQL
*execution* is faked. Unlike the canned-by-call-order fakes elsewhere in the
family, this module's fake genuinely EVALUATES each query's WHERE criteria
against an in-memory row set — so a query that forgets the episode predicate
really does leak the other episode's rows, which is exactly the regression
being pinned. Only the three operators these queries use (``==``, ``is_``,
``in_``) are evaluated.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List, Optional

import pytest

from app.models import (
    Projects,
    ProjectStageNodeMembers,
    ProjectStageNodes,
)
from app.repositories.project_stage_nodes_repository import (
    ProjectStageNodesRepository,
)

EP1 = 8001
EP2 = 8002
PID = 50


def _node(node_id: int, episode_id: int, **overrides: Any) -> ProjectStageNodes:
    base = dict(
        id=node_id,
        project_id=PID,
        source_template_node_id=None,
        legacy_stage_id=None,
        name=f"Script-{node_id}",
        sort_order=1,
        # Template-cloned VERBATIM => identical across episodes. This shared
        # value is precisely what makes the cross-episode leak possible.
        parallel_group="draft",
        episode_id=episode_id,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
        folder_id=None,
        completion_policy="owner",
        events={},
        metadata_={},
        form_schema=[],
        form_data={},
        brief="",
        surface=None,
    )
    base.update(overrides)
    return ProjectStageNodes(**base)


# ── a FakeSession that really evaluates each query's WHERE clause ────────────


def _matches(obj: Any, criteria: Any) -> bool:
    """True when ``obj`` satisfies every predicate in a statement's
    ``_where_criteria`` (AND-combined). Handles only the operators the repo's
    queries actually use: ``==`` (eq), ``is_`` (skipped.is_(False)), ``in_``
    (node_id membership)."""
    for crit in criteria:
        op = crit.operator.__name__
        actual = getattr(obj, crit.left.key)
        if op == "eq":
            if actual != crit.right.value:
                return False
        elif op == "is_":
            expected = crit.right.__class__.__name__ == "True_"
            if bool(actual) is not expected:
                return False
        elif op == "in_op":
            if actual not in crit.right.value:
                return False
        else:  # pragma: no cover - defensive: an unexpected query shape
            raise AssertionError(f"unhandled operator {op!r} in fake session")
    return True


class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = list(rows)

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FilteringSession:
    def __init__(
        self,
        nodes: List[ProjectStageNodes],
        members: Optional[List[ProjectStageNodeMembers]] = None,
        current_node_id: Optional[int] = None,
    ):
        self._nodes = nodes
        self._members = members or []
        self._current_node_id = current_node_id

    async def execute(self, stmt: Any) -> _Result:
        entity = stmt.column_descriptions[0]["entity"]
        crit = stmt._where_criteria
        if entity is Projects:
            # get_active_group cursor read: returns a 1-tuple row, indexed [0].
            return _Result([(self._current_node_id,)])
        if entity is ProjectStageNodes:
            return _Result([n for n in self._nodes if _matches(n, crit)])
        if entity is ProjectStageNodeMembers:
            return _Result([m for m in self._members if _matches(m, crit)])
        # project_stage_node_deps lookup (empty in these fixtures)
        return _Result([])


def _read_scope_with(session: Any):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _install(monkeypatch, session: Any) -> None:
    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "read_scope", _read_scope_with(session))


# ── list_nodes_by_episode: one episode only ─────────────────────────────────


@pytest.mark.asyncio
async def test_list_nodes_by_episode_returns_only_that_episode(monkeypatch):
    ep1_node = _node(1, EP1)
    ep2_node = _node(2, EP2)
    _install(monkeypatch, _FilteringSession([ep1_node, ep2_node]))

    repo = ProjectStageNodesRepository()
    rows = await repo.list_nodes_by_episode(str(PID), str(EP1))

    ids = {r["id"] for r in rows}
    assert ids == {"1"}
    assert all(r["episode_id"] == str(EP1) for r in rows)
    assert "2" not in ids  # Ep2's template-cloned twin must not leak in


@pytest.mark.asyncio
async def test_list_nodes_still_returns_whole_project(monkeypatch):
    # Back-compat: the unfiltered listing keeps its project-wide scope.
    ep1_node = _node(1, EP1)
    ep2_node = _node(2, EP2)
    _install(monkeypatch, _FilteringSession([ep1_node, ep2_node]))

    repo = ProjectStageNodesRepository()
    rows = await repo.list_nodes(str(PID))

    assert {r["id"] for r in rows} == {"1", "2"}


# ── get_active_group: episode filter stops the cross-episode guard leak ──────


@pytest.mark.asyncio
async def test_get_active_group_excludes_other_episode_same_parallel_group(
    monkeypatch,
):
    # Cursor sits on Ep1's node. Ep2 has a node with the SAME parallel_group
    # ("draft"). The active group for Ep1 must contain only Ep1's node.
    ep1_node = _node(1, EP1)
    ep2_node = _node(2, EP2)
    session = _FilteringSession([ep1_node, ep2_node], current_node_id=1)
    _install(monkeypatch, session)

    repo = ProjectStageNodesRepository()
    group = await repo.get_active_group(str(PID), episode_id=str(EP1))

    ids = {n["id"] for n in group}
    assert ids == {"1"}
    assert "2" not in ids  # Ep2's identically-grouped twin must NOT be active


@pytest.mark.asyncio
async def test_get_active_group_without_episode_sees_both_twins(monkeypatch):
    # Proves the fixture genuinely exercises filtering: the SAME data, queried
    # WITHOUT an episode filter (legacy call), collapses both episodes' twins
    # into one active group -- i.e. the exact P0 bug T0 fixes, still reachable
    # through the back-compat signature.
    ep1_node = _node(1, EP1)
    ep2_node = _node(2, EP2)
    session = _FilteringSession([ep1_node, ep2_node], current_node_id=1)
    _install(monkeypatch, session)

    repo = ProjectStageNodesRepository()
    group = await repo.get_active_group(str(PID))

    assert {n["id"] for n in group} == {"1", "2"}
