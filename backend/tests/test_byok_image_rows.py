"""The BYOK tier of the image chain: a user's own Settings → AI providers,
projected into ``mediahub_models``-shaped rows.

Every case here is about ADMISSION — which of a user's enabled models may be
dialled by the agent image chain and with whose credential. The tier must fail
CLOSED (no user → no rows, unreadable settings → no rows + a warning) because
the alternative is either a 401 from an upstream provider or, worse, a row
built from somebody else's key.
"""

from __future__ import annotations

import pytest
from loguru import logger

from app.services.media.parsers.video_providers import byok_rows as mod
from app.services.media.parsers.video_providers.byok_rows import (
    BYOK_IMAGE_BASE_URLS,
    byok_image_rows,
)

ARK_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"
SEEDREAM = "doubao-seedream-5-0-pro-260628"
CHAT_MODEL = "doubao-seed-2-0-lite-260428"


def _settings(providers: dict) -> dict:
    return {"ai_providers": providers}


def _patch_settings(monkeypatch, value, *, calls: list | None = None):
    """Replace the settings loader in ``byok_rows``' own namespace."""

    async def fake_get_ai_settings(user_id: str) -> dict:
        if calls is not None:
            calls.append(user_id)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(mod, "get_ai_settings", fake_get_ai_settings)


# ── the happy path ─────────────────────────────────────────────────────────


async def test_doubao_image_model_becomes_one_ark_row(monkeypatch):
    """The chat model in the same enabled_models list must NOT produce a row —
    an image chain that dials a chat id gets a 400 from Ark, not an image."""
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "doubao": {
                    "enabled": True,
                    "api_key": "k1",
                    "enabled_models": [CHAT_MODEL, SEEDREAM],
                }
            }
        ),
    )
    rows = await byok_image_rows("u1")

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == SEEDREAM
    assert row["actual_model"] == SEEDREAM
    # The canonical PROTOCOL key, not the settings key: db_registry dispatches
    # on it and ai_model_prices is keyed by (model_id, "ark").
    assert row["actual_provider"] == "ark"
    assert row["api_key"] == "k1"
    assert row["base_url"] == ARK_DEFAULT == BYOK_IMAGE_BASE_URLS["doubao"]
    assert row["type"] == "image"
    assert row["is_enabled"] is True
    assert row["owner_user_id"] == "u1"
    assert row["source"] == "byok"
    assert row["byok_provider"] == "doubao"
    assert row["sort_order"] >= 10_000  # after every catalog row


async def test_explicit_base_url_wins_over_the_default(monkeypatch):
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "doubao": {
                    "enabled": True,
                    "api_key": "k1",
                    "base_url": " https://ark.example/api/v3 ",
                    "enabled_models": [SEEDREAM],
                }
            }
        ),
    )
    rows = await byok_image_rows("u1")
    assert [r["base_url"] for r in rows] == ["https://ark.example/api/v3"]


async def test_selected_model_is_used_when_enabled_models_is_absent(monkeypatch):
    _patch_settings(
        monkeypatch,
        _settings(
            {"doubao": {"enabled": True, "api_key": "k1", "selected_model": SEEDREAM}}
        ),
    )
    rows = await byok_image_rows("u1")
    assert [r["actual_model"] for r in rows] == [SEEDREAM]


async def test_user_order_is_preserved(monkeypatch):
    other = "doubao-seedream-4-0-250828"
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "doubao": {
                    "enabled": True,
                    "api_key": "k1",
                    "enabled_models": [other, CHAT_MODEL, SEEDREAM],
                }
            }
        ),
    )
    rows = await byok_image_rows("u1")
    assert [r["actual_model"] for r in rows] == [other, SEEDREAM]
    assert rows[0]["sort_order"] < rows[1]["sort_order"]


async def test_the_settings_dict_is_never_mutated(monkeypatch):
    entry = {"enabled": True, "api_key": "k1", "enabled_models": [SEEDREAM]}
    settings = _settings({"doubao": entry})
    _patch_settings(monkeypatch, settings)
    await byok_image_rows("u1")
    assert entry == {"enabled": True, "api_key": "k1", "enabled_models": [SEEDREAM]}
    assert settings == _settings({"doubao": entry})


# ── the api_key's REAL shape (str | list[str]) ─────────────────────────────


async def test_api_key_list_takes_the_first_non_empty_entry(monkeypatch):
    """``api_key`` is a ``list[str]`` under Sprint 2 multi-key rotation
    (see app/core/secure_settings.py) — the shape, not a nicety."""
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "doubao": {
                    "enabled": True,
                    "api_key": ["", "  k2  "],
                    "enabled_models": [SEEDREAM],
                }
            }
        ),
    )
    rows = await byok_image_rows("u1")
    assert [r["api_key"] for r in rows] == ["k2"]


async def test_a_list_of_empty_keys_admits_nothing(monkeypatch):
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "doubao": {
                    "enabled": True,
                    "api_key": ["", "   "],
                    "enabled_models": [SEEDREAM],
                }
            }
        ),
    )
    assert await byok_image_rows("u1") == []


async def test_a_missing_key_admits_nothing(monkeypatch):
    _patch_settings(
        monkeypatch,
        _settings({"doubao": {"enabled": True, "enabled_models": [SEEDREAM]}}),
    )
    assert await byok_image_rows("u1") == []


# ── exclusions ─────────────────────────────────────────────────────────────


async def test_a_disabled_provider_admits_nothing(monkeypatch):
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "doubao": {
                    "enabled": False,
                    "api_key": "k1",
                    "enabled_models": [SEEDREAM],
                }
            }
        ),
    )
    assert await byok_image_rows("u1") == []


async def test_a_chat_only_provider_admits_nothing(monkeypatch):
    """qwen is an OpenAI-compatible CHAT protocol — no ``image`` in
    ``model_types``, so no amount of image-looking model ids makes it a
    generator this process knows how to dial."""
    _patch_settings(
        monkeypatch,
        _settings(
            {
                "qwen": {
                    "enabled": True,
                    "api_key": "k1",
                    "base_url": "https://qwen.example/v1",
                    "enabled_models": ["wan2-2-t2i-flash"],
                }
            }
        ),
    )
    assert await byok_image_rows("u1") == []


async def test_a_provider_with_no_resolvable_base_url_is_skipped(monkeypatch):
    """ArkImageProvider raises on an empty base_url; skipping here keeps the
    catalog tier usable instead of exploding the whole resolution."""
    monkeypatch.setitem(BYOK_IMAGE_BASE_URLS, "doubao", "")
    _patch_settings(
        monkeypatch,
        _settings(
            {"doubao": {"enabled": True, "api_key": "k1", "enabled_models": [SEEDREAM]}}
        ),
    )
    assert await byok_image_rows("u1") == []


# ── failing closed ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("user_id", [None, ""])
async def test_no_user_means_no_rows_and_no_settings_read(monkeypatch, user_id):
    """Fail closed AND do not hit the DB: there is no user whose key this
    could legitimately be."""
    calls: list = []
    _patch_settings(monkeypatch, _settings({}), calls=calls)
    assert await byok_image_rows(user_id) == []
    assert calls == []


async def test_an_unreadable_settings_row_degrades_to_no_rows_with_a_warning(
    monkeypatch,
):
    """The catalog tier must keep working when settings are unreadable — but
    loudly: a silent [] here looks exactly like "the user configured nothing"."""
    _patch_settings(monkeypatch, RuntimeError("boom"))
    records: list = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        assert await byok_image_rows("u1") == []
    finally:
        logger.remove(sink_id)

    assert records, "the failure was swallowed without a warning"
    assert any("boom" in str(r) for r in records)


async def test_rows_are_built_only_for_the_requesting_user(monkeypatch):
    """There is no cross-user path by construction: the loader is called with
    exactly the user_id passed in, and the row is stamped with the same id."""
    calls: list = []
    _patch_settings(
        monkeypatch,
        _settings(
            {"doubao": {"enabled": True, "api_key": "k1", "enabled_models": [SEEDREAM]}}
        ),
        calls=calls,
    )
    rows = await byok_image_rows("u-42")
    assert calls == ["u-42"]
    assert [r["owner_user_id"] for r in rows] == ["u-42"]
