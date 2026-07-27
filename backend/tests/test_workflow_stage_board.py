"""Stage Board aggregate endpoint (M2 PR-F, Task F1).

``GET /projects/{id}/workflow/nodes/{node_id}/board`` is the single data
source for the Stage Board workspace module (F2/F3): one request returns the
node's full row, its mirror issue (+ sub-issues), and the files filed into
its deliverable folder.

Fakes only at the repository-getter seam (mirrors
``test_workflow_flow_rules.py``'s ``test_get_project_workflow_response_...``
pattern) — goes through the real router function end-to-end. Uses
``importlib`` rather than ``import app.api.projects_router as m`` because
``app/api/__init__.py`` shadows the submodule attribute on the ``app.api``
package with the bare ``APIRouter`` instance once imported.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException


class _Auth:
    def __init__(self, user_id: str):
        self.user_id = user_id


_USER = "00000000-0000-0000-0000-000000000001"
_PROJECT = "100"
_NODE = "1"


def _node_row(**overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": _NODE,
        "project_id": _PROJECT,
        "source_template_node_id": "10",
        "legacy_stage_id": None,
        "name": "Script",
        "sort_order": 1,
        "parallel_group": None,
        "status": "in_progress",
        "owner_user_id": None,
        "owner_agent_id": None,
        "planned_start": None,
        "planned_due": None,
        "review_required": False,
        "deliverable_required": True,
        "deliverable_label": None,
        "skipped": False,
        "folder_id": None,
        "completion_policy": "owner",
        "events": {
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        "members": [],
    }
    row.update(overrides)
    return row


class _FakeNodesRepo:
    def __init__(
        self,
        node: Optional[Dict[str, Any]],
        *,
        files: Optional[List[Dict[str, Any]]] = None,
    ):
        self._node = node
        # Only non-trashed rows land here — mirrors the real
        # list_folder_files' WHERE is_trashed=false at the SQL layer, so the
        # router never has to re-filter.
        self._files = files or []

    async def get_node(self, node_id, project_id=None):
        return self._node

    async def list_folder_files(self, folder_id):
        return [f for f in self._files if str(f.get("folder_id")) == str(folder_id)]


class _FakeIssueRepo:
    def __init__(
        self,
        *,
        by_origin: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        children: Optional[Dict[int, List[Dict[str, Any]]]] = None,
        identifiers: Optional[Dict[str, str]] = None,
    ):
        self._by_origin = by_origin or {}
        self._children = children or {}
        self._identifiers = identifiers or {}

    async def list_by_origin(self, kind, origin_id):
        return list(self._by_origin.get(origin_id, []))

    async def list_children(self, issue_id):
        return self._children.get(issue_id, [])

    async def map_identifiers(self, issue_ids):
        return {
            str(i): self._identifiers[str(i)]
            for i in issue_ids
            if str(i) in self._identifiers
        }


def _install(monkeypatch, *, nodes_repo, issue_repo):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.issue_repository.get_issue_repository",
        lambda: issue_repo,
    )


def _router():
    # See module docstring — must use importlib, not the shadowed app.api
    # package attribute.
    return importlib.import_module("app.api.projects_router")


# ── 404 before anything else ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_not_found_404(monkeypatch):
    nodes_repo = _FakeNodesRepo(None)
    issue_repo = _FakeIssueRepo()
    _install(monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo)

    with pytest.raises(HTTPException) as exc:
        await _router().get_stage_board(_PROJECT, _NODE, _Auth(_USER), None)

    assert exc.value.status_code == 404


# ── issue=null cases ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_mirror_issue_returns_null(monkeypatch):
    node = _node_row()
    nodes_repo = _FakeNodesRepo(node)
    issue_repo = _FakeIssueRepo()  # no origin ever registered
    _install(monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo)

    result = await _router().get_stage_board(_PROJECT, _NODE, _Auth(_USER), None)

    assert result["success"] is True
    assert result["data"]["issue"] is None
    assert result["data"]["files"] == []


@pytest.mark.asyncio
async def test_legacy_two_segment_origin_does_not_500(monkeypatch):
    """A project migrated from the old global-SOP mirror stamped its issue
    with the OLD two-segment origin (``project_stage:{stage_id}``, no project
    scope). The endpoint always queries the NEW three-segment origin
    (``project_stage:{project_id}:{node_id}``) — the legacy row simply never
    matches, degrading to ``issue=null`` rather than crashing on a shape it
    doesn't recognize."""
    node = _node_row()
    nodes_repo = _FakeNodesRepo(node)
    legacy_origin = "project_stage:1"  # old shape — no project_id segment
    issue_repo = _FakeIssueRepo(
        by_origin={
            legacy_origin: [
                {
                    "id": 900,
                    "identifier": "MH-900",
                    "title": "Legacy stage issue",
                    "status": "todo",
                    "assignee_user_id": None,
                    "assignee_agent_id": None,
                }
            ]
        }
    )
    _install(monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo)

    result = await _router().get_stage_board(_PROJECT, _NODE, _Auth(_USER), None)

    assert result["data"]["issue"] is None


# ── files: only non-trashed, from the node's folder ────────────────────────


@pytest.mark.asyncio
async def test_files_only_non_trashed_from_node_folder(monkeypatch):
    node = _node_row(folder_id="900")
    files = [
        {
            "id": "f1",
            "filename": "draft.mp4",
            "folder_id": "900",
            "file_size_bytes": 1024,
            "created_at": "2026-07-01T00:00:00+00:00",
            "source_issue_id": None,
        },
        # A file in a DIFFERENT folder must not leak in.
        {
            "id": "fx",
            "filename": "other.mp4",
            "folder_id": "111",
            "file_size_bytes": 2048,
            "created_at": "2026-07-02T00:00:00+00:00",
            "source_issue_id": None,
        },
    ]
    nodes_repo = _FakeNodesRepo(node, files=files)
    issue_repo = _FakeIssueRepo()
    _install(monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo)

    result = await _router().get_stage_board(_PROJECT, _NODE, _Auth(_USER), None)

    out_files = result["data"]["files"]
    assert len(out_files) == 1
    assert out_files[0]["id"] == "f1"
    assert out_files[0]["filename"] == "draft.mp4"
    assert out_files[0]["size"] == 1024


# ── full success shape: issue + sub-issues + files + string ids ───────────


@pytest.mark.asyncio
async def test_full_board_shape_with_subissues_and_source_issue_identifier(
    monkeypatch,
):
    node = _node_row(folder_id="900")
    origin_id = "project_stage:100:1"
    mirror = {
        "id": 900,
        "identifier": "MH-900",
        "title": "Project — Script",
        "status": "in_review",
        "assignee_user_id": _USER,
        "assignee_agent_id": None,
    }
    sub_issue = {
        "id": 901,
        "identifier": "MH-901",
        "title": "Sub task",
        "status": "todo",
        "assignee_user_id": None,
        "assignee_agent_id": "00000000-0000-0000-0000-0000000000aa",
    }
    files = [
        {
            "id": "f1",
            "filename": "draft.mp4",
            "folder_id": "900",
            "file_size_bytes": 4096,
            "created_at": "2026-07-01T00:00:00+00:00",
            "source_issue_id": 901,
        }
    ]
    nodes_repo = _FakeNodesRepo(node, files=files)
    issue_repo = _FakeIssueRepo(
        by_origin={origin_id: [mirror]},
        children={900: [sub_issue]},
        identifiers={"901": "MH-901"},
    )
    _install(monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo)

    result = await _router().get_stage_board(_PROJECT, _NODE, _Auth(_USER), None)

    data = result["data"]
    assert data["node"]["id"] == "1"

    issue = data["issue"]
    assert issue["id"] == "900"
    assert issue["identifier"] == "MH-900"
    assert issue["status"] == "in_review"
    assert issue["assignee"] == {"user_id": _USER, "agent_id": None}
    assert len(issue["sub_issues"]) == 1
    sub = issue["sub_issues"][0]
    assert sub["id"] == "901"
    assert sub["assignee"] == {
        "user_id": None,
        "agent_id": "00000000-0000-0000-0000-0000000000aa",
    }

    out_files = data["files"]
    assert len(out_files) == 1
    assert out_files[0]["id"] == "f1"
    assert out_files[0]["source_issue_identifier"] == "MH-901"
