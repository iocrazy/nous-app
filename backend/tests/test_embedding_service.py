from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.providers.embedding_config import EmbeddingConfig
from app.services.ai.providers.embedding_service import EmbeddingService


@pytest.mark.asyncio
async def test_disabled_when_unconfigured_returns_none() -> None:
    with patch(
        "app.services.ai.providers.embedding_service.get_embedding_config",
        AsyncMock(return_value=None),
    ):
        svc = EmbeddingService()
        assert await svc.generate_embedding("hello") is None


@pytest.mark.asyncio
async def test_uses_settings_config_and_model() -> None:
    cfg = EmbeddingConfig(
        base_url="http://10.0.0.10:8000/v1",
        api_key="sk-x",
        model="qwen3-embedding-8b",
        dimensions=4096,
    )
    fake_resp = MagicMock()
    fake_resp.data = [MagicMock(embedding=[0.1] * 4096)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)

    with (
        patch(
            "app.services.ai.providers.embedding_service.get_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ) as mk,
    ):
        svc = EmbeddingService()
        out = await svc.generate_embedding("hello")

    assert out == [0.1] * 4096
    mk.assert_called_once_with(api_key="sk-x", base_url="http://10.0.0.10:8000/v1")
    _, kwargs = fake_client.embeddings.create.call_args
    assert kwargs["model"] == "qwen3-embedding-8b"


@pytest.mark.asyncio
async def test_no_openai_env_read() -> None:
    import inspect

    import app.services.ai.providers.embedding_service as mod

    src = inspect.getsource(mod)
    assert "OPENAI_API_KEY" not in src
    assert "OPENAI_EMBEDDING_MODEL" not in src
