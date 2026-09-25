"""Workflow template routes: wire parity after they gained response models (P9).

Every route runs over real HTTP against the REAL repository, whose
``read_scope`` / ``write_scope`` hand out a stub session. Rows are transient
ORM objects carrying every column with its native type
(``tests/api/wire_parity.py``), so they reach the router through the
repository's own ``_template_row`` / ``_node_row`` / ``_member_row`` exactly as
in production. The body must equal what FastAPI sent for the bare dict.
"""

from __future__ import annotations

import importlib
import uuid
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import (
    ProjectStages,
    WorkflowTemplateNodeDeps,
    WorkflowTemplateNodeMembers,
    WorkflowTemplateNodes,
    WorkflowTemplates,
)
from app.schemas.workflow_responses import (
    WorkflowStageLibraryEntry,
    WorkflowTemplateNodeMember,
    WorkflowTemplateNodeRow,
    WorkflowTemplateSummary,
)
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

repo_mod = importlib.import_module("app.repositories.workflow_templates_repository")
wtr = importlib.import_module("app.api.workflow_templates_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
BASE = "/api/v1/workflows"


class _Result:
    def __init__(self, value: Any = None):
        self._value = value

    def scalars(self) -> "_Result":
        return self

    def first(self) -> Any:
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value

    def all(self) -> Any:
        return self._value


class _Session:
    """Hands back queued results in execute order; writes are no-ops."""

    def __init__(self) -> None:
        self.results: List[_Result] = []
        # What the DB fills in on INSERT (id, server defaults), set on refresh.
        self.server_side: dict[str, Any] = {}

    async def execute(self, stmt, *args, **kwargs):
        return self.results.pop(0) if self.results else _Result([])

    def add(self, obj: Any) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        for key, value in self.server_side.items():
            setattr(obj, key, value)

    async def delete(self, obj: Any) -> None:
        pass


@pytest.fixture
def session(monkeypatch) -> _Session:
    s = _Session()

    @asynccontextmanager
    async def _scope(*args, **kwargs):
        yield s

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(repo_mod, "write_scope", _scope)

    async def _role(user_id, *, project_id=None, team_id=None):
        return "manager"

    monkeypatch.setattr(wtr, "resolve_effective_role", _role)
    return s


@pytest.fixture(autouse=True)
def _auth():
    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _nullable(model: Any) -> dict[str, None]:
    return {
        prop.key: None
        for prop in inspect(model).column_attrs
        if prop.columns[0].nullable and not prop.columns[0].primary_key
    }


def template(nulls: bool = False) -> WorkflowTemplates:
    return sample_orm(
        WorkflowTemplates, **(_nullable(WorkflowTemplates) if nulls else {})
    )


def node(nulls: bool = False) -> WorkflowTemplateNodes:
    overrides: dict[str, Any] = {
        "completion_policy": "any_editor",
        "surface": "storyboard",
        # A legacy row may carry keys the current models no longer know.
        "events": {"notify_on_arrival": False, "legacy_toggle": True},
        "form_schema": [
            {"key": "cut", "label": "Cut", "type": "text", "required": True},
            {"key": "tone", "label": "Tone", "type": "select", "options": ["a"]},
        ],
    }
    if nulls:
        overrides.update(_nullable(WorkflowTemplateNodes))
        overrides["events"] = {}
        overrides["form_schema"] = []
    return sample_orm(WorkflowTemplateNodes, **overrides)


def member(**overrides: Any) -> WorkflowTemplateNodeMembers:
    return sample_orm(WorkflowTemplateNodeMembers, **overrides)


def dep(n: WorkflowTemplateNodes) -> WorkflowTemplateNodeDeps:
    return sample_orm(WorkflowTemplateNodeDeps, node_id=n.id)


def detail_raw(tpl, nodes, members, deps) -> dict[str, Any]:
    out = repo_mod._template_row(tpl, len(nodes))
    out["nodes"] = [
        repo_mod._node_row(
            n,
            [repo_mod._member_row(m) for m in members if m.node_id == n.id],
            [str(d.depends_on_node_id) for d in deps if d.node_id == n.id],
        )
        for n in nodes
    ]
    return out


def queue_detail(session: _Session, tpl, nodes, members, deps) -> None:
    session.results += [
        _Result([tpl]),
        _Result(nodes),
        _Result(members),
        _Result(deps),
    ]


# ── the models cover every column the repository emits ──────────────────


def test_models_cover_every_emitted_column():
    tpl_keys = set(repo_mod._template_row(template(), 0))
    assert set(WorkflowTemplateSummary.model_fields) == tpl_keys
    n = node()
    assert set(WorkflowTemplateNodeRow.model_fields) == set(
        repo_mod._node_row(n, [], [])
    )
    assert set(WorkflowTemplateNodeMember.model_fields) == column_names(
        WorkflowTemplateNodeMembers
    )
    # Every node column is on the wire (the row is the table, plus members/deps).
    assert column_names(WorkflowTemplateNodes) <= set(
        WorkflowTemplateNodeRow.model_fields
    )


# ── stage-library ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_library(client, session):
    full = sample_orm(ProjectStages, phase="plan")
    bare = sample_orm(
        ProjectStages, phase="ship", default_role_label=None, deliverable_label=None
    )
    # The dict the handler returned: the real repository over the same rows.
    session.results = [_Result([full, bare])]
    data = await repo_mod.WorkflowTemplatesRepository().list_stage_library()
    assert set(WorkflowStageLibraryEntry.model_fields) == set(data[0])

    session.results = [_Result([full, bare])]
    resp = await client.get(f"{BASE}/stage-library")
    assert_wire_unchanged(resp, {"success": True, "data": data})


# ── collection ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True], ids=["full", "nulls"])
async def test_list_templates(client, session, nulls):
    tpl = template(nulls)
    # list_templates: the template rows, then one node-id query per template.
    session.results = [_Result([tpl]), _Result([(1,), (2,), (3,)])]
    resp = await client.get(BASE, params={"team_id": "42"})
    assert_wire_unchanged(
        resp, {"success": True, "data": [repo_mod._template_row(tpl, 3)]}
    )


@pytest.mark.asyncio
async def test_list_templates_personal_team_missing(client, session, monkeypatch):
    async def no_team(self, owner_id):
        return None

    monkeypatch.setattr(
        wtr.get_team_repository().__class__, "get_personal_team_id", no_team
    )
    resp = await client.get(BASE)
    assert_wire_unchanged(resp, {"success": True, "data": []})


@pytest.mark.asyncio
async def test_create_template(client, session):
    # The only nullable column, created_by, is always the caller here.
    tpl = template()
    tpl.team_id, tpl.name = 42, "Reel"
    tpl.created_by = uuid.UUID(USER)
    session.server_side = {
        "id": tpl.id,
        "is_default": tpl.is_default,
        "created_at": tpl.created_at,
        "updated_at": tpl.updated_at,
    }
    session.results = [_Result([])]  # count_templates
    resp = await client.post(BASE, params={"team_id": "42"}, json={"name": "Reel"})
    assert_wire_unchanged(
        resp, {"success": True, "data": repo_mod._template_row(tpl, 0)}
    )


# ── one template ─────────────────────────────────────────────────────────


def _team_of(tpl) -> _Result:
    return _Result((tpl.team_id,))


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True], ids=["full", "nulls"])
async def test_get_template(client, session, nulls):
    tpl = template(nulls)
    first, second = node(nulls), node()
    second.id = first.id + 1
    members = [
        member(node_id=first.id, agent_id=None),
        member(node_id=first.id, user_id=None),
    ]
    deps = [dep(second)]
    session.results = [_team_of(tpl)]
    queue_detail(session, tpl, [first, second], members, deps)
    resp = await client.get(f"{BASE}/{tpl.id}")
    assert_wire_unchanged(
        resp,
        {"success": True, "data": detail_raw(tpl, [first, second], members, deps)},
    )


@pytest.mark.asyncio
async def test_get_template_without_nodes(client, session):
    tpl = template()
    session.results = [_team_of(tpl), _Result([tpl]), _Result([])]
    resp = await client.get(f"{BASE}/{tpl.id}")
    assert_wire_unchanged(resp, {"success": True, "data": detail_raw(tpl, [], [], [])})


@pytest.mark.asyncio
async def test_patch_template(client, session):
    tpl = template()
    n = node()
    members = [member(node_id=n.id, agent_id=None)]
    session.results = [_team_of(tpl), _Result([tpl])]  # role lookup, update select
    queue_detail(session, tpl, [n], members, [])
    resp = await client.patch(f"{BASE}/{tpl.id}", json={"name": "Renamed"})
    assert tpl.name == "Renamed"
    assert_wire_unchanged(
        resp, {"success": True, "data": detail_raw(tpl, [n], members, [])}
    )


@pytest.mark.asyncio
async def test_delete_template(client, session):
    tpl = template()
    session.results = [_team_of(tpl), _Result([tpl])]
    resp = await client.delete(f"{BASE}/{tpl.id}")
    assert_wire_unchanged(resp, {"success": True, "data": {"deleted": True}})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
async def test_template_gone_between_check_and_read_is_typed_404(
    client, session, method
):
    tpl = template()
    session.results = [_team_of(tpl), _Result([])]
    kwargs = {"json": {"name": "X"}} if method == "patch" else {}
    resp = await getattr(client, method)(f"{BASE}/{tpl.id}", **kwargs)
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"
