"""TopicEmbeddingService is now a thin delegate over the shared
EmbeddingService — these tests cover the wrapper contract (empty-input guard,
delegation, the topic-specific 2000-char trim). The provider resolution + Ark
multimodal request/parse are tested in tests/test_embedding_service.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.topics.embedding_service import TopicEmbeddingService

_GEN = "app.services.ai.providers.embedding_service.EmbeddingService.generate_embedding"


@pytest.mark.asyncio
async def test_embed_text_empty_input_returns_none():
    assert await TopicEmbeddingService().embed_text("   ") is None


@pytest.mark.asyncio
async def test_embed_text_delegates_to_embedding_service():
    with patch(_GEN, new=AsyncMock(return_value=[0.5, 0.6])) as gen:
        out = await TopicEmbeddingService().embed_text("hello world")
    assert out == [0.5, 0.6]
    gen.assert_awaited_once()
    (arg,), _ = gen.call_args
    assert arg == "hello world"


@pytest.mark.asyncio
async def test_embed_text_trims_to_max_chars():
    gen = AsyncMock(return_value=[0.1])
    with patch(_GEN, new=gen):
        await TopicEmbeddingService().embed_text("x" * 5000)
    (arg,), _ = gen.call_args
    assert len(arg) == 2000


@pytest.mark.asyncio
async def test_embed_text_propagates_none_when_disabled():
    with patch(_GEN, new=AsyncMock(return_value=None)):
        assert await TopicEmbeddingService().embed_text("hello") is None
