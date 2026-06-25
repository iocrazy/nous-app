"""Task 6: verify the L2 inject path routes through the provider registry.

_safe_recall_honcho_context must:
  1. Acquire the L2 provider via memory_registry.l2_provider().
  2. Gate on provider.is_operative() — returns None when not operative.
  3. Gate on prefs.inject — returns None when inject is False.
  4. Call provider.get_context() to fetch the representation (NOT the
     honcho service directly for this step).
  5. Still swallow any exception and return None.

The "About me" card-merge path remains on the honcho service and is
exercised by the separate test_honcho_l2_injection.py suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.chat import ai_library_chat_wiring as wiring


@pytest.mark.asyncio
async def test_inject_uses_l2_provider_get_context() -> None:
    """Happy path: operative provider → get_context result returned."""
    provider = AsyncMock()
    provider.is_operative = AsyncMock(return_value=True)
    provider.get_context = AsyncMock(return_value="user likes brevity")

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.memory_registry.l2_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "app.services.ai.memory.memory_prefs.get_memory_prefs",
            new=AsyncMock(return_value=type("P", (), {"inject": True})()),
        ),
        patch(
            "app.workflows.write_memory._resolve_team_workspace",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.memory.honcho_memory.get_honcho_memory_service",
            return_value=_card_less_service(),
        ),
    ):
        out = await wiring._safe_recall_honcho_context(user_id="u1", session_id="s1")

    assert out is not None
    provider.get_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_inject_returns_none_when_provider_none() -> None:
    """l2_provider() returning None disables L2 injection."""
    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.memory_registry.l2_provider",
            new=AsyncMock(return_value=None),
        ),
    ):
        out = await wiring._safe_recall_honcho_context(user_id="u1", session_id=None)

    assert out is None


@pytest.mark.asyncio
async def test_inject_returns_none_when_not_operative() -> None:
    """Non-operative provider gates out before fetching representation."""
    provider = AsyncMock()
    provider.is_operative = AsyncMock(return_value=False)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.memory_registry.l2_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        out = await wiring._safe_recall_honcho_context(user_id="u1", session_id=None)

    assert out is None
    provider.get_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_inject_returns_none_when_inject_pref_off() -> None:
    """prefs.inject=False suppresses injection even with an operative provider."""
    provider = AsyncMock()
    provider.is_operative = AsyncMock(return_value=True)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.memory_registry.l2_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "app.services.ai.memory.memory_prefs.get_memory_prefs",
            new=AsyncMock(return_value=type("P", (), {"inject": False})()),
        ),
    ):
        out = await wiring._safe_recall_honcho_context(user_id="u1", session_id=None)

    assert out is None
    provider.get_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_inject_swallows_provider_exception() -> None:
    """Any exception from the provider is swallowed and returns None."""
    provider = AsyncMock()
    provider.is_operative = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.memory_registry.l2_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        out = await wiring._safe_recall_honcho_context(user_id="u1", session_id=None)

    assert out is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _card_less_service():
    """Minimal honcho-service stand-in: has no get_peer_card attribute so
    the card-merge path returns the representation as-is.  Using a simple
    object avoids importing HonchoMemoryService into the test."""

    class _CardlessService:
        pass

    return _CardlessService()
