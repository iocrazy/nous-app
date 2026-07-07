"""A5 typed resolvers — resolve_scorer_config / resolve_embedding_ai_config.

Read-only reporters for the health board (Phase B): they mirror the runtime
resolution order of TopicScorer._resolve_candidates and
resolve_embedding_config but report only the first hit as a
ResolvedAIConfig with an origin tag. Never raise.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.providers.ai_provider_helpers import (
    ResolvedAIConfig,
    resolve_embedding_ai_config,
    resolve_scorer_config,
)
from app.services.ai.providers.embedding_config import EmbeddingConfig

pytestmark = pytest.mark.asyncio


def _gov(model="", api_key="", base_url=""):
    return SimpleNamespace(
        model=model,
        api_key=api_key,
        api_key_present=bool(api_key),
        base_url=base_url,
        allowed=True,
    )


# ─── resolve_scorer_config ────────────────────────────────────────────


async def test_scorer_governance_wins():
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_gov("doubao-pro", "sk-admin", "https://gov/v1")),
    ):
        cfg = await resolve_scorer_config()
    assert isinstance(cfg, ResolvedAIConfig)
    assert cfg.origin == "governance"
    assert cfg.model == "doubao-pro"
    assert cfg.provider_key == "doubao"
    assert cfg.provider_config["api_key"] == "sk-admin"
    assert cfg.agent_slug == "topic-scorer"


async def test_scorer_falls_to_first_enabled_platform_llm():
    repo = MagicMock()
    repo.list_enabled = AsyncMock(return_value=[{"name": "mediahub-deepseek-v4-flash"}])
    repo.get_by_name = AsyncMock(
        return_value={
            "name": "mediahub-deepseek-v4-flash",
            "actual_model": "deepseek-v4-flash",
            "base_url": "https://cat/v1",
            "api_key": "sk-cat",
            "app_id": "",
        }
    )
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_gov()),
        ),
        patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
            return_value=repo,
        ),
    ):
        cfg = await resolve_scorer_config()
    assert cfg.origin == "platform"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.provider_key == "deepseek"
    repo.list_enabled.assert_awaited_once_with("llm")


async def test_scorer_nothing_configured_degrades_not_raises():
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_gov()),
        ),
        patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            AsyncMock(return_value=False),
        ),
    ):
        cfg = await resolve_scorer_config()
    assert cfg.origin == "env"
    assert cfg.model == ""
    assert cfg.provider_config["api_key"] == ""


async def test_scorer_governance_read_failure_degrades():
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        cfg = await resolve_scorer_config()
    assert cfg.origin == "env"
    assert cfg.model == ""


# ─── resolve_embedding_ai_config ──────────────────────────────────────


async def test_embedding_platform_source_maps_to_platform_origin():
    emb = EmbeddingConfig(
        base_url="https://cat/v1",
        api_key="sk-cat",
        model="doubao-embedding-vision-251215",
        dimensions=0,
        multimodal=True,
        source="platform",
    )
    with patch(
        "app.services.ai.providers.embedding_config.resolve_embedding_config",
        AsyncMock(return_value=emb),
    ):
        cfg = await resolve_embedding_ai_config()
    assert cfg.origin == "platform"
    assert cfg.model == "doubao-embedding-vision-251215"
    assert cfg.provider_key == "doubao"
    assert cfg.provider_config["base_url"] == "https://cat/v1"


async def test_embedding_governance_source_and_unknown_prefix():
    emb = EmbeddingConfig(
        base_url="https://box.example/v1",
        api_key="sk-box",
        model="qwen3-embedding-8b",  # unknown factory prefix → provider_key ""
        dimensions=4096,
        source="governance",
    )
    with patch(
        "app.services.ai.providers.embedding_config.resolve_embedding_config",
        AsyncMock(return_value=emb),
    ):
        cfg = await resolve_embedding_ai_config()
    assert cfg.origin == "governance"
    assert cfg.provider_key == ""
    assert cfg.model == "qwen3-embedding-8b"


async def test_embedding_disabled_reports_not_configured():
    with patch(
        "app.services.ai.providers.embedding_config.resolve_embedding_config",
        AsyncMock(return_value=None),
    ):
        cfg = await resolve_embedding_ai_config()
    assert cfg.origin == "env"
    assert cfg.model == ""


async def test_embedding_read_failure_degrades_not_raises():
    with patch(
        "app.services.ai.providers.embedding_config.resolve_embedding_config",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        cfg = await resolve_embedding_ai_config()
    assert cfg.origin == "env"


async def test_embedding_legacy_source_blank_defaults_to_governance():
    emb = EmbeddingConfig(
        base_url="https://x/v1", api_key="k", model="deepseek-emb", dimensions=0
    )
    with patch(
        "app.services.ai.providers.embedding_config.resolve_embedding_config",
        AsyncMock(return_value=emb),
    ):
        cfg = await resolve_embedding_ai_config()
    assert cfg.origin == "governance"
