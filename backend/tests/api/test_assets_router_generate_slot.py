"""GET/POST /assets/{id}/generate-slot — wire contract (no DB, no provider).

The service is faked; what is pinned here is the HTTP surface: the scope gate,
the request schema (``extra="forbid"``, the ``count`` bounds), the 202 (these
products are NOT part of the asset yet), and — the load-bearing one — that a
partially successful run reports its ``failed`` ledger on the wire while a
fully failed one comes back as the same ``{success:false, error:{code, detail}}``
envelope every other refusal on this router uses, with the provider's own
message intact.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError
from tests.api.test_assets_router import USER

GENERATE = "/api/v1/assets/5/generate-slot?scope_id=9000"
PREVIEW = "/api/v1/assets/5/generate-slot/preview?scope_id=9000&slot=sheet"


class _AuthStub:
    user_id = USER


class _FakeSlotService:
    def __init__(self):
        self.preview_calls: list[tuple] = []
        self.generate_calls: list[dict] = []
        self.raises: AssetError | None = None
        self.result = {
            "generation_ids": ["901", "902"],
            "failed": [],
            "skipped_references": [],
            "inbox_state": "unreviewed",
        }

    async def preview_generate_slot(self, asset_id, scope_id, slot, loadout_id):
        self.preview_calls.append((asset_id, scope_id, slot, loadout_id))
        if self.raises:
            raise self.raises
        return {
            "positive": "a swordswoman, character sheet",
            "negative": "text, watermark",
            "reference_resource_ids": ["727145299382534146"],
            "aspect_ratio": "16:9",
            "model": None,
        }

    async def generate_slot(self, asset_id, scope_id, user_id, payload):
        self.generate_calls.append(
            {
                "asset_id": asset_id,
                "scope_id": scope_id,
                "user_id": user_id,
                "slot": payload.slot,
                "loadout_id": payload.loadout_id,
                "model": payload.model,
                "count": payload.count,
            }
        )
        if self.raises:
            raise self.raises
        return self.result


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
    fake = _FakeSlotService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


# ── preview ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_200_returns_the_prompt_and_references(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(PREVIEW)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "character sheet" in data["positive"]
    assert data["reference_resource_ids"] == ["727145299382534146"]
    assert data["aspect_ratio"] == "16:9"
    assert data["model"] is None
    assert app.state.fake.preview_calls == [(5, 9000, "sheet", None)]


@pytest.mark.asyncio
async def test_preview_passes_the_loadout_through(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(PREVIEW + "&loadout_id=7001")
    assert r.status_code == 200, r.text
    assert app.state.fake.preview_calls[0][3] == "7001"


@pytest.mark.asyncio
async def test_preview_without_a_slot_is_422(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/5/generate-slot/preview?scope_id=9000")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_preview_refusal_keeps_the_error_envelope(app):
    app.state.fake.raises = AssetError(
        422, "slot_not_generatable", "The 'primary' slot of a audio asset cannot"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(PREVIEW)
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "slot_not_generatable"


# ── generate ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_answers_202_with_the_inbox_state(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json={"slot": "sheet", "count": 2})
    assert r.status_code == 202, r.text
    data = r.json()["data"]
    assert data["generation_ids"] == ["901", "902"]
    assert data["failed"] == []
    assert data["skipped_references"] == []
    assert data["inbox_state"] == "unreviewed"
    assert app.state.fake.generate_calls == [
        {
            "asset_id": 5,
            "scope_id": 9000,
            "user_id": USER,
            "slot": "sheet",
            "loadout_id": None,
            "model": None,
            "count": 2,
        }
    ]


@pytest.mark.asyncio
async def test_a_partially_failed_run_reports_the_ledger_on_the_wire(app):
    app.state.fake.result = {
        "generation_ids": ["901"],
        "failed": [{"index": 1, "code": "generation_failed", "detail": "provider 503"}],
        "skipped_references": [],
        "inbox_state": "unreviewed",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json={"slot": "sheet", "count": 2})
    assert r.status_code == 202, r.text
    data = r.json()["data"]
    assert data["generation_ids"] == ["901"]
    assert data["failed"] == [
        {"index": 1, "code": "generation_failed", "detail": "provider 503"}
    ]


@pytest.mark.asyncio
async def test_every_unit_failing_is_a_503_envelope_with_the_ledger(app):
    app.state.fake.raises = AssetError(
        503,
        "generation_failed",
        "no image model configured",
        {"failed": [{"index": 0, "code": "generation_failed", "detail": "boom"}]},
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json={"slot": "sheet"})
    assert r.status_code == 503
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "generation_failed"
    assert body["error"]["detail"] == "no image model configured"
    assert body["error"]["failed"][0]["index"] == 0


@pytest.mark.asyncio
async def test_a_non_member_never_reaches_the_service(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/generate-slot?scope_id=666", json={"slot": "sheet"}
        )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_a_member"
    assert app.state.fake.generate_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{"slot": "sheet", "count": 0}, {"slot": "sheet", "count": 5}]
)
async def test_count_is_bounded(app, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json=body)
    assert r.status_code == 422
    assert app.state.fake.generate_calls == []


@pytest.mark.asyncio
async def test_an_unknown_body_field_is_refused(app):
    """A typo'd ``{"loadout": …}`` answering 202 would be a paid run that did
    something other than what was asked."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json={"slot": "sheet", "loadout": "7001"})
    assert r.status_code == 422
    assert app.state.fake.generate_calls == []


@pytest.mark.asyncio
async def test_a_blank_slot_is_refused(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json={"slot": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_an_out_of_range_loadout_id_is_refused_at_the_boundary(app):
    """``SnowflakeId`` bounds the digit string at int64 — past that the value
    parses, reaches asyncpg and fails at BIND, i.e. a reachable 500."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            GENERATE, json={"slot": "sheet", "loadout_id": "99999999999999999999"}
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_a_dropped_reference_is_visible_on_the_wire(app):
    """A run that generated without the asset's primary image must say so —
    otherwise the user sees a picture that ignored their reference and has no
    way to learn why."""
    app.state.fake.result = {
        "generation_ids": ["901"],
        "failed": [],
        "skipped_references": [
            {"resource_id": "727145299382534146", "reason": "no_image_file"}
        ],
        "inbox_state": "unreviewed",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(GENERATE, json={"slot": "sheet"})
    assert r.status_code == 202, r.text
    assert r.json()["data"]["skipped_references"] == [
        {"resource_id": "727145299382534146", "reason": "no_image_file"}
    ]
