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
        "app.repositories.nous_model_repository.get_nous_model_repository",
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

    @pytest.mark.parametrize(
        "label,model",
        [
            ("kimi", "kimi-k2.5"),
            ("minimax", "MiniMax-M2.5"),
            # No prefix rule knows local model ids — the row's label is the
            # only thing that routes them. Before 2026-09-22 these went to qwen.
            ("ollama", "qwen2.5:7b"),
            ("lmstudio", "local-model"),
        ],
    )
    def test_former_byok_only_labels_dispatch_to_their_own_protocol(self, label, model):
        assert resolve_provider_key(label, model) == label

    def test_volcengine_label_is_not_a_chat_dispatch_key(self):
        # A speech key. It must not become a chat key; its ASR path reads
        # actual_provider directly and never comes through here.
        assert resolve_provider_key("volcengine", "no-prefix-asr") == "qwen"

    def test_unknown_label_falls_back_to_prefix_rule(self):
        assert resolve_provider_key("volcengine-ark", "doubao-pro-32k") == "doubao"

    def test_unknown_label_and_prefix_fall_back_to_openai_compatible(self):
        # Same contract the health probe validates: base_url + /chat/completions.
        #
        # The sentinel used to be "nous", which stopped being unknown the day
        # the self-hosted engine got its own protocol — the test then asserted
        # the fallback while exercising a real key. A sentinel has to be a
        # string that cannot become a provider, not merely one that is not a
        # provider yet.
        assert (
            resolve_provider_key("not-a-registered-protocol", "qwen3-6-35b") == "qwen"
        )


class TestModelPrefixRules:
    """BYOK chat resolves the provider from the model id alone
    (``get_adapter_for_user``). Until 2026-09-22 there was no rule for the Kimi
    and MiniMax model ids the Settings card offers, so every BYOK chat turn on
    them raised "unsupported model"."""

    @pytest.mark.parametrize(
        "model,key",
        [
            ("kimi-k2.5", "kimi"),
            ("kimi-k2", "kimi"),
            ("moonshot-v1-128k", "kimi"),
            ("MiniMax-M2.5", "minimax"),
            ("MiniMax-M2.5-highspeed", "minimax"),
            ("minimax-m2", "minimax"),
        ],
    )
    def test_vendor_prefixes(self, model, key):
        from app.services.ai.adapters.factory import provider_key_for_model

        assert provider_key_for_model(model) == key

    def test_byok_kimi_builds_the_kimi_adapter_end_to_end(self):
        from app.services.ai.adapters.factory import get_adapter_for_user
        from app.services.ai.adapters.kimi import KimiAdapter

        a = get_adapter_for_user("kimi-k2.5", {"kimi": {"api_key": "k"}}, None)
        assert isinstance(a, KimiAdapter)
        assert a.api_url == "https://api.moonshot.cn/v1/chat/completions"

    def test_byok_minimax_without_key_is_typed_not_configured(self):
        from app.services.ai.adapters.factory import get_adapter_for_user
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        with pytest.raises(ProviderNotConfiguredError) as exc:
            get_adapter_for_user("MiniMax-M2.5", {}, None)
        assert exc.value.provider == "minimax"


class TestCatalogDispatch:
    @pytest.mark.asyncio
    async def test_blank_key_kimi_row_raises_typed_error_not_qwen(self):
        """Behaviour change, pinned: a kimi platform row with no key used to
        fall through to the generic qwen adapter and 401 upstream. It now
        raises the same typed error every other keyed protocol raises."""
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        with pytest.raises(ProviderNotConfiguredError) as exc:
            await _resolve(
                _row(actual_provider="kimi", actual_model="kimi-k2.5", api_key="")
            )
        assert exc.value.provider == "kimi"

    @pytest.mark.asyncio
    async def test_nous_qwen3_35b_row_builds_an_adapter(self):
        """The exact real-machine failure: unknown-prefix actual_model on an
        enabled catalog row must build, not raise 'unsupported model'."""
        adapter = await _resolve(_row())
        assert adapter is not None

    @pytest.mark.asyncio
    async def test_free_text_actual_provider_still_builds(self):
        adapter = await _resolve(_row(actual_provider="some-free-text-label"))
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
