"""POST /assets/{id}/prompt/{translate,regenerate} — wire contract (no DB).

The service is faked; what is pinned here is the HTTP surface: the scope gate,
the request schema (``extra="forbid"`` and the ``target_lang`` enumeration),
and — the load-bearing one — that a 503 from an unreachable agent comes back in
the SAME ``{success:false, error:{code, detail}}`` envelope as every other
refusal on this router, with the provider's own message intact. A provider
failure that arrives as a bare 500 (whose body ``core/exceptions.py`` masks to
"Internal server error") is invisible to the user and to the error funnel.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.schemas.assets import PromptTranslateRequest
from app.services.assets.assets_service import AssetError
from tests.api.test_assets_router import USER, detail_row

TRANSLATE = "/api/v1/assets/5/prompt/translate?scope_id=9000"
REGENERATE = "/api/v1/assets/5/prompt/regenerate?scope_id=9000"


class _AuthStub:
    user_id = USER


class _FakePromptService:
    def __init__(self):
        self.translate_calls: list[dict] = []
        self.regenerate_calls: list[tuple] = []
        self.raises: AssetError | None = None

    async def translate_prompt(self, asset_id, scope_id, payload, user_id):
        self.translate_calls.append(
            {
                "asset_id": asset_id,
                "scope_id": scope_id,
                "target_lang": payload.target_lang,
                "force": payload.force,
                "user_id": user_id,
            }
        )
        if self.raises:
            raise self.raises
        return detail_row(
            id=str(asset_id), prompt_positive="a rooftop", prompt_positive_zh="屋顶"
        )

    async def regenerate_prompt(self, asset_id, scope_id, user_id):
        self.regenerate_calls.append((asset_id, scope_id, user_id))
        if self.raises:
            raise self.raises
        return detail_row(id=str(asset_id), prompt_positive="a rooftop at dusk")


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
    fake = _FakePromptService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


# ── happy paths ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_translate_200_returns_the_detail_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(TRANSLATE, json={"target_lang": "zh"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["prompt_positive_zh"] == "屋顶"
    assert "loadouts" in body["data"], "the DETAIL row, not the summary one"
    call = app.state.fake.translate_calls[0]
    assert call == {
        "asset_id": 5,
        "scope_id": 9000,
        "target_lang": "zh",
        "force": False,
        "user_id": USER,
    }


@pytest.mark.asyncio
async def test_force_reaches_the_service(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(TRANSLATE, json={"target_lang": "en", "force": True})
    assert r.status_code == 200, r.text
    call = app.state.fake.translate_calls[0]
    assert call["target_lang"] == "en" and call["force"] is True


@pytest.mark.asyncio
async def test_regenerate_200_returns_the_detail_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(REGENERATE)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["prompt_positive"] == "a rooftop at dusk"
    assert app.state.fake.regenerate_calls == [(5, 9000, USER)]


# ── refusals keep the envelope ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TRANSLATE, REGENERATE])
async def test_provider_outage_is_a_503_in_the_error_envelope(app, url):
    code = "translate_unavailable" if "translate" in url else "caption_unavailable"
    app.state.fake.raises = AssetError(503, code, "qwen-max: 429 rate limited")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(url, json={"target_lang": "zh"})
    assert r.status_code == 503
    assert r.json() == {
        "success": False,
        "error": {"code": code, "detail": "qwen-max: 429 rate limited"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,code",
    [
        (TRANSLATE, "nothing_to_translate"),
        (REGENERATE, "no_primary_file"),
        (REGENERATE, "not_applicable"),
    ],
)
async def test_422_refusals_keep_the_envelope(app, url, code):
    app.state.fake.raises = AssetError(422, code, "nope")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(url, json={"target_lang": "zh"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TRANSLATE, REGENERATE])
async def test_preset_is_403_in_the_envelope(app, url):
    app.state.fake.raises = AssetError(
        403, "system_preset_readonly", "System presets are read-only"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(url, json={"target_lang": "zh"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "system_preset_readonly"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/assets/5/prompt/translate?scope_id=666",
        "/api/v1/assets/5/prompt/regenerate?scope_id=666",
    ],
)
async def test_both_routes_are_scope_gated(app, url):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(url, json={"target_lang": "zh"})
    assert r.status_code == 403, r.text
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }
    assert not app.state.fake.translate_calls and not app.state.fake.regenerate_calls


# ── request schema ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},  # target_lang is required — there is no sensible default direction
        {"target_lang": "fr"},  # not one of the two columns pairs we hold
        {"target_lang": "ZH"},
        {"target_lang": "zh", "targetlang": "en"},  # extra="forbid"
        {"target_lang": "zh", "force": "yes-please"},
    ],
)
async def test_bad_translate_bodies_are_422(app, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(TRANSLATE, json=body)
    assert r.status_code == 422, r.text
    assert not app.state.fake.translate_calls


def test_schema_forbids_extra_and_defaults_force_false():
    req = PromptTranslateRequest(target_lang="zh")
    assert req.force is False
    with pytest.raises(ValueError):
        PromptTranslateRequest(target_lang="zh", nope=1)


@pytest.mark.asyncio
async def test_regenerate_takes_no_body(app):
    """A body would imply knobs the endpoint does not have — the primary slot
    and the caption agent are both derived, not chosen."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(REGENERATE, json={"slot": "stills"})
    assert r.status_code == 200, r.text
    assert app.state.fake.regenerate_calls == [(5, 9000, USER)]


def test_openapi_documents_the_503_only_where_it_is_reachable(app):
    schema = app.openapi()
    paths = schema["paths"]
    for path in (
        "/api/v1/assets/{asset_id}/prompt/translate",
        "/api/v1/assets/{asset_id}/prompt/regenerate",
    ):
        responses = paths[path]["post"]["responses"]
        assert "503" in responses, f"{path} can 503 but does not say so"
        ref = responses["503"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("ErrorEnvelope")
    assert (
        "503" not in paths["/api/v1/assets/{asset_id}"]["delete"]["responses"]
    ), "a route with no agent behind it must not advertise a provider outage"
