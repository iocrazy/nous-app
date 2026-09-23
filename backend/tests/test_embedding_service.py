from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.embedding_space import EMBEDDING_DIM
from app.services.ai.providers.embedding_config import EmbeddingConfig
from app.services.ai.providers.embedding_service import EmbeddingService


@pytest.mark.asyncio
async def test_disabled_when_unconfigured_returns_none() -> None:
    with patch(
        "app.services.ai.providers.embedding_service.resolve_embedding_config",
        AsyncMock(return_value=None),
    ):
        svc = EmbeddingService()
        assert await svc.generate_embedding("hello") is None


@pytest.mark.asyncio
async def test_uses_openai_shape_for_non_multimodal() -> None:
    cfg = EmbeddingConfig(
        base_url="http://10.0.0.10:8000/v1",
        api_key="sk-x",
        model="qwen3-embedding-8b",
        dimensions=4096,
        multimodal=False,
    )
    fake_resp = MagicMock()
    # Column width, not the model's native 4096: the writer seam refuses any
    # other width (tests/test_embedding_space.py).
    fake_resp.data = [MagicMock(embedding=[0.1] * EMBEDDING_DIM)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)

    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ) as mk,
    ):
        svc = EmbeddingService()
        out = await svc.generate_embedding("hello")

    assert out == [0.1] * EMBEDDING_DIM
    mk.assert_called_once()
    ck = mk.call_args.kwargs
    assert ck["api_key"] == "sk-x" and ck["base_url"] == "http://10.0.0.10:8000/v1"
    # Bounded: the SDK default (600s x 2 retries) would let one search hang
    # for half an hour now that hybrid search embeds the query.
    assert ck["max_retries"] == 1 and ck["timeout"].read == 30.0
    _, kwargs = fake_client.embeddings.create.call_args
    assert kwargs["model"] == "qwen3-embedding-8b"


@pytest.mark.asyncio
async def test_multimodal_uses_ark_shape() -> None:
    cfg = EmbeddingConfig(
        base_url="https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal",
        api_key="sk-doubao",
        model="doubao-embedding-vision-250615",
        dimensions=0,
        multimodal=True,
    )
    posted = {}

    class _Resp:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {"data": {"embedding": [0.2] * 2048}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            posted["url"] = url
            posted["json"] = json
            posted["headers"] = headers
            return _Resp()

    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.httpx.AsyncClient",
            _Client,
        ),
    ):
        svc = EmbeddingService()
        out = await svc.generate_embedding("hello")

    assert out == [0.2] * 2048
    assert posted["url"].endswith("/embeddings/multimodal")
    assert posted["json"] == {
        "model": "doubao-embedding-vision-250615",
        "input": [{"type": "text", "text": "hello"}],
    }
    assert posted["headers"]["Authorization"] == "Bearer sk-doubao"


@pytest.mark.asyncio
async def test_no_openai_env_read() -> None:
    import inspect

    import app.services.ai.providers.embedding_service as mod

    src = inspect.getsource(mod)
    assert "OPENAI_API_KEY" not in src
    assert "OPENAI_EMBEDDING_MODEL" not in src


# ---------------------------------------------------------------------------
# try_embed: the reason travels with the None (2026-09-15)
#
# ``generate_embedding`` collapses three different situations into one
# ``None`` — embedder not configured, empty input, provider call failed.
# analyze_l1 read that ``None`` as "skip quietly" for months, which is why
# ``resource_analysis.content_embedding`` stayed at zero rows while the
# workflow reported success. ``try_embed`` keeps the vector-or-None contract
# but returns WHY, so the caller can record it instead of swallowing it.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_try_embed_reports_unconfigured() -> None:
    with patch(
        "app.services.ai.providers.embedding_service.resolve_embedding_config",
        AsyncMock(return_value=None),
    ):
        vec, reason = await EmbeddingService().try_embed("hello")
    assert vec is None
    assert reason == "unconfigured"


@pytest.mark.asyncio
async def test_try_embed_reports_empty_text() -> None:
    cfg = EmbeddingConfig(
        base_url="http://x/v1", api_key="k", model="m", dimensions=4, multimodal=False
    )
    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch("app.services.ai.providers.embedding_service.AsyncOpenAI"),
    ):
        vec, reason = await EmbeddingService().try_embed("   ")
    assert vec is None
    assert reason == "empty_text"


@pytest.mark.asyncio
async def test_try_embed_reports_provider_error_with_message() -> None:
    cfg = EmbeddingConfig(
        base_url="http://x/v1", api_key="k", model="m", dimensions=4, multimodal=False
    )
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(side_effect=RuntimeError("boom 503"))
    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ),
    ):
        vec, reason = await EmbeddingService().try_embed("hello")
    assert vec is None
    assert reason is not None and reason.startswith("provider_error")
    assert "boom 503" in reason


@pytest.mark.asyncio
async def test_try_embed_returns_vector_and_no_reason_on_success() -> None:
    cfg = EmbeddingConfig(
        base_url="http://x/v1", api_key="k", model="m", dimensions=3, multimodal=False
    )
    fake_resp = MagicMock()
    fake_resp.data = [MagicMock(embedding=[0.1] * EMBEDDING_DIM)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)
    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ),
    ):
        vec, reason = await EmbeddingService().try_embed("hello")
    assert vec == [0.1] * EMBEDDING_DIM
    assert reason is None


@pytest.mark.asyncio
async def test_generate_embedding_is_try_embed_without_the_reason() -> None:
    """The old entry point keeps its contract for its many callers."""
    with patch(
        "app.services.ai.providers.embedding_service.resolve_embedding_config",
        AsyncMock(return_value=None),
    ):
        assert await EmbeddingService().generate_embedding("hello") is None


@pytest.mark.asyncio
async def test_try_embed_reports_a_200_with_no_vector_as_a_provider_error() -> None:
    """The multimodal leg returns ``None`` when the body carries no vector.

    That is a failure, not a skip — without the explicit ``if not vec`` guard
    it would fall through to ``return vec, None`` and hand the caller
    ``(None, None)``, i.e. the exact swallowed-``None`` shape ``try_embed``
    exists to abolish.
    """
    cfg = EmbeddingConfig(
        base_url="https://ark/api/v3/embeddings/multimodal",
        api_key="sk-doubao",
        model="doubao-embedding-vision-250615",
        dimensions=0,
        multimodal=True,
    )

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {}}  # 200, but no embedding in the body

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            return _Resp()

    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch("app.services.ai.providers.embedding_service.httpx.AsyncClient", _Client),
    ):
        vec, reason = await EmbeddingService().try_embed("hello")

    assert vec is None
    assert reason == "provider_error: empty vector in response"


@pytest.mark.asyncio
async def test_try_embed_truncates_over_long_text_before_calling_the_provider() -> None:
    from app.services.ai.providers.embedding_service import _MAX_CHARS

    cfg = EmbeddingConfig(
        base_url="http://x/v1", api_key="k", model="m", dimensions=3, multimodal=False
    )
    fake_resp = MagicMock()
    fake_resp.data = [MagicMock(embedding=[0.1] * EMBEDDING_DIM)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)

    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ),
    ):
        vec, reason = await EmbeddingService().try_embed("x" * (_MAX_CHARS + 500))

    assert vec == [0.1] * EMBEDDING_DIM and reason is None
    _, kwargs = fake_client.embeddings.create.call_args
    assert len(kwargs["input"]) == _MAX_CHARS


@pytest.mark.asyncio
async def test_generate_embedding_returns_the_vector_on_success() -> None:
    """The wrapper drops the reason but must not drop the vector."""
    cfg = EmbeddingConfig(
        base_url="http://x/v1", api_key="k", model="m", dimensions=2, multimodal=False
    )
    fake_resp = MagicMock()
    fake_resp.data = [MagicMock(embedding=[0.4] * EMBEDDING_DIM)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)

    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ),
    ):
        assert await EmbeddingService().generate_embedding("hello") == (
            [0.4] * EMBEDDING_DIM
        )


@pytest.mark.parametrize(
    "reason,code",
    [
        (None, None),
        ("unconfigured", "unconfigured"),
        ("empty_text", "empty_text"),
        ("provider_error: 502 for url 'https://ark.internal/v3'", "provider_error"),
        ("provider_error", "provider_error"),
        ("something new: detail", "provider_error"),
    ],
)
def test_classify_embed_reason_strips_provider_text(reason, code) -> None:
    """The part after the colon is raw SDK/provider text (URLs, bodies): it
    belongs in the log, never in a task subtitle or an API response."""
    from app.services.ai.providers.embedding_service import classify_embed_reason

    assert classify_embed_reason(reason) == code


@pytest.mark.asyncio
async def test_try_embed_turns_a_client_init_failure_into_a_reason() -> None:
    """A bad base_url raises inside the SDK constructor, outside the call
    try/except; hybrid search must still get (None, reason), not a 500."""
    cfg = EmbeddingConfig(
        base_url="https://host:badport/v1",
        api_key="k",
        model="m",
        dimensions=4,
        multimodal=False,
    )
    with (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            side_effect=ValueError("Invalid URL"),
        ),
    ):
        vec, reason = await EmbeddingService().try_embed("hello")
    assert vec is None
    assert reason is not None and reason.startswith("provider_error")
