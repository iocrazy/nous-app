"""Authz wiring + repo behavior tests for the beat-templates router / repository
(Beats M3.5 — user custom templates).

Three halves, mirroring test_script_beats.py:

1. **Guard matrix**: every route requires auth; the id-scoped routes (PUT /
   DELETE) additionally declare the ownership guard verify_beat_template_access —
   a future id-scoped endpoint added without it fails here instead of shipping an
   IDOR. Plus the guard resolution chain: a foreign owner gets 403, a missing
   template 404 (before the 403).

2. **Repository** (capture-emitted-SQL, no DSN): create binds user_id / name /
   anchors; list_by_user filters to the owner; rename bumps name; delete.

3. **Schema validation**: malformed anchors / names 422 at the boundary.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

import app.repositories.beat_template_repository as tpl_mod
from app.api.beat_templates_router import router as tpl_router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_beat_template_access
from app.main import app
from app.models.scripts import BeatTemplates
from app.repositories.beat_template_repository import BeatTemplateRepository

pytestmark = pytest.mark.unit

_TPL_ID = 900000000000000101
_USER_ID = str(uuid4())
FAKE_USER_ID = str(uuid4())


# --------------------------------------------------------------------------- #
# 1a. Structural: routes require auth; id-scoped routes declare the guard
# --------------------------------------------------------------------------- #


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


_ALL_ROUTES = [r for r in list(tpl_router.routes) if hasattr(r, "dependant")]
_ID_ROUTES = [r for r in _ALL_ROUTES if "{template_id}" in r.path]


@pytest.mark.parametrize(
    "route", _ALL_ROUTES, ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}"
)
def test_beat_template_route_requires_auth(route):
    calls = set(_flat_dependency_calls(route.dependant))
    assert get_auth in calls, f"{sorted(route.methods)} {route.path} requires auth"


@pytest.mark.parametrize(
    "route", _ID_ROUTES, ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}"
)
def test_id_scoped_route_declares_ownership_guard(route):
    calls = set(_flat_dependency_calls(route.dependant))
    assert (
        verify_beat_template_access in calls
    ), f"{sorted(route.methods)} {route.path} declares no ownership guard"


# --------------------------------------------------------------------------- #
# 1b. Behavior: verify_beat_template_access resolution chain
# --------------------------------------------------------------------------- #


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_rename_403_foreign_owner(client, monkeypatch):
    async def fake_get(self, template_id):
        return {"id": template_id, "user_id": _USER_ID}  # owned by someone else

    monkeypatch.setattr(BeatTemplateRepository, "get_by_id", fake_get)
    resp = await client.put("/api/v1/beat-templates/123", json={"name": "Mine"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_403_foreign_owner(client, monkeypatch):
    async def fake_get(self, template_id):
        return {"id": template_id, "user_id": _USER_ID}

    monkeypatch.setattr(BeatTemplateRepository, "get_by_id", fake_get)
    resp = await client.delete("/api/v1/beat-templates/123")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_missing_template_returns_404(client, monkeypatch):
    async def fake_get(self, template_id):
        return None

    monkeypatch.setattr(BeatTemplateRepository, "get_by_id", fake_get)
    resp = await client.delete("/api/v1/beat-templates/123")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_can_delete(client, monkeypatch):
    async def fake_get(self, template_id):
        return {"id": template_id, "user_id": FAKE_USER_ID}  # the caller owns it

    async def fake_delete(self, template_id):
        return True

    monkeypatch.setattr(BeatTemplateRepository, "get_by_id", fake_get)
    monkeypatch.setattr(BeatTemplateRepository, "delete", fake_delete)
    resp = await client.delete("/api/v1/beat-templates/123")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_scopes_owner_to_caller(client, monkeypatch):
    """POST never trusts a body user_id — the owner is always the auth caller."""
    captured: Dict[str, Any] = {}

    async def fake_create(self, user_id, name, anchors):
        captured.update({"user_id": user_id, "name": name, "anchors": anchors})
        return {"id": "1", "user_id": user_id, "name": name, "anchors": anchors}

    monkeypatch.setattr(BeatTemplateRepository, "create", fake_create)
    resp = await client.post(
        "/api/v1/beat-templates",
        json={
            "user_id": "spoofed-owner",  # must be ignored
            "name": "My Method",
            "anchors": [{"title": "Open", "pctStart": 0, "pctEnd": 10}],
        },
    )
    assert resp.status_code == 200
    assert captured["user_id"] == FAKE_USER_ID
    assert captured["name"] == "My Method"
    # model_dump normalizes the anchor to the full shape.
    assert captured["anchors"] == [
        {"title": "Open", "summary": None, "pctStart": 0, "pctEnd": 10, "color": None}
    ]


# --------------------------------------------------------------------------- #
# 3. Schema validation (422 at the boundary)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "name": "",
            "anchors": [{"title": "A", "pctStart": 0, "pctEnd": 10}],
        },  # empty name
        {"name": "X", "anchors": []},  # no anchors
        {
            "name": "X",
            "anchors": [{"title": "", "pctStart": 0, "pctEnd": 10}],
        },  # empty title
        {
            "name": "X",
            "anchors": [{"title": "A", "pctStart": -1, "pctEnd": 10}],
        },  # pct < 0
        {
            "name": "X",
            "anchors": [{"title": "A", "pctStart": 0, "pctEnd": 101}],
        },  # pct > 100
        {
            "name": "X",
            "anchors": [{"title": "A", "pctStart": 40, "pctEnd": 20}],
        },  # end < start
        {
            "name": "X",
            "anchors": [{"title": "A", "pctStart": 0, "pctEnd": 10, "color": "red"}],
        },  # non-hex color
    ],
)
async def test_create_rejects_invalid_payload(client, monkeypatch, payload):
    async def fake_create(self, user_id, name, anchors):  # pragma: no cover
        raise AssertionError("repo must not be reached on a 422")

    monkeypatch.setattr(BeatTemplateRepository, "create", fake_create)
    resp = await client.post("/api/v1/beat-templates", json=payload)
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# 2. Repository behavior (capture-emitted-SQL, no DSN)
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, *, scalar_first=None, all_rows=None):
        self._scalar_first = scalar_first
        self._all_rows = all_rows or []

    def scalars(self):
        return self

    def first(self):
        return self._scalar_first

    def all(self):
        return self._all_rows


class _CaptureSession:
    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0)


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _tpl_obj(name="My Method", anchors=None):
    return BeatTemplates(
        id=_TPL_ID,
        user_id=_USER_ID,
        name=name,
        anchors=(
            anchors
            if anchors is not None
            else [{"title": "A", "pctStart": 0, "pctEnd": 10}]
        ),
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_create_binds_user_name_anchors():
    anchors = [
        {"title": "Open", "summary": None, "pctStart": 0, "pctEnd": 10, "color": None}
    ]
    session = _CaptureSession([_FakeResult(scalar_first=_tpl_obj(anchors=anchors))])
    with patch.object(tpl_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await BeatTemplateRepository().create(_USER_ID, "Open Method", anchors)
    ins_sql, ins_params = _rendered(session.statements[0])
    assert ins_sql.strip().upper().startswith("INSERT INTO PUBLIC.BEAT_TEMPLATES")
    assert _USER_ID in ins_params.values()
    assert "Open Method" in ins_params.values()
    assert anchors in ins_params.values()
    # Read-back parity: id stays native int, uuid → str, anchors stays a list.
    assert out["id"] == _TPL_ID
    assert out["user_id"] == _USER_ID
    assert out["anchors"] == anchors


@pytest.mark.asyncio
async def test_list_by_user_filters_and_orders():
    session = _CaptureSession([_FakeResult(all_rows=[_tpl_obj()])])
    with patch.object(tpl_mod, "read_scope", lambda: _ScopeCtx(session)):
        rows = await BeatTemplateRepository().list_by_user(_USER_ID)
    sel_sql, sel_params = _rendered(session.statements[0])
    assert sel_sql.strip().upper().startswith("SELECT")
    assert "ORDER BY" in sel_sql.upper()
    assert _USER_ID in sel_params.values()
    assert len(rows) == 1
    assert rows[0]["name"] == "My Method"


@pytest.mark.asyncio
async def test_rename_bumps_name():
    session = _CaptureSession([_FakeResult(scalar_first=_tpl_obj(name="Renamed"))])
    with patch.object(tpl_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await BeatTemplateRepository().rename(str(_TPL_ID), "Renamed")
    upd_sql, upd_params = _rendered(session.statements[0])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.BEAT_TEMPLATES")
    assert "Renamed" in upd_params.values()
    # id is bigint-coerced (native int, never a str bind — the 5.3 trap).
    assert _TPL_ID in upd_params.values()
    assert str(_TPL_ID) not in upd_params.values()
    assert out["name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_emits_delete():
    session = _CaptureSession([_FakeResult(scalar_first=None)])
    with patch.object(tpl_mod, "write_scope", lambda: _ScopeCtx(session)):
        assert await BeatTemplateRepository().delete(str(_TPL_ID)) is True
    del_sql, del_params = _rendered(session.statements[0])
    assert del_sql.strip().upper().startswith("DELETE FROM PUBLIC.BEAT_TEMPLATES")
    assert _TPL_ID in del_params.values()
