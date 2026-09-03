"""GET /assets/resolve-legacy — the pre-P3 canvas card → asset lookup.

A canvas saved before P3 holds ``character`` / ``location`` / ``prop`` cards
keyed by ``_legacy_project_*`` row ids. Those are NOT ``assets.id``, so the
canvas has to ask which asset the migration produced before it can treat one as
an asset at all.

Four things can go wrong and only one of them is about the query:

* the route is shadowed by ``/assets/{asset_id}`` (registration order),
* the scope gate is skipped, so one team reads another's provenance,
* "no asset carries that provenance" is reported as a failure instead of as the
  answer it is, or the reverse — a real refusal arriving as ``null``,
* the kind → table label mapping drifts (covered next door, in
  ``tests/services/assets/test_legacy_refs_mirror.py``).

The service is faked. The containment predicate itself is pinned by
``tests/services/assets/test_assets_repository_sql.py`` and executed against a
real PostgreSQL by ``tests/db/test_assets_repository_integration.py`` (case 21).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError
from tests.api.test_assets_router import USER, _FakeService

SCOPE = "727145299382534200"
OTHER_SCOPE = "666"
ASSET = "700000000000000001"


class _AuthStub:
    user_id = USER


class _LegacyService(_FakeService):
    """The shelf fake plus the one method this route calls.

    ``found`` maps ``(kind, legacy_id)`` → asset id; anything else answers the
    real service's "nothing migrated" shape, which is a 200 body with a null.
    """

    def __init__(self):
        super().__init__()
        self.found: dict[tuple[str, int], str] = {("character", 12): ASSET}
        self.legacy_calls: list[tuple] = []

    async def resolve_legacy(self, scope_id, kind, legacy_id):
        self.legacy_calls.append((scope_id, kind, int(legacy_id)))
        if kind not in ("character", "location", "prop"):
            raise AssetError(422, "unknown_legacy_kind", f"Not a legacy kind: {kind!r}")
        return {"asset_id": self.found.get((kind, int(legacy_id)))}


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != OTHER_SCOPE

    @asynccontextmanager
    async def _no_uow():
        yield None

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _LegacyService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


def _url(kind="character", legacy_id="12", scope=SCOPE) -> str:
    return f"/api/v1/assets/resolve-legacy?scope_id={scope}&kind={kind}&legacy_id={legacy_id}"


@pytest.mark.asyncio
async def test_a_migrated_character_resolves_to_its_asset_id(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url())

    assert r.status_code == 200, r.text
    assert r.json() == {"success": True, "data": {"asset_id": ASSET}}
    # The gated scope, and the id as an int, reached the service.
    assert app.state.fake.legacy_calls == [(int(SCOPE), "character", 12)]


@pytest.mark.asyncio
async def test_the_id_stays_a_string_on_the_wire(app):
    """``assets.id`` is a Snowflake BIGINT — a JSON number loses precision in
    the browser, which is how an id turns into a neighbouring row's id."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url())

    assert isinstance(r.json()["data"]["asset_id"], str)


@pytest.mark.asyncio
async def test_nothing_migrated_is_a_200_with_an_explicit_null(app):
    """A real answer, not a failure: the request was well formed, and the
    caller acts on it by keeping the legacy card as it is. A 404 would say the
    request was wrong; a missing key would read like a truncated body."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url(legacy_id="99"))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert "asset_id" in body["data"] and body["data"]["asset_id"] is None


@pytest.mark.asyncio
async def test_a_non_member_is_refused_before_anything_is_read(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url(scope=OTHER_SCOPE))

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_a_member"
    # Not "answered null": the provenance of another team's assets was never
    # queried. An empty answer here would leak that the scope exists and is
    # empty, and would read to the client exactly like a legitimate miss.
    assert app.state.fake.legacy_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["costume", "prompt", "audio", "shot", ""])
async def test_a_kind_that_never_had_legacy_ids_is_a_422(app, kind):
    """Refused at the boundary by the route's own pattern. Answering ``null``
    for a kind the resolver cannot map would report "not migrated" for a
    question that was never askable."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url(kind=kind))

    assert r.status_code == 422
    assert app.state.fake.legacy_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_id", ["abc", "-1", "", "9" * 21])
async def test_a_legacy_id_that_is_not_a_snowflake_is_a_422(app, legacy_id):
    """Same boundary rule as every other id on this router: a bare string would
    reach ``int()`` and surface as a 500, and a 21-digit value parses fine in
    Python and then fails at driver BIND."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url(legacy_id=legacy_id))

    assert r.status_code == 422
    assert app.state.fake.legacy_calls == []


@pytest.mark.asyncio
async def test_location_and_prop_are_both_accepted(app):
    """They shared one legacy table, so both have to reach the resolver — the
    kind is what tells it which label to look for."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for kind in ("location", "prop"):
            assert (await c.get(_url(kind=kind))).status_code == 200

    assert [k for _s, k, _i in app.state.fake.legacy_calls] == ["location", "prop"]


@pytest.mark.asyncio
async def test_resolve_legacy_is_not_captured_as_an_asset_id(app):
    """Route-order regression.

    Registered below ``/assets/{asset_id}``, FastAPI would match
    "resolve-legacy" as the ``int`` path param and answer 422 about an id
    nobody sent — a failure that looks like bad client input. The line after is
    the negative control: the dynamic route still works, so this cannot pass by
    the single-asset route being broken.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(_url())
        other = await c.get(f"/api/v1/assets/5?scope_id={SCOPE}")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["asset_id"] == ASSET
    assert other.status_code == 200 and other.json()["data"]["id"] == "5"


def test_resolve_legacy_route_is_registered_before_the_dynamic_one():
    """Reading the order off the router itself, not off one request — a future
    edit that moved the literal below the dynamic route would still pass the
    request test above on some FastAPI versions."""
    paths = [getattr(r, "path", "") for r in ar.router.routes]
    assert "/assets/resolve-legacy" in paths and "/assets/{asset_id}" in paths
    assert paths.index("/assets/resolve-legacy") < paths.index("/assets/{asset_id}")
