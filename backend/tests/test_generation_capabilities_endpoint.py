"""The capabilities endpoint: the UI's licence to hide a knob.

Server-side projection keyed by catalog row NAME, so the frontend never
learns ``actual_provider`` and never re-implements the registry lookup —
one predicate, one place (the same rule as the owner-scope fix in P1).
Visibility must match ``generation-models`` exactly: a model the picker
shows must have a caps entry, and a hidden model must not leak its caps.

Hermetic: auth overridden, catalog repository stubbed, and the Settings
platform gate stubbed at its own seam so the REAL
``filter_platform_models_for_user`` runs — the visibility test would be
worthless if it mocked away the very call it is meant to pin.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

FAKE_USER_ID = str(uuid4())

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


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _catalog(monkeypatch, rows: list[dict]) -> None:
    """Stub the catalog repository, honouring its REAL projection.

    ``list_enabled`` pops ``actual_provider`` and leaves only the derived
    ``is_local`` bit (the 2026-08-14 leak tripwire) unless the caller opts in
    with ``include_actual_provider=True``. The first version of this stub just
    handed back whatever rows the test wrote — so every test carried a field
    production never returns, and the endpoint shipped reading a key that was
    always ``None``. A boundary stub that does not model the boundary is not
    a test; this one reproduces both settings of the flag.
    """
    from app.repositories import mediahub_model_repository as repo_mod

    async def _list_enabled(
        type_filter=None, viewer_user_id=None, include_actual_provider=False
    ):
        out = []
        for r in rows:
            row = dict(r)
            provider = row.pop("actual_provider", None)
            row["is_local"] = provider in ("codex-local", "jimeng-local")
            if include_actual_provider:
                row["actual_provider"] = provider
            out.append(row)
        return out

    repo = SimpleNamespace(list_enabled=_list_enabled)
    monkeypatch.setattr(repo_mod, "get_mediahub_model_repository", lambda: repo)


def _gate(monkeypatch, *, allowed: bool = True, disabled: frozenset[str] = frozenset()):
    """Stub the user's Settings platform-model gate (the filter's own seam)."""

    async def _fake_gate(user_id):
        return allowed, disabled

    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.platform_model_gate", _fake_gate
    )


@pytest.mark.asyncio
async def test_returns_caps_keyed_by_catalog_name(client, monkeypatch):
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch)

    resp = await client.get("/api/v1/canvases/generation-capabilities")

    assert resp.status_code == 200
    data = resp.json()["data"]
    codex = data["codex-local-image"]
    assert codex["quality"] is False  # P1 flipped this to honest False
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
    """The binding constraint, asserted against the other endpoint itself.

    Both endpoints read ``_visible_generation_rows``, so today this cannot
    drift — but this test is what goes red if someone re-inlines one side and
    adds a condition to only one of them. The silent failure it guards is a
    picker entry with no caps entry: the UI then shows a knob it was told to
    hide, and nothing else in the stack says a word.
    """
    _catalog(monkeypatch, _ROWS)
    _gate(monkeypatch, disabled=frozenset({"mediahub-doubao-seedream-t2i"}))

    caps = await client.get("/api/v1/canvases/generation-capabilities")
    models = await client.get("/api/v1/canvases/generation-models")

    assert caps.status_code == 200 and models.status_code == 200
    picker = {row["name"] for row in models.json()["data"]}
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
            "resolution",
            "max_refs",
            "negative",
            "video_modes",
        }
