"""Lazy stage-folder materialization + issue-side deliverable routing (M2-W1).

Repo/issue layers are faked (the seam pattern from ``test_advance_predicate``),
so every branch runs without a database.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.deliverable_uploads as deliverable_uploads
import app.services.workflow.node_folders as node_folders

_PROJECT = "100"
_USER = "00000000-0000-0000-0000-000000000001"


class _FakeProjectsRepo:
    def __init__(
        self, folders: Optional[List[Dict[str, Any]]] = None, *, raise_on_folders=False
    ):
        self._folders = folders or []
        self.created: List[Dict[str, Any]] = []
        self._raise = raise_on_folders

    async def get_folders(self, project_id):
        if self._raise:
            raise RuntimeError("boom")
        return self._folders

    async def create_folder(self, data):
        row = {"id": "999", **data}
        self.created.append(data)
        return row


class _FakeNodesRepo:
    def __init__(self, node: Optional[Dict[str, Any]] = None):
        self._node = node
        self.folder_backfills: List[Any] = []

    async def set_node_folder_id(self, node_id, folder_id):
        self.folder_backfills.append((node_id, folder_id))
        return folder_id

    async def get_node(self, node_id, project_id=None):
        return self._node


class _FakeIssueRepo:
    def __init__(self, issue: Optional[Dict[str, Any]] = None):
        self._issue = issue

    async def get_by_id(self, issue_id):
        return self._issue


def _patch_repos(monkeypatch, *, projects_repo=None, nodes_repo=None, issue_repo=None):
    if projects_repo is not None:
        monkeypatch.setattr(
            "app.repositories.projects_repository.get_projects_repository",
            lambda: projects_repo,
        )
    if nodes_repo is not None:
        monkeypatch.setattr(
            "app.repositories.project_stage_nodes_repository."
            "get_project_stage_nodes_repository",
            lambda: nodes_repo,
        )
    if issue_repo is not None:
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issue_repo,
        )


# ── ensure_node_folder ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_folder_returns_existing_without_touching_store(monkeypatch):
    projects_repo = _FakeProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)

    node = {"id": "1", "name": "Script", "folder_id": "42"}
    result = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert result == "42"
    assert projects_repo.created == []
    assert nodes_repo.folder_backfills == []


@pytest.mark.asyncio
async def test_ensure_folder_reuses_name_matched_folder(monkeypatch):
    projects_repo = _FakeProjectsRepo(folders=[{"id": "77", "name": "script"}])
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)

    node = {"id": "1", "name": "Script", "folder_id": None}
    result = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert result == "77"  # case-insensitive reuse, no new folder
    assert projects_repo.created == []
    assert nodes_repo.folder_backfills == [("1", "77")]


@pytest.mark.asyncio
async def test_ensure_folder_creates_and_backfills(monkeypatch):
    projects_repo = _FakeProjectsRepo(folders=[])
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)

    node = {"id": "1", "name": "Storyboard", "folder_id": None}
    result = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert result == "999"
    assert projects_repo.created[0]["name"] == "Storyboard"
    assert nodes_repo.folder_backfills == [("1", "999")]


@pytest.mark.asyncio
async def test_ensure_folder_best_effort_none_on_error(monkeypatch):
    projects_repo = _FakeProjectsRepo(raise_on_folders=True)
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)

    node = {"id": "1", "name": "Script", "folder_id": None}
    result = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert result is None  # swallowed, never raised


@pytest.mark.asyncio
async def test_ensure_folder_skips_nameless_node(monkeypatch):
    projects_repo = _FakeProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)

    result = await node_folders.ensure_node_folder(
        _PROJECT, {"id": "1", "name": "", "folder_id": None}, _USER
    )
    assert result is None
    assert projects_repo.created == []


# ── resolve_deliverable_folder ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_folder_for_stage_mirror(monkeypatch):
    issue_repo = _FakeIssueRepo(
        {
            "id": 501,
            "origin_kind": "project_stage",
            "origin_id": f"project_stage:{_PROJECT}:1",
        }
    )
    nodes_repo = _FakeNodesRepo(node={"id": "1", "name": "Script", "folder_id": None})
    _patch_repos(monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo)

    async def _ensure(project_id, node, user_id):
        return "888"

    monkeypatch.setattr(node_folders, "ensure_node_folder", _ensure)

    folder = await deliverable_uploads.resolve_deliverable_folder(
        _PROJECT, "501", _USER
    )
    assert folder == "888"


@pytest.mark.asyncio
async def test_resolve_folder_non_mirror_issue_is_root(monkeypatch):
    issue_repo = _FakeIssueRepo({"id": 501, "origin_kind": "manual", "origin_id": "x"})
    _patch_repos(monkeypatch, issue_repo=issue_repo)

    folder = await deliverable_uploads.resolve_deliverable_folder(
        _PROJECT, "501", _USER
    )
    assert folder is None


@pytest.mark.asyncio
async def test_resolve_folder_wrong_project_is_root(monkeypatch):
    # Origin names a DIFFERENT project → no node folder for this project.
    issue_repo = _FakeIssueRepo(
        {
            "id": 501,
            "origin_kind": "project_stage",
            "origin_id": "project_stage:999:1",
        }
    )
    _patch_repos(monkeypatch, issue_repo=issue_repo)

    folder = await deliverable_uploads.resolve_deliverable_folder(
        _PROJECT, "501", _USER
    )
    assert folder is None


@pytest.mark.asyncio
async def test_resolve_folder_legacy_two_segment_origin_is_root(monkeypatch):
    # Old two-segment origin (project_stage:{stage_id}) has no project scope.
    issue_repo = _FakeIssueRepo(
        {"id": 501, "origin_kind": "project_stage", "origin_id": "project_stage:1"}
    )
    _patch_repos(monkeypatch, issue_repo=issue_repo)

    folder = await deliverable_uploads.resolve_deliverable_folder(
        _PROJECT, "501", _USER
    )
    assert folder is None
