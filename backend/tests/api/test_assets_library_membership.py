"""The wire contract for explicit library membership (mig 448).

Three surfaces, and each has its own way of going wrong silently:

* ``POST``/``DELETE /assets/{id}/library`` — the named action the UI sends. It
  must be gated, typed, and must answer the ROW (the caller re-renders the card
  it just acted on).
* ``GET /assets?library=`` — the shelf's default is ``in``. A default that
  drifted back to ``all`` would restore the pre-448 shelf with nothing failing.
* ``GET /projects/{id}/assets`` — the opposite default, ``all``, and it is NOT
  a caller preference: a script import that landed rows the project page then
  refuses to show is the silent no-op this router exists to prevent.

The service is faked. The membership WRITE itself is the repository's, proved
against a real Postgres in ``tests/db/test_assets_repository_integration.py``;
what this file owns is what the router asks for and what it puts on the wire.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError
from tests.api.test_assets_router import USER, _FakeService, asset_row

PROJECT = "727145299382534301"


class _AuthStub:
    user_id = USER


class _LibraryService(_FakeService):
    """``_FakeService`` plus the membership write, recording what it was asked
    for so the routes can be told apart by more than their status code."""

    def __init__(self):
        super().__init__()
        self.membership_calls: list[tuple[int, int, bool]] = []

    async def set_library_membership(self, asset_id, scope_id, *, in_library):
        self.membership_calls.append((asset_id, scope_id, in_library))
        if asset_id == 404:
            raise AssetError(404, "asset_not_found", "Asset not found")
        if asset_id == 403:
            raise AssetError(
                403,
                "system_preset_readonly",
                "System presets are read-only; duplicate to edit",
            )
        return asset_row(id=str(asset_id), in_library=in_library)


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    async def _project_gate_ok(project_id, auth, *, write):
        return None

    async def _project_scope(project_id):
        return 9000

    @asynccontextmanager
    async def _no_uow():
        yield None

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    monkeypatch.setattr(ar, "_project_gate", _project_gate_ok)
    monkeypatch.setattr(ar, "_project_scope_id", _project_scope)
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _LibraryService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ── the two actions ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_adds_and_answers_the_updated_row(app):
    async with _client(app) as c:
        r = await c.post("/api/v1/assets/5/library?scope_id=9000")

    # 200, not 201: nothing was created, a column moved.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["id"] == "5"
    assert body["data"]["in_library"] is True
    assert app.state.fake.membership_calls == [(5, 9000, True)]


@pytest.mark.asyncio
async def test_delete_removes_and_answers_the_updated_row(app):
    async with _client(app) as c:
        r = await c.delete("/api/v1/assets/5/library?scope_id=9000")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["in_library"] is False
    assert app.state.fake.membership_calls == [(5, 9000, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "delete"])
async def test_a_non_member_is_refused_before_any_write(app, method):
    async with _client(app) as c:
        r = await getattr(c, method)("/api/v1/assets/5/library?scope_id=666")

    assert r.status_code == 403
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }
    assert app.state.fake.membership_calls == [], "the gate ran BEFORE the write"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "asset_id,status,code",
    [(404, 404, "asset_not_found"), (403, 403, "system_preset_readonly")],
)
async def test_service_refusals_reach_the_wire_as_the_error_envelope(
    app, asset_id, status, code
):
    async with _client(app) as c:
        r = await c.post(f"/api/v1/assets/{asset_id}/library?scope_id=9000")

    assert r.status_code == status
    body = r.json()
    assert body["success"] is False and body["error"]["code"] == code


@pytest.mark.asyncio
async def test_the_scope_id_is_required_and_validated(app):
    """Same boundary rule as every other route here: a missing or non-numeric
    scope is a 422 at the edge, not a ValueError deep in ``_is_member``."""
    async with _client(app) as c:
        missing = await c.post("/api/v1/assets/5/library")
        bad = await c.post("/api/v1/assets/5/library?scope_id=abc")

    assert missing.status_code == 422 and bad.status_code == 422
    assert app.state.fake.membership_calls == []


@pytest.mark.asyncio
async def test_the_library_routes_are_not_shadowed_and_do_not_shadow_counts(app):
    """Registration order. ``/assets/{asset_id}`` takes an ``int`` path param,
    so a literal captured by it answers 422 about an id nobody sent. These two
    sit one segment deeper, and ``/assets/counts`` sits above them — this pins
    that all three still resolve to their own handler."""
    routes = {
        (r.path, tuple(sorted(m for m in r.methods if m != "HEAD")))
        for r in app.routes
        if getattr(r, "methods", None)
    }
    assert ("/api/v1/assets/{asset_id}/library", ("POST",)) in routes
    assert ("/api/v1/assets/{asset_id}/library", ("DELETE",)) in routes

    async with _client(app) as c:
        counts = await c.get("/api/v1/assets/counts?scope_id=9000")
        toggle = await c.post("/api/v1/assets/5/library?scope_id=9000")

    assert counts.status_code == 200, "counts is still not captured as an asset id"
    assert toggle.status_code == 200
    assert app.state.fake.membership_calls == [(5, 9000, True)]


@pytest.mark.asyncio
async def test_patching_the_column_is_refused_at_the_wire(app):
    """The other half of "one write path", checked where a client would try it.

    ``AssetUpdate`` does not declare ``in_library`` and forbids extras, so this
    is a 422 from FastAPI's own validation — the service is never reached. That
    is what stops the PATCH surface becoming a second way in that converges with
    these routes only at the repository.
    """
    async with _client(app) as c:
        r = await c.patch("/api/v1/assets/5?scope_id=9000", json={"in_library": False})

    assert r.status_code == 422, r.text
    # Names the offending key, so the refusal is actionable rather than opaque.
    assert "in_library" in r.text
    assert app.state.fake.membership_calls == []


# ── the read defaults ──────────────────────────────────────────────────────


def _last_list(app):
    return [c for c in app.state.fake.calls if c[0] == "list"][-1][2]


@pytest.mark.asyncio
async def test_the_shelf_defaults_to_library_members_only(app):
    async with _client(app) as c:
        r = await c.get("/api/v1/assets?scope_id=9000")

    assert r.status_code == 200, r.text
    assert _last_list(app)["library"] == "in"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["in", "out", "all"])
async def test_every_library_value_is_forwarded_verbatim(app, value):
    async with _client(app) as c:
        r = await c.get(f"/api/v1/assets?scope_id=9000&library={value}")

    assert r.status_code == 200, r.text
    assert _last_list(app)["library"] == value


@pytest.mark.asyncio
async def test_an_unknown_library_value_is_a_422_not_an_unfiltered_shelf(app):
    """Quietly ignoring it would return the whole shelf looking filtered — the
    same rule ``sort`` and ``readiness`` already follow."""
    async with _client(app) as c:
        r = await c.get("/api/v1/assets?scope_id=9000&library=maybe")

    assert r.status_code == 422
    assert not [c for c in app.state.fake.calls if c[0] == "list"]


@pytest.mark.asyncio
async def test_the_project_page_reads_both_membership_states(app):
    """NOT the shelf's default, and not a caller preference. 一键导入 lands rows
    outside the library; a project page that filtered them out would make the
    import look like it did nothing."""
    async with _client(app) as c:
        r = await c.get(f"/api/v1/projects/{PROJECT}/assets")

    assert r.status_code == 200, r.text
    assert _last_list(app)["library"] == "all"
