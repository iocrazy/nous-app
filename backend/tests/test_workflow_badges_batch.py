"""Project-card workflow badge batch (M2-W3-3).

``workflow_badges_for_projects`` must compute the list-page badge (current node
name, position/total over non-skipped nodes, running-agent count) for every
project in a FIXED THREE queries — no N+1 as the project count grows. The ORM
session is faked to feed canned rows by call order and to count executes, so the
query budget is asserted directly without a database.
"""

from __future__ import annotations

import pytest

import app.repositories.project_stage_nodes_repository as mod
from app.repositories.project_stage_nodes_repository import (
    ProjectStageNodesRepository,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = results
        self.execute_count = 0

    async def execute(self, stmt):
        rows = self._results[self.execute_count]
        self.execute_count += 1
        return _FakeResult(rows)


class _FakeScope:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _install(monkeypatch, session):
    monkeypatch.setattr(mod, "read_scope", lambda: _FakeScope(session))


@pytest.mark.asyncio
async def test_badges_shape_and_position(monkeypatch):
    # 100: cursor on node 900; nodes 900(Script),901(Storyboard),902(skipped);
    #      2 running agents. 200: no cursor, one node, no agents.
    #      300: no nodes → absent from the badge map.
    # No project here is per-episode (episode cursor query returns nothing),
    # so every badge is sourced from the legacy projects.current_node_id
    # column — see test_badges_prefers_earliest_cursored_episode below for
    # the per-episode source.
    session = _FakeSession(
        [
            [(100, 900), (200, None), (300, None)],  # legacy cursors
            [],  # episode cursors (B6 T5) — none of these projects are per-episode
            [
                (100, 900, "Script", 0, False),
                (100, 901, "Storyboard", 1, False),
                (100, 902, "Voiceover", 2, True),  # skipped → not counted
                (200, 910, "Editing", 0, False),
            ],  # nodes
            [(100, 2)],  # running agent counts
        ]
    )
    _install(monkeypatch, session)

    out = await ProjectStageNodesRepository().workflow_badges_for_projects(
        [100, 200, 300]
    )

    assert set(out) == {"100", "200"}  # 300 has no workflow → no badge
    assert out["100"] == {
        "current_node_name": "Script",
        "workflow_total": 2,  # skipped node excluded
        "workflow_position": 1,
        "agents_active": 2,
    }
    assert out["200"] == {
        "current_node_name": None,  # no cursor
        "workflow_total": 1,
        "workflow_position": None,
        "agents_active": 0,  # no running agents
    }
    # No N+1: exactly four queries regardless of the three projects.
    assert session.execute_count == 4


@pytest.mark.asyncio
async def test_badges_prefers_earliest_cursored_episode_over_legacy_column(
    monkeypatch,
):
    """B6 T5 现网 bug: a per-episode project never writes
    ``projects.current_node_id`` (mig 402, B2/B3 write ``episodes.
    current_node_id`` instead), so that column alone renders a permanently
    empty badge. The badge cursor must fall back to the earliest
    (smallest sort_order) episode that actually HAS a cursor — a
    DISTINCT ON query the repo issues once for the whole batch (still no
    N+1), not per-project."""
    session = _FakeSession(
        [
            [(400, None)],  # legacy cursor: none — project 400 is per-episode
            # episode cursors: DISTINCT ON already resolved, per project, to
            # the smallest-sort_order episode that has a cursor set.
            [(400, 950)],
            [
                (400, 940, "Script", 0, False),
                (400, 950, "Storyboard", 1, False),
            ],  # nodes
            [],  # running agent counts
        ]
    )
    _install(monkeypatch, session)

    out = await ProjectStageNodesRepository().workflow_badges_for_projects([400])

    assert out["400"] == {
        "current_node_name": "Storyboard",
        "workflow_total": 2,
        "workflow_position": 2,
        "agents_active": 0,
    }
    assert session.execute_count == 4


@pytest.mark.asyncio
async def test_badges_query_count_is_constant_across_project_counts(monkeypatch):
    many = list(range(1, 51))  # 50 projects
    session = _FakeSession(
        [
            [(p, None) for p in many],
            [],
            [(p, p * 10, "Script", 0, False) for p in many],
            [],
        ]
    )
    _install(monkeypatch, session)
    out = await ProjectStageNodesRepository().workflow_badges_for_projects(many)
    assert len(out) == 50
    assert session.execute_count == 4  # still constant — no N+1


@pytest.mark.asyncio
async def test_badges_empty_input_short_circuits(monkeypatch):
    session = _FakeSession([])
    _install(monkeypatch, session)
    out = await ProjectStageNodesRepository().workflow_badges_for_projects([])
    assert out == {}
    assert session.execute_count == 0


@pytest.mark.asyncio
async def test_badges_never_raises_on_db_error(monkeypatch):
    class _BoomSession:
        execute_count = 0

        async def execute(self, stmt):
            raise RuntimeError("db down")

    _install(monkeypatch, _BoomSession())
    out = await ProjectStageNodesRepository().workflow_badges_for_projects([1, 2])
    assert out == {}  # enrichment must not sink the list
