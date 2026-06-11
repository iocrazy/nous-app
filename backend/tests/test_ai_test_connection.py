"""Tests for AIProviderFactory.test_connection truthfulness.

Regression for the "disabled key still shows Connected" bug: the
OpenAI-compatible ``list_models`` used to swallow auth failures into an
empty list, so test_connection reported success and the Settings UI kept
rendering the stale model catalog.
"""

from __future__ import annotations

from typing import List

import pytest

from app.services.ai.providers.ai_provider import (
    AIProviderFactory,
    OpenAICompatibleProvider,
)


class _FakeProvider:
    def __init__(
        self, *, models: List[str] | None = None, error: Exception | None = None
    ):
        self._models = models or []
        self._error = error

    async def list_models(self) -> List[str]:
        if self._error:
            raise self._error
        return self._models


@pytest.mark.asyncio
async def test_failing_list_models_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProviderFactory,
        "get_provider",
        classmethod(
            lambda cls, key, config: _FakeProvider(
                error=RuntimeError("Error code: 401 - invalid api key")
            )
        ),
    )
    result = await AIProviderFactory.test_connection(
        provider_key="doubao", config={"api_key": "revoked"}
    )
    assert result["success"] is False
    assert "401" in result["error"]
    assert result["models"] is None


@pytest.mark.asyncio
async def test_working_provider_reports_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProviderFactory,
        "get_provider",
        classmethod(lambda cls, key, config: _FakeProvider(models=["m1", "m2"])),
    )
    result = await AIProviderFactory.test_connection(
        provider_key="doubao", config={"api_key": "ok"}
    )
    assert result == {"success": True, "models": ["m1", "m2"], "error": None}


@pytest.mark.asyncio
async def test_compatible_list_models_propagates_auth_errors() -> None:
    """list_models must NOT swallow failures into [] — that is exactly
    what made a revoked key look "Connected"."""

    provider = OpenAICompatibleProvider(api_key="bad", base_url="http://x")

    class _Boom:
        async def list(self):
            raise RuntimeError("401 unauthorized")

    provider._client.models = _Boom()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="401"):
        await provider.list_models()
