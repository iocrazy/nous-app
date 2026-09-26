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


@pytest.fixture
def platform(monkeypatch):
    """The platform provider view's own seams (catalog full rows, governance,
    engine) — the scorer reads ``platform_rows``, never the table."""
    from tests.services.ai.test_platform_provider import Env

    env = Env(monkeypatch)
    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_gov()),
    )
    # The per-module nous gate is its own check; pin it open so these tests
    # prove the platform view's gates, not that one.
    for target in (
        "app.services.ai.governance.ai_governance.is_nous_allowed",
        "app.services.topics.topic_scorer.is_nous_allowed",
    ):
        monkeypatch.setattr(target, AsyncMock(return_value=True))
    return env


async def test_scorer_falls_to_first_enabled_platform_llm(platform):
    from tests.services.ai.test_platform_provider import catalog_row

    platform.rows = [
        catalog_row(
            "mediahub-deepseek-v4-flash",
            actual_provider="deepseek",
            actual_model="deepseek-v4-flash",
            base_url="https://cat/v1",
            api_key="sk-cat",
            last_test_status="not_probed",
        )
    ]
    cfg = await resolve_scorer_config()
    assert cfg.origin == "platform"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.provider_key == "deepseek"
    assert cfg.provider_config["api_key"] == "sk-cat"
    platform.repo.list_enabled_private.assert_awaited_with(None)


async def test_scorer_platform_pick_prefers_ok_over_idle_and_skips_fail(platform):
    """Same default rule as the canvas Catalog default (default_model_pick),
    over the platform view's live status: an idle (not loaded on nous-engine)
    row ranks after an ok one, a failing row is never reported."""
    from tests.services.ai.test_platform_provider import (
        catalog_row,
        engine_row,
        listed,
    )

    platform.rows = [
        engine_row("nous-qwen3-8-27b", "qwen3-8-27b"),
        catalog_row("broken", actual_model="doubao-pro", last_test_status="fail"),
        catalog_row(
            "mediahub-deepseek-v4-flash",
            actual_provider="deepseek",
            actual_model="deepseek-v4-flash",
        ),
    ]
    platform.engine_answers = [listed(("qwen3-8-27b", False))]
    cfg = await resolve_scorer_config()
    assert cfg.origin == "platform"
    assert cfg.model == "deepseek-v4-flash"


async def test_scorer_skips_a_service_the_engine_no_longer_lists(platform):
    from tests.services.ai.test_platform_provider import engine_row, listed

    platform.rows = [
        engine_row("nous-gone", "gone-llm"),
        engine_row("nous-here", "here-llm"),
    ]
    platform.engine_answers = [listed(("here-llm", True))]
    cfg = await resolve_scorer_config()
    assert cfg.model == "here-llm"


async def test_scorer_governance_off_has_no_platform_pick(platform):
    from tests.services.ai.test_platform_provider import catalog_row

    platform.governance = False
    platform.rows = [catalog_row("nous-a")]
    cfg = await resolve_scorer_config()
    assert cfg.origin == "env" and cfg.model == ""


async def test_scorer_pool_is_the_same_rows_in_the_same_order(platform):
    """``TopicScorerService._nous_candidates`` (the real failover pool) and
    the health reporter read one computation: same rows, same rank."""
    from app.services.topics.topic_scorer import TopicScorerService
    from tests.services.ai.test_platform_provider import (
        catalog_row,
        engine_row,
        listed,
    )

    platform.rows = [
        engine_row("nous-idle", "idle-llm"),
        engine_row("nous-gone", "gone-llm"),
        catalog_row("bad", last_test_status="fail"),
        catalog_row("good", actual_model="good-llm"),
    ]
    platform.engine_answers = [listed(("idle-llm", False))]
    pool = await TopicScorerService()._nous_candidates()
    assert [model for _, model in pool] == ["good-llm", "idle-llm"]
    cfg = await resolve_scorer_config()
    assert cfg.model == pool[0][1]


async def test_scorer_pool_is_empty_when_governance_is_off(platform):
    from app.services.topics.topic_scorer import TopicScorerService
    from tests.services.ai.test_platform_provider import catalog_row

    platform.governance = False
    platform.rows = [catalog_row("good")]
    assert await TopicScorerService()._nous_candidates() == []


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
