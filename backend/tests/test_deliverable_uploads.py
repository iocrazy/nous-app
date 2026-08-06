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


# ── ensure_node_folder: per-episode scoping (B2 T2) ─────────────────────────


class _StatefulProjectsRepo:
    """A projects repo whose ``get_folders`` reflects folders created via
    ``create_folder`` — needed to prove same-episode idempotent reuse (a second
    ensure call must name-match the folder the first one created, not mint a
    duplicate)."""

    def __init__(self):
        self._folders: List[Dict[str, Any]] = []
        self.created: List[Dict[str, Any]] = []
        self._next = 1

    async def get_folders(self, project_id):
        return list(self._folders)

    async def create_folder(self, data):
        row = {"id": str(1000 + self._next), **data}
        self._next += 1
        self._folders.append(row)
        self.created.append(data)
        return row


class _FakeEpisodeRepo:
    def __init__(self, titles: Optional[Dict[str, str]] = None):
        self._titles = titles or {}

    async def get_by_id(self, episode_id):
        title = self._titles.get(str(episode_id))
        return {"id": str(episode_id), "title": title} if title else None


def _patch_episode_repo(monkeypatch, episode_repo):
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: episode_repo,
    )


@pytest.mark.asyncio
async def test_episode_nodes_get_distinct_folders_across_episodes(monkeypatch):
    """Two episodes' same-named 'Script' node must land in DIFFERENT folders —
    the P0 trap② fix: without a per-episode prefix Ep2's Script would reuse
    Ep1's folder (case-insensitive name match) and gate2 would misfire."""
    projects_repo = _StatefulProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)
    _patch_episode_repo(
        monkeypatch, _FakeEpisodeRepo({"501": "Episode One", "502": "Episode Two"})
    )

    ep1 = {"id": "11", "name": "Script", "folder_id": None, "episode_id": "501"}
    ep2 = {"id": "21", "name": "Script", "folder_id": None, "episode_id": "502"}

    f1 = await node_folders.ensure_node_folder(_PROJECT, ep1, _USER)
    f2 = await node_folders.ensure_node_folder(_PROJECT, ep2, _USER)

    assert f1 is not None and f2 is not None
    assert f1 != f2  # per-episode isolation — no cross-episode reuse
    # Two distinct folders created, each carrying its episode's scoped name.
    assert len(projects_repo.created) == 2
    names = {c["name"] for c in projects_repo.created}
    assert len(names) == 2
    # Node name still present in each scoped folder name.
    assert all("Script" in n for n in names)


@pytest.mark.asyncio
async def test_same_episode_node_reuses_its_own_folder(monkeypatch):
    """Repeated ensure calls for the SAME episode's node hit the same folder —
    the scoped name used for CREATE must equal the one used for MATCH, else
    every call mints a duplicate."""
    projects_repo = _StatefulProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)
    _patch_episode_repo(monkeypatch, _FakeEpisodeRepo({"501": "Episode One"}))

    node = {"id": "11", "name": "Script", "folder_id": None, "episode_id": "501"}
    first = await node_folders.ensure_node_folder(_PROJECT, node, _USER)
    second = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert first == second
    assert len(projects_repo.created) == 1  # no duplicate on the second call


@pytest.mark.asyncio
async def test_episode_node_scoped_name_unique_even_for_duplicate_titles(monkeypatch):
    """Two episodes sharing a title still get distinct folders — uniqueness is
    anchored on episode_id, not the (user-editable, non-unique) title."""
    projects_repo = _StatefulProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)
    _patch_episode_repo(monkeypatch, _FakeEpisodeRepo({"501": "Pilot", "502": "Pilot"}))

    ep1 = {"id": "11", "name": "Script", "folder_id": None, "episode_id": "501"}
    ep2 = {"id": "21", "name": "Script", "folder_id": None, "episode_id": "502"}
    f1 = await node_folders.ensure_node_folder(_PROJECT, ep1, _USER)
    f2 = await node_folders.ensure_node_folder(_PROJECT, ep2, _USER)

    assert f1 != f2
    assert len(projects_repo.created) == 2


@pytest.mark.asyncio
async def test_episode_node_falls_back_to_id_when_title_unavailable(monkeypatch):
    """A missing/unfetchable episode title degrades to an episode_id-based
    scoped name (still unique + stable), never raising or blocking."""
    projects_repo = _StatefulProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)
    _patch_episode_repo(monkeypatch, _FakeEpisodeRepo({}))  # no titles

    node = {"id": "11", "name": "Script", "folder_id": None, "episode_id": "777"}
    folder = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert folder is not None
    name = projects_repo.created[0]["name"]
    assert "777" in name and "Script" in name


@pytest.mark.asyncio
async def test_legacy_none_episode_node_keeps_bare_node_name(monkeypatch):
    """Backward-compat: a node with episode_id=None (or absent) is filed under
    the bare node name, byte-for-byte as before — no prefix, no episode fetch."""
    projects_repo = _StatefulProjectsRepo()
    nodes_repo = _FakeNodesRepo()
    _patch_repos(monkeypatch, projects_repo=projects_repo, nodes_repo=nodes_repo)

    node = {"id": "1", "name": "Script", "folder_id": None, "episode_id": None}
    folder = await node_folders.ensure_node_folder(_PROJECT, node, _USER)

    assert folder is not None
    assert projects_repo.created[0]["name"] == "Script"  # exact, no prefix


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
