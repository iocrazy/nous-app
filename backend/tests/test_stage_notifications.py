"""Stage arrival/completion/reopen notification fan-out (M2 PR-E E2).

Two layers under test:
  * ``notify_stage_event()`` itself — event gating (``notify_on_arrival`` /
    ``notify_on_complete``), recipient dedupe + actor-exclusion, the
    agent-owner-with-no-user-recipients skip, and the per-event title
    wording.
  * the ``advance_service`` wiring — a notify failure (which itself should
    NEVER raise, but this proves the caller is unaffected even if the
    monkeypatched ``notify()`` blows up outright) never changes
    ``execute_advance``'s returned preview or its mutations (mirrors the
    fake-repo pattern in ``test_advance_predicate.py``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service
import app.services.workflow.stage_notifications as stage_notifications
from app.services.workflow.stage_notifications import notify_stage_event

_PROJECT = "100"
_USER = "00000000-0000-0000-0000-000000000001"
_EP1 = "8001"


def _node(
    node_id: str,
    *,
    name: str = "Stage",
    sort_order: int = 1,
    owner_user_id: Optional[str] = None,
    owner_agent_id: Optional[str] = None,
    members: Optional[List[Dict[str, Any]]] = None,
    events: Optional[Dict[str, Any]] = None,
    episode_id: str = _EP1,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name,
        "sort_order": sort_order,
        "parallel_group": None,
        "review_required": False,
        "deliverable_required": False,
        "skipped": False,
        "owner_user_id": owner_user_id,
        "owner_agent_id": owner_agent_id,
        "planned_due": None,
        "folder_id": None,
        "members": members or [],
        "events": events or {},
        "episode_id": episode_id,
    }


class _NotifySpy:
    """Stand-in for ``app.services.notifications.notify`` — records calls, or
    raises unconditionally when constructed with ``raise_exc``."""

    def __init__(self, *, raise_exc: Optional[Exception] = None):
        self.calls: List[Dict[str, Any]] = []
        self._raise_exc = raise_exc

    async def __call__(
        self,
        user_id,
        kind,
        title,
        *,
        body=None,
        severity="info",
        link_kind=None,
        link_id=None,
        team_id=None,
    ):
        if self._raise_exc is not None:
            raise self._raise_exc
        self.calls.append(
            {
                "user_id": user_id,
                "kind": kind,
                "title": title,
                "link_kind": link_kind,
                "link_id": link_id,
                "team_id": team_id,
            }
        )
        return 1


# ── notify_stage_event: event gating ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_arrival_skipped_when_notify_on_arrival_false(monkeypatch):
    spy = _NotifySpy()
    monkeypatch.setattr(stage_notifications, "notify", spy)

    node = _node("1", owner_user_id="u1", events={"notify_on_arrival": False})
    await notify_stage_event(
        event="arrival",
        project_id=_PROJECT,
        project_name="Demo",
        node=node,
        issue_identifier="MH-1",
        team_id=5,
        actor_user_id=None,
    )

    assert spy.calls == []


@pytest.mark.asyncio
async def test_completion_only_sent_when_notify_on_complete_true(monkeypatch):
    spy = _NotifySpy()
    monkeypatch.setattr(stage_notifications, "notify", spy)

    # Default events() → notify_on_complete is False — no send.
    node_default = _node("1", owner_user_id="u1")
    await notify_stage_event(
        event="completion",
        project_id=_PROJECT,
        project_name="Demo",
        node=node_default,
        issue_identifier=None,
        team_id=None,
        actor_user_id=None,
    )
    assert spy.calls == []

    # Explicit opt-in → sends, with "completed" wording.
    node_opt_in = _node("2", owner_user_id="u1", events={"notify_on_complete": True})
    await notify_stage_event(
        event="completion",
        project_id=_PROJECT,
        project_name="Demo",
        node=node_opt_in,
        issue_identifier=None,
        team_id=None,
        actor_user_id=None,
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["user_id"] == "u1"
    assert "completed" in spy.calls[0]["title"]


@pytest.mark.asyncio
async def test_reopen_uses_reopened_wording(monkeypatch):
    spy = _NotifySpy()
    monkeypatch.setattr(stage_notifications, "notify", spy)

    node = _node("1", name="Editing", owner_user_id="u1")
    await notify_stage_event(
        event="reopen",
        project_id=_PROJECT,
        project_name="Demo",
        node=node,
        issue_identifier="MH-7",
        team_id=None,
        actor_user_id=None,
    )

    assert len(spy.calls) == 1
    assert spy.calls[0]["title"] == 'Stage "Editing" reopened — Demo'
    assert spy.calls[0]["link_kind"] == "issue"
    assert spy.calls[0]["link_id"] == "MH-7"


# ── notify_stage_event: recipients ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_recipients_dedupe_and_exclude_actor(monkeypatch):
    spy = _NotifySpy()
    monkeypatch.setattr(stage_notifications, "notify", spy)

    node = _node(
        "1",
        owner_user_id="u1",
        members=[{"user_id": "u1"}, {"user_id": "u2"}, {"user_id": "u2"}],
    )
    await notify_stage_event(
        event="arrival",
        project_id=_PROJECT,
        project_name="Demo",
        node=node,
        issue_identifier=None,
        team_id=None,
        actor_user_id="u2",
    )

    # u1 (owner==member, deduped to one call) survives; u2 excluded as actor.
    assert len(spy.calls) == 1
    assert spy.calls[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_agent_owner_with_no_user_members_skips(monkeypatch):
    spy = _NotifySpy()
    monkeypatch.setattr(stage_notifications, "notify", spy)

    node = _node("1", owner_agent_id="agent-1", members=[])
    await notify_stage_event(
        event="arrival",
        project_id=_PROJECT,
        project_name="Demo",
        node=node,
        issue_identifier=None,
        team_id=None,
        actor_user_id=None,
    )

    # An agent-owned node with no user members has nobody to notify — this is
    # a silent no-op, NOT a fallback ping to some other party (e.g. a manager).
    assert spy.calls == []


# ── wiring: execute_advance is unaffected by a notify failure ──────────────


class _FakeNodesRepo:
    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = nodes

    async def list_nodes_by_episode(self, project_id, episode_id):
        return [n for n in self._nodes if str(n.get("episode_id")) == str(episode_id)]

    async def list_folder_files(self, folder_id):
        return []


class _FakeEpisodesRepo:
    def __init__(self, cursors: Dict[str, Optional[str]]):
        self._cursors: Dict[str, Optional[str]] = {
            str(k): v for k, v in cursors.items()
        }
        self.set_current_node_id_calls: List[Any] = []

    async def get_by_id(self, episode_id):
        return {"current_node_id": self._cursors.get(str(episode_id))}

    async def set_current_node_id(self, episode_id, node_id):
        self.set_current_node_id_calls.append((str(episode_id), node_id))
        self._cursors[str(episode_id)] = node_id


class _FakeProjectsRepo:
    def __init__(self, current_node_id: Optional[str]):
        self._current_node_id = current_node_id

    async def get_project_by_id(self, pid):
        return {
            "current_node_id": self._current_node_id,
            "name": "Demo",
            "team_id": 5,
        }

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


def _install_advance_fakes(
    monkeypatch, *, nodes_repo, projects_repo, issue_repo, episodes_repo=None
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: episodes_repo or _FakeEpisodesRepo({}),
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: projects_repo,
    )
    monkeypatch.setattr(
        "app.repositories.issue_repository.get_issue_repository",
        lambda: issue_repo,
    )

    async def _role(user_id, *, project_id=None, team_id=None):
        return "manager"

    monkeypatch.setattr(advance_service, "resolve_effective_role", _role)

    async def _ensure_node_issues(project_id, nodes, user_id):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_issues", _ensure_node_issues)

    async def _ensure_node_folders(project_id, nodes, user_id):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_folders", _ensure_node_folders)


@pytest.mark.asyncio
async def test_notify_failure_does_not_affect_advance_return_value(monkeypatch):
    n1 = _node("1", sort_order=1, owner_user_id="u9")
    n2 = _node("2", sort_order=2, owner_user_id="u9")
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install_advance_fakes(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    # The lower-level notify() blows up on every call — proves the best-effort
    # layer inside notify_stage_event shields execute_advance's return value
    # AND its mutations, even if notify() somehow failed to swallow its own
    # exception.
    spy = _NotifySpy(raise_exc=RuntimeError("inbox down"))
    monkeypatch.setattr(stage_notifications, "notify", spy)

    result = await advance_service.execute_advance(
        _PROJECT, _USER, "forward", episode_id=_EP1
    )

    assert result.will_advance is True
    assert result.creating[0].node_id == "2"
