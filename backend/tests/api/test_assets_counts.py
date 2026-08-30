"""GET /assets/counts — the six sidebar badges.

Three things can go wrong here and only one of them is about arithmetic:

* the route is shadowed by ``/assets/{asset_id}`` (registration order),
* the scope gate is skipped, so one team reads another's tallies,
* a type with no rows is omitted instead of reported as 0, which the sidebar
  renders as a missing badge rather than a zero.

The service is faked — the counting itself is the repository's job and is
proved against a real Postgres in
``tests/db/test_assets_repository_integration.py`` (case 16). What this file
owns is the wire contract on top of it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.models.assets import ASSET_TYPES
from tests.api.test_assets_router import USER, _FakeService


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


@pytest.mark.asyncio
async def test_counts_answers_every_type_in_the_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/counts?scope_id=9000")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    # Every type present, none invented: a sidebar that renders one badge per
    # key must not be handed a seventh, nor be left guessing about a sixth.
    assert set(body["data"]) == set(ASSET_TYPES)
    assert body["data"]["character"] == 2
    assert app.state.fake.calls[-1] == ("count_by_type", 9000)


@pytest.mark.asyncio
async def test_a_type_with_no_rows_is_reported_as_zero_not_omitted(app):
    """The service's zero-fill has to survive the response model.

    ``prompt`` is absent from the fake's dict on purpose. The model's per-field
    default fills it — which is the behaviour the client depends on, since
    ``undefined`` and ``0`` render as two different badges.
    """
    app.state.fake.counts = {"character": 3}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/counts?scope_id=9000")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["character"] == 3
    assert all(data[t] == 0 for t in ASSET_TYPES if t != "character")


@pytest.mark.asyncio
async def test_non_member_gets_the_error_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/counts?scope_id=666")

    assert r.status_code == 403
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }
    # The gate refused BEFORE any counting happened.
    assert not [c for c in app.state.fake.calls if c[0] == "count_by_type"]


@pytest.mark.asyncio
async def test_counts_is_not_captured_as_an_asset_id(app):
    """Route-order regression.

    ``/assets/counts`` is registered above ``/assets/{asset_id}``. Registered
    below it, FastAPI would match "counts" as the ``int`` path param and answer
    422 about an id nobody sent — a failure that looks like bad client input.
    The negative control is the line after: the dynamic route still works, so
    this cannot pass by the single-asset route being broken.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/counts?scope_id=9000")
        other = await c.get("/api/v1/assets/5?scope_id=9000")

    assert r.status_code == 200, r.text
    assert set(r.json()["data"]) == set(ASSET_TYPES)
    assert other.status_code == 200 and other.json()["data"]["id"] == "5"


def test_counts_route_is_registered_before_the_dynamic_one():
    """Reading the order off the router itself, not off one request.

    A future edit that moves the literal route below the dynamic one would
    still pass the request test above on some FastAPI versions; this asserts
    the property the route ordering rests on.
    """
    paths = [getattr(r, "path", "") for r in ar.router.routes]
    assert "/assets/counts" in paths and "/assets/{asset_id}" in paths
    assert paths.index("/assets/counts") < paths.index("/assets/{asset_id}")


@pytest.mark.asyncio
async def test_a_missing_type_key_cannot_be_shipped_as_a_partial_body(app):
    """The response model is enforced, not decoration — a service that answered
    with a non-int for a type fails loudly instead of shipping a badge the
    client renders as NaN."""
    app.state.fake.counts = {"character": "many"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with pytest.raises(ResponseValidationError):
            await c.get("/api/v1/assets/counts?scope_id=9000")
