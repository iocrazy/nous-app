from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    _is_multimodal,
    get_embedding_config,
    resolve_embedding_config,
)


class _Gov:
    def __init__(self, base_url="", model="", api_key=""):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.api_key_present = bool(api_key.strip())


def test_is_multimodal_detection() -> None:
    assert _is_multimodal("doubao-embedding-vision-250615", "https://x/v3")
    assert _is_multimodal("m", "https://x/api/v3/embeddings/multimodal")
    assert not _is_multimodal("qwen3-embedding-8b", "http://10.0.0.10:8000/v1")


@pytest.mark.asyncio
async def test_resolve_prefers_admin_module_config() -> None:
    gov = _Gov(
        base_url="https://ark/api/v3/embeddings/multimodal",
        model="doubao-embedding-vision-250615",
        api_key="sk-doubao",
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=gov),
    ):
        cfg = await resolve_embedding_config()
    assert cfg.base_url == "https://ark/api/v3/embeddings/multimodal"
    assert cfg.model == "doubao-embedding-vision-250615"
    assert cfg.multimodal is True


@pytest.mark.asyncio
async def test_resolve_falls_back_to_graph_embedder() -> None:
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_Gov()),  # admin not configured
        ),
        patch(
            "app.services.ai.providers.embedding_config.get_embedding_config",
            AsyncMock(
                return_value=EmbeddingConfig(
                    base_url="http://10.0.0.10:8000/v1",
                    api_key="k",
                    model="qwen3-embedding-8b",
                    dimensions=4096,
                )
            ),
        ),
    ):
        cfg = await resolve_embedding_config()
    assert cfg.model == "qwen3-embedding-8b"
    assert cfg.multimodal is False


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
        source="governance",
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


@pytest.mark.asyncio
async def test_resolve_embedding_uses_catalog_model() -> None:
    """When ai_module.embedding.model is a catalog name, resolve via the catalog
    (ungated) — base_url/key/actual_model from the platform model."""
    gov = _Gov(base_url="", model="mediahub-doubao-embedding-vision", api_key="")
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=gov),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(
                return_value=(
                    "doubao",
                    {
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                        "api_key": "k",
                        "model": "doubao-embedding-vision-251215",
                    },
                    "doubao-embedding-vision-251215",
                )
            ),
        ),
    ):
        cfg = await resolve_embedding_config()
    assert cfg.base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg.model == "doubao-embedding-vision-251215"
    assert cfg.api_key == "k"
    assert cfg.multimodal is True
