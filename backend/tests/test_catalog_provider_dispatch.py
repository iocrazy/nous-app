"""Platform catalog rows dispatch on actual_provider, never model-prefix
guessing (real-machine bug 2026-07-12).

The user picked "Nous Qwen3 35B" — a REAL enabled llm catalog row whose
``actual_model`` is ``qwen3-6-35b``. ``resolve_db_adapter`` resolved the row,
then handed the actual_model to the factory, whose ``provider_key_for_model``
prefix rule doesn't know ``qwen3-`` and raised "unsupported model". Meanwhile
the admin health probe validates the SAME row via a plain OpenAI-compatible
``base_url + /chat/completions`` call — green dot, dead canvas. One catalog
row, two interpretations: exactly the data-source drift this epic kills.

Contract pinned here: a catalog hit dispatches on the row's ``actual_provider``
(the admin named it explicitly); unknown labels fall back to the prefix rule,
and finally to the OpenAI-compatible QwenAdapter — the same contract the
health probe exercises. These tests drive the REAL resolve chain (repo is the
only stub), per the epic's testing discipline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.adapters.factory import resolve_provider_key
from app.services.ai.providers.ai_provider_helpers import resolve_db_adapter


def _row(**overrides) -> dict:
    base = {
        "name": "nous-qwen3-35b",
        "display_name": "Nous Qwen3 35B",
        "type": "llm",
        "is_enabled": True,
        "actual_provider": "qwen",
        "actual_model": "qwen3-6-35b",  # prefix the factory does NOT know
        "api_key": "platform-key",
        "base_url": "https://vllm.example.com/v1",
        "app_id": None,
    }
    base.update(overrides)
    return base


def _repo(row: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=row)
    repo.get_by_actual_model = AsyncMock(return_value=None)
    return repo


async def _resolve(row: dict):
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=_repo(row),
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            return await resolve_db_adapter("nous-qwen3-35b", "canvas")


class TestResolveProviderKey:
    def test_known_actual_provider_wins_over_unknown_prefix(self):
        assert resolve_provider_key("qwen", "qwen3-6-35b") == "qwen"

    def test_known_actual_provider_wins_over_conflicting_prefix(self):
        # Admin says doubao; a qwen-prefixed actual_model must not override.
        assert resolve_provider_key("doubao", "qwen-plus") == "doubao"

    def test_unknown_label_falls_back_to_prefix_rule(self):
        assert resolve_provider_key("volcengine-ark", "doubao-pro-32k") == "doubao"

    def test_unknown_label_and_prefix_fall_back_to_openai_compatible(self):
        # Same contract the health probe validates: base_url + /chat/completions.
        assert resolve_provider_key("nous", "qwen3-6-35b") == "qwen"


class TestCatalogDispatch:
    @pytest.mark.asyncio
    async def test_nous_qwen3_35b_row_builds_an_adapter(self):
        """The exact real-machine failure: unknown-prefix actual_model on an
        enabled catalog row must build, not raise 'unsupported model'."""
        adapter = await _resolve(_row())
        assert adapter is not None

    @pytest.mark.asyncio
    async def test_free_text_actual_provider_still_builds(self):
        adapter = await _resolve(_row(actual_provider="nous"))
        assert adapter is not None

    @pytest.mark.asyncio
    async def test_known_prefix_row_unchanged(self):
        # Regression net: doubao rows keep building the doubao adapter path.
        adapter = await _resolve(
            _row(
                actual_provider="doubao",
                actual_model="doubao-pro-32k",
                base_url="https://ark.example.com/v1",
            )
        )
        assert adapter is not None
