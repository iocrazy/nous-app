"""autopilot_tick — the M4 Autopilot engine (task O2, design spec §2).

Two layers under test, mirroring ``test_stage_hook.py``'s split:
  * ``_autopilot_tick_impl`` / ``_auto_start_pass`` with ``start_node_now``
    mocked as a spy — proves the engine's OWN decision logic (which
    candidates get selected, dispatch vs. prepare, quota accounting) without
    depending on ``node_start``'s own internals (covered by
    ``test_start_early.py`` instead, via the real function).
  * ``_cascade_pass`` against the REAL ``execute_advance`` (fakes only at the
    repo-getter seam, exactly ``test_advance_predicate.py``'s ``_install``
    pattern) — proves the review gate is NEVER crossed and cascade-blocked
    notifies exactly once, end to end through the real predicate rather than
    trusting a mock.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service
import app.workflows.autopilot as autopilot

pytestmark = pytest.mark.asyncio

_PROJECT = "100"
_OWNER = "00000000-0000-0000-0000-0000000000aa"
_AGENT = "00000000-0000-0000-0000-0000000000bb"


def _node(
    node_id: str,
    *,
    sort_order: int = 1,
    status: str = "pending",
    skipped: bool = False,
    auto_start: bool = True,
    owner_agent_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    depends_on: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    review_required: bool = False,
    parallel_group: Optional[int] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name or f"Node {node_id}",
        "sort_order": sort_order,
        "parallel_group": parallel_group,
        "status": status,
        "skipped": skipped,
        "owner_user_id": owner_user_id,
        "owner_agent_id": owner_agent_id,
        "members": [],
        "events": {"auto_start": auto_start},
        "metadata": metadata or {},
        "depends_on": depends_on or [],
        "review_required": review_required,
        "deliverable_required": False,
        "folder_id": None,
        "planned_due": None,
    }


class _FakeProjectsRepo:
    def __init__(
        self,
        *,
        autopilot_enabled: bool = True,
        current_node_id: Optional[str] = None,
        owner_id: Optional[str] = _OWNER,
        name: str = "Proj",
        team_id: Optional[int] = None,
    ):
        self._row = {
            "autopilot_enabled": autopilot_enabled,
            "current_node_id": current_node_id,
            "owner_id": owner_id,
            "name": name,
            "team_id": team_id,
        }
        self.get_calls = 0

    async def get_project_by_id(self, pid):
        self.get_calls += 1
        return dict(self._row)


class _FakeNodesRepo:
    def __init__(
        self,
        nodes: List[Dict[str, Any]],
        *,
        projects_repo: Optional["_FakeProjectsRepo"] = None,
    ):
        self._nodes = nodes
        # Wired to the SAME projects fake (when given) so
        # set_current_node_id actually moves the cursor the next
        # get_project_by_id() read sees — real behavior (both write/read the
        # `projects.current_node_id` column); without this a cascade's
        # SECOND compute_advance_preview would re-read a stale cursor.
        self._projects_repo = projects_repo
        self.list_nodes_calls = 0
        self.metadata_patches: List[Any] = []
        self.current_node_id_calls: List[Any] = []

    async def list_nodes(self, project_id):
        self.list_nodes_calls += 1
        return list(self._nodes)

    async def get_node(self, node_id, project_id=None):
        for n in self._nodes:
            if str(n["id"]) == str(node_id):
                return dict(n)
        return None

    async def set_node_metadata(self, node_id, patch):
        self.metadata_patches.append((str(node_id), dict(patch)))
        for n in self._nodes:
            if str(n["id"]) == str(node_id):
                merged = dict(n.get("metadata") or {})
                merged.update(patch)
                n["metadata"] = merged
                return dict(merged)
        return None

    async def set_current_node_id(self, project_id, node_id):
        self.current_node_id_calls.append((project_id, node_id))
        if self._projects_repo is not None:
            self._projects_repo._row["current_node_id"] = node_id

    async def list_folder_files(self, folder_id):
        return []


class _FakeIssueRepo:
    def __init__(self, by_node: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self._by_node = by_node or {}
        self.transitions: List[Any] = []

    async def list_by_origin(self, kind, origin_id):
        node_id = origin_id.rsplit(":", 1)[-1]
        return list(self._by_node.get(node_id, []))

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {"id": issue_id, "status": new_status}

    async def list_children(self, issue_id):
        return []


class _FakeAgentRunsRepo:
    def __init__(self, count: int = 0):
        self.count = count

    async def count_auto_dispatches_today(self, project_id):
        return self.count


class _NotifySpy:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, user_id, kind, title, **kwargs):
        self.calls.append({"user_id": user_id, "kind": kind, "title": title})
        return 1


def _install_repos(
    monkeypatch,
    *,
    nodes_repo: _FakeNodesRepo,
    projects_repo: _FakeProjectsRepo,
    issue_repo: Optional[_FakeIssueRepo] = None,
    agent_runs_repo: Optional[_FakeAgentRunsRepo] = None,
    notify_spy: Optional[_NotifySpy] = None,
    role: Optional[str] = "manager",
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: projects_repo,
    )
    if issue_repo is not None:
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issue_repo,
        )
    monkeypatch.setattr(
        "app.repositories.agent_runs_repository.get_agent_runs_repository",
        lambda: agent_runs_repo or _FakeAgentRunsRepo(count=0),
    )

    async def _default_limit():
        return 20

    monkeypatch.setattr(autopilot, "_daily_auto_runs_limit", _default_limit)

    if notify_spy is not None:
        monkeypatch.setattr("app.services.notifications.notify", notify_spy)

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr(advance_service, "resolve_effective_role", _role)

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_folders", _noop)
    monkeypatch.setattr(advance_service, "notify_stage_event", _noop)

    async def _ensure_node_issues(project_id, nodes, user_id):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_issues", _ensure_node_issues)


class _StartNodeSpy:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, project_id, node, **kwargs):
        self.calls.append({"project_id": project_id, "node_id": node["id"], **kwargs})
        return node


# ── autopilot_enabled off → fully silent ────────────────────────────────────


async def test_tick_silent_when_autopilot_disabled(monkeypatch):
    projects_repo = _FakeProjectsRepo(autopilot_enabled=False)
    nodes_repo = _FakeNodesRepo([_node("1")])
    _install_repos(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert spy.calls == []
    # The tick returns before ever listing nodes — nothing to evaluate.
    assert nodes_repo.list_nodes_calls == 0


async def test_tick_noop_when_project_missing(monkeypatch):
    class _MissingProjectsRepo:
        async def get_project_by_id(self, pid):
            return None

    nodes_repo = _FakeNodesRepo([_node("1")])
    _install_repos(
        monkeypatch, nodes_repo=nodes_repo, projects_repo=_MissingProjectsRepo()
    )

    # Must not raise.
    await autopilot._autopilot_tick_impl(_PROJECT)


# ── auto-start pass: candidate selection ────────────────────────────────────


async def test_tick_auto_starts_node_with_satisfied_deps_no_agent_owner(monkeypatch):
    done_dep = _node("1", status="done", auto_start=False, sort_order=1)
    candidate = _node("2", depends_on=["1"], sort_order=2)
    nodes_repo = _FakeNodesRepo([done_dep, candidate])
    projects_repo = _FakeProjectsRepo()
    _install_repos(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)

    async def _fake_cascade(project_id, project):
        return None

    monkeypatch.setattr(autopilot, "_cascade_pass", _fake_cascade)

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["node_id"] == "2"
    assert call["actor_user_id"] is None
    assert call["dispatch"] is False
    assert call["dispatch_auto"] is True


async def test_tick_skips_node_with_unmet_deps(monkeypatch):
    pending_dep = _node("1", status="pending", auto_start=False, sort_order=1)
    candidate = _node("2", depends_on=["1"], sort_order=2)
    nodes_repo = _FakeNodesRepo([pending_dep, candidate])
    projects_repo = _FakeProjectsRepo()
    _install_repos(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)
    monkeypatch.setattr(autopilot, "_cascade_pass", lambda *a: _noop_coro())

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert spy.calls == []


async def _noop_coro():
    return None


async def test_tick_ignores_skipped_and_non_pending_and_non_auto_start(monkeypatch):
    skipped = _node("1", skipped=True, sort_order=1)
    already_running = _node("2", status="in_progress", sort_order=2)
    no_flag = _node("3", auto_start=False, sort_order=3)
    nodes_repo = _FakeNodesRepo([skipped, already_running, no_flag])
    projects_repo = _FakeProjectsRepo()
    _install_repos(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)
    monkeypatch.setattr(autopilot, "_cascade_pass", lambda *a: _noop_coro())

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert spy.calls == []


# ── quota ────────────────────────────────────────────────────────────────────


async def test_tick_dispatches_agent_owner_under_quota(monkeypatch):
    candidate = _node("1", owner_agent_id=_AGENT, sort_order=1)
    nodes_repo = _FakeNodesRepo([candidate])
    projects_repo = _FakeProjectsRepo()
    agent_runs_repo = _FakeAgentRunsRepo(count=5)
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        agent_runs_repo=agent_runs_repo,
    )
    monkeypatch.setattr(autopilot, "_daily_auto_runs_limit", _const_limit(20))

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)
    monkeypatch.setattr(autopilot, "_cascade_pass", lambda *a: _noop_coro())

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert len(spy.calls) == 1
    assert spy.calls[0]["dispatch"] is True
    assert spy.calls[0]["dispatch_auto"] is True
    assert spy.calls[0].get("prepare_title") is None


async def test_tick_quota_exceeded_routes_to_prepare_not_dispatch(monkeypatch):
    candidate = _node("1", owner_agent_id=_AGENT, sort_order=1)
    nodes_repo = _FakeNodesRepo([candidate])
    projects_repo = _FakeProjectsRepo()
    agent_runs_repo = _FakeAgentRunsRepo(count=20)
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        agent_runs_repo=agent_runs_repo,
    )
    monkeypatch.setattr(autopilot, "_daily_auto_runs_limit", _const_limit(20))

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)
    monkeypatch.setattr(autopilot, "_cascade_pass", lambda *a: _noop_coro())

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert len(spy.calls) == 1
    assert spy.calls[0]["dispatch"] is False
    assert "daily limit reached" in spy.calls[0]["prepare_title"]


async def test_tick_quota_boundary_within_same_tick_20_then_21st(monkeypatch):
    """Two agent-owned candidates in ONE tick, quota already at 19/20: the
    first dispatches (bringing the running count to 20), the second — still
    within the SAME tick — is over quota and routes to prepare. Proves the
    in-process running counter (not a stale per-candidate DB re-query)."""
    c1 = _node("1", owner_agent_id=_AGENT, sort_order=1)
    c2 = _node("2", owner_agent_id=_AGENT, sort_order=2)
    nodes_repo = _FakeNodesRepo([c1, c2])
    projects_repo = _FakeProjectsRepo()
    agent_runs_repo = _FakeAgentRunsRepo(count=19)
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        agent_runs_repo=agent_runs_repo,
    )
    monkeypatch.setattr(autopilot, "_daily_auto_runs_limit", _const_limit(20))

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)
    monkeypatch.setattr(autopilot, "_cascade_pass", lambda *a: _noop_coro())

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert len(spy.calls) == 2
    assert spy.calls[0]["node_id"] == "1"
    assert spy.calls[0]["dispatch"] is True
    assert spy.calls[1]["node_id"] == "2"
    assert spy.calls[1]["dispatch"] is False


def _const_limit(n: int):
    async def _f():
        return n

    return _f


# ── idempotency ──────────────────────────────────────────────────────────────


async def test_tick_idempotent_second_call_skips_already_started_node(monkeypatch):
    candidate = _node("1", sort_order=1)
    nodes_repo = _FakeNodesRepo([candidate])
    projects_repo = _FakeProjectsRepo()
    _install_repos(monkeypatch, nodes_repo=nodes_repo, projects_repo=projects_repo)

    spy = _StartNodeSpy()
    monkeypatch.setattr(autopilot, "start_node_now", spy)
    monkeypatch.setattr(autopilot, "_cascade_pass", lambda *a: _noop_coro())

    await autopilot._autopilot_tick_impl(_PROJECT)
    assert len(spy.calls) == 1

    # Simulate the real effect of the first tick's start_node_now call — the
    # node is no longer 'pending'.
    candidate["status"] = "in_progress"

    await autopilot._autopilot_tick_impl(_PROJECT)
    assert len(spy.calls) == 1  # unchanged — second tick found nothing new


# ── cascade: real execute_advance, review gate never crossed ───────────────


async def test_tick_cascade_never_turns_in_review_to_done_and_notifies_once(
    monkeypatch,
):
    blocked_node = _node(
        "1",
        sort_order=1,
        review_required=True,
        auto_start=False,
        status="in_review",
        owner_user_id=_OWNER,
    )
    nodes_repo = _FakeNodesRepo([blocked_node])
    projects_repo = _FakeProjectsRepo(current_node_id="1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "in_review"}]})
    notify_spy = _NotifySpy()
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        notify_spy=notify_spy,
    )

    await autopilot._autopilot_tick_impl(_PROJECT)

    # The hard line: the mirror issue was NEVER transitioned to done.
    assert "done" not in [t[1] for t in issue_repo.transitions]
    assert issue_repo.transitions == []
    # Blocked-cascade notification fired exactly once.
    assert len(notify_spy.calls) == 1
    assert "review" in notify_spy.calls[0]["title"].lower()

    # A second tick, same blocked state — no repeat notification.
    await autopilot._autopilot_tick_impl(_PROJECT)
    assert len(notify_spy.calls) == 1


async def test_tick_cascade_advances_through_multiple_groups(monkeypatch):
    n1 = _node("1", sort_order=1, auto_start=False, status="done")
    n2 = _node("2", sort_order=2, auto_start=False, status="pending")
    projects_repo = _FakeProjectsRepo(current_node_id="1")
    nodes_repo = _FakeNodesRepo([n1, n2], projects_repo=projects_repo)
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "done"}]})
    notify_spy = _NotifySpy()
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        notify_spy=notify_spy,
    )

    await autopilot._autopilot_tick_impl(_PROJECT)

    # Cascade advanced the cursor onto group 2 (the only remaining group) —
    # BLOCK_NO_NEXT stops it there silently (no notification: NO_NEXT isn't a
    # human-actionable gate).
    assert nodes_repo.current_node_id_calls
    assert nodes_repo.current_node_id_calls[-1] == (_PROJECT, "2")
    assert notify_spy.calls == []


async def test_tick_cascade_reentrancy_guard_skips_nested_enqueue(monkeypatch):
    n1 = _node("1", sort_order=1, auto_start=False, status="done")
    n2 = _node("2", sort_order=2, auto_start=False, status="pending")
    projects_repo = _FakeProjectsRepo(current_node_id="1")
    nodes_repo = _FakeNodesRepo([n1, n2], projects_repo=projects_repo)
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "done"}]})
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        notify_spy=_NotifySpy(),
    )

    enqueue_calls: List[str] = []

    async def _spy_enqueue(project_id):
        enqueue_calls.append(project_id)

    monkeypatch.setattr(autopilot, "enqueue_autopilot_tick", _spy_enqueue)

    await autopilot._autopilot_tick_impl(_PROJECT)

    # execute_advance's own tail-enqueue call would normally fire once per
    # successful forward move — the re-entrancy guard must suppress ALL of
    # them while _cascade_pass's own loop is driving those calls.
    assert enqueue_calls == []


async def test_tick_deps_pending_cascade_notifies_once(monkeypatch):
    """A DEPS_PENDING block (mig 391 gate 5) is also a genuine gate worth a
    human notification — distinct code path from REVIEW_PENDING above."""
    n1 = _node("1", sort_order=1, auto_start=False, status="done", owner_user_id=_OWNER)
    n2 = _node("2", sort_order=2, auto_start=False, status="pending", depends_on=["3"])
    n3 = _node("3", sort_order=3, auto_start=False, status="pending")
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo(current_node_id="1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "done"}]})
    notify_spy = _NotifySpy()
    _install_repos(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        notify_spy=notify_spy,
    )

    await autopilot._autopilot_tick_impl(_PROJECT)

    assert len(notify_spy.calls) == 1
    assert "waiting on" in notify_spy.calls[0]["title"].lower()
    assert "Node 3" in notify_spy.calls[0]["title"]
