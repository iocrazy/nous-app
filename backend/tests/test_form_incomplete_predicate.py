"""FORM_INCOMPLETE advance gate (mig 390, M3 PR-I task I2).

Two layers under test:
  1. ``advance_service._form_incomplete`` — the pure predicate. Required-fill
     rules per field type (spec §2): text/textarea/select/date need a
     non-empty string; number needs the KEY present (``0`` counts as filled —
     a presence check, not a truthiness check); checkbox needs the value to be
     exactly ``True`` (``False`` does not satisfy it). A field with
     ``required`` falsy is never checked, and a node with no ``form_schema``
     (or an empty one) never contributes a missing label — zero impact on
     every workflow that predates mig 390.
  2. ``compute_advance_preview``/``execute_advance`` sharing this SAME
     predicate for the new ``BLOCK_FORM_INCOMPLETE`` code — mirroring the
     #1400 discipline already pinned for REVIEW_PENDING/DELIVERABLE_MISSING in
     ``test_advance_predicate.py`` (fake-repo pattern reused here, trimmed to
     just what the form gate needs). A skipped node is proven exempt: it never
     joins a group (``_build_groups`` drops skipped nodes outright), so its
     unfilled required fields can never block an advance.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service
from app.schemas.workflow import BLOCK_FORM_INCOMPLETE
from app.services.workflow.advance_service import _form_incomplete

_EP1 = "8001"


def _field(
    key: str, ftype: str, *, required: bool = True, label: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label or key.title(),
        "type": ftype,
        "required": required,
    }


def _node_row(
    node_id: str,
    *,
    sort_order: int,
    parallel_group: Optional[int] = None,
    skipped: bool = False,
    form_schema: Optional[List[Dict[str, Any]]] = None,
    form_data: Optional[Dict[str, Any]] = None,
    episode_id: str = _EP1,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": f"Node {node_id}",
        "sort_order": sort_order,
        "parallel_group": parallel_group,
        "review_required": False,
        "deliverable_required": False,
        "skipped": skipped,
        "owner_user_id": None,
        "owner_agent_id": None,
        "planned_due": None,
        "folder_id": None,
        "form_schema": form_schema or [],
        "form_data": form_data or {},
        "episode_id": episode_id,
    }


# ── _form_incomplete: string-like types (text/textarea/select/date) ────────


@pytest.mark.parametrize(
    "ftype,filled_value",
    [
        ("text", "hello"),
        ("textarea", "hello"),
        ("select", "option-a"),
        ("date", "2026-07-27"),
    ],
)
def test_string_like_types_filled_when_non_empty(ftype, filled_value):
    node = {"form_schema": [_field("f1", ftype)], "form_data": {"f1": filled_value}}
    assert _form_incomplete(node) == []


@pytest.mark.parametrize("ftype", ["text", "textarea", "select", "date"])
def test_string_like_types_missing_when_empty_string(ftype):
    node = {
        "form_schema": [_field("f1", ftype, label="Notes")],
        "form_data": {"f1": ""},
    }
    assert _form_incomplete(node) == ["Notes"]


@pytest.mark.parametrize("ftype", ["text", "textarea", "select", "date"])
def test_string_like_types_missing_when_key_absent(ftype):
    node = {"form_schema": [_field("f1", ftype, label="Notes")], "form_data": {}}
    assert _form_incomplete(node) == ["Notes"]


# ── _form_incomplete: number (0 boundary — presence, not truthiness) ───────


def test_number_filled_when_key_present_even_zero():
    node = {
        "form_schema": [_field("count", "number", label="Count")],
        "form_data": {"count": 0},
    }
    assert _form_incomplete(node) == []


def test_number_missing_when_key_absent():
    node = {"form_schema": [_field("count", "number", label="Count")], "form_data": {}}
    assert _form_incomplete(node) == ["Count"]


# ── _form_incomplete: checkbox (false boundary — must be exactly True) ─────


def test_checkbox_filled_only_when_true():
    node = {
        "form_schema": [_field("ack", "checkbox", label="Acknowledge")],
        "form_data": {"ack": True},
    }
    assert _form_incomplete(node) == []


def test_checkbox_missing_when_false():
    node = {
        "form_schema": [_field("ack", "checkbox", label="Acknowledge")],
        "form_data": {"ack": False},
    }
    assert _form_incomplete(node) == ["Acknowledge"]


def test_checkbox_missing_when_key_absent():
    node = {
        "form_schema": [_field("ack", "checkbox", label="Acknowledge")],
        "form_data": {},
    }
    assert _form_incomplete(node) == ["Acknowledge"]


# ── required=False is never checked ─────────────────────────────────────────


def test_not_required_field_never_reported_missing():
    node = {
        "form_schema": [_field("f1", "text", required=False, label="Optional")],
        "form_data": {},
    }
    assert _form_incomplete(node) == []


# ── no schema (or empty schema) → zero impact ───────────────────────────────


def test_no_form_schema_key_returns_empty():
    node = {"name": "n"}
    assert _form_incomplete(node) == []


def test_empty_form_schema_list_returns_empty():
    node = {"form_schema": [], "form_data": {}}
    assert _form_incomplete(node) == []


# ── multiple fields: only missing REQUIRED labels are listed ───────────────


def test_multiple_fields_lists_only_missing_required_labels():
    node = {
        "form_schema": [
            _field("a", "text", label="Title"),
            _field("b", "number", required=False, label="Optional Count"),
            _field("c", "checkbox", label="Confirmed"),
        ],
        "form_data": {"a": "filled", "c": False},
    }
    assert _form_incomplete(node) == ["Confirmed"]


# ── preview/execute share the SAME predicate (#1400) ────────────────────────


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
        return {"current_node_id": self._current_node_id}

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
    projects_repo: _FakeProjectsRepo,
    issue_repo: _FakeIssueRepo,
    role: Optional[str] = "manager",
    episodes_repo: Optional[_FakeEpisodesRepo] = None,
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
        return role

    monkeypatch.setattr(advance_service, "resolve_effective_role", _role)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_issues", _noop)
    monkeypatch.setattr(advance_service, "ensure_node_folders", _noop)
    monkeypatch.setattr(advance_service, "notify_stage_event", _noop)


@pytest.mark.asyncio
async def test_preview_blocks_forward_on_missing_required_field(monkeypatch):
    n1 = _node_row(
        "1", sort_order=1, form_schema=[_field("notes", "text", label="Notes")]
    )
    n2 = _node_row("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_FORM_INCOMPLETE
    assert preview.missing_fields == ["Notes"]


@pytest.mark.asyncio
async def test_preview_allows_forward_once_required_field_filled(monkeypatch):
    n1 = _node_row(
        "1",
        sort_order=1,
        form_schema=[_field("notes", "text", label="Notes")],
        form_data={"notes": "done"},
    )
    n2 = _node_row("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    assert preview.missing_fields == []


@pytest.mark.asyncio
async def test_execute_advance_blocked_by_form_incomplete_does_not_mutate(monkeypatch):
    n1 = _node_row(
        "1", sort_order=1, form_schema=[_field("notes", "text", label="Notes")]
    )
    n2 = _node_row("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    result = await advance_service.execute_advance(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert result.will_advance is False
    assert result.blocked_reason == BLOCK_FORM_INCOMPLETE
    assert episodes_repo.set_current_node_id_calls == []


@pytest.mark.asyncio
async def test_skipped_node_with_incomplete_form_is_exempt(monkeypatch):
    """A skipped node never joins a group (``_build_groups`` drops it before
    grouping), so its unfilled required fields can never gate the advance —
    even when it shares a parallel_group with an active node."""
    n1 = _node_row("1", sort_order=1, parallel_group=1)
    skipped = _node_row(
        "2",
        sort_order=2,
        parallel_group=1,
        skipped=True,
        form_schema=[_field("notes", "text", label="Notes")],
    )
    n3 = _node_row("3", sort_order=3)
    nodes_repo = _FakeNodesRepo([n1, skipped, n3])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    assert preview.missing_fields == []
