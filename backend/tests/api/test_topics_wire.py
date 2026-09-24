"""``POST /topics/{hotspot_id}/generate-script``: wire parity after it gained a
response model (P6).

Real HTTP through the real router. The hotspot row the repository hands back
comes from the ``Hotspots`` mapper (``sample_row``); the script agent is
stubbed at ``_generate_script_for`` (the LLM call), so the body is exactly
``{"success": True, "script": <outline>}`` and must equal what FastAPI sent
for that dict with no model.

The hotspot lookup is scoped to the caller's visible sources; a hotspot
outside them is a 404 and the agent never runs (no spend on a row the caller
cannot see).
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models.topics import Hotspots
from app.schemas.topics import TopicScriptChapter, TopicScriptResponse
from tests.api.wire_parity import assert_wire_unchanged, sample_row

tr = sys.modules["app.api.topics_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
HOTSPOT = sample_row(Hotspots)
HOTSPOT_ID = str(HOTSPOT["id"])
VISIBLE_SOURCE = str(HOTSPOT["source_id"])

OUTLINE = [
    {"title": "Chapter 1", "summary": "The opening."},
    {"title": "Chapter 2", "summary": ""},
]


class _State:
    lookups: list[tuple[str, Any]] = []
    generated: list[tuple[str, str]] = []
    outline: list[dict[str, str]] = OUTLINE


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _State.lookups = []
    _State.generated = []
    _State.outline = OUTLINE

    class _Repo:
        async def get_by_id(self, hid, source_ids=None):
            _State.lookups.append((hid, source_ids))
            if hid == HOTSPOT_ID and VISIBLE_SOURCE in (source_ids or []):
                return dict(HOTSPOT)
            return None

    async def _visible(user_id):
        assert user_id == USER
        return [VISIBLE_SOURCE]

    async def _resolve(user_id):
        from app.services.ai.providers.ai_provider_helpers import ResolvedAIConfig

        return ResolvedAIConfig(
            provider_key="nous",
            provider_config={},
            model="qwen3",
            agent_slug="script_ai",
            origin="platform",
        )

    async def _script(title, summary, user_id, **kwargs):
        assert user_id == USER
        _State.generated.append((title, summary))
        return _State.outline

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _Repo())
    monkeypatch.setattr(tr, "_visible_source_ids", _visible)
    monkeypatch.setattr(tr, "resolve_script_ai_config", _resolve)
    monkeypatch.setattr(tr, "_generate_script_for", _script)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_models_declare_exactly_the_keys_the_handler_builds() -> None:
    assert set(TopicScriptResponse.model_fields) == {"success", "script"}
    # generate_outline always emits both keys per chapter.
    assert set(TopicScriptChapter.model_fields) == {"title", "summary"}


@pytest.mark.asyncio
@pytest.mark.parametrize("outline", [OUTLINE, []])
async def test_generate_script_wire_unchanged(client, outline) -> None:
    _State.outline = outline
    resp = await client.post(f"/api/v1/topics/{HOTSPOT_ID}/generate-script")
    assert_wire_unchanged(resp, {"success": True, "script": outline})
    # The prompt is built from the caller-visible row.
    assert _State.generated == [(HOTSPOT["title"], HOTSPOT["ai_summary"])]


@pytest.mark.asyncio
async def test_hotspot_outside_the_callers_sources_is_404_and_costs_nothing(
    client,
) -> None:
    resp = await client.post("/api/v1/topics/999/generate-script")
    assert resp.status_code == 404, resp.text
    assert _State.generated == []
    # The lookup was bounded by the caller's visible sources.
    assert _State.lookups == [("999", [VISIBLE_SOURCE])]
