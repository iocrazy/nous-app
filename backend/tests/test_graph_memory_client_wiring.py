"""Backend wiring for frontend-settable graph memory (Phase 4, follows #706).

Two pieces:
  * _build_llm_and_embedder — turn the admin-set extractor/embedder config into
    explicit Graphiti OpenAI-compatible clients (else (None, None) => Graphiti
    keeps its own OPENAI_* env defaults).
  * GraphMemoryService._ensure_config — load config from system_settings (async
    from_settings) on first real use, so the gate/host/provider come from DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai.memory.graph_memory import (
    GraphMemoryConfig,
    GraphMemoryService,
    _build_llm_and_embedder,
)


def _cfg(**kw) -> GraphMemoryConfig:
    return GraphMemoryConfig(**kw)


# ----- _build_llm_and_embedder -----------------------------------------


def test_build_returns_none_triple_when_no_extractor_key():
    llm, embedder, reranker = _build_llm_and_embedder(_cfg(extractor_api_key=""))
    assert llm is None and embedder is None and reranker is None


def test_build_llm_carries_explicit_config():
    llm, embedder, reranker = _build_llm_and_embedder(
        _cfg(
            extractor_api_key="ms-key",
            extractor_base_url="https://ms.example/v1",
            extractor_model="Qwen/Qwen2.5-72B-Instruct",
        )
    )
    assert llm is not None
    assert llm.config.api_key == "ms-key"
    assert llm.config.base_url == "https://ms.example/v1"
    assert llm.config.model == "Qwen/Qwen2.5-72B-Instruct"
    # no embedder key => embedder stays None (Graphiti env default)
    assert embedder is None
    # cross_encoder is ALWAYS built from the extractor config when a key exists —
    # otherwise Graphiti's __init__ falls back to OpenAIRerankerClient() which
    # reads OPENAI_API_KEY and crashes the whole client build (the #709 bug this
    # validation surfaced against dev FalkorDB).
    assert reranker is not None
    assert reranker.config.api_key == "ms-key"
    assert reranker.config.base_url == "https://ms.example/v1"


def test_build_llm_defaults_to_json_object_mode():
    # ModelScope/Qwen (and DeepSeek-class providers) reject Graphiti's complex
    # json_schema constrained-decoding payload (returns choices=None); json_object
    # — schema injected into the prompt — is the working path, so it is the default.
    llm, _, _ = _build_llm_and_embedder(_cfg(extractor_api_key="ms-key"))
    assert llm.structured_output_mode == "json_object"


def test_build_llm_honours_explicit_structured_output_mode():
    llm, _, _ = _build_llm_and_embedder(
        _cfg(extractor_api_key="ms-key", extractor_structured_output_mode="json_schema")
    )
    assert llm.structured_output_mode == "json_schema"


def test_build_embedder_when_embedder_key_set():
    llm, embedder, _ = _build_llm_and_embedder(
        _cfg(
            extractor_api_key="ms-key",
            embedder_api_key="ms-key",
            embedder_base_url="https://ms.example/v1",
            embedder_model="Qwen/Qwen3-Embedding-4B",
        )
    )
    assert llm is not None
    assert embedder is not None
    assert embedder.config.embedding_model == "Qwen/Qwen3-Embedding-4B"
    # Default dimension flows into the embedder config (sizes the FalkorDB index).
    assert embedder.config.embedding_dim == 1536


def test_build_embedder_carries_explicit_dimension():
    # Admin-set dimension is passed into OpenAIEmbedderConfig.embedding_dim.
    _, embedder, _ = _build_llm_and_embedder(
        _cfg(
            extractor_api_key="ms-key",
            embedder_api_key="ms-key",
            embedder_dimensions=1024,
        )
    )
    assert embedder is not None
    assert embedder.config.embedding_dim == 1024


# ----- _ensure_config --------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_config_loads_from_settings_once(monkeypatch):
    loaded = GraphMemoryConfig(enabled=True, falkordb_host="db-host")
    fake = AsyncMock(return_value=loaded)
    monkeypatch.setattr(GraphMemoryConfig, "from_settings", fake)

    svc = GraphMemoryService()  # starts from env (disabled)
    await svc._ensure_config()
    assert svc.config.falkordb_host == "db-host"
    assert svc.config.enabled is True

    # second call is a no-op (single load)
    await svc._ensure_config()
    assert fake.await_count == 1


@pytest.mark.asyncio
async def test_ensure_config_skipped_when_graphiti_injected(monkeypatch):
    fake = AsyncMock(return_value=GraphMemoryConfig(enabled=True, falkordb_host="x"))
    monkeypatch.setattr(GraphMemoryConfig, "from_settings", fake)

    # Tests inject a client + their own config — never override it.
    test_cfg = GraphMemoryConfig(enabled=True)
    svc = GraphMemoryService(config=test_cfg, graphiti=object())
    await svc._ensure_config()
    assert svc.config is test_cfg
    assert fake.await_count == 0


@pytest.mark.asyncio
async def test_ensure_config_keeps_env_config_on_failure(monkeypatch):
    boom = AsyncMock(side_effect=RuntimeError("settings down"))
    monkeypatch.setattr(GraphMemoryConfig, "from_settings", boom)

    svc = GraphMemoryService()
    env_cfg = svc.config
    await svc._ensure_config()  # must not raise
    assert svc.config is env_cfg  # unchanged on failure
