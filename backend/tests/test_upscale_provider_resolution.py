"""Which backend serves the canvas 放大, and where upscale-only rows may NOT go.

``resolve_upscale_provider``: nous-engine rows first, jimeng-cli second, a
clear error when neither exists. The other half is the negative space: a
nous-engine image row is an upscaler that needs an input image, so it must stay
out of the text-to-image default pick and the canvas picker — otherwise adding
the row would silently change which model an unnamed prompt lands on.
"""

from __future__ import annotations

import pytest

from app.services.media.parsers.video_providers import db_registry
from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider
from app.services.media.parsers.video_providers.nous_images import (
    NousImagesProvider,
)

USER = "11111111-1111-1111-1111-111111111111"


def _nous(name="nous-studio-upscale", **over) -> dict:
    row = {
        "name": name,
        "type": "image",
        "is_enabled": True,
        "actual_provider": "nous",
        "actual_model": "studio-upscale",
        "api_key": "sk",
        "base_url": "http://engine:8000/v1",
    }
    row.update(over)
    return row


def _jimeng(name="jimeng-cli-image") -> dict:
    return {
        "name": name,
        "type": "image",
        "is_enabled": True,
        "actual_provider": "jimeng-cli",
        "actual_model": "5.0",
        "api_key": "",
    }


def _ark(name="ark-t2i") -> dict:
    return {
        "name": name,
        "type": "image",
        "is_enabled": True,
        "actual_provider": "ark",
        "actual_model": "doubao-seedream",
        "api_key": "k",
        "base_url": "https://ark.example/api/v3",
    }


def _catalog(monkeypatch, rows, byok=()):
    async def _enabled(media_type, user_id=None):
        return [r for r in rows if r["type"] == media_type]

    async def _byok(user_id):
        return list(byok)

    monkeypatch.setattr(db_registry, "_enabled_rows", _enabled)
    monkeypatch.setattr(db_registry, "byok_image_rows", _byok)


# ───────────────────────── resolve_upscale_provider ─────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nous_wins_even_when_jimeng_sorts_first(monkeypatch):
    _catalog(monkeypatch, [_jimeng(), _ark(), _nous()])
    provider, model, row_name = await db_registry.resolve_upscale_provider(user_id=USER)
    assert isinstance(provider, NousImagesProvider)
    assert (model, row_name) == ("studio-upscale", "nous-studio-upscale")
    assert provider.provider_key == "nous"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_jimeng_is_the_fallback_family(monkeypatch):
    _catalog(monkeypatch, [_ark(), _jimeng()])
    provider, _, row_name = await db_registry.resolve_upscale_provider(user_id=USER)
    # The raw CLI provider — the image adapter has no upscale_image.
    assert isinstance(provider, JimengCliProvider)
    assert hasattr(provider, "upscale_image")
    assert row_name == "jimeng-cli-image"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_upscale_capable_row_is_a_clear_error(monkeypatch):
    # ark generates images but cannot upscale; jimeng-local runs on the
    # user's device and has no server-side upscale either.
    local = {**_jimeng("jimeng-local-image"), "actual_provider": "jimeng-local"}
    _catalog(monkeypatch, [_ark(), local])
    with pytest.raises(RuntimeError, match="no upscale-capable image model"):
        await db_registry.resolve_upscale_provider(user_id=USER)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_another_users_private_nous_row_is_not_used(monkeypatch):
    private = _nous("someone-elses-upscale", owner_user_id="22222222-2222")
    _catalog(monkeypatch, [private, _jimeng()])
    _, _, row_name = await db_registry.resolve_upscale_provider(user_id=USER)
    assert row_name == "jimeng-cli-image"


# ─────────────── upscale-only rows stay out of text-to-image ───────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_image_pick_skips_the_upscaler(monkeypatch):
    """No jimeng row, nous row sorted FIRST: without the filter the default
    pick would be rows[0] = the upscaler, and every prompt would fail."""
    _catalog(monkeypatch, [_nous(), _ark()])
    provider, model = await db_registry.resolve_image_provider(None, user_id=USER)
    assert provider.provider_key == "ark"
    assert model == "doubao-seedream"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_image_pick_uses_the_shared_predicate(monkeypatch):
    """The default pick and ``platform_models[name].generatable`` answer from
    ONE function. If ``generates_from_prompt`` said the upscaler generates,
    the default pick (upscaler sorted first) must land on it — proving there
    is no second, private copy of the text-to-image rule in db_registry."""
    import inspect

    from app.services.generation import model_capabilities as mc

    src = inspect.getsource(db_registry)
    assert "_is_text_to_image" not in src
    assert "generates_from_prompt" in src

    real = mc.generates_from_prompt
    seen: list = []

    def _spy(row_type, provider):
        seen.append((row_type, provider))
        return True if provider == "nous" else real(row_type, provider)

    monkeypatch.setattr(mc, "generates_from_prompt", _spy)
    _catalog(monkeypatch, [_nous(), _ark()])
    with pytest.raises(RuntimeError, match="upscale-only"):
        await db_registry.resolve_image_provider(None, user_id=USER)
    assert ("image", "nous") in seen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_naming_the_upscaler_for_text_to_image_is_refused_by_name(monkeypatch):
    """Named explicitly it must RAISE naming the row — never quietly
    substitute another model."""
    _catalog(monkeypatch, [_nous(), _ark()])
    with pytest.raises(RuntimeError, match="upscale-only"):
        await db_registry.resolve_image_provider("nous-studio-upscale", user_id=USER)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_only_an_upscaler_means_no_image_model(monkeypatch):
    _catalog(monkeypatch, [_nous()])
    with pytest.raises(RuntimeError, match="no image model configured"):
        await db_registry.resolve_image_provider(None, user_id=USER)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_picker_rows_exclude_the_upscaler(monkeypatch):
    """The generation rows a picker (and the asset bundle) offers come from
    the platform provider view with purpose ``picker``: the upscale-only
    nous-engine row is left out, the provider string stays server side."""
    from unittest.mock import AsyncMock

    from app.services.generation.model_capabilities import generation_rows_for
    from tests.services.ai.test_platform_provider import (
        Env,
        catalog_row,
        engine_row,
        listed,
    )

    env = Env(monkeypatch)
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value={}),
    )
    env.rows = [
        engine_row("nous-studio-upscale", "studio-upscale", type="image"),
        catalog_row("ark-t2i", type="image", actual_provider="ark"),
        catalog_row("jimeng-cli-image", type="image", actual_provider="jimeng-cli"),
    ]
    env.engine_answers = [listed(("studio-upscale", True))]
    rows = await generation_rows_for(USER)
    assert [r["name"] for r in rows] == ["ark-t2i", "jimeng-cli-image"]
    assert [r["actual_provider"] for r in rows] == ["ark", "jimeng-cli"]


@pytest.mark.parametrize(
    ("row_type", "provider", "expected"),
    [
        ("image", "nous", False),  # nous-engine image = super-resolution only
        ("image", "jimeng-local", True),
        ("image", "codex-local", True),
        ("video", "jimeng-local", True),
        ("image", "no-such-protocol", True),  # unknown protocol is not "cannot"
        ("llm", "nous", False),
        ("embedding", "ark", False),
        (None, None, False),
    ],
)
def test_generates_from_prompt_is_the_one_predicate(row_type, provider, expected):
    """Shared by the generation pickers' server row set and
    ``platform_models[name].generatable`` (spec 2026-09-25): one answer, always
    a bool."""
    from app.services.generation.model_capabilities import generates_from_prompt

    assert generates_from_prompt(row_type, provider) is expected
