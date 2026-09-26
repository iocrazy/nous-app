"""The capabilities endpoint: the UI's licence to hide a knob.

Server-side projection keyed by catalog row NAME, so the frontend never
learns ``actual_provider`` and never re-implements the registry lookup —
one predicate, one place (the same rule as the owner-scope fix in P1).
Visibility must match the generation pickers exactly: since spec 2026-09-25
§3.8 the pickers map from ``GET /ai/settings`` (``enabled_models`` ×
``platform_models[name].generatable``), and this endpoint keys its answer by
``platform_provider.generation_picker_models`` over the SAME view. A model a
picker shows must have a caps entry, and a hidden model must not leak its caps.

Hermetic: auth overridden, catalog repository, admin governance and the
user's stored ``ai_providers.nous`` stubbed at their own seams, so the REAL
``platform_provider_view`` runs — the visibility tests would be worthless if
they mocked away the very computation they pin.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.services.ai.engine_catalog as ec
from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import NousModels
from app.repositories.nous_model_repository import _row as repo_row
from tests.api.wire_parity import sample_orm

FAKE_USER_ID = str(uuid4())
_ENGINE = "http://engine.test/v1"

_ROWS = [
    {"name": "codex-local-image", "type": "image", "actual_provider": "codex-local"},
    {
        "name": "mediahub-doubao-seedream-t2i",
        "type": "image",
        "actual_provider": "doubao",
    },
    {"name": "some-chat-model", "type": "llm", "actual_provider": "deepseek"},
]


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture(autouse=True)
def _engine_cache():
    ec.reset_engine_cache()
    yield
    ec.reset_engine_cache()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _catalog_row(values: dict) -> dict:
    """A row as ``list_enabled_private`` returns it in production (the real
    repository conversion over an ORM object carrying every column), so
    ``actual_provider`` is present exactly as the view reads it."""
    full = {
        "display_name": values["name"],
        "actual_model": f"{values['name']}-upstream",
        "api_key": "sk-platform",
        "base_url": "https://provider.example/v1",
        "pricing_type": "per_call",
        "is_enabled": True,
        "owner_user_id": None,
        "last_test_status": "ok",
        "last_test_code": None,
        **values,
    }
    return repo_row(sample_orm(NousModels, **full))


def _catalog(monkeypatch, rows: list[dict]) -> None:
    repo = MagicMock()
    repo.list_enabled_private = AsyncMock(
        side_effect=lambda _viewer=None: [_catalog_row(r) for r in rows]
    )
    monkeypatch.setattr(
        "app.repositories.nous_model_repository.get_nous_model_repository",
        lambda: repo,
    )
    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
        AsyncMock(return_value=True),
    )


def _gate(monkeypatch, *, allowed: bool = True, disabled: frozenset[str] = frozenset()):
    """Stub the user's stored platform card (``ai_providers.nous``)."""
    stored = {"enabled": allowed, "disabled_models": sorted(disabled)}
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value=stored),
    )


@pytest.mark.asyncio
async def test_returns_caps_keyed_by_catalog_name(client, monkeypatch):
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    assert resp.status_code == 200
    data = resp.json()["data"]
    codex = data["codex-local-image"]
    # P1 said False (nothing forwarded it); P3 flipped it back once daemon
    # 0.4.0 forwards --quality and older daemons are refused outright.
    assert codex["quality"] is True
    assert codex["max_refs"] == 9
    assert len(codex["ratios"]) == 8
    assert codex["resolution"] is False
    assert codex["negative"] is False
    assert codex["video_modes"] == []
    ark = data["mediahub-doubao-seedream-t2i"]
    assert set(ark["ratios"]) == {"16:9", "9:16", "1:1", "4:3", "3:4"}
    assert ark["max_refs"] == 0


@pytest.mark.asyncio
async def test_ratios_follow_the_declared_aspect_order(client, monkeypatch):
    """The UI renders the grid in this order; a set would shuffle per run."""
    from app.services.generation.aspect import ASPECT_RATIOS

    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    assert data["codex-local-image"]["ratios"] == list(ASPECT_RATIOS)
    assert data["mediahub-doubao-seedream-t2i"]["ratios"] == [
        r for r in ASPECT_RATIOS if r in {"16:9", "9:16", "1:1", "4:3", "3:4"}
    ]


@pytest.mark.asyncio
async def test_non_generation_rows_are_absent(client, monkeypatch):
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    assert "some-chat-model" not in data
    assert set(data) == {"codex-local-image", "mediahub-doubao-seedream-t2i"}


@pytest.mark.asyncio
async def test_unknown_provider_maps_to_restrictive_default_not_500(
    client, monkeypatch
):
    """A catalog row whose actual_provider has no protocol must not break the
    endpoint — it gets the none() projection (drops loudly downstream)."""
    _catalog(
        monkeypatch,
        [{"name": "ghost-model", "type": "image", "actual_provider": "ghost"}],
    )
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    assert resp.status_code == 200
    ghost = resp.json()["data"]["ghost-model"]
    assert ghost["ratios"] == []
    assert ghost["quality"] is False
    assert ghost["quality_tiers"] == []
    assert ghost["resolution"] is False
    assert ghost["max_refs"] == 0
    assert ghost["negative"] is False
    assert ghost["video_modes"] == []


@pytest.mark.asyncio
async def test_visibility_filter_applies(client, monkeypatch):
    """A model Settings hides must not leak its caps entry."""
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch, disabled=frozenset({"mediahub-doubao-seedream-t2i"}))

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    assert "mediahub-doubao-seedream-t2i" not in data
    assert "codex-local-image" in data


@pytest.mark.asyncio
async def test_master_switch_off_yields_no_entries(client, monkeypatch):
    """Platform card master switch off — the picker is empty, so is this."""
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch, allowed=False)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    assert resp.json()["data"] == {}


@pytest.mark.asyncio
async def test_honours_ratio_is_not_exposed(client, monkeypatch):
    """Internal strategy, nothing the UI can act on — one fewer drift face."""
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    assert "honours_ratio" not in data["codex-local-image"]
    assert "honours_ratio" not in data["mediahub-doubao-seedream-t2i"]


@pytest.mark.asyncio
async def test_capability_keys_match_the_picker_row_for_row(client, monkeypatch):
    """The binding constraint, asserted against what the pickers map from.

    The generation pickers list ``enabled_models`` rows whose
    ``platform_models[name]`` is ``generatable`` (frontend
    ``useGenerationModels``); both come from the same view object this
    endpoint reads. The silent failure it guards is a picker entry with no
    caps entry: the UI then shows a knob it was told to hide.

    The upscale-only nous-engine row is listed by the engine and enabled, but
    not generatable — it must be absent from BOTH sides.
    """
    import app.services.ai.platform_provider as pp

    async def _listed(base_url, api_key):
        return ec._Read(
            services={
                "studio-upscale": ec.EngineService(
                    id="studio-upscale",
                    type="image",
                    ready=True,
                    context_window=None,
                    capabilities=None,
                )
            }
        )

    monkeypatch.setattr(ec, "_fetch", _listed)
    rows = [
        *_ROWS,
        {
            "name": "nous-studio-upscale",
            "type": "image",
            "actual_provider": "nous",
            "actual_model": "studio-upscale",
            "base_url": _ENGINE,
        },
    ]
    _catalog(monkeypatch, rows)
    _gate(monkeypatch, disabled=frozenset({"mediahub-doubao-seedream-t2i"}))

    caps = await client.get("/api/v1/canvases/generation-capabilities")
    assert caps.status_code == 200

    view = await pp.platform_provider_view(FAKE_USER_ID)
    card, mapping = view.provider_entry(), view.platform_models()
    assert "nous-studio-upscale" in card["enabled_models"]  # listed and on
    assert mapping["nous-studio-upscale"]["generatable"] is False
    picker = {n for n in card["enabled_models"] if mapping[n]["generatable"]}
    assert picker == {"codex-local-image"}  # the fixture really did filter
    assert set(caps.json()["data"]) == picker


@pytest.mark.asyncio
async def test_caps_survive_the_repository_leak_tripwire(client, monkeypatch):
    """Prod incident 2026-08-30: every model came back as none().

    ``list_enabled`` strips ``actual_provider`` by default, so the endpoint's
    ``r.get("actual_provider")`` was always None, every row resolved to no
    protocol, and the UI hid the ratio grid down to Auto for every model. The
    original tests missed it because their stub returned rows carrying a field
    the real repository never returns. This test is that missing one: it fails
    unless the endpoint actually asks for the provider.
    """
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    assert data["codex-local-image"]["max_refs"] == 9, "degraded to none()"
    assert len(data["codex-local-image"]["ratios"]) == 8
    assert len(data["mediahub-doubao-seedream-t2i"]["ratios"]) == 5


@pytest.mark.asyncio
async def test_provider_string_never_reaches_the_response(client, monkeypatch):
    """The endpoint asks the repository for ``actual_provider`` — so pin that
    it consumes it and never emits it. Upstream identity stays private (the
    2026-08-14 leak tripwire); the UI gets capability values only."""
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    # NB: a raw substring check would false-positive — the catalog NAME
    # "codex-local-image" is itself public and legitimately in the body. The
    # real assertion is the exact key set of each entry.
    for entry in resp.json()["data"].values():
        assert set(entry) == {
            "ratios",
            "quality",
            "quality_tiers",
            "resolution",
            "max_refs",
            "negative",
            "video_modes",
        }


@pytest.mark.asyncio
async def test_quality_tiers_are_projected_in_declared_order(client, monkeypatch):
    """``quality`` alone cannot tell the pill which tiers to offer.

    The wire field is a LIST because order is meaning here — low→max is a
    ramp the UI renders left to right — and it is derived from
    ``QUALITY_TIER_ORDER`` rather than sorted, which would put ``high``
    before ``low``. A model that honours no tiers projects an empty list, so
    the pill has one thing to read instead of two.
    """
    from app.services.ai.provider_protocols.base import QUALITY_TIER_ORDER

    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    assert data["codex-local-image"]["quality_tiers"] == ["low", "medium", "high"]
    # Derived from the ordering constant, never re-listed: a tier added there
    # must show up here in that position.
    assert data["codex-local-image"]["quality_tiers"] == [
        t for t in QUALITY_TIER_ORDER if t in {"low", "medium", "high"}
    ]
    # doubao declares quality=False, so it offers no tiers at all.
    assert data["mediahub-doubao-seedream-t2i"]["quality"] is False
    assert data["mediahub-doubao-seedream-t2i"]["quality_tiers"] == []
