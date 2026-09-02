"""HTTP surface of ``GET /assets/{id}/bundle`` (asset lib P4 Task 2).

Two things only a router test can answer:

* **the wire shape**, validated by the declared ``Envelope[BundleResponse]``
  rather than by whatever the service happened to return — ids as STRINGS
  (bigIntSafeFetch discipline) and ``dropped`` present even when empty;
* **route ordering**. ``/assets/{asset_id}/bundle`` sits among
  ``/assets/counts`` (a literal that must keep winning against the id
  placeholder) and the P4 sibling ``/assets/{asset_id}/canvas-refs``. FastAPI
  matches in registration order, so a route added in the wrong place answers
  the wrong handler — with a 200, which no assertion about status would catch.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError

USER = "11111111-1111-1111-1111-111111111111"
ASSET_ID = "727145299382534145"
MODEL = "mediahub-doubao-seedream-t2i"

_BUNDLE = {
    "prompt": {
        "positive": "a swordswoman, a long coat",
        "negative": "glasses, wrinkles",
    },
    # Strings on the wire: these are BIGINT resource ids, and a JSON number
    # past 2**53 loses precision in the browser.
    "reference_resource_ids": ["727145299382534146", "727145299382534147"],
    "dropped": [{"resource_id": "727145299382534148", "reason": "over_limit"}],
    "max_refs": 2,
}


class _AuthStub:
    user_id = USER


class _FakeService:
    """Records every argument the route handed down.

    ``model`` and ``user_id`` are the two that decide the answer — the ceiling
    comes from the model, and an owner-scoped catalog row is visible only to
    its owner — so both are asserted, not merely a 200.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self.result = dict(_BUNDLE)
        self.error: AssetError | None = None

    async def get_bundle(
        self, asset_id, scope_id, *, model, loadout_id, selected_file_ids, user_id
    ):
        # ``selected_file_ids`` is REQUIRED here, not defaulted: the router
        # always passes it, and a fake that quietly accepted its absence would
        # let a route that stopped forwarding the card's checklist pass. It is
        # also recorded RAW, because the three wire states (absent / given /
        # given-but-empty) are three different answers.
        self.calls.append(
            (asset_id, scope_id, model, loadout_id, selected_file_ids, user_id)
        )
        if self.error is not None:
            raise self.error
        return self.result

    async def list_canvas_refs(self, asset_id, scope_id):
        self.calls.append(("canvas-refs", asset_id, scope_id))
        return []

    async def count_by_type(self, scope_id):
        self.calls.append(("counts", scope_id))
        return {
            "character": 0,
            "location": 0,
            "prop": 0,
            "costume": 0,
            "prompt": 0,
            "audio": 0,
        }


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    @asynccontextmanager
    async def _no_uow():
        yield None

    application.dependency_overrides[get_auth] = _auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest_asyncio.fixture
async def client(app) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_the_wire_shape_survives_the_response_model(app, client):
    resp = await client.get(
        f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": True, "data": _BUNDLE}


@pytest.mark.asyncio
async def test_model_loadout_and_caller_all_reach_the_service(app, client):
    await client.get(
        f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}&loadout_id=400"
    )

    assert app.state.fake.calls == [(int(ASSET_ID), 9000, MODEL, "400", None, USER)]


@pytest.mark.asyncio
async def test_loadout_is_optional_and_arrives_as_none(app, client):
    await client.get(f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}")

    assert app.state.fake.calls[0][3] is None


@pytest.mark.asyncio
async def test_a_missing_model_is_422_not_a_default(client):
    """The answer depends on the provider, so there is no honest default: a
    bundle silently trimmed to somebody else's ceiling is a wrong answer that
    looks right."""
    resp = await client.get(f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000")

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("qs", ["model=", "model=" + "x" * 101, "loadout_id=abc"])
async def test_bad_query_values_are_422(client, qs):
    resp = await client.get(f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&{qs}")

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_a_non_member_gets_the_asset_error_envelope(client):
    resp = await client.get(
        f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=666&model={MODEL}"
    )

    assert resp.status_code == 403
    assert resp.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }


@pytest.mark.asyncio
async def test_model_unknown_keeps_its_extra_on_the_wire(app, client):
    """The refusal names the model back. A bare "422 model_unknown" leaves the
    client unable to say WHICH of the two models on the node was rejected."""
    app.state.fake.error = AssetError(
        422, "model_unknown", "Model 'ghost' is not available", {"model": "ghost"}
    )

    resp = await client.get(
        f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model=ghost"
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "model_unknown"
    assert resp.json()["error"]["model"] == "ghost"


@pytest.mark.asyncio
async def test_an_invented_drop_reason_fails_loudly(app, client):
    """``DroppedReason`` is a closed vocabulary, and the response model is what
    enforces it: a reason the UI has no string for would otherwise render as a
    blank row — the silent drop this field exists to end."""
    app.state.fake.result = {
        **_BUNDLE,
        "dropped": [{"resource_id": "1", "reason": "because"}],
    }

    with pytest.raises(Exception):
        await client.get(
            f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}"
        )


@pytest.mark.asyncio
async def test_dropped_is_required_not_defaulted_to_empty(app, client):
    """A service that stopped reporting drops must fail, not answer "nothing
    was dropped"."""
    app.state.fake.result = {k: v for k, v in _BUNDLE.items() if k != "dropped"}

    with pytest.raises(Exception):
        await client.get(
            f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}"
        )


# ── the card's checklist, on the wire (C1) ─────────────────────────────────
#
# THREE states, and a query string can only tell them apart because the empty
# one is spelled `?selected_file_ids=` rather than by omitting the parameter.
# These are the pin on that spelling: collapse "absent" and "empty" into each
# other and unticking every box on a card ships every reference the user just
# removed — the endpoint reads absent as "no checklist, send them all".


async def _selection(app, client, query: str):
    resp = await client.get(
        f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}{query}"
    )
    assert resp.status_code == 200, resp.text
    return app.state.fake.calls[-1][4]


@pytest.mark.asyncio
async def test_an_absent_selection_reaches_the_service_as_none(app, client):
    """The asset sheet's request, unchanged by this parameter's arrival — and
    the reason the sheet-side callers needed no edit."""
    assert await _selection(app, client, "") is None


@pytest.mark.asyncio
async def test_a_repeated_selection_arrives_in_order(app, client):
    picked = await _selection(
        app, client, "&selected_file_ids=727145299382534146&selected_file_ids=8"
    )
    assert picked == ("727145299382534146", "8")


@pytest.mark.asyncio
async def test_an_empty_selection_is_empty_not_absent(app, client):
    """`?selected_file_ids=` is "the user unticked everything"."""
    picked = await _selection(app, client, "&selected_file_ids=")
    assert picked == ()
    assert picked is not None


@pytest.mark.asyncio
async def test_a_blank_entry_among_real_ids_is_dropped_not_carried(app, client):
    picked = await _selection(app, client, "&selected_file_ids=7&selected_file_ids=%20")
    assert picked == ("7",)


@pytest.mark.asyncio
async def test_an_over_long_selection_entry_is_refused_at_the_boundary(client):
    resp = await client.get(
        f"/api/v1/assets/{ASSET_ID}/bundle?scope_id=9000&model={MODEL}"
        "&selected_file_ids=" + ("9" * 41)
    )
    assert resp.status_code == 422


# ── route ordering ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bundle_does_not_shadow_the_sibling_id_sub_paths(app, client):
    """``/assets/{id}/canvas-refs`` (P4 Task 1) must still reach its own
    handler, and ``/assets/counts`` must still beat the id placeholder."""
    refs = await client.get(f"/api/v1/assets/{ASSET_ID}/canvas-refs?scope_id=9000")
    counts = await client.get("/api/v1/assets/counts?scope_id=9000")

    assert refs.status_code == 200 and counts.status_code == 200
    kinds = [c[0] for c in app.state.fake.calls]
    assert kinds == ["canvas-refs", "counts"]


def test_the_bundle_route_is_registered_exactly_once(app):
    paths = [r.path for r in app.routes if getattr(r, "path", "").endswith("/bundle")]

    assert paths == ["/api/v1/assets/{asset_id}/bundle"]
