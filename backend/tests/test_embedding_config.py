from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    get_embedding_config,
)


@pytest.mark.asyncio
async def test_returns_config_from_settings() -> None:
    rows = {
        "graph_embedder_base_url": "http://10.0.0.10:8000/v1",
        "graph_embedder_api_key": "sk-x",
        "graph_embedder_model": "qwen3-embedding-8b",
        "graph_embedder_dimensions": 4096,
    }
    with patch(
        "app.services.ai.providers.embedding_config._read_settings",
        AsyncMock(return_value=rows),
    ):
        cfg = await get_embedding_config()
    assert cfg == EmbeddingConfig(
        base_url="http://10.0.0.10:8000/v1",
        api_key="sk-x",
        model="qwen3-embedding-8b",
        dimensions=4096,
    )


@pytest.mark.asyncio
async def test_none_when_no_base_url_or_key() -> None:
    with patch(
        "app.services.ai.providers.embedding_config._read_settings",
        AsyncMock(return_value={"graph_embedder_model": "qwen3-embedding-8b"}),
    ):
        assert await get_embedding_config() is None


@pytest.mark.asyncio
async def test_none_when_db_unavailable() -> None:
    with patch(
        "app.services.ai.providers.embedding_config._read_settings",
        AsyncMock(return_value={}),
    ):
        assert await get_embedding_config() is None
