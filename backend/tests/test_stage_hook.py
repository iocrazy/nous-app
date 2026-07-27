"""stage_hook_dispatch — prepares an agent run behind the confirm gate, never
dispatches (M3 PR-H2, task-H2 brief).

Two layers under test, mirroring ``test_stage_notifications.py``'s split:
  * ``_stage_hook_dispatch_impl`` (the plain, undecorated function the
    ``@DBOS.workflow()`` wrapper delegates to — tests drive it directly, no
    DBOS runtime needed): the double-check gate (owner_agent_id +
    events.prepare_agent_run), the ``run_prepared_at`` idempotency short-
    circuit, the mirror-issue lookup bail, the notify recipients, and the
    metadata merge (via a fake repo whose ``set_node_metadata`` mimics the
    real shallow-merge).
  * ``enqueue_stage_hook_dispatch`` (the arrival-site wiring helper): only
    enqueues agent-owned + prepare_agent_run-enabled nodes, and swallows an
    enqueue failure without raising.

The "never dispatches" discipline is asserted the same way
``test_project_stage_auto_issue.py::test_agent_owner_arrival_never_dispatches``
does: monkeypatch the dispatch entry point to blow up loudly, then prove the
call under test never reaches it.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest

from app.workflows.stage_hook import (
    _stage_hook_dispatch_impl,
    enqueue_stage_hook_dispatch,
)

pytestmark = pytest.mark.asyncio

_PROJECT = "100"
_NODE_ID = "20"
_AGENT = "00000000-0000-0000-0000-0000000000aa"


def _node(
    *,
    node_id: str = _NODE_ID,
    name: str = "Canvas",
    owner_agent_id: Optional[str] = _AGENT,
    owner_user_id: Optional[str] = None,
    members: Optional[List[Dict[str, Any]]] = None,
    prepare_agent_run: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
    skipped: bool = False,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name,
        "owner_agent_id": owner_agent_id,
        "owner_user_id": owner_user_id,
        "members": members or [],
        "events": {"prepare_agent_run": prepare_agent_run},
        "metadata": metadata or {},
        "skipped": skipped,
    }


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeNodesRepo:
    """``get_node`` + ``set_node_metadata`` — the latter mimics the real
    repository's shallow JSONB merge so tests can assert existing keys
    survive."""

    def __init__(self, node: Optional[Dict[str, Any]]):
        self._node = dict(node) if node is not None else None
        self.metadata_patches: List[Dict[str, Any]] = []

    async def get_node(self, node_id, project_id=None):
        return dict(self._node) if self._node is not None else None

    async def set_node_metadata(self, node_id, patch):
        self.metadata_patches.append(dict(patch))
        if self._node is None:
            return None
        merged = dict(self._node.get("metadata") or {})
        merged.update(patch)
        self._node["metadata"] = merged
        return dict(merged)


class _FakeIssueRepo:
    def __init__(self, issues: Optional[List[Dict[str, Any]]] = None):
        self._issues = issues if issues is not None else []
        self.list_by_origin_calls: List[Any] = []

    async def list_by_origin(self, kind, origin_id):
        self.list_by_origin_calls.append((kind, origin_id))
        return list(self._issues)


class _NotifySpy:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

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
        self.calls.append(
            {
                "user_id": user_id,
                "kind": kind,
                "title": title,
                "link_kind": link_kind,
                "link_id": link_id,
            }
        )
        return 1


def _install(monkeypatch, *, nodes_repo, issue_repo, notify_spy):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.issue_repository.get_issue_repository",
        lambda: issue_repo,
    )
    monkeypatch.setattr(
        "app.services.notifications.notify",
        notify_spy,
    )


# ── _stage_hook_dispatch_impl: never dispatches ─────────────────────────────


async def test_prepare_never_dispatches(monkeypatch):
    """Mirrors test_agent_owner_arrival_never_dispatches: the dispatch entry
    point is monkeypatched to raise, proving prepare never reaches it even on
    the full happy path (agent owner + flag on + mirror issue present)."""
    issues_router_mod = importlib.import_module("app.api.issues_router")

    def _boom(issue_id, wf_id):
        raise AssertionError("stage-hook prepare must never dispatch an agent run")

    monkeypatch.setattr(issues_router_mod, "_dispatch_execute_issue", _boom)

    nodes_repo = _FakeNodesRepo(_node(owner_user_id="u1"))
    issue_repo = _FakeIssueRepo([{"id": 501, "identifier": "MH-9"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert len(notify_spy.calls) == 1
    assert notify_spy.calls[0]["title"] == 'Agent run ready — "Canvas"'
    assert notify_spy.calls[0]["link_kind"] == "issue"
    assert notify_spy.calls[0]["link_id"] == "MH-9"
    assert nodes_repo.metadata_patches
    assert "run_prepared_at" in nodes_repo.metadata_patches[-1]


# ── gates ────────────────────────────────────────────────────────────────────


async def test_no_agent_owner_skips(monkeypatch):
    nodes_repo = _FakeNodesRepo(_node(owner_agent_id=None, owner_user_id="u1"))
    issue_repo = _FakeIssueRepo([{"id": 1, "identifier": "MH-1"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert notify_spy.calls == []
    assert nodes_repo.metadata_patches == []
    assert issue_repo.list_by_origin_calls == []


async def test_prepare_agent_run_flag_off_skips(monkeypatch):
    nodes_repo = _FakeNodesRepo(_node(prepare_agent_run=False))
    issue_repo = _FakeIssueRepo([{"id": 1, "identifier": "MH-1"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert notify_spy.calls == []
    assert nodes_repo.metadata_patches == []


async def test_missing_node_skips(monkeypatch):
    nodes_repo = _FakeNodesRepo(None)
    issue_repo = _FakeIssueRepo()
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert notify_spy.calls == []


async def test_no_mirror_issue_skips(monkeypatch):
    nodes_repo = _FakeNodesRepo(_node(owner_user_id="u1"))
    issue_repo = _FakeIssueRepo([])  # no mirror issue at all
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert notify_spy.calls == []
    assert nodes_repo.metadata_patches == []


async def test_no_user_recipients_still_stamps_metadata(monkeypatch):
    """An agent-owned node with no user owner/members has nobody to notify —
    silent no-op on the notify side — but the node is still marked prepared
    so a later arrival with the same config doesn't re-attempt indefinitely."""
    nodes_repo = _FakeNodesRepo(_node(owner_user_id=None, members=[]))
    issue_repo = _FakeIssueRepo([{"id": 1, "identifier": "MH-1"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert notify_spy.calls == []
    assert nodes_repo.metadata_patches
    assert "run_prepared_at" in nodes_repo.metadata_patches[-1]


# ── idempotency ──────────────────────────────────────────────────────────────


async def test_idempotent_skip_when_already_prepared(monkeypatch):
    nodes_repo = _FakeNodesRepo(
        _node(
            owner_user_id="u1",
            metadata={"run_prepared_at": "2026-01-01T00:00:00+00:00"},
        )
    )
    issue_repo = _FakeIssueRepo([{"id": 1, "identifier": "MH-1"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    assert notify_spy.calls == []
    assert nodes_repo.metadata_patches == []
    # Idempotency check short-circuits BEFORE the mirror-issue lookup.
    assert issue_repo.list_by_origin_calls == []


# ── metadata merge preserves existing keys ──────────────────────────────────


async def test_metadata_merge_preserves_existing_keys(monkeypatch):
    nodes_repo = _FakeNodesRepo(
        _node(owner_user_id="u1", metadata={"form_schema": {"a": 1}})
    )
    issue_repo = _FakeIssueRepo([{"id": 1, "identifier": "MH-1"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    stored = nodes_repo._node["metadata"]
    assert stored["form_schema"] == {"a": 1}
    assert "run_prepared_at" in stored


# ── recipients: dedupe owner + members ──────────────────────────────────────


async def test_recipients_dedupe_owner_and_members(monkeypatch):
    nodes_repo = _FakeNodesRepo(
        _node(
            owner_user_id="u1",
            members=[{"user_id": "u1"}, {"user_id": "u2"}, {"user_id": "u2"}],
        )
    )
    issue_repo = _FakeIssueRepo([{"id": 1, "identifier": "MH-1"}])
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    await _stage_hook_dispatch_impl(_PROJECT, _NODE_ID)

    recipients = {c["user_id"] for c in notify_spy.calls}
    assert recipients == {"u1", "u2"}


# ── enqueue_stage_hook_dispatch: arrival-site wiring ────────────────────────


async def test_enqueue_skips_non_agent_owned_and_flag_off_nodes(monkeypatch):
    dispatched: List[Dict[str, Any]] = []

    async def _fake_start_workflow_routed(
        task_type,
        *,
        dbos_workflow_callable,
        dbos_workflow_kwargs=None,
        workflow_id=None,
    ):
        dispatched.append(dict(dbos_workflow_kwargs or {}))
        return {"mode": "dbos", "task_type": task_type}

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        _fake_start_workflow_routed,
    )

    nodes = [
        _node(node_id="1", owner_agent_id=_AGENT, prepare_agent_run=True),
        _node(node_id="2", owner_agent_id=None, prepare_agent_run=True),
        _node(node_id="3", owner_agent_id=_AGENT, prepare_agent_run=False),
        _node(node_id="4", owner_agent_id=_AGENT, prepare_agent_run=True, skipped=True),
    ]

    await enqueue_stage_hook_dispatch(_PROJECT, nodes)

    assert len(dispatched) == 1
    assert dispatched[0]["node_id"] == "1"
    assert dispatched[0]["project_id"] == _PROJECT


async def test_enqueue_failure_swallowed(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("dbos not launched")

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _boom
    )

    nodes = [_node(owner_agent_id=_AGENT, prepare_agent_run=True)]

    # Must not raise.
    await enqueue_stage_hook_dispatch(_PROJECT, nodes)
