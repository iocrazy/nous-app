"""Assets endpoints: payload shape + error mapping + scope gating (no DB)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError

USER = "11111111-1111-1111-1111-111111111111"


class _AuthStub:
    user_id = USER


class _FakeService:
    def __init__(self):
        self.calls = []
        self.attach_attempts = []

    async def list_assets(self, scope_id, **f):
        self.calls.append(("list", scope_id, f))
        return [{"id": "1", "name": "Sang Yao", "asset_type": "character"}]

    async def create_asset(self, scope_id, payload, user_id):
        if payload.name == "dup":
            raise AssetError(409, "asset_exists", "exists", {"existing_asset_id": "7"})
        return {"id": "2", "name": payload.name, "asset_type": payload.asset_type}

    async def get_asset(self, asset_id, scope_id):
        if asset_id == 404:
            raise AssetError(404, "asset_not_found", "nope")
        return {
            "id": str(asset_id),
            "files": [],
            "links": [],
            "linked_by": [],
            "loadouts": [],
        }

    # Set to a resource_id that should blow up, to exercise the batch path.
    fail_on_resource_id = None

    async def attach_file(self, asset_id, scope_id, req, user_id):
        self.attach_attempts.append(req.resource_id)
        if req.resource_id == self.fail_on_resource_id:
            raise AssetError(404, "resource_not_found", "nope")
        return {
            "asset_id": str(asset_id),
            "resource_id": req.resource_id,
            "slot": req.slot,
        }

    async def add_link(self, asset_id, scope_id, req):
        raise AssetError(422, "link_not_allowed", "no", {})


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    @asynccontextmanager
    async def _no_uow():
        yield None

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    # The fake service never touches a DB, so the real unit_of_work() would try
    # to open a session. Atomicity itself is the transaction's job (asserted in
    # the fix report against its docstring); what the fake CAN prove is that the
    # loop stops at the first failure.
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest.mark.asyncio
async def test_list_passes_filters_and_wraps(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/assets?scope_id=9000&type=character&project_id=55&q=sang"
        )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["data"][0]["name"] == "Sang Yao"
    _, scope, f = app.state.fake.calls[0]
    assert (
        scope == 9000
        and f["asset_type"] == "character"
        and f["project_id"] == 55
        and f["q"] == "sang"
    )


@pytest.mark.asyncio
async def test_non_member_403(app):
    """I4: the gate answers in the SAME envelope as every other failure. It used
    to raise a bare HTTPException, so the one 403 that matters most was the only
    failure with no ``code`` for a client to branch on."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=666")
    assert r.status_code == 403
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "not_a_member"
    assert "detail" not in body, "bare HTTPException shape leaked out"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/api/v1/assets?scope_id=666"),
        ("get", "/api/v1/assets/5?scope_id=666"),
        ("delete", "/api/v1/assets/5?scope_id=666"),
        ("post", "/api/v1/assets/5/files?scope_id=666"),
        ("delete", "/api/v1/assets/5/files/6/sheet?scope_id=666"),
        ("post", "/api/v1/assets/5/links?scope_id=666"),
        ("delete", "/api/v1/assets/5/links/6/wears?scope_id=666"),
        ("post", "/api/v1/assets/5/loadouts?scope_id=666"),
        ("patch", "/api/v1/assets/5/loadouts/6?scope_id=666"),
        ("delete", "/api/v1/assets/5/loadouts/6?scope_id=666"),
        ("post", "/api/v1/assets/5/project-refs?scope_id=666"),
        ("delete", "/api/v1/assets/5/project-refs/7?scope_id=666"),
    ],
)
async def test_every_gated_route_uses_the_error_envelope(app, method, url):
    bodies = {
        "post": {
            "files": {"resource_id": "1", "slot": "sheet"},
            "links": {"to_asset_id": "6", "relation": "wears"},
            "loadouts": {"name": "Night"},
            "project-refs": {"project_id": "7"},
        }
    }
    json_body = None
    if method in ("post", "patch"):
        json_body = next(
            (v for k, v in bodies["post"].items() if f"/{k}" in url), {"name": "Night"}
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await getattr(c, method)(url, **({"json": json_body} if json_body else {}))
    assert r.status_code == 403, r.text
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }


@pytest.mark.asyncio
async def test_create_201_and_409_shape(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "Blade"}
        )
        assert r.status_code == 201 and r.json()["data"]["id"] == "2"
        r = await c.post(
            "/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "dup"}
        )
    assert r.status_code == 409
    err = r.json()
    assert err["success"] is False
    assert (
        err["error"]["code"] == "asset_exists"
        and err["error"]["existing_asset_id"] == "7"
    )


@pytest.mark.asyncio
async def test_get_404_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/404?scope_id=9000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "asset_not_found"


@pytest.mark.asyncio
async def test_attach_single_and_batch(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"resource_id": "727145299382534146", "slot": "sheet"},
        )
        assert r.status_code == 201 and r.json()["data"]["slot"] == "sheet"
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={
                "items": [{"resource_id": "1"}, {"resource_id": "2", "slot": "stills"}]
            },
        )
    assert r.status_code == 201 and len(r.json()["data"]) == 2


@pytest.mark.asyncio
async def test_link_422_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/links?scope_id=9000",
            json={"to_asset_id": "6", "relation": "wears"},
        )
    assert r.status_code == 422 and r.json()["error"]["code"] == "link_not_allowed"


# ── I-1: ids that reach int() are pinned at the boundary ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/assets?scope_id=abc",
        "/api/v1/assets?scope_id=",
        "/api/v1/assets?scope_id=9000&project_id=abc",
        "/api/v1/assets/5?scope_id=abc",
        "/api/v1/projects/abc/assets",
    ],
)
async def test_non_numeric_ids_are_422_not_500(app, url):
    """A bare str param would reach int() and raise ValueError → 500."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(url)
    assert r.status_code == 422, r.text


# ── I-2: batch attach is all-or-nothing ────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_attach_stops_at_first_failure_and_reports_it(app):
    app.state.fake.fail_on_resource_id = "2"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={
                "items": [
                    {"resource_id": "1"},
                    {"resource_id": "2"},
                    {"resource_id": "3"},
                ]
            },
        )
    assert r.status_code == 404
    body = r.json()
    assert body["success"] is False and body["error"]["code"] == "resource_not_found"
    # Nothing after the failing item was attempted — no work past the raise.
    assert app.state.fake.attach_attempts == ["1", "2"]


@pytest.mark.asyncio
async def test_batch_attach_runs_inside_one_unit_of_work(app, monkeypatch):
    """The rollback of items 1..N-1 is the transaction's job, so the thing to
    pin here is that the loop really is wrapped in one."""
    entered = []

    @asynccontextmanager
    async def _spy():
        entered.append("enter")
        yield None

    monkeypatch.setattr(ar, "unit_of_work", _spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"items": [{"resource_id": "1"}, {"resource_id": "2"}]},
        )
    assert r.status_code == 201
    assert entered == ["enter"], "batch must run in exactly one unit_of_work()"


@pytest.mark.asyncio
async def test_single_attach_opens_no_unit_of_work(app, monkeypatch):
    """One write commits on its own; wrapping it would be pointless overhead."""
    entered = []

    @asynccontextmanager
    async def _spy():
        entered.append("enter")
        yield None

    monkeypatch.setattr(ar, "unit_of_work", _spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"resource_id": "1", "slot": "sheet"},
        )
    assert r.status_code == 201 and entered == []


# ── I-3: personal-project fallback uses the OWNER's team ───────────────────


@pytest.mark.asyncio
async def test_personal_project_resolves_owner_team_not_callers(app, monkeypatch):
    """A collaborator (project_members) on someone else's personal project must
    see the owner's scope; resolving the caller's own team would return an
    empty list that is indistinguishable from an empty library."""
    OWNER = "22222222-2222-2222-2222-222222222222"

    async def _guard_ok(project_id, auth):
        return None

    class _Row:
        def first(self):
            return (OWNER, None)  # team_id NULL → personal project

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _fake_read_scope():
        yield _Session()

    seen = {}

    async def _fake_resolver(user_id):
        seen["user_id"] = user_id
        return "777"

    monkeypatch.setattr(ar, "verify_project_read_access", _guard_ok)
    monkeypatch.setattr(ar, "read_scope", _fake_read_scope)
    monkeypatch.setattr(ar, "_resolve_personal_team_id", _fake_resolver)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/projects/55/assets")

    assert r.status_code == 200
    assert seen["user_id"] == OWNER, "resolved the caller's team, not the owner's"
    _, scope, f = app.state.fake.calls[0]
    assert scope == 777 and f["project_id"] == 55


# ── I2: project-refs are WRITES, and both halves are guarded the same ──────


@pytest.fixture
def guard_spy(monkeypatch):
    """Records which project guard each route reached. ``_project_gate`` resolves
    the guard by module global at call time, so patching the names here is what
    the router really calls."""
    seen = {"read": [], "write": []}

    async def _read(project_id, auth):
        seen["read"].append(str(project_id))

    async def _write(project_id, auth):
        seen["write"].append(str(project_id))

    monkeypatch.setattr(ar, "verify_project_read_access", _read)
    monkeypatch.setattr(ar, "verify_project_write_access", _write)
    return seen


@pytest.mark.asyncio
async def test_link_project_ref_uses_the_write_guard(app, guard_spy):
    """``_resolve_project_access`` grants can_read to ANY project_members row —
    a viewer. Creating an asset_project_refs row is a write."""

    async def _link(asset_id, scope_id, project_id, user_id):
        return None

    app.state.fake.link_project = _link
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/project-refs?scope_id=9000", json={"project_id": "77"}
        )
    assert r.status_code == 201 and r.json()["data"] == {"linked": True}
    assert guard_spy["write"] == ["77"]
    assert guard_spy["read"] == [], "a write path must not gate on read access"


@pytest.mark.asyncio
async def test_unlink_project_ref_uses_the_write_guard(app, guard_spy):
    """This route had NO project guard at all — the two halves of one operation
    were guarded differently."""

    async def _unlink(asset_id, scope_id, project_id):
        return None

    app.state.fake.unlink_project = _unlink
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete("/api/v1/assets/5/project-refs/77?scope_id=9000")
    assert r.status_code == 200 and r.json()["data"] == {"unlinked": True}
    assert guard_spy["write"] == ["77"]
    assert guard_spy["read"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_code",
    [(403, "project_forbidden"), (404, "project_not_found")],
)
async def test_project_guard_refusal_wears_the_asset_envelope(
    app, monkeypatch, status_code, expected_code
):
    """The guards raise FastAPI HTTPExceptions ({"detail": ...}); on this router
    they must come back in the one shape clients branch on."""
    from fastapi import HTTPException

    async def _deny(project_id, auth):
        raise HTTPException(status_code=status_code, detail="nope")

    monkeypatch.setattr(ar, "verify_project_write_access", _deny)
    monkeypatch.setattr(ar, "verify_project_read_access", _deny)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        post = await c.post(
            "/api/v1/assets/5/project-refs?scope_id=9000", json={"project_id": "77"}
        )
        delete = await c.delete("/api/v1/assets/5/project-refs/77?scope_id=9000")
        listing = await c.get("/api/v1/projects/77/assets")
    for r in (post, delete, listing):
        assert r.status_code == status_code, r.text
        body = r.json()
        assert body["success"] is False and body["error"]["code"] == expected_code


# ── M1: a query-string id past int64 is 422, not a 500 at driver BIND ──────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/assets?scope_id=99999999999999999999",
        "/api/v1/assets?scope_id=9000&project_id=99999999999999999999",
        "/api/v1/assets/99999999999999999999?scope_id=9000",
        "/api/v1/projects/99999999999999999999/assets",
    ],
)
async def test_ids_past_int64_are_422_not_500(app, url):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(url)
    assert r.status_code == 422, r.text
