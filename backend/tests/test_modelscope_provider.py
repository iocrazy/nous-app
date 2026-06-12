"""ModelScope (魔搭) provider — OpenAI-compatible inference.

Model IDs on ModelScope are ``org/name`` (e.g. ``Qwen/Qwen3-235B-A22B``),
so the slash is the routing discriminator — and it must be checked BEFORE
any prefix rule (``deepseek-ai/DeepSeek-V3`` starts with ``deepseek-``).
"""

from __future__ import annotations

import pytest

from app.services.ai.adapters.factory import (
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai.adapters.modelscope import (
    MODELSCOPE_DEFAULT_URL,
    ModelScopeAdapter,
)
from app.services.ai.providers.ai_provider import AIProviderFactory


class _Settings:
    """Duck-typed fallback settings without any MODELSCOPE_* attrs —
    the factory must not require new global settings for a BYO-only
    provider."""

    LLM_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    LLM_API_KEY = "global-qwen-key"
    LLM_MODEL = "qwen-max"


# ============================================================
# Routing
# ============================================================


class TestRouting:
    def test_slash_model_routes_to_modelscope(self) -> None:
        assert provider_key_for_model("Qwen/Qwen3-235B-A22B") == "modelscope"

    def test_slash_wins_over_prefix_rules(self) -> None:
        # Starts with "deepseek-" but the slash marks it as ModelScope.
        assert provider_key_for_model("deepseek-ai/DeepSeek-V3.1") == "modelscope"

    def test_prefix_rules_unaffected(self) -> None:
        assert provider_key_for_model("deepseek-chat") == "deepseek"
        assert provider_key_for_model("qwen-max") == "qwen"

    def test_unknown_model_still_raises(self) -> None:
        with pytest.raises(ValueError):
            provider_key_for_model("totally-unknown-model")


# ============================================================
# Chat adapter (AgentRunner path)
# ============================================================


class TestAdapter:
    def test_byo_config_builds_modelscope_adapter(self) -> None:
        adapter = get_adapter_for_user(
            "Qwen/Qwen3-235B-A22B",
            {"modelscope": {"api_key": "ms-key", "base_url": ""}},
            _Settings(),
        )
        assert isinstance(adapter, ModelScopeAdapter)
        assert adapter.api_key == "ms-key"
        assert adapter.api_url == MODELSCOPE_DEFAULT_URL

    def test_user_base_url_override(self) -> None:
        adapter = get_adapter_for_user(
            "Qwen/Qwen3-235B-A22B",
            {"modelscope": {"api_key": "ms-key", "base_url": "https://my-proxy/v1"}},
            _Settings(),
        )
        assert "my-proxy" in adapter.api_url

    def test_no_user_config_builds_keyless_adapter(self) -> None:
        # No global MODELSCOPE_* settings exist; missing user config must
        # not crash — it builds an adapter with an empty key (the request
        # will fail with a clear auth error from ModelScope).
        adapter = get_adapter_for_user("Qwen/Qwen3-235B-A22B", {}, _Settings())
        assert isinstance(adapter, ModelScopeAdapter)
        assert adapter.api_key == ""

    def test_default_url_is_chat_completions(self) -> None:
        assert MODELSCOPE_DEFAULT_URL == (
            "https://api-inference.modelscope.cn/v1/chat/completions"
        )


# ============================================================
# Settings-page provider (test-connection / list models path)
# ============================================================


class TestSettingsProvider:
    def test_registered_in_factory(self) -> None:
        assert "modelscope" in AIProviderFactory.available_providers()

    def test_default_base_url(self) -> None:
        provider = AIProviderFactory.get_provider("modelscope", {"api_key": "ms-key"})
        assert str(provider._client.base_url).startswith(
            "https://api-inference.modelscope.cn/v1"
        )

    @pytest.mark.asyncio
    async def test_list_models_probes_auth_with_chat(self) -> None:
        """ModelScope's /v1/models is public — it 200s even for an invalid
        token. list_models must therefore also fire a 1-token chat so a
        bad key fails Test Connection instead of showing Connected
        (#659 bug class)."""
        provider = AIProviderFactory.get_provider("modelscope", {"api_key": "bad"})
        provider._client.models = _models_stub(["Qwen/Qwen2.5-72B-Instruct"])
        provider._client.chat = _chat_stub(
            error=RuntimeError("Error code: 401 - invalid token")
        )

        with pytest.raises(RuntimeError, match="401"):
            await provider.list_models()

    @pytest.mark.asyncio
    async def test_list_models_returns_catalog_when_auth_ok(self) -> None:
        provider = AIProviderFactory.get_provider("modelscope", {"api_key": "good"})
        provider._client.models = _models_stub(
            ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3.1"]
        )
        provider._client.chat = _chat_stub()

        models = await provider.list_models()
        assert models == ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3.1"]

    @pytest.mark.asyncio
    async def test_quota_captured_from_response_headers(self) -> None:
        provider = AIProviderFactory.get_provider("modelscope", {"api_key": "good"})
        provider._client.models = _models_stub(["Qwen/Qwen2.5-72B-Instruct"])
        provider._client.chat = _chat_stub(
            headers={
                "modelscope-ratelimit-requests-limit": "2000",
                "modelscope-ratelimit-requests-remaining": "200",
                "modelscope-ratelimit-model-requests-limit": "200",
                "modelscope-ratelimit-model-requests-remaining": "20",
            }
        )

        await provider.list_models()
        assert provider.last_quota == {
            "requests_limit": 2000,
            "requests_remaining": 200,
            "model_requests_limit": 200,
            "model_requests_remaining": 20,
        }

    @pytest.mark.asyncio
    async def test_quota_none_when_headers_absent(self) -> None:
        provider = AIProviderFactory.get_provider("modelscope", {"api_key": "good"})
        provider._client.models = _models_stub(["Qwen/Qwen2.5-72B-Instruct"])
        provider._client.chat = _chat_stub(headers={})

        await provider.list_models()
        assert provider.last_quota is None

    @pytest.mark.asyncio
    async def test_test_connection_includes_quota(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeProvider:
            last_quota = {"requests_limit": 2000, "requests_remaining": 200}

            async def list_models(self):
                return ["Qwen/Qwen2.5-72B-Instruct"]

        monkeypatch.setattr(
            AIProviderFactory,
            "get_provider",
            classmethod(lambda cls, key, config: _FakeProvider()),
        )
        result = await AIProviderFactory.test_connection(
            provider_key="modelscope", config={"api_key": "ms-x"}
        )
        assert result["success"] is True
        assert result["quota"] == {"requests_limit": 2000, "requests_remaining": 200}

    @pytest.mark.asyncio
    async def test_test_connection_quota_none_for_plain_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _PlainProvider:
            async def list_models(self):
                return ["deepseek-chat"]

        monkeypatch.setattr(
            AIProviderFactory,
            "get_provider",
            classmethod(lambda cls, key, config: _PlainProvider()),
        )
        result = await AIProviderFactory.test_connection(
            provider_key="deepseek", config={"api_key": "x"}
        )
        assert result["quota"] is None


def _models_stub(ids: list[str]):
    class _Models:
        async def list(self):
            class _Resp:
                data = [type("_Item", (), {"id": i})() for i in ids]

            return _Resp()

    return _Models()


def _chat_stub(*, error: Exception | None = None, headers: dict | None = None):
    """Mimics AsyncOpenAI's chat namespace incl. with_raw_response."""

    class _Raw:
        def __init__(self):
            self.headers = headers or {}

    class _RawCompletions:
        async def create(self, **kwargs):
            if error:
                raise error
            return _Raw()

    class _Completions:
        with_raw_response = _RawCompletions()

        async def create(self, **kwargs):
            if error:
                raise error
            return {"ok": True}

    class _Chat:
        completions = _Completions()

    return _Chat()


class TestEmptyModelProbe:
    """Regression: Settings Test Connection sends model="" (the frontend
    omits the field, the schema defaults to empty string), which bypassed
    the keyword default and made the auth probe fail with
    "Invalid model id: " (400)."""

    def test_empty_model_falls_back_to_default(self) -> None:
        provider = AIProviderFactory.get_provider(
            "modelscope", {"api_key": "ms-x", "base_url": "", "model": ""}
        )
        assert provider.model == provider.DEFAULT_MODEL

    def test_explicit_model_kept(self) -> None:
        provider = AIProviderFactory.get_provider(
            "modelscope", {"api_key": "ms-x", "model": "deepseek-ai/DeepSeek-V3.1"}
        )
        assert provider.model == "deepseek-ai/DeepSeek-V3.1"
