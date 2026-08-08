"""B2 T3 — the ``episode_id`` query param on the four workflow-cursor HTTP
entrypoints in ``projects_router``:

  * ``GET  /{project_id}/advance-preview``  (``get_advance_preview``)
  * ``POST /{project_id}/advance``          (``post_advance``)
  * ``GET  /{project_id}/workflow``         (``get_project_workflow``)
  * ``POST /{project_id}/workflow/nodes/{node_id}/start-early``
        (``start_workflow_node_early``)

``episode_id`` reads/writes that one episode only, never a sibling's
template-cloned twins (B2 陷阱①). These go through the REAL router functions
+ the REAL ``advance_service`` predicate end to end, faking only at the
repository-getter seam (mirrors ``test_start_early.py`` /
``test_workflow_flow_rules.py``'s pattern). The router's own ``episode_id``
query param is still ``Optional`` at this layer (Task 8 makes it required) —
that transitional surface is not this file's concern; every test here passes
a real episode_id.
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
    """Serves the per-episode (``list_nodes_by_episode``) read path from one
    node table keyed by the node's ``episode_id`` field."""

    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = [dict(n) for n in nodes]

    async def list_nodes_by_episode(self, project_id, episode_id):
        return [
            dict(n) for n in self._nodes if str(n.get("episode_id")) == str(episode_id)
        ]

    async def get_node(self, node_id, project_id=None):
        for n in self._nodes:
            if str(n["id"]) == str(node_id):
                return dict(n)
        return None

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
        # Which project each episode belongs to — the ownership guard
        # (``_require_project_episode``) compares this against the path project.
        # Defaults every known episode to _PROJECT unless overridden.
        self.project_of = {str(k): _PROJECT for k in self.cursors}

    async def get_by_id(self, episode_id):
        if str(episode_id) not in self.cursors:
            return None
        return {
            "id": str(episode_id),
            "project_id": self.project_of.get(str(episode_id), _PROJECT),
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
    # ep1 cursor moved 11 -> 12; ep2 cursor untouched.
    assert episode_repo.cursors[_EP1] == "12"
    assert episode_repo.cursors[_EP2] == "21"
    assert episode_repo.writes == [(_EP1, "12")]


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


# ── review Important: mismatched episode_id must not bypass the dep gate ──────


@pytest.mark.asyncio
async def test_start_early_mismatched_episode_id_422_no_bypass(monkeypatch):
    """The dependency-gate bypass this fix closes: node 12 (ep1) depends on the
    still-pending node 11 (ep1). Passing a MISMATCHED episode_id (ep2) used to
    make ``list_nodes_by_episode(ep2)`` return a set without 11, so the unmet
    dep read as satisfied-by-absence and the node started anyway. Now a query
    episode_id that != the node's own episode is rejected 422 BEFORE any start —
    the node is never dispatched."""
    from fastapi import HTTPException

    nodes = _two_episode_nodes()
    nodes[1]["depends_on"] = ["11"]  # node 12 (ep1) depends on 11 (ep1, pending)
    nodes_repo = _FakeNodesRepo(nodes)
    episode_repo = _FakeEpisodeRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    async def _noop(*a, **kw):
        return None

    node_start = importlib.import_module("app.services.workflow.node_start")

    async def _boom_start(*a, **kw):
        raise AssertionError("a dependency-blocked node must never be started")

    monkeypatch.setattr(node_start, "start_node_now", _boom_start)
    monkeypatch.setattr(node_start, "_open_mirror_issue", _noop)

    with pytest.raises(HTTPException) as exc:
        await _router().start_workflow_node_early(
            _PROJECT, "12", _Auth(), episode_id=_EP2  # wrong episode
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "EPISODE_MISMATCH"


@pytest.mark.asyncio
async def test_start_early_correct_episode_gates_unmet_same_episode_dep(monkeypatch):
    """With the CORRECT episode_id, the node's own-episode dependency IS seen:
    node 12 (ep1) depends on pending node 11 (ep1) → DEPS_PENDING, not started."""
    from fastapi import HTTPException

    nodes = _two_episode_nodes()
    nodes[1]["depends_on"] = ["11"]
    nodes_repo = _FakeNodesRepo(nodes)
    episode_repo = _FakeEpisodeRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    async def _noop(*a, **kw):
        return None

    node_start = importlib.import_module("app.services.workflow.node_start")

    async def _boom_start(*a, **kw):
        raise AssertionError("a dependency-blocked node must never be started")

    monkeypatch.setattr(node_start, "start_node_now", _boom_start)
    monkeypatch.setattr(node_start, "_open_mirror_issue", _noop)

    with pytest.raises(HTTPException) as exc:
        await _router().start_workflow_node_early(
            _PROJECT, "12", _Auth(), episode_id=_EP1
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "DEPS_PENDING"
    assert exc.value.detail["waiting_on"] == ["Node 11"]


# ── review Minor: episode_id ownership — a foreign project's episode 404s ─────


@pytest.mark.asyncio
async def test_workflow_foreign_episode_404_no_cursor_echo(monkeypatch):
    """GET /workflow?episode_id=<other project's ep> must 404 — never confirm
    the foreign episode's existence nor echo its current_node_id."""
    from fastapi import HTTPException

    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    episode_repo = _FakeEpisodeRepo({_EP1: "11"})
    # _EP1 actually belongs to a DIFFERENT project.
    episode_repo.project_of[_EP1] = "999"
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    with pytest.raises(HTTPException) as exc:
        await _router().get_project_workflow(_PROJECT, _Auth(), episode_id=_EP1)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_advance_preview_foreign_episode_404(monkeypatch):
    from fastapi import HTTPException

    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    episode_repo = _FakeEpisodeRepo({_EP1: "11"})
    episode_repo.project_of[_EP1] = "999"
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    # The predicate service must never run for a foreign episode.
    def _boom(*a, **kw):
        raise AssertionError("compute_advance_preview must not run for a foreign ep")

    monkeypatch.setattr(advance_service, "compute_advance_preview", _boom)

    with pytest.raises(HTTPException) as exc:
        await _router().get_advance_preview(
            _PROJECT, _Auth(), direction="forward", episode_id=_EP1
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_advance_foreign_episode_404(monkeypatch):
    from fastapi import HTTPException

    nodes_repo = _FakeNodesRepo(_two_episode_nodes())
    episode_repo = _FakeEpisodeRepo({_EP1: "11"})
    episode_repo.project_of[_EP1] = "999"
    _install(monkeypatch, nodes_repo=nodes_repo, episode_repo=episode_repo)

    def _boom(*a, **kw):
        raise AssertionError("execute_advance must not run for a foreign ep")

    monkeypatch.setattr(advance_service, "execute_advance", _boom)

    with pytest.raises(HTTPException) as exc:
        await _router().post_advance(
            _PROJECT, _Auth(), direction="forward", episode_id=_EP1
        )

    assert exc.value.status_code == 404
