"""B2 T3 — the ``episode_id`` query param on the four workflow-cursor HTTP
entrypoints in ``projects_router``:

  * ``GET  /{project_id}/advance-preview``  (``get_advance_preview``)
  * ``POST /{project_id}/advance``          (``post_advance``)
  * ``GET  /{project_id}/workflow``         (``get_project_workflow``)
  * ``POST /{project_id}/workflow/nodes/{node_id}/start-early``
        (``start_workflow_node_early``)

``episode_id`` is optional everywhere and defaults to None. None reproduces
the legacy project-level behaviour byte-for-byte (regression); a given
``episode_id`` reads/writes that one episode only, never a sibling's
template-cloned twins (B2 陷阱①). These go through the REAL router functions
+ the REAL ``advance_service`` predicate end to end, faking only at the
repository-getter seam (mirrors ``test_start_early.py`` /
``test_workflow_flow_rules.py``'s pattern).
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service

_USER = "00000000-0000-0000-0000-000000000001"
_PROJECT = "100"
_EP1 = "9001"
_EP2 = "9002"


class _Auth:
    def __init__(self, user_id: str = _USER):
        self.user_id = user_id


def _node(
    node_id: str,
    *,
    sort_order: int,
    episode_id: Optional[str] = None,
    depends_on: Optional[List[str]] = None,
    status: str = "pending",
    skipped: bool = False,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "project_id": _PROJECT,
        "name": f"Node {node_id}",
        "sort_order": sort_order,
        "parallel_group": None,
        "status": status,
        "skipped": skipped,
        "owner_user_id": None,
        "owner_agent_id": None,
        "planned_due": None,
        "review_required": False,
        "deliverable_required": False,
        "folder_id": None,
        "depends_on": depends_on or [],
        "events": {"auto_start": False},
        "metadata": {},
        "episode_id": episode_id,
    }


class _FakeNodesRepo:
    """Serves both the legacy (``list_nodes``) and per-episode
    (``list_nodes_by_episode``) read paths from one node table keyed by the
    node's ``episode_id`` field."""

    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = [dict(n) for n in nodes]
        self.project_cursor_writes: List[tuple] = []

    async def list_nodes(self, project_id):
        return [dict(n) for n in self._nodes]

    async def list_nodes_by_episode(self, project_id, episode_id):
        return [
            dict(n) for n in self._nodes if str(n.get("episode_id")) == str(episode_id)
        ]

    async def get_node(self, node_id, project_id=None):
        for n in self._nodes:
            if str(n["id"]) == str(node_id):
                return dict(n)
        return None

    async def set_current_node_id(self, project_id, node_id):
        self.project_cursor_writes.append((str(project_id), str(node_id)))

    async def count_running_agent_runs(self, project_id):
        return 0

    async def set_node_metadata(self, node_id, patch):
        return dict(patch)


class _FakeProjectsRepo:
    def __init__(self, current_node_id: Optional[str] = None):
        self.current_node_id = current_node_id

    async def get_project_by_id(self, project_id):
        return {"current_node_id": self.current_node_id, "name": "Proj"}

    async def get_project_files(self, project_id):
        return []


class _FakeEpisodeRepo:
    def __init__(self, cursors: Dict[str, Optional[str]]):
        self.cursors = {str(k): v for k, v in cursors.items()}
        self.writes: List[tuple] = []

    async def get_by_id(self, episode_id):
        return {
            "id": str(episode_id),
            "current_node_id": self.cursors.get(str(episode_id)),
        }

    async def set_current_node_id(self, episode_id, node_id):
        self.cursors[str(episode_id)] = str(node_id)
        self.writes.append((str(episode_id), str(node_id)))


def _install(
    monkeypatch,
    *,
    nodes_repo: _FakeNodesRepo,
    projects_repo: Optional[_FakeProjectsRepo] = None,
    episode_repo: Optional[_FakeEpisodeRepo] = None,
    role: str = "manager",
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: projects_repo or _FakeProjectsRepo(),
    )
    if episode_repo is not None:
        monkeypatch.setattr(
            "app.repositories.episode_repository.get_episode_repository",
            lambda: episode_repo,
        )

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    # start-early does a LOCAL import from app.core.workflow_roles, so patching
    # the source module reaches it; advance_service binds the name at MODULE
    # level (top-of-file import), so its own already-bound name must be patched
    # too or the real DB-backed resolver runs.
    monkeypatch.setattr("app.core.workflow_roles.resolve_effective_role", _role)
    monkeypatch.setattr(advance_service, "resolve_effective_role", _role)

    # Success-path advisory warnings hit the issue repo — irrelevant to cursor
    # routing, stub to empty.
    async def _no_warnings(project_id, group):
        return []

    monkeypatch.setattr(advance_service, "_open_subissue_warnings", _no_warnings)


def _router():
    return importlib.import_module("app.api.projects_router")


# Two episodes, each a clean 2-node linear workflow (group0 -> group1).
def _two_episode_nodes() -> List[Dict[str, Any]]:
    return [
        _node("11", sort_order=1, episode_id=_EP1),
        _node("12", sort_order=2, episode_id=_EP1),
        _node("21", sort_order=1, episode_id=_EP2),
        _node("22", sort_order=2, episode_id=_EP2),
    ]


# ── (a) advance-preview / advance target ONE episode's cursor ────────────────


@pytest.mark.asyncio
async def test_advance_preview_scoped_to_episode(monkeypatch):
    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    episode_repo = _FakeEpisodeRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    preview = await _router().get_advance_preview(
        _PROJECT, _Auth(), direction="forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    # Closing ep1's group0 (node 11), creating ep1's group1 (node 12) — a
    # sibling episode's nodes (21/22) must never appear.
    assert [c.node_id for c in preview.closing] == ["11"]
    assert [c.node_id for c in preview.creating] == ["12"]


@pytest.mark.asyncio
async def test_execute_advance_moves_only_target_episode_cursor(monkeypatch):
    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    episode_repo = _FakeEpisodeRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    result = await _router().post_advance(
        _PROJECT, _Auth(), direction="forward", episode_id=_EP1
    )

    assert result["success"] is True
    # ep1 cursor moved 11 -> 12; ep2 cursor untouched; project column never
    # written (episode path writes episodes.current_node_id only).
    assert episode_repo.cursors[_EP1] == "12"
    assert episode_repo.cursors[_EP2] == "21"
    assert episode_repo.writes == [(_EP1, "12")]
    assert nodes_repo.project_cursor_writes == []


# ── (b) no episode_id → legacy project-level path unchanged ──────────────────


@pytest.mark.asyncio
async def test_advance_preview_legacy_project_path_when_no_episode(monkeypatch):
    # A clean single project-level workflow (episode_id=None nodes), cursor on
    # the project column.
    nodes_repo = _FakeNodesRepo([_node("1", sort_order=1), _node("2", sort_order=2)])
    projects_repo = _FakeProjectsRepo(current_node_id="1")

    def _boom_episode():
        raise AssertionError("legacy path must not touch the episode repo")

    _install(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository", _boom_episode
    )

    preview = await _router().get_advance_preview(
        _PROJECT, _Auth(), direction="forward", episode_id=None
    )

    assert preview.will_advance is True
    assert [c.node_id for c in preview.closing] == ["1"]
    assert [c.node_id for c in preview.creating] == ["2"]


@pytest.mark.asyncio
async def test_execute_advance_legacy_writes_project_cursor(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node("1", sort_order=1), _node("2", sort_order=2)])
    projects_repo = _FakeProjectsRepo(current_node_id="1")
    episode_repo = _FakeEpisodeRepo({})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        episode_repo=episode_repo,
    )

    result = await _router().post_advance(
        _PROJECT, _Auth(), direction="forward", episode_id=None
    )

    assert result["success"] is True
    # Legacy path writes the PROJECT cursor and never the episode cursor.
    assert nodes_repo.project_cursor_writes == [(_PROJECT, "2")]
    assert episode_repo.writes == []


# ── (c) GET /workflow scoped to one episode ──────────────────────────────────


@pytest.mark.asyncio
async def test_get_workflow_scoped_to_episode(monkeypatch):
    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    episode_repo = _FakeEpisodeRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    result = await _router().get_project_workflow(_PROJECT, _Auth(), episode_id=_EP1)

    assert result.has_workflow is True
    # Only ep1's nodes (11, 12) — never ep2's (21, 22).
    assert sorted(n.id for n in result.nodes) == ["11", "12"]
    # Cursor comes from the EPISODE, not the project column.
    assert result.current_node_id == "11"


@pytest.mark.asyncio
async def test_get_workflow_legacy_project_path_when_no_episode(monkeypatch):
    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    projects_repo = _FakeProjectsRepo(current_node_id="11")

    def _boom_episode():
        raise AssertionError("legacy workflow read must not touch the episode repo")

    _install(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository", _boom_episode
    )

    result = await _router().get_project_workflow(_PROJECT, _Auth(), episode_id=None)

    assert result.has_workflow is True
    # Legacy path returns ALL project nodes (every episode's) and the project
    # cursor — byte-for-byte the pre-B2 behaviour.
    assert sorted(n.id for n in result.nodes) == ["11", "12", "21", "22"]
    assert result.current_node_id == "11"


# ── start-early dependency map is episode-scoped ─────────────────────────────


@pytest.mark.asyncio
async def test_start_early_deps_scoped_to_episode(monkeypatch):
    """A node depending on a sibling-episode node id must NOT be gated by it:
    the per-episode dependency map never contains the sibling, so
    ``_unmet_dependency_names`` sees the dep as satisfied-by-absence (its
    cross-episode contamination is exactly what episode-scoping removes)."""
    # ep1 node "12" declares a dependency on "21" (an ep2 node). With the
    # episode-scoped map, "21" is not present → no block.
    nodes = _two_episode_nodes()
    nodes[1]["depends_on"] = ["21"]  # node 12 (ep1) depends on 21 (ep2)
    nodes_repo = _FakeNodesRepo(nodes)
    episode_repo = _FakeEpisodeRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    async def _noop(*a, **kw):
        return None

    # start_node_now side effects are out of scope — stub the whole call.
    node_start = importlib.import_module("app.services.workflow.node_start")

    async def _fake_start(project_id, node, *, actor_user_id=None, dispatch=False):
        return {"id": node["id"], "status": "in_progress"}

    monkeypatch.setattr(node_start, "start_node_now", _fake_start)
    monkeypatch.setattr(node_start, "_open_mirror_issue", _noop)

    result = await _router().start_workflow_node_early(
        _PROJECT, "12", _Auth(), episode_id=_EP1
    )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_start_early_legacy_deps_see_all_project_nodes(monkeypatch):
    """Without episode_id the dependency map is project-wide (legacy): a dep on
    a still-pending project node DOES gate the start-early (422 DEPS_PENDING)."""
    from fastapi import HTTPException

    nodes = _two_episode_nodes()
    nodes[1]["depends_on"] = ["21"]  # node 12 depends on 21 (still pending)
    nodes_repo = _FakeNodesRepo(nodes)
    _install(monkeypatch, nodes_repo=nodes_repo)

    async def _noop(*a, **kw):
        return None

    node_start = importlib.import_module("app.services.workflow.node_start")
    monkeypatch.setattr(node_start, "_open_mirror_issue", _noop)

    with pytest.raises(HTTPException) as exc:
        await _router().start_workflow_node_early(
            _PROJECT, "12", _Auth(), episode_id=None
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "DEPS_PENDING"
    # It's the sibling node 21 that gates it in the legacy project-wide map.
    assert exc.value.detail["waiting_on"] == ["Node 21"]
