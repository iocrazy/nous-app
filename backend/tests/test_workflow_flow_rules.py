"""Flow Rules / Events config (mig 386, M2 PR-D2).

Three surfaces under test:
  - the owner-review guard (``issues_router._assert_stage_owner_or_manager``)
    honoring ``completion_policy == 'any_editor'`` — plus the 'owner'
    regression guard, since M1's default semantics must not shift
  - ``ProjectStageNodesRepository.instantiate_from_template`` copying
    ``completion_policy``/``events`` from the template node to the project
    node instance
  - ``WorkflowTemplatesRepository.update_template``'s full node-list replace
    carrying ``completion_policy``/``events`` through to the constructed ORM
    rows (and NOT passing them at all — so the NOT NULL columns fall back to
    their DB server_default — when the caller's node dict omits them, as
    ``template_seeder.py``'s hand-built dicts do)
  - ``GET /projects/{id}/workflow`` (``projects_router.get_project_workflow``)
    actually surfacing both fields in the response JSON. This one is a
    regression pin: ``NodeOut`` initially didn't declare either field, so
    pydantic silently dropped them from ``ProjectWorkflowOut`` even though the
    repository read path returned them — the instance read path never reached
    the frontend. A later task (E3's suggest-agent-run chip) reads
    ``node.events.suggest_agent_run`` straight off this endpoint's payload, so
    it must actually be there.

The repository tests are FakeSession-backed (``write_scope`` monkeypatched),
the same house pattern as ``test_canvas_repository_persist.py``: statement
construction, ``add``/``flush``, and the method-matrix branch really run —
only SQL execution is faked. Matches the documented convention that a full
integration harness against a live Postgres is deferred (see
``tests/test_workflow_instantiation.py``'s module docstring).
"""

from __future__ import annotations

import datetime
import importlib
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import (
    ProjectStageNodeMembers,
    ProjectStageNodes,
    WorkflowTemplateNodes,
    WorkflowTemplates,
)
from app.repositories.project_stage_nodes_repository import (
    ProjectStageNodesRepository,
)
from app.repositories.workflow_templates_repository import (
    WorkflowTemplatesRepository,
)
from app.schemas.issue import IssueStatus, IssueStatusTransition
from app.schemas.workflow import TemplateNodeIn, WorkflowNodeEvents

issues_router = importlib.import_module("app.api.issues_router")

_NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
_OWNER = "00000000-0000-0000-0000-000000000001"
_OTHER = "00000000-0000-0000-0000-000000000002"


# ── guard: completion_policy honored ────────────────────────────────────────


class _Auth:
    def __init__(self, user_id: str):
        self.user_id = user_id


def _issue_row(**overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": 900,
        "issue_number": 1,
        "identifier": "MH-900",
        "title": "Script",
        "description": None,
        "status": "in_review",
        "priority": "medium",
        "team_id": None,
        "project_id": 100,
        "parent_id": None,
        "assignee_user_id": None,
        "assignee_agent_id": None,
        "origin_kind": "project_stage",
        "origin_id": "project_stage:100:1",
        "origin_fingerprint": "default",
        "billing_code": None,
        "created_by_user_id": _OTHER,
        "created_by_agent_id": None,
        "dbos_workflow_id": None,
        "execution_locked_at": None,
        "execution_state": None,
        "request_depth": 0,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "hidden_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    row.update(overrides)
    return row


class _FakeIssueRepo:
    def __init__(self, issue: Dict[str, Any]):
        self._issue = issue
        self.transitions: List[Any] = []

    async def get_by_id(self, issue_id):
        return dict(self._issue)

    async def is_team_member(self, user_id, team_id):
        return True

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {**self._issue, "status": new_status}


class _FakeNodesRepo:
    def __init__(self, node: Optional[Dict[str, Any]]):
        self._node = node

    async def get_node(self, node_id, project_id=None):
        return self._node


def _install_guard(monkeypatch, *, issue_repo, node: Dict[str, Any], role: str):
    monkeypatch.setattr(issues_router, "issue_repository", issue_repo)
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _FakeNodesRepo(node),
    )

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr("app.core.workflow_roles.resolve_effective_role", _role)


@pytest.mark.asyncio
async def test_any_editor_node_lets_non_owner_editor_complete_review(monkeypatch):
    """The plan's headline behavior: completion_policy='any_editor' lets any
    manager/editor -- not just the node's own owner -- push in_review->done."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    node = {
        "owner_user_id": _OWNER,
        "owner_agent_id": None,
        "completion_policy": "any_editor",
    }
    _install_guard(monkeypatch, issue_repo=issue_repo, node=node, role="editor")

    result = await issues_router.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE
    assert issue_repo.transitions == [(900, "done")]


@pytest.mark.asyncio
async def test_any_editor_still_blocks_viewer(monkeypatch):
    """'any_editor' widens the gate to manager/editor -- not to every role."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    node = {
        "owner_user_id": _OWNER,
        "owner_agent_id": None,
        "completion_policy": "any_editor",
    }
    _install_guard(monkeypatch, issue_repo=issue_repo, node=node, role="viewer")

    with pytest.raises(HTTPException) as exc:
        await issues_router.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_policy_non_owner_editor_still_403_no_regression(monkeypatch):
    """M1 semantics must be unchanged for completion_policy='owner' (the
    default): a non-owner editor is still rejected."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    node = {
        "owner_user_id": _OWNER,
        "owner_agent_id": None,
        "completion_policy": "owner",
    }
    _install_guard(monkeypatch, issue_repo=issue_repo, node=node, role="editor")

    with pytest.raises(HTTPException) as exc:
        await issues_router.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403
    assert issue_repo.transitions == []


@pytest.mark.asyncio
async def test_missing_completion_policy_key_defaults_to_owner_semantics(
    monkeypatch,
):
    """A node dict that predates mig 386 (no completion_policy key at all, as
    every pre-existing owner-guard fixture assumes) must keep failing closed,
    exactly like an explicit 'owner'."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    node = {"owner_user_id": _OWNER, "owner_agent_id": None}
    _install_guard(monkeypatch, issue_repo=issue_repo, node=node, role="editor")

    with pytest.raises(HTTPException) as exc:
        await issues_router.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403


# ── shared FakeSession plumbing (house pattern per test_canvas_repository_persist) ──


class _Result:
    """Wraps a canned/dynamic row list so both the ``.scalars().all()/.first()``
    and the bare ``.all()`` (``_load_slug_map``) access patterns work."""

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


# ── instantiate_from_template: copies completion_policy + events ───────────


class _InstantiateFakeSession:
    """Deterministic, call-order-based fake for one full
    ``instantiate_from_template`` pass (method=None, non-empty template, no
    overrides -- the exact path this test exercises). Real production code
    (statement construction, add/flush, the method-matrix branch) really
    runs; only SQL execution is faked.

    Call order (mirrors ``instantiate_from_template`` + ``_list_nodes_in_session``):
      1. existing-nodes check         -> empty (not yet instantiated)
      2. template nodes select        -> canned tpl_nodes
      3. template node-members select -> empty (no members in this fixture)
      4. template node-deps select    -> empty (mig 391, M3 PR-J — no deps in
                                          this fixture)
      5. node-bank slug map select    -> empty (fixture nodes carry no
                                          source_stage_id, so it's never
                                          consulted)
      6. final node listing           -> whatever was committed via add()
      7. final member listing         -> whatever was committed via add()
      8. final deps listing           -> empty (mig 391, M3 PR-J)
    """

    def __init__(self, tpl_nodes: List[WorkflowTemplateNodes]):
        self._tpl_nodes = tpl_nodes
        self.added: List[Any] = []
        self._next_id = 9000
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def execute(self, stmt: Any) -> _Result:
        await self.flush()  # autoflush emulation (real AsyncSession does this)
        self._calls += 1
        if self._calls == 1:
            return _Result([])
        if self._calls == 2:
            return _Result(self._tpl_nodes)
        if self._calls == 3:
            return _Result([])
        if self._calls == 4:
            return _Result([])  # template node-deps select (mig 391, M3 PR-J)
        if self._calls == 5:
            return _Result([])
        if self._calls == 6:
            nodes = [o for o in self.added if isinstance(o, ProjectStageNodes)]
            nodes.sort(key=lambda n: n.sort_order)
            return _Result(nodes)
        if self._calls == 7:
            members = [o for o in self.added if isinstance(o, ProjectStageNodeMembers)]
            return _Result(members)
        if self._calls == 8:
            return _Result([])  # final deps listing (mig 391, M3 PR-J)
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


def _tpl_node(
    *,
    node_id: int,
    sort_order: int,
    completion_policy: str,
    events: Dict[str, bool],
) -> WorkflowTemplateNodes:
    return WorkflowTemplateNodes(
        id=node_id,
        template_id=1,
        name=f"node-{node_id}",
        sort_order=sort_order,
        parallel_group=None,
        default_owner_user_id=None,
        default_owner_agent_id=None,
        skip_default=False,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        source_stage_id=None,
        duration_days=None,
        completion_policy=completion_policy,
        events=events,
    )


@pytest.mark.asyncio
async def test_instantiate_copies_completion_policy_and_events(monkeypatch):
    tpl_nodes = [
        _tpl_node(
            node_id=1,
            sort_order=1,
            completion_policy="owner",
            events={
                "notify_on_arrival": True,
                "notify_on_complete": False,
                "suggest_agent_run": False,
            },
        ),
        _tpl_node(
            node_id=2,
            sort_order=2,
            completion_policy="any_editor",
            events={
                "notify_on_arrival": False,
                "notify_on_complete": True,
                "suggest_agent_run": True,
            },
        ),
    ]
    session = _InstantiateFakeSession(tpl_nodes)

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.instantiate_from_template("50", "1")

    assert len(result) == 2
    by_sort = {n["sort_order"]: n for n in result}
    assert by_sort[1]["completion_policy"] == "owner"
    assert by_sort[1]["events"] == {
        "notify_on_arrival": True,
        "notify_on_complete": False,
        "suggest_agent_run": False,
    }
    assert by_sort[2]["completion_policy"] == "any_editor"
    assert by_sort[2]["events"] == {
        "notify_on_arrival": False,
        "notify_on_complete": True,
        "suggest_agent_run": True,
    }


# ── update_template full-replace: carries completion_policy/events ────────


class _TemplateFakeSession:
    """Just enough to drive ``update_template``'s tpl fetch + node delete +
    insert. The read-back (``get_template``) is monkeypatched away on the repo
    instance itself so this test needs only ONE session/scope, not the two
    (write_scope then a fresh read_scope) the real method chains through."""

    def __init__(self, tpl: WorkflowTemplates):
        self._tpl = tpl
        self.added: List[Any] = []
        self._next_id = 7000
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def execute(self, stmt: Any) -> _Result:
        await self.flush()
        self._calls += 1
        if self._calls == 1:
            return _Result([self._tpl])  # tpl select
        return _Result([])  # the node-list delete


def _fake_tpl() -> WorkflowTemplates:
    return WorkflowTemplates(
        id=1,
        team_id=2,
        name="Short-form",
        is_default=True,
        created_by=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.asyncio
async def test_update_template_full_replace_carries_completion_policy_and_events(
    monkeypatch,
):
    session = _TemplateFakeSession(_fake_tpl())

    import app.repositories.workflow_templates_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = WorkflowTemplatesRepository()

    async def _fake_get_template(template_id, team_id):
        return {"id": str(template_id), "sentinel": True}

    monkeypatch.setattr(repo, "get_template", _fake_get_template)

    node_payload = {
        "name": "Script",
        "sort_order": 1,
        "completion_policy": "any_editor",
        "events": {
            "notify_on_arrival": False,
            "notify_on_complete": True,
            "suggest_agent_run": True,
        },
        "members": [],
    }
    result = await repo.update_template("1", "2", nodes=[node_payload])

    assert result == {"id": "1", "sentinel": True}
    written = [o for o in session.added if isinstance(o, WorkflowTemplateNodes)]
    assert len(written) == 1
    assert written[0].completion_policy == "any_editor"
    assert written[0].events == {
        "notify_on_arrival": False,
        "notify_on_complete": True,
        "suggest_agent_run": True,
    }


@pytest.mark.asyncio
async def test_update_template_node_without_flow_rules_keys_omits_kwargs(
    monkeypatch,
):
    """template_seeder's hand-built node dicts carry neither key -- the repo
    must NOT pass an explicit None (that would violate the NOT NULL columns
    at flush time against a real DB); it must omit the kwarg entirely and let
    the DB server_default apply. Pinned here as: the constructed (transient,
    un-flushed) ORM object reads back None for both fields, proving the
    kwarg was never passed to the constructor."""
    session = _TemplateFakeSession(_fake_tpl())

    import app.repositories.workflow_templates_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = WorkflowTemplatesRepository()

    async def _fake_get_template(template_id, team_id):
        return None

    monkeypatch.setattr(repo, "get_template", _fake_get_template)

    node_payload = {"name": "Script", "sort_order": 1, "members": []}
    await repo.update_template("1", "2", nodes=[node_payload])

    written = [o for o in session.added if isinstance(o, WorkflowTemplateNodes)][0]
    assert written.completion_policy is None
    assert written.events is None


# ── GET /projects/{id}/workflow: NodeOut must not drop the two fields ──────


@pytest.mark.asyncio
async def test_get_project_workflow_response_carries_completion_policy_and_events(
    monkeypatch,
):
    """Regression pin: NodeOut initially had no completion_policy/events
    fields, so pydantic silently ignored them coming out of
    ProjectStageNodesRepository.list_nodes and the frontend never saw them
    despite the repository read path already returning both (D2's original
    _node_row fix). Goes through the real endpoint function end-to-end
    (fakes only at the repository-getter seam), asserting on both the parsed
    response object AND its serialized JSON shape."""
    node_row = {
        "id": "1",
        "project_id": "100",
        "source_template_node_id": "10",
        "legacy_stage_id": None,
        "name": "Script",
        "sort_order": 1,
        "parallel_group": None,
        "status": "pending",
        "owner_user_id": None,
        "owner_agent_id": None,
        "planned_start": None,
        "planned_due": None,
        "review_required": False,
        "deliverable_required": False,
        "deliverable_label": None,
        "skipped": False,
        "folder_id": None,
        "completion_policy": "any_editor",
        "events": {
            "notify_on_arrival": False,
            "notify_on_complete": True,
            "suggest_agent_run": True,
        },
        "members": [],
    }

    class _NodesRepo:
        async def list_nodes(self, project_id):
            return [node_row]

        async def count_running_agent_runs(self, project_id):
            return 0

    class _ProjectsRepo:
        async def get_project_by_id(self, project_id):
            return {"current_node_id": None}

        async def get_project_files(self, project_id):
            return []

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _NodesRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: _ProjectsRepo(),
    )

    # NOTE: must use importlib, not ``import app.api.projects_router as m`` —
    # app/api/__init__.py does ``from app.api.projects_router import router as
    # projects_router``, which shadows the submodule attribute on the ``app.api``
    # package with the bare APIRouter instance once that package has loaded
    # (matches the importlib pattern already used by
    # test_workflow_node_router.py / test_owner_review_guard.py for this exact
    # reason).
    projects_router_mod = importlib.import_module("app.api.projects_router")

    result = await projects_router_mod.get_project_workflow("100", _Auth(_OTHER), None)

    assert result.has_workflow is True
    node_out = result.nodes[0]
    assert node_out.completion_policy == "any_editor"
    assert node_out.events.suggest_agent_run is True
    assert node_out.events.notify_on_arrival is False
    assert node_out.events.notify_on_complete is True
    # mig 389 (M3 PR-H3): the fixture's node_row predates the metadata column
    # entirely (no "metadata" key at all) — NodeOut must default to {} rather
    # than raising, so a pre-mig-389 row never breaks this endpoint.
    assert node_out.metadata == {}

    # Pin the actual response JSON shape too — what the frontend receives.
    payload = result.model_dump()
    assert payload["nodes"][0]["completion_policy"] == "any_editor"
    assert payload["nodes"][0]["events"] == {
        "notify_on_arrival": False,
        "notify_on_complete": True,
        "suggest_agent_run": True,
        # mig 389 (M3 PR-H1): new hook keys ride the same events blob and
        # default in when the fixture's DB row predates them (proves the
        # NodeOut path carries them automatically — no code change needed
        # per-field, per the brief's self-review note).
        "prepare_agent_run": False,
        "on_complete_workflow": None,
    }
    assert payload["nodes"][0]["metadata"] == {}


@pytest.mark.asyncio
async def test_get_project_workflow_response_carries_metadata_run_prepared_at(
    monkeypatch,
):
    """Regression pin, same shape as the completion_policy/events test above:
    ``NodeOut`` initially had no ``metadata`` field, so pydantic would silently
    drop ``ProjectStageNodesRepository._node_row``'s ``"metadata": obj.metadata_``
    entry converting the row dict → NodeOut, and the Run now chip (H3) would
    never see ``run_prepared_at`` even though the repository read path already
    returns it (H1's ``set_node_metadata`` writer)."""
    node_row = {
        "id": "1",
        "project_id": "100",
        "source_template_node_id": "10",
        "legacy_stage_id": None,
        "name": "Script",
        "sort_order": 1,
        "parallel_group": None,
        "status": "in_progress",
        "owner_user_id": None,
        "owner_agent_id": "agent-1",
        "planned_start": None,
        "planned_due": None,
        "review_required": False,
        "deliverable_required": False,
        "deliverable_label": None,
        "skipped": False,
        "folder_id": None,
        "completion_policy": "owner",
        "events": {"suggest_agent_run": True, "prepare_agent_run": True},
        "metadata": {"run_prepared_at": "2026-07-27T00:00:00+00:00"},
        "members": [],
    }

    class _NodesRepo:
        async def list_nodes(self, project_id):
            return [node_row]

        async def count_running_agent_runs(self, project_id):
            return 0

    class _ProjectsRepo:
        async def get_project_by_id(self, project_id):
            return {"current_node_id": None}

        async def get_project_files(self, project_id):
            return []

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _NodesRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: _ProjectsRepo(),
    )

    projects_router_mod = importlib.import_module("app.api.projects_router")
    result = await projects_router_mod.get_project_workflow("100", _Auth(_OTHER), None)

    node_out = result.nodes[0]
    assert node_out.metadata == {"run_prepared_at": "2026-07-27T00:00:00+00:00"}

    payload = result.model_dump()
    assert payload["nodes"][0]["metadata"] == {
        "run_prepared_at": "2026-07-27T00:00:00+00:00"
    }


# ── mig 389: WorkflowNodeEvents hook keys (prepare_agent_run / on_complete_workflow) ──


def test_prepare_agent_run_round_trips_through_template_node_in_and_node_to_dict():
    """New hook toggle: must validate through TemplateNodeIn, survive
    ``events.model_dump()``, and reach ``_node_to_dict``'s payload (the same
    seam that carries every other events key into the repo write path)."""
    workflow_templates_router = importlib.import_module(
        "app.api.workflow_templates_router"
    )

    node = TemplateNodeIn(
        name="Script",
        sort_order=1,
        events={"prepare_agent_run": True},
    )

    assert node.events.prepare_agent_run is True
    dumped = node.events.model_dump()
    assert dumped["prepare_agent_run"] is True

    payload = workflow_templates_router._node_to_dict(node)
    assert payload["events"]["prepare_agent_run"] is True


def test_on_complete_workflow_none_is_accepted():
    """The default / explicit None must pass validation untouched."""
    events = WorkflowNodeEvents(on_complete_workflow=None)
    assert events.on_complete_workflow is None

    node = TemplateNodeIn(name="Script", sort_order=1)
    assert node.events.on_complete_workflow is None


def test_on_complete_workflow_non_null_rejected_as_not_implemented():
    """M3 does not implement on_complete_workflow yet -- any non-None value
    must be rejected with a clear 'not implemented' message (surfaces as a
    FastAPI 422 through TemplateNodeIn)."""
    with pytest.raises(ValidationError) as exc:
        WorkflowNodeEvents(on_complete_workflow="some_workflow_slug")

    assert "not implemented in M3" in str(exc.value)

    with pytest.raises(ValidationError):
        TemplateNodeIn(
            name="Script",
            sort_order=1,
            events={"on_complete_workflow": "x"},
        )
