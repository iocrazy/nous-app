"""Cross-episode dependencies (T1 data-layer edge + T2 Gate 5 judgment).

A node in a later episode may depend on a node in an EARLIER episode of the
same project (e.g. Ep2.storyboard depends on Ep1.script). The edge reuses the
existing ``project_stage_node_deps`` table verbatim — cross-episode-ness is
DERIVED from the two endpoints living in different episodes, so no new table /
column is introduced.

Two layers under test:
  1. ``ProjectStageNodesRepository.add_cross_episode_dep`` (T1) — validates
     (same project / both episode-scoped / strictly-earlier EPISODE
     sort_order / no self-ref) and idempotently inserts. Fake-session
     plumbing (house pattern per test_workflow_deps.py) — real statement
     construction + add runs, only SQL execution is faked.
  2. ``advance_service`` Gate 5 (T2) — a cross-episode dependency target lives
     OUTSIDE the advancing episode's ``list_nodes_by_episode`` node set, so the
     within-episode ``node_by_id`` cannot resolve it. Gate 5 supplements with a
     precise id-only point lookup (``get_node_statuses_by_ids``) used ONLY for
     the done/skipped judgment — never fed into groups / cursor math (三重护栏).
     A project with NO cross-episode edge never triggers the lookup and behaves
     byte-for-byte as before.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service
from app.models import ProjectStageNodeDeps
from app.repositories.project_stage_nodes_repository import ProjectStageNodesRepository
from app.schemas.workflow import BLOCK_DEPS_PENDING, CrossEpisodeDepInvalid

# ── shared fake-session plumbing (house pattern) ────────────────────────────


class _Result:
    """Wraps a canned row list for both ``.scalars().all()/.first()`` and the
    bare ``.all()`` access patterns."""

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


# ── T1: add_cross_episode_dep ───────────────────────────────────────────────


class _XepFakeSession:
    """Drives ``add_cross_episode_dep``'s call order:
      1. two-node fetch  (id, project_id, episode_id)
      2. two-episode sort_order fetch  (id, sort_order)   [only if (1) passes]
      3. existing-edge check  [only if (2) passes]
    Some validations raise before reaching (2) / (3); the fake simply never
    receives those calls.
    """

    def __init__(
        self,
        node_rows: List[tuple],
        episode_rows: Optional[List[tuple]] = None,
        existing_edges: Optional[List[Any]] = None,
    ):
        self._node_rows = node_rows
        self._episode_rows = episode_rows or []
        self._existing = existing_edges or []
        self.added: List[Any] = []
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: Any) -> _Result:
        self._calls += 1
        if self._calls == 1:
            return _Result(self._node_rows)
        if self._calls == 2:
            return _Result(self._episode_rows)
        return _Result(self._existing)


def _install_repo(monkeypatch, session: Any) -> ProjectStageNodesRepository:
    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))
    return ProjectStageNodesRepository()


@pytest.mark.asyncio
async def test_add_cross_episode_dep_forward_edge_accepted_and_written(monkeypatch):
    """Ep2.storyboard (node 200, episode sort 2) depends on Ep1.script (node
    100, episode sort 1) — a strictly-earlier-episode edge, accepted."""
    session = _XepFakeSession(
        node_rows=[(200, 50, 8002), (100, 50, 8001)],
        episode_rows=[(8002, 2), (8001, 1)],
        existing_edges=[],
    )
    repo = _install_repo(monkeypatch, session)

    out = await repo.add_cross_episode_dep("200", "100")

    assert out == {"node_id": "200", "depends_on_node_id": "100"}
    written = [o for o in session.added if isinstance(o, ProjectStageNodeDeps)]
    assert len(written) == 1
    assert written[0].node_id == 200
    assert written[0].depends_on_node_id == 100


@pytest.mark.asyncio
async def test_add_cross_episode_dep_later_episode_rejected(monkeypatch):
    """Ep2 (sort 2) depending on Ep3 (sort 3) is a forward/later edge — the
    cross-episode analogue of a forward intra-episode dep — rejected."""
    session = _XepFakeSession(
        node_rows=[(200, 50, 8002), (300, 50, 8003)],
        episode_rows=[(8002, 2), (8003, 3)],
    )
    repo = _install_repo(monkeypatch, session)

    with pytest.raises(CrossEpisodeDepInvalid):
        await repo.add_cross_episode_dep("200", "300")
    assert session.added == []


@pytest.mark.asyncio
async def test_add_cross_episode_dep_same_episode_rejected(monkeypatch):
    """Two nodes in the SAME episode must go through update_node's
    intra-episode backward-only path, never this cross-episode entry."""
    session = _XepFakeSession(node_rows=[(200, 50, 8001), (100, 50, 8001)])
    repo = _install_repo(monkeypatch, session)

    with pytest.raises(CrossEpisodeDepInvalid):
        await repo.add_cross_episode_dep("200", "100")
    assert session.added == []


@pytest.mark.asyncio
async def test_add_cross_episode_dep_cross_project_rejected(monkeypatch):
    """The two endpoints belong to different projects — rejected."""
    session = _XepFakeSession(node_rows=[(200, 50, 8002), (100, 99, 8001)])
    repo = _install_repo(monkeypatch, session)

    with pytest.raises(CrossEpisodeDepInvalid):
        await repo.add_cross_episode_dep("200", "100")
    assert session.added == []


@pytest.mark.asyncio
async def test_add_cross_episode_dep_self_reference_rejected(monkeypatch):
    """A node depending on itself never touches the DB — raised up front."""
    session = _XepFakeSession(node_rows=[])
    repo = _install_repo(monkeypatch, session)

    with pytest.raises(CrossEpisodeDepInvalid):
        await repo.add_cross_episode_dep("200", "200")
    assert session.added == []


@pytest.mark.asyncio
async def test_add_cross_episode_dep_non_episode_node_rejected(monkeypatch):
    """A legacy project-level node (episode_id NULL) can't carry a
    cross-episode edge — rejected before any episode sort_order lookup."""
    session = _XepFakeSession(node_rows=[(200, 50, 8002), (100, 50, None)])
    repo = _install_repo(monkeypatch, session)

    with pytest.raises(CrossEpisodeDepInvalid):
        await repo.add_cross_episode_dep("200", "100")
    assert session.added == []


@pytest.mark.asyncio
async def test_add_cross_episode_dep_missing_node_rejected(monkeypatch):
    """A depends_on target that doesn't exist — only one row comes back."""
    session = _XepFakeSession(node_rows=[(200, 50, 8002)])
    repo = _install_repo(monkeypatch, session)

    with pytest.raises(CrossEpisodeDepInvalid):
        await repo.add_cross_episode_dep("200", "100")
    assert session.added == []


@pytest.mark.asyncio
async def test_add_cross_episode_dep_is_idempotent(monkeypatch):
    """A duplicate edge (already present) is a silent no-op, never an error
    and never a second row."""
    session = _XepFakeSession(
        node_rows=[(200, 50, 8002), (100, 50, 8001)],
        episode_rows=[(8002, 2), (8001, 1)],
        existing_edges=[ProjectStageNodeDeps(node_id=200, depends_on_node_id=100)],
    )
    repo = _install_repo(monkeypatch, session)

    out = await repo.add_cross_episode_dep("200", "100")

    assert out == {"node_id": "200", "depends_on_node_id": "100"}
    # Nothing NEW added — the existing edge already covers it.
    assert [o for o in session.added if isinstance(o, ProjectStageNodeDeps)] == []


# ── T2: Gate 5 with a cross-episode dependency target ───────────────────────


def _node(
    node_id: str,
    *,
    sort_order: int,
    episode_id: str,
    status: str = "pending",
    parallel_group: Optional[Any] = None,
    skipped: bool = False,
    depends_on: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name or f"Node {node_id}",
        "sort_order": sort_order,
        "parallel_group": parallel_group,
        "review_required": False,
        "deliverable_required": False,
        "skipped": skipped,
        "status": status,
        "owner_user_id": None,
        "owner_agent_id": None,
        "planned_due": None,
        "folder_id": None,
        "form_schema": [],
        "form_data": {},
        "episode_id": episode_id,
        "depends_on": depends_on or [],
    }


class _FakeNodesRepo:
    """Episode-scoped node store. ``get_node_statuses_by_ids`` mirrors the real
    repo's project-scoped id-only point lookup — it can see EVERY node in the
    project (both episodes), but returns ONLY the stripped gate fields, exactly
    as the real method does."""

    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = nodes
        self.current_node_id_calls: List[Any] = []
        self.status_lookup_calls: List[Any] = []

    async def list_nodes(self, project_id):
        return list(self._nodes)

    async def list_nodes_by_episode(self, project_id, episode_id):
        return [n for n in self._nodes if str(n.get("episode_id")) == str(episode_id)]

    async def set_current_node_id(self, project_id, node_id):
        self.current_node_id_calls.append((project_id, node_id))

    async def list_folder_files(self, folder_id):
        return []

    async def get_node_statuses_by_ids(self, project_id, node_ids):
        self.status_lookup_calls.append((project_id, list(node_ids)))
        wanted = {str(x) for x in node_ids}
        out: Dict[str, Dict[str, Any]] = {}
        for n in self._nodes:
            if str(n["id"]) in wanted:
                out[str(n["id"])] = {
                    "status": n.get("status"),
                    "skipped": n.get("skipped"),
                    "name": n.get("name"),
                    "episode_id": n.get("episode_id"),
                }
        return out


class _FakeEpisodesRepo:
    def __init__(self, cursors: Dict[str, Optional[str]]):
        self._cursors = {str(k): v for k, v in cursors.items()}
        self.set_current_node_id_calls: List[Any] = []

    async def get_by_id(self, episode_id):
        return {"current_node_id": self._cursors.get(str(episode_id))}

    async def set_current_node_id(self, episode_id, node_id):
        self.set_current_node_id_calls.append((str(episode_id), node_id))
        self._cursors[str(episode_id)] = node_id


class _FakeProjectsRepo:
    async def get_project_by_id(self, pid):
        return {"current_node_id": None, "name": "Test Project", "team_id": None}

    async def get_folders(self, project_id):
        return []

    async def get_project_files(self, project_id):
        return []


class _FakeIssueRepo:
    async def list_by_origin(self, kind, origin_id):
        return []

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        return {"id": issue_id, "status": new_status}

    async def list_children(self, issue_id):
        return []


def _install(
    monkeypatch,
    *,
    nodes_repo: _FakeNodesRepo,
    episodes_repo: _FakeEpisodesRepo,
    role: str = "manager",
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: episodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: _FakeProjectsRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.issue_repository.get_issue_repository",
        lambda: _FakeIssueRepo(),
    )

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr(advance_service, "resolve_effective_role", _role)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_issues", _noop)
    monkeypatch.setattr(advance_service, "ensure_node_folders", _noop)
    monkeypatch.setattr(advance_service, "notify_stage_event", _noop)


_PROJECT = "100"
_USER = "user-1"
_EP1 = "8001"
_EP2 = "8002"


def _two_episode_nodes(ep1_script_status: str, *, ep1_skipped: bool = False):
    """Ep1: script(11) + review(12). Ep2: draft(21, cursor) + storyboard(22)
    that CROSS-EPISODE depends on Ep1's script(11)."""
    ep1_script = _node(
        "11",
        sort_order=1,
        episode_id=_EP1,
        status=ep1_script_status,
        skipped=ep1_skipped,
        name="Ep1 Script",
    )
    ep1_next = _node("12", sort_order=2, episode_id=_EP1, name="Ep1 Review")
    ep2_draft = _node("21", sort_order=1, episode_id=_EP2, name="Ep2 Draft")
    ep2_board = _node(
        "22",
        sort_order=2,
        episode_id=_EP2,
        depends_on=["11"],  # cross-episode edge → Ep1 script
        name="Ep2 Storyboard",
    )
    return [ep1_script, ep1_next, ep2_draft, ep2_board]


@pytest.mark.asyncio
async def test_ep2_blocked_when_cross_episode_dependency_not_done(monkeypatch):
    """Ep2.storyboard depends on Ep1.script; Ep1.script still pending → Ep2
    advance BLOCK_DEPS_PENDING, with the Ep1 node name in waiting_on."""
    nodes_repo = _FakeNodesRepo(_two_episode_nodes("pending"))
    episodes_repo = _FakeEpisodesRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episodes_repo=episodes_repo)

    preview = await advance_service.compute_advance_preview(
        _PROJECT, _USER, "forward", episode_id=_EP2
    )

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_DEPS_PENDING
    assert preview.waiting_on == ["Ep1 Script"]
    # The cross-episode target WAS resolved via the precise point lookup.
    assert nodes_repo.status_lookup_calls == [(_PROJECT, ["11"])]


@pytest.mark.asyncio
async def test_ep2_allowed_when_cross_episode_dependency_done(monkeypatch):
    """Ep1.script done → the cross-episode gate clears, Ep2 advances."""
    nodes_repo = _FakeNodesRepo(_two_episode_nodes("done"))
    episodes_repo = _FakeEpisodesRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episodes_repo=episodes_repo)

    preview = await advance_service.compute_advance_preview(
        _PROJECT, _USER, "forward", episode_id=_EP2
    )

    assert preview.will_advance is True
    assert preview.waiting_on == []
    assert preview.creating[0].node_id == "22"


@pytest.mark.asyncio
async def test_ep2_allowed_when_cross_episode_dependency_skipped(monkeypatch):
    """A skipped Ep1 target satisfies the cross-episode gate (skipped counts
    as done, spec §3) even though its status never reached done."""
    nodes_repo = _FakeNodesRepo(_two_episode_nodes("pending", ep1_skipped=True))
    episodes_repo = _FakeEpisodesRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episodes_repo=episodes_repo)

    preview = await advance_service.compute_advance_preview(
        _PROJECT, _USER, "forward", episode_id=_EP2
    )

    assert preview.will_advance is True
    assert preview.waiting_on == []


@pytest.mark.asyncio
async def test_ep2_execute_blocked_does_not_move_cursor(monkeypatch):
    """execute_advance honours the same gate — a blocked cross-episode dep
    mutates nothing (no episode cursor write)."""
    nodes_repo = _FakeNodesRepo(_two_episode_nodes("pending"))
    episodes_repo = _FakeEpisodesRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episodes_repo=episodes_repo)

    result = await advance_service.execute_advance(
        _PROJECT, _USER, "forward", episode_id=_EP2
    )

    assert result.will_advance is False
    assert result.blocked_reason == BLOCK_DEPS_PENDING
    assert episodes_repo.set_current_node_id_calls == []
    assert nodes_repo.current_node_id_calls == []


@pytest.mark.asyncio
async def test_no_cross_episode_edge_never_triggers_point_lookup(monkeypatch):
    """三重护栏③ regression: an episode whose next_group has NO out-of-episode
    dependency never calls get_node_statuses_by_ids — behaviour is identical
    to the pre-cross-episode path."""
    # Ep2 nodes carry NO cross-episode edge (22 has no depends_on).
    ep2_draft = _node("21", sort_order=1, episode_id=_EP2)
    ep2_board = _node("22", sort_order=2, episode_id=_EP2)
    ep1_a = _node("11", sort_order=1, episode_id=_EP1)
    nodes_repo = _FakeNodesRepo([ep1_a, ep2_draft, ep2_board])
    episodes_repo = _FakeEpisodesRepo({_EP1: "11", _EP2: "21"})
    _install(monkeypatch, nodes_repo=nodes_repo, episodes_repo=episodes_repo)

    preview = await advance_service.compute_advance_preview(
        _PROJECT, _USER, "forward", episode_id=_EP2
    )

    assert preview.will_advance is True
    assert nodes_repo.status_lookup_calls == []  # never consulted
