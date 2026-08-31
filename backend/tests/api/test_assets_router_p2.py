"""P2 router surface: list filters, declared response models, and the read half
of the personal-team resolution failure.

The Envelope tests are the load-bearing ones. Before P2 every route answered
with a bare ``JSONResponse``, so the payload shape was whatever the service
happened to return: nothing checked that a field the client reads was still
there, and the generated OpenAPI advertised ``{}`` for both success and
failure. Declaring ``response_model=Envelope[...]`` makes FastAPI validate the
real payload on the way out — which only helps as long as EVERY route carries
one, hence the sweep below rather than a spot check.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.schemas.assets import Envelope, ErrorEnvelope
from app.services.assets.assets_service import AssetError
from tests.api.test_assets_router import USER, _FakeService, asset_row


class _AuthStub:
    user_id = USER


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
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


def _asset_routes(app):
    return [
        r
        for r in app.routes
        if getattr(r, "endpoint", None) is not None
        and getattr(r.endpoint, "__module__", "") == ar.__name__
    ]


# ── list filters ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_filters_reach_the_service(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/assets?scope_id=9000&readiness=draft&tag=hero&sort=name"
        )
    assert r.status_code == 200, r.text
    _, _, f = app.state.fake.calls[0]
    assert f["readiness"] == "draft" and f["tag"] == "hero" and f["sort"] == "name"


@pytest.mark.asyncio
async def test_filters_default_to_unset_and_recent(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/api/v1/assets?scope_id=9000")
    _, _, f = app.state.fake.calls[0]
    assert f["readiness"] is None and f["tag"] is None and f["sort"] == "recent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qs",
    [
        "readiness=partial",  # not a readiness state
        "readiness=",
        "sort=updated_at",  # not one of the three offered orderings
        "sort=name;DROP",
        "tag=" + "x" * 201,  # past the length bound
    ],
)
async def test_bad_filter_values_are_422(app, qs):
    """An unknown value must be refused, not quietly ignored — a filter that
    silently does nothing returns a full shelf that looks filtered."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/assets?scope_id=9000&{qs}")
    assert r.status_code == 422, r.text


# ── every route declares the envelope, in both directions ──────────────────


def test_every_route_declares_an_envelope_response_model(app):
    routes = _asset_routes(app)
    assert routes, "no assets routes discovered — the sweep would pass vacuously"
    for route in routes:
        model = route.response_model
        assert model is not None, f"{route.path} has no response_model"
        origin = getattr(model, "__pydantic_generic_metadata__", {}).get("origin")
        assert origin is Envelope, f"{route.path} answers {model!r}, not Envelope[...]"


@pytest.mark.parametrize("status_code", [403, 404, 409, 422])
def test_every_route_documents_the_error_envelope(app, status_code):
    for route in _asset_routes(app):
        declared = route.responses.get(status_code, {}).get("model")
        assert (
            declared is ErrorEnvelope
        ), f"{route.path} does not document {status_code} as ErrorEnvelope"


@pytest.mark.asyncio
async def test_response_validation_is_live_not_merely_declared(app):
    """A declared response_model that is never enforced is decoration. Feed the
    router a row missing a required field and it must refuse to answer."""

    async def _bad_list(scope_id, **f):
        row = asset_row()
        del row["readiness"]
        return [row]

    app.state.fake.list_assets = _bad_list
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with pytest.raises(ResponseValidationError):
            await c.get("/api/v1/assets?scope_id=9000")


@pytest.mark.asyncio
async def test_success_envelope_still_has_success_and_data(app):
    """The wire shape clients already branch on must be byte-compatible."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=9000")
    body = r.json()
    assert body["success"] is True
    assert body["data"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_error_envelope_is_not_reshaped_by_the_response_model(app):
    """``_err`` returns a JSONResponse, which bypasses response_model — the 403
    body must keep its ``error`` object rather than being coerced into the
    success envelope (or 500ing on validation)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=666")
    assert r.status_code == 403
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }


def test_openapi_advertises_both_envelopes(app):
    """The generated contract is what the frontend's types are read off."""
    schema = app.openapi()
    responses = schema["paths"]["/api/v1/assets"]["get"]["responses"]
    ok = responses["200"]["content"]["application/json"]["schema"]["$ref"]
    assert "Envelope" in ok
    forbidden = responses["403"]["content"]["application/json"]["schema"]["$ref"]
    assert forbidden.endswith("ErrorEnvelope")


# ── GET /projects/{id}/assets: the owner has no personal team ──────────────


@pytest.mark.asyncio
async def test_personal_project_without_owner_team_is_422_not_500(app, monkeypatch):
    """``_resolve_personal_team_id`` raises ValueError for a legacy owner with no
    personal team. Letting it out was an untyped 500 on a read whose honest
    answer is "this project's asset scope cannot be resolved" — the write half
    (link_project) already answered that way."""
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

    async def _no_team(user_id):
        raise ValueError(f"No personal team found for user {user_id}")

    monkeypatch.setattr(ar, "verify_project_read_access", _guard_ok)
    monkeypatch.setattr(ar, "read_scope", _fake_read_scope)
    monkeypatch.setattr(ar, "_resolve_personal_team_id", _no_team)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/projects/55/assets")

    assert r.status_code == 422, r.text
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "personal_team_missing"
    assert app.state.fake.calls == [], "no scope resolved → no listing attempted"


@pytest.mark.asyncio
async def test_service_asset_error_from_the_list_still_maps(app):
    """Sanity that the try/except around the read half did not swallow the
    service's own typed failures."""

    async def _boom(scope_id, **f):
        raise AssetError(403, "not_a_member", "nope")

    app.state.fake.list_assets = _boom
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=9000")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"


# ── POST /assets/{id}/duplicate ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_answers_201_with_the_new_asset_detail(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets/5/duplicate?scope_id=9000", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["id"] == "99"
    assert body["data"]["source"] == "duplicated"
    assert body["data"]["duplicated_from"] == "5"
    # The detail envelope, not the summary one: the caller opens the copy next.
    assert body["data"]["loadouts"] == [] and body["data"]["files"] == []
    assert app.state.fake.calls[0] == ("duplicate", 9000, {"asset_id": 5, "name": None})


@pytest.mark.asyncio
async def test_duplicate_passes_an_explicit_name_through(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/duplicate?scope_id=9000", json={"name": "Sang Yao (v2)"}
        )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["name"] == "Sang Yao (v2)"
    assert app.state.fake.calls[0][2]["name"] == "Sang Yao (v2)"


@pytest.mark.asyncio
async def test_duplicate_name_collision_keeps_the_error_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets/409/duplicate?scope_id=9000", json={})
    assert r.status_code == 409
    assert r.json() == {
        "success": False,
        "error": {
            "code": "asset_exists",
            "detail": "exists",
            "existing_asset_id": "7",
        },
    }


@pytest.mark.asyncio
async def test_duplicate_rejects_an_unknown_body_field(app):
    """``extra="forbid"``: a typo'd ``naem`` would otherwise answer 201 with a
    copy under the DEFAULT name — a request that did something else and said
    it succeeded."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/duplicate?scope_id=9000", json={"naem": "typo"}
        )
    assert r.status_code == 422, r.text
    assert app.state.fake.calls == []
