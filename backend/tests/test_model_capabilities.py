# backend/tests/test_model_capabilities.py
from unittest.mock import AsyncMock

import pytest

from app.services.ai import model_capabilities as m


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets a fresh cache so previous state can't leak in."""
    m._cache.clear()
    m._cache_loaded = False
    yield
    m._cache.clear()
    m._cache_loaded = False


@pytest.mark.asyncio
async def test_returns_true_for_seeded_vision_model(monkeypatch):
    """When the DB row says supports_vision=True, return True."""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {
                    "model": "doubao-seed-2-0-pro-260215",
                    "provider": "doubao",
                    "supports_vision": True,
                },
                {"model": "doubao-pro", "provider": "doubao", "supports_vision": True},
                {"model": "qwen-plus", "provider": "qwen", "supports_vision": False},
            ]
        ),
    )
    assert await m.model_supports_vision("doubao-seed-2-0-pro-260215", "doubao") is True
    assert await m.model_supports_vision("doubao-pro", "doubao") is True
    assert await m.model_supports_vision("qwen-plus", "qwen") is False


@pytest.mark.asyncio
async def test_db_lookup_ignores_provider_when_unique(monkeypatch):
    """If only one provider has the model, provider can be omitted."""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {"model": "gpt-4o", "provider": "openai", "supports_vision": True},
            ]
        ),
    )
    assert await m.model_supports_vision("gpt-4o") is True
    assert await m.model_supports_vision("gpt-4o", "openai") is True


@pytest.mark.asyncio
async def test_falls_back_to_prefix_heuristic_for_unknown_model(monkeypatch):
    """A model not in the DB falls back to the legacy prefix check."""
    monkeypatch.setattr(m, "_fetch_capabilities", AsyncMock(return_value=[]))
    # claude-3 prefix → True via heuristic
    assert await m.model_supports_vision("claude-3-haiku-20240307", "anthropic") is True
    # Unknown plain text model → False
    assert await m.model_supports_vision("totally-made-up-model") is False


@pytest.mark.asyncio
async def test_cache_loads_once(monkeypatch):
    """The DB fetch happens once across multiple calls (cache hit subsequent)."""
    fetch = AsyncMock(
        return_value=[
            {"model": "gpt-4o", "provider": "openai", "supports_vision": True},
        ]
    )
    monkeypatch.setattr(m, "_fetch_capabilities", fetch)
    for _ in range(5):
        await m.model_supports_vision("gpt-4o", "openai")
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_db_fetch_error_falls_back_to_heuristic(monkeypatch):
    """If the DB lookup raises, the helper degrades to prefix heuristic, never throws."""
    monkeypatch.setattr(
        m, "_fetch_capabilities", AsyncMock(side_effect=RuntimeError("db down"))
    )
    # gpt-4o matches prefix heuristic → True
    assert await m.model_supports_vision("gpt-4o", "openai") is True
    # Unknown model → False
    assert await m.model_supports_vision("nope") is False


@pytest.mark.asyncio
async def test_empty_or_none_model_returns_false():
    assert await m.model_supports_vision("") is False
    assert await m.model_supports_vision(None) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_db_row_with_supports_vision_false_overrides_prefix_match(monkeypatch):
    """If a row exists with supports_vision=False, it WINS over the prefix heuristic.
    Admin can opt OUT a model that happens to match a vision prefix."""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {
                    "model": "qwen-vl-broken",
                    "provider": "qwen",
                    "supports_vision": False,
                },
            ]
        ),
    )
    # 'qwen-vl' prefix would say True via heuristic, but DB row wins.
    assert await m.model_supports_vision("qwen-vl-broken", "qwen") is False


@pytest.mark.asyncio
async def test_refresh_reloads_cache(monkeypatch):
    """refresh_capabilities() forces a fresh DB fetch."""
    fetch = AsyncMock(
        side_effect=[
            [{"model": "gpt-4o", "provider": "openai", "supports_vision": True}],
            [{"model": "gpt-4o", "provider": "openai", "supports_vision": False}],
        ]
    )
    monkeypatch.setattr(m, "_fetch_capabilities", fetch)
    assert await m.model_supports_vision("gpt-4o", "openai") is True
    await m.refresh_capabilities()
    assert await m.model_supports_vision("gpt-4o", "openai") is False
    assert fetch.await_count == 2
