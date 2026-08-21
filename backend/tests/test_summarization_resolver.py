"""摘要模型解析收口 —— 模型来源只有 agent 配置(2026-08-20)。

背景(生产地面真值):`ai_agents.summarize.model` 早就配着一个好模型
(`doubao-seed-2-0-lite-260428`、探针绿),但摘要走的是一条它自己的
"provider 优先级扫描"(doubao → qwen → openai → deepseek,取各卡的
`selected_model`),**从头到尾没读过 agent 行**。用户在 doubao 卡里把
`selected_model` 选成 embedding 模型之后,每次摘要都拿 embedding id 去打
`/v1/chat/completions`,必败 —— 而 agent 里配的那个好模型一次都没被用过。

收口后摘要与 caption / visual_analysis / classification / translation 共用
``resolve_task_ai_config``:governance 锁定优先 → ``task_assignment.summarization``
指派的 agent slug(Settings → AI 的 Summarization 下拉,后端过去从不读) →
``nous:<model>`` 直选 → agent 行的 model / fallback_models。

这些用例是可证伪的:改 agent 行的 model,解析结果必须跟着变;把用户的
provider `selected_model` 设成 embedding 模型,解析结果必须不受影响。
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.providers import ai_provider_helpers as helpers

pytestmark = pytest.mark.asyncio

TASK_KEY = "summarization"
DEFAULT_SLUG = helpers.DEFAULT_SUMMARIZE_AGENT_SLUG

# 生产 ai_agents.summarize.model 的真值(探针绿的那个模型)。
PROD_AGENT_MODEL = "doubao-seed-2-0-lite-260428"
# 用户事故的真值形态:doubao 卡的 selected_model 被选成了 embedding 模型。
EMBEDDING_MODEL = "doubao-embedding-vision-251215"


def _allowed() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=True)


def _locked(model: str = "mediahub-summary", api_key: str = "admin-key"):
    return AIModuleGovernance(
        allowed=False, base_url="https://admin/v1", model=model, api_key=api_key
    )


def _repo(agent: Optional[dict]) -> MagicMock:
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=agent)
    return repo


async def _resolve(
    *,
    agent: Optional[dict],
    ai_settings: dict,
    governance: AIModuleGovernance | None = None,
    mediahub: Any = None,
    repo: MagicMock | None = None,
):
    """Run the real resolver with the DB seams faked (no DB)."""
    repo = repo if repo is not None else _repo(agent)
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=governance or _allowed()),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=repo,
        ),
        patch.object(
            helpers, "get_ai_settings", new=AsyncMock(return_value=ai_settings)
        ),
        patch.object(
            helpers, "resolve_mediahub_model", new=AsyncMock(return_value=mediahub)
        ),
        patch.object(
            helpers, "resolve_platform_model", new=AsyncMock(return_value=None)
        ),
    ):
        return await helpers.resolve_task_ai_config("user-1", TASK_KEY, DEFAULT_SLUG)


# ── agent 行的 model 就是解析结果(可证伪:改一个,另一个必须跟着改) ──────


@pytest.mark.parametrize("agent_model", [PROD_AGENT_MODEL, "qwen-max", "deepseek-chat"])
async def test_model_comes_from_the_agent_row(agent_model: str) -> None:
    cfg = await _resolve(
        agent={"slug": DEFAULT_SLUG, "model": agent_model},
        ai_settings={
            "task_assignment": {},
            "ai_providers": {
                "doubao": {"api_key": "k", "enabled": True},
                "qwen": {"api_key": "k", "enabled": True},
                "deepseek": {"api_key": "k", "enabled": True},
            },
        },
    )
    assert cfg.model == agent_model
    assert cfg.provider_config["model"] == agent_model
    assert cfg.agent_slug == DEFAULT_SLUG


async def test_default_slug_is_read_when_no_assignment() -> None:
    repo = _repo({"slug": DEFAULT_SLUG, "model": PROD_AGENT_MODEL})
    await _resolve(
        agent=None,
        repo=repo,
        ai_settings={"task_assignment": {}, "ai_providers": {}},
    )
    repo.get_by_slug.assert_awaited_once_with("summarize")


# ── 用户事故的正向用例 ────────────────────────────────────────────────


async def test_provider_selected_model_no_longer_steers_summarization() -> None:
    """用户事故形态:doubao 卡 selected_model = embedding 模型且 enabled。

    旧解析器扫 provider 优先级,doubao 第一个命中 → 摘要拿 embedding id 打
    /chat/completions,必败。收口后 provider 卡上的 selected_model 与摘要无关。
    """
    cfg = await _resolve(
        agent={"slug": DEFAULT_SLUG, "model": PROD_AGENT_MODEL},
        ai_settings={
            "task_assignment": {},
            "ai_providers": {
                "doubao": {
                    "api_key": "user-doubao-key",
                    "enabled": True,
                    "selected_model": EMBEDDING_MODEL,
                }
            },
        },
    )
    assert cfg.model == PROD_AGENT_MODEL
    assert EMBEDDING_MODEL not in (cfg.model, cfg.provider_config.get("model"))
    # agent 的模型是 doubao 前缀,所以仍然配到用户那张 doubao 卡的 key。
    assert cfg.provider_key == "doubao"
    assert cfg.provider_config["api_key"] == "user-doubao-key"
    assert cfg.origin == "byok"


async def test_openai_summary_model_field_is_no_longer_consulted() -> None:
    """上一轮为旧扫描加的 openai ``summary_model`` / whisper 守卫连同扫描一起
    删除:openai 卡的三个下拉都不再参与摘要模型解析。"""
    cfg = await _resolve(
        agent={"slug": DEFAULT_SLUG, "model": PROD_AGENT_MODEL},
        ai_settings={
            "task_assignment": {},
            "ai_providers": {
                "openai": {
                    "api_key": "k",
                    "enabled": True,
                    "selected_model": "whisper-1",
                    "summary_model": "gpt-4o-mini",
                }
            },
            "default_summary_model": "gpt-3.5-turbo",
        },
    )
    assert cfg.model == PROD_AGENT_MODEL
    assert not hasattr(helpers, "_summary_model_from_provider")
    assert not hasattr(helpers, "resolve_summarization_config")


# ── task_assignment.summarization(UI 下拉)现在真的生效 ────────────────


async def test_assigned_agent_slug_is_honored() -> None:
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(
        return_value={"slug": "my-summarizer", "model": "qwen-max"}
    )
    cfg = await _resolve(
        agent=None,
        repo=repo,
        ai_settings={
            "task_assignment": {"summarization": "my-summarizer"},
            "ai_providers": {"qwen": {"api_key": "k", "enabled": True}},
        },
    )
    repo.get_by_slug.assert_awaited_once_with("my-summarizer")
    assert cfg.agent_slug == "my-summarizer"
    assert cfg.model == "qwen-max"


async def test_nous_direct_pick_resolves_platform_config() -> None:
    cfg = await _resolve(
        agent={"slug": DEFAULT_SLUG, "model": PROD_AGENT_MODEL},
        ai_settings={
            "task_assignment": {"summarization": "nous:mediahub-doubao-seed-2-0-lite"},
            "ai_providers": {},
        },
        mediahub=(
            "doubao",
            {"api_key": "platform-key", "base_url": "https://ark", "model": "actual"},
            "actual",
        ),
    )
    assert cfg.origin == "platform"
    assert cfg.model == "actual"
    assert cfg.provider_config["api_key"] == "platform-key"
    # 平台直选不读 agent 行 → 空 fallback 池(resolve_task_ai_config 的契约)。
    assert cfg.fallback_models == ()


# ── governance 锁定仍然最优先 ─────────────────────────────────────────


async def test_governance_lock_short_circuits_before_the_agent_row() -> None:
    repo = _repo({"slug": DEFAULT_SLUG, "model": PROD_AGENT_MODEL})
    cfg = await _resolve(
        agent=None,
        repo=repo,
        governance=_locked(model="admin-model", api_key="admin-key"),
        ai_settings={"task_assignment": {}, "ai_providers": {}},
    )
    assert cfg.origin == "governance"
    assert cfg.model == "admin-model"
    assert cfg.provider_config["api_key"] == "admin-key"
    # 锁定分支不该读 agent 行 —— 用户/agent 配置一律被绕过。
    repo.get_by_slug.assert_not_awaited()


async def test_governance_locked_without_admin_key_fails_closed() -> None:
    with pytest.raises(RuntimeError):
        await _resolve(
            agent={"slug": DEFAULT_SLUG, "model": PROD_AGENT_MODEL},
            governance=_locked(model="admin-model", api_key=""),
            ai_settings={"task_assignment": {}, "ai_providers": {}},
        )


# ── fallback 池只从解析所依据的那一行 agent 带出 ──────────────────────


async def test_fallback_models_come_from_the_resolved_agent_row() -> None:
    cfg = await _resolve(
        agent={
            "slug": DEFAULT_SLUG,
            "model": PROD_AGENT_MODEL,
            "fallback_models": ["qwen-max", "deepseek-chat"],
        },
        ai_settings={
            "task_assignment": {},
            "ai_providers": {"doubao": {"api_key": "k", "enabled": True}},
        },
    )
    assert cfg.fallback_models == ("qwen-max", "deepseek-chat")


async def test_agent_without_model_resolves_to_empty_no_silent_default() -> None:
    """无 fallback 契约:解析器不替 agent 编一个模型出来(工作流据此 raise)。"""
    cfg = await _resolve(
        agent={"slug": DEFAULT_SLUG, "model": ""},
        ai_settings={
            "task_assignment": {},
            "ai_providers": {"doubao": {"api_key": "k", "enabled": True}},
        },
    )
    assert cfg.model == ""
    assert cfg.provider_config == {}
