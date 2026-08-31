"""/generated endpoints: filter translation + envelopes + scope gating (no DB).

Same shape as ``tests/api/test_assets_router.py`` — the service is a fake, so
what is under test is the HTTP boundary: which arguments the query string turns
into, which failures become which envelope, and whether the response body can
actually be serialised.

The fake returns rows in the REAL service shape — ``created_at`` is a
``datetime`` object, because ``GeneratedInboxService`` builds items with
``model_dump()`` (not ``mode="json"``). A router that hands that straight to
``JSONResponse`` raises ``TypeError`` at render time, i.e. a 500 on the happy
path that no fixture with a pre-stringified timestamp would ever catch.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.api import generated_router as gr
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError

USER = "11111111-1111-1111-1111-111111111111"
SCOPE = "727145299382534200"
GEN = "800000000000000001"
ASSET = "700000000000000001"
RESOURCE = "600000000000000001"
NOW = datetime.datetime(2026, 8, 20, 12, 0, tzinfo=datetime.timezone.utc)


class _AuthStub:
    user_id = USER


def make_item(**kw) -> dict:
    """What ``GeneratedInboxService._build_item`` really returns."""
    item = {
        "id": GEN,
        "scope_id": SCOPE,
        "media_kind": "image",
        "mime": "image/png",
        "prompt": "A wide shot of the harbour.",
        "model": "doubao-seedream",
        "provider": "volcengine",
        "origin_kind": "canvas_run",
        "canvas_id": "900000000000000001",
        "node_id": "node-7",
        "created_at": NOW,
        "promoted_resource_id": None,
        "review_state": "unreviewed",
        "source_asset_id": None,
        "source": {
            "kind": "canvas_run",
            "label": "Canvas · Harbour",
            "canvas_id": "900000000000000001",
            "node_id": "node-7",
            "shot_id": None,
            "conversation_id": None,
            "deep_link": "/projects/1/canvas/900000000000000001",
        },
        "title": "A wide shot of the harbour",
    }
    item.update(kw)
    return item


class _FakeService:
    def __init__(self):
        self.calls: list[tuple] = []
        self.raise_on_save_as_asset: AssetError | None = None

    async def list(self, scope_id, team_id, **f):
        self.calls.append(("list", scope_id, team_id, f))
        return {"items": [make_item()], "next_cursor": "cur-2"}

    async def counts(self, scope_id):
        self.calls.append(("counts", scope_id))
        return {"unreviewed": 4, "saved": 2, "in_assets": 1}

    async def save(self, gen_id, scope_id, user_id):
        self.calls.append(("save", gen_id, scope_id, user_id))
        return make_item(review_state="saved", promoted_resource_id=RESOURCE)

    async def save_as_asset(self, gen_id, scope_id, user_id, req):
        self.calls.append(("save_as_asset", gen_id, scope_id, user_id, req))
        if self.raise_on_save_as_asset is not None:
            raise self.raise_on_save_as_asset
        return {
            "generation": make_item(review_state="in_assets"),
            "asset_id": ASSET,
            "resource_id": RESOURCE,
        }

    async def delete(self, gen_id, scope_id):
        self.calls.append(("delete", gen_id, scope_id))

    async def batch(self, req, scope_id, user_id):
        self.calls.append(("batch", req, scope_id, user_id))
        return {"ok": [GEN], "failed": []}

    async def cleanup(self, req, scope_id):
        self.calls.append(("cleanup", req, scope_id))
        return {
            "dry_run": req.dry_run,
            "count": 3,
            "sample": [make_item()],
            "deleted": 0,
            "truncated": False,
        }


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(gr.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    application.dependency_overrides[get_auth] = _fake_auth
    # ``_gate`` is imported from assets_router, so the membership check has to
    # be patched where it is DEFINED — patching a re-export here would leave
    # the real query running and the test would silently hit the database.
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    fake = _FakeService()
    monkeypatch.setattr(gr, "_service", lambda: fake)
    application.state.fake = fake
    return application


def _filters(app) -> dict:
    return next(c for c in app.state.fake.calls if c[0] == "list")[3]


# ── list ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_defaults_to_unreviewed_and_serialises(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["next_cursor"] == "cur-2"
    # The datetime survived the boundary as an ISO string, not a 500.
    assert body["data"]["items"][0]["created_at"].startswith("2026-08-20T12:00:00")
    _, scope_id, team_id, f = next(c for c in app.state.fake.calls if c[0] == "list")
    assert scope_id == int(SCOPE) and team_id == SCOPE
    assert f["state"] == "unreviewed"
    assert f["limit"] == 60


@pytest.mark.asyncio
async def test_state_all_passes_none(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&state=all")
    assert r.status_code == 200
    assert _filters(app)["state"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unreviewed", "saved", "in_assets"])
async def test_state_is_passed_through(app, state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&state={state}")
    assert r.status_code == 200
    assert _filters(app)["state"] == state


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["deleted", "bogus", ""])
async def test_invalid_state_is_422(app, state):
    """``deleted`` is a real column value but not a tab: accepting it here would
    expose a listing the product deliberately has no surface for."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&state={state}")
    assert r.status_code == 422
    assert not [c for c in app.state.fake.calls if c[0] == "list"]


@pytest.mark.asyncio
async def test_origin_kind_is_repeatable(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            f"/api/v1/generated?scope_id={SCOPE}"
            "&origin_kind=canvas_run&origin_kind=chat"
        )
    assert r.status_code == 200
    assert _filters(app)["origin_kinds"] == ["canvas_run", "chat"]


@pytest.mark.asyncio
async def test_absent_origin_kind_is_none_not_empty_list(app):
    """``[]`` and ``None`` are the same filter today, but only ``None`` says
    "no filter" at the repo boundary — an empty IN () is a different query."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}")
    assert r.status_code == 200
    assert _filters(app)["origin_kinds"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["Canvas_Run", "canvas-run", "canvas run", "a" * 41])
async def test_bad_origin_kind_is_422(app, bad):
    """Every origin_kind a writer emits is lowercase-with-underscores. A
    caller typo answered with an empty page reads exactly like "nothing
    generated here yet" — the wrong answer that looks right. ``Query`` has no
    per-item bound (``max_length`` there counts LIST items), so the bound
    lives on the item type."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&origin_kind={bad}")
    assert r.status_code == 422, r.text
    assert not [call for call in app.state.fake.calls if call[0] == "list"]


@pytest.mark.asyncio
async def test_one_bad_origin_kind_rejects_the_whole_request(app):
    """Not "drop the bad one and filter by the rest": a silently narrowed
    filter returns a plausible page that answers a question nobody asked."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            f"/api/v1/generated?scope_id={SCOPE}&origin_kind=canvas_run&origin_kind=X"
        )
    assert r.status_code == 422
    assert not [call for call in app.state.fake.calls if call[0] == "list"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "good", ["canvas_run", "chat_upload", "shot_generate", "unknown"]
)
async def test_real_origin_kinds_still_pass(app, good):
    """The bound must not reject what the writers actually store — including
    ``unknown``, the label the service substitutes for a NULL column."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&origin_kind={good}")
    assert r.status_code == 200, r.text
    assert _filters(app)["origin_kinds"] == [good]


@pytest.mark.asyncio
async def test_naive_since_is_read_as_utc(app):
    """A ``since`` with no offset is UTC, not the server's local zone. The
    difference is a silent window shift of however many hours the deploy host
    happens to be from UTC — same 200, different rows."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&since=2026-08-01T00:00:00")
    assert r.status_code == 200, r.text
    since = _filters(app)["since"]
    assert since.tzinfo is not None
    assert since == datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)


@pytest.mark.asyncio
async def test_aware_since_keeps_its_offset(app):
    """Normalising the naive case must not rewrite an explicit offset."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            f"/api/v1/generated?scope_id={SCOPE}&since=2026-08-01T08:00:00%2B08:00"
        )
    assert r.status_code == 200, r.text
    since = _filters(app)["since"]
    assert since == datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
    assert since.utcoffset() == datetime.timedelta(hours=8)


@pytest.mark.asyncio
async def test_remaining_filters_are_typed(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            f"/api/v1/generated?scope_id={SCOPE}&project_id=55&media_kind=video"
            "&model=seedance&since=2026-08-01T00:00:00Z&cursor=abc&limit=5"
        )
    assert r.status_code == 200, r.text
    f = _filters(app)
    assert f["project_id"] == 55 and isinstance(f["project_id"], int)
    assert f["media_kind"] == "video" and f["model"] == "seedance"
    assert f["since"] == datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
    assert f["cursor"] == "abc" and f["limit"] == 5


@pytest.mark.asyncio
async def test_source_asset_id_is_typed_and_passed_through(app):
    """The asset-history filter reaches the service as an int, not a string."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            f"/api/v1/generated?scope_id={SCOPE}&state=all&source_asset_id={ASSET}"
        )
    assert r.status_code == 200, r.text
    f = _filters(app)
    assert f["source_asset_id"] == int(ASSET) and isinstance(f["source_asset_id"], int)


@pytest.mark.asyncio
async def test_absent_source_asset_id_is_none_not_zero(app):
    """The negative control. ``0`` would filter on an asset id nothing has,
    turning the default inbox into an empty page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}")
    assert r.status_code == 200, r.text
    assert _filters(app)["source_asset_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "99999999999999999999", "-1"])
async def test_bad_source_asset_id_is_422_not_500(app, bad):
    """Same validator as every other id query param: a bare ``str`` would
    reach ``int()`` (or the driver) and surface as a 500."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&source_asset_id={bad}")
    assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["0", "201"])
async def test_limit_is_bounded(app, limit):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&limit={limit}")
    assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "99999999999999999999"])
async def test_out_of_range_scope_id_is_422_not_500(app, bad):
    """Both would reach ``int()``/the driver and surface as a 500 if the query
    param were a bare ``str``."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={bad}")
    assert r.status_code == 422


# ── gate ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url,body",
    [
        ("get", "/api/v1/generated?scope_id=666", None),
        ("get", "/api/v1/generated/counts?scope_id=666", None),
        ("post", f"/api/v1/generated/{GEN}/save?scope_id=666", None),
        (
            "post",
            f"/api/v1/generated/{GEN}/save-as-asset?scope_id=666",
            {"asset_id": ASSET},
        ),
        ("delete", f"/api/v1/generated/{GEN}?scope_id=666", None),
        (
            "post",
            "/api/v1/generated/batch?scope_id=666",
            {"ids": [GEN], "action": "save"},
        ),
        ("post", "/api/v1/generated/cleanup?scope_id=666", {}),
    ],
)
async def test_every_route_uses_the_error_envelope_for_non_members(
    app, method, url, body
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await getattr(c, method)(
            url, **({"json": body} if body is not None else {})
        )
    assert r.status_code == 403, r.text
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }
    assert app.state.fake.calls == []


# ── counts / save / delete ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_counts(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated/counts?scope_id={SCOPE}")
    assert r.status_code == 200
    assert r.json()["data"] == {"unreviewed": 4, "saved": 2, "in_assets": 1}
    assert app.state.fake.calls[0] == ("counts", int(SCOPE))


@pytest.mark.asyncio
async def test_save_returns_the_item(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/generated/{GEN}/save?scope_id={SCOPE}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["review_state"] == "saved"
    assert app.state.fake.calls[0] == ("save", int(GEN), int(SCOPE), USER)


@pytest.mark.asyncio
async def test_delete(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete(f"/api/v1/generated/{GEN}?scope_id={SCOPE}")
    assert r.status_code == 200
    assert r.json()["data"] == {"deleted": True}
    assert app.state.fake.calls[0] == ("delete", int(GEN), int(SCOPE))


# ── save-as-asset ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_as_asset_201(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/{GEN}/save-as-asset?scope_id={SCOPE}",
            json={"asset_id": ASSET, "slot": "sheet"},
        )
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["asset_id"] == ASSET and data["resource_id"] == RESOURCE
    assert data["generation"]["review_state"] == "in_assets"
    assert data["generation"]["created_at"].startswith("2026-08-20T12:00:00")
    req = app.state.fake.calls[0][4]
    assert req.asset_id == ASSET and req.slot == "sheet"


@pytest.mark.asyncio
async def test_save_as_asset_conflict_keeps_the_code(app):
    app.state.fake.raise_on_save_as_asset = AssetError(
        409, "file_missing", "backing file is gone", {"gen_id": GEN}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/{GEN}/save-as-asset?scope_id={SCOPE}",
            json={"new_asset": {"asset_type": "prop", "name": "Blade"}},
        )
    assert r.status_code == 409
    err = r.json()
    assert err["success"] is False
    assert err["error"]["code"] == "file_missing"
    assert err["error"]["gen_id"] == GEN


@pytest.mark.asyncio
async def test_save_as_asset_requires_exactly_one_target(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/{GEN}/save-as-asset?scope_id={SCOPE}", json={}
        )
    assert r.status_code == 422
    assert app.state.fake.calls == []


# ── batch ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_passes_the_request_through(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/batch?scope_id={SCOPE}",
            json={"ids": [GEN], "action": "delete"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"ok": [GEN], "failed": []}
    _, req, scope_id, user_id = app.state.fake.calls[0]
    assert req.action == "delete" and scope_id == int(SCOPE) and user_id == USER


@pytest.mark.asyncio
async def test_batch_422_when_save_as_asset_payload_is_missing(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/batch?scope_id={SCOPE}",
            json={"ids": [GEN], "action": "save_as_asset"},
        )
    assert r.status_code == 422
    assert app.state.fake.calls == []


# ── cleanup ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_defaults_to_dry_run(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/generated/cleanup?scope_id={SCOPE}", json={})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["dry_run"] is True and body["count"] == 3
    # The scan cap has to be visible on the wire: "3 matched" from a capped
    # pass is not "3 exist", and a UI that shows the former as the latter tells
    # the user the cleanup is done when it is not.
    assert body["truncated"] is False
    assert body["sample"][0]["created_at"].startswith("2026-08-20T12:00:00")
    req = app.state.fake.calls[0][1]
    assert req.dry_run is True and req.older_than_days == 30


@pytest.mark.asyncio
async def test_cleanup_live_run_is_explicit(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/cleanup?scope_id={SCOPE}",
            json={"dry_run": False, "older_than_days": 90},
        )
    assert r.status_code == 200
    req = app.state.fake.calls[0][1]
    assert req.dry_run is False and req.older_than_days == 90


@pytest.mark.asyncio
async def test_cleanup_rejects_unknown_fields(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/api/v1/generated/cleanup?scope_id={SCOPE}", json={"dryrun": False}
        )
    assert r.status_code == 422


# ── registration ───────────────────────────────────────────────────────────


def test_router_is_mounted_on_the_api_router():
    from app.api import api_router

    paths = {r.path for r in api_router.routes}
    assert "/generated" in paths
    assert "/generated/counts" in paths
