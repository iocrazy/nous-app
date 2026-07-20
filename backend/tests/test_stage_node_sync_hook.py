"""issue -> project_stage_node status回流 hook (M1 PR-B B2).

``_fire_stage_node_sync`` projects a ``project_stage`` mirror issue's status
onto its ``project_stage_nodes.status`` after every transition (issue is the
execution fact source, node status is the projection — spec §6.2). These are
direct unit tests of the hook's decision logic, plus a wiring test proving
``IssueRepository.transition_status`` calls it post-write and survives its
failure (mirrors the instance-monkeypatch pattern in
``test_subissue_barrier_wiring.py`` — no DB needed).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.repositories import issue_repository as repo_mod
from app.services.library.project_stage_issues import build_stage_origin_id


class _FakeNodesRepo:
    def __init__(self, *, raise_error: bool = False):
        self.calls: List[Tuple[str, str]] = []
        self._raise = raise_error

    async def set_node_status(self, node_id, status):
        if self._raise:
            raise RuntimeError("boom")
        self.calls.append((node_id, status))
        return status


def _install_nodes_repo(monkeypatch, repo: _FakeNodesRepo) -> None:
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: repo,
    )


# ── status mapping, one case per edge ────────────────────────────────────────


@pytest.mark.parametrize(
    "issue_status,expected_node_status",
    [
        ("todo", "pending"),
        ("in_progress", "in_progress"),
        ("in_review", "in_review"),
        ("done", "done"),
        ("cancelled", "pending"),
    ],
)
@pytest.mark.asyncio
async def test_status_mapping(monkeypatch, issue_status, expected_node_status):
    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)
    origin_id = build_stage_origin_id("100", "9")
    issue = {"id": 1, "origin_kind": "project_stage", "origin_id": origin_id}

    await repo_mod._fire_stage_node_sync(issue, issue_status)

    assert nodes_repo.calls == [("9", expected_node_status)]


@pytest.mark.asyncio
async def test_unmapped_status_is_a_no_op(monkeypatch):
    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)
    origin_id = build_stage_origin_id("100", "9")
    issue = {"id": 1, "origin_kind": "project_stage", "origin_id": origin_id}

    # "blocked" / "needs_followup" / "backlog" carry no node-status projection.
    await repo_mod._fire_stage_node_sync(issue, "blocked")

    assert nodes_repo.calls == []


# ── scope: only NEW three-segment project_stage origins ─────────────────────


@pytest.mark.asyncio
async def test_old_two_segment_origin_skips_sync(monkeypatch):
    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)
    issue = {"id": 1, "origin_kind": "project_stage", "origin_id": "project_stage:55"}

    await repo_mod._fire_stage_node_sync(issue, "done")

    assert nodes_repo.calls == []


@pytest.mark.asyncio
async def test_non_project_stage_origin_never_triggers(monkeypatch):
    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)
    issue = {"id": 1, "origin_kind": "manual", "origin_id": "manual:1"}

    await repo_mod._fire_stage_node_sync(issue, "done")

    assert nodes_repo.calls == []


@pytest.mark.asyncio
async def test_missing_origin_id_is_a_no_op(monkeypatch):
    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)
    issue = {"id": 1, "origin_kind": "project_stage", "origin_id": None}

    await repo_mod._fire_stage_node_sync(issue, "done")

    assert nodes_repo.calls == []


@pytest.mark.asyncio
async def test_none_issue_is_a_no_op(monkeypatch):
    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)

    await repo_mod._fire_stage_node_sync(None, "done")

    assert nodes_repo.calls == []


# ── best-effort: hook failure never propagates ───────────────────────────────


@pytest.mark.asyncio
async def test_set_node_status_failure_is_swallowed(monkeypatch):
    nodes_repo = _FakeNodesRepo(raise_error=True)
    _install_nodes_repo(monkeypatch, nodes_repo)
    origin_id = build_stage_origin_id("100", "9")
    issue = {"id": 1, "origin_kind": "project_stage", "origin_id": origin_id}

    # Must not raise.
    await repo_mod._fire_stage_node_sync(issue, "done")


# ── wiring: transition_status fires the hook post-write, best-effort ────────


@pytest.mark.asyncio
async def test_transition_status_fires_stage_node_sync(monkeypatch):
    repo = repo_mod.IssueRepository()
    origin_id = build_stage_origin_id("100", "9")

    async def fake_get(issue_id):
        return {"id": issue_id, "status": "in_progress"}

    async def fake_update(issue_id, patch):
        return {
            "id": issue_id,
            "origin_kind": "project_stage",
            "origin_id": origin_id,
            **patch,
        }

    monkeypatch.setattr(repo, "get_by_id", fake_get)
    monkeypatch.setattr(repo, "update", fake_update)

    nodes_repo = _FakeNodesRepo()
    _install_nodes_repo(monkeypatch, nodes_repo)

    out = await repo.transition_status(42, "done")

    assert out["status"] == "done"
    assert nodes_repo.calls == [("9", "done")]


@pytest.mark.asyncio
async def test_transition_status_survives_sync_hook_failure(monkeypatch):
    repo = repo_mod.IssueRepository()
    origin_id = build_stage_origin_id("100", "9")

    async def fake_get(issue_id):
        return {"id": issue_id, "status": "in_review"}

    async def fake_update(issue_id, patch):
        return {
            "id": issue_id,
            "origin_kind": "project_stage",
            "origin_id": origin_id,
            **patch,
        }

    monkeypatch.setattr(repo, "get_by_id", fake_get)
    monkeypatch.setattr(repo, "update", fake_update)
    _install_nodes_repo(monkeypatch, _FakeNodesRepo(raise_error=True))

    # The transition still commits and returns despite the hook blowing up.
    out = await repo.transition_status(42, "done")
    assert out["status"] == "done"
