"""后台任务跟随用户的模型定制,提示词一律出厂(口径 A,用户拍板 2026-08-21)。

五个后台模块(summarization / caption / visual_analysis / classification /
translation,即 ``resolve_task_ai_config`` 的调用方)现在读用户在 AI Library
里对系统预设 agent 做的 ``agent_overrides.model``;``identity_md`` /
``soul_md`` / ``agent_md`` 以及 ``temperature`` / ``max_tokens`` /
``fallback_models`` **不读** —— 这些模块按固定契约解析 agent 的输出(严格
JSON 等),用户改提示词只会打断解析器,不是换个人格。

可证伪点(每一条都对应下面一组用例):
  1. 正向:用户设了 override → 解析出的 model 就是 override 的值。
  2. 守恒:没设 → 出厂值,且**一次 DB 都不查**(非系统预设直接短路)。
  3. 反向(钉住口径 A):override 行里带了 identity_md / temperature /
     fallback_models,只有 model 越过边界。
  4. 单源:override 在 provider 推导**之前**生效,所以 key/base_url 与
     model 永远同源;composer 那边靠 ``background_composer_input`` 显式接
     解析结果,不自己查 —— 把任一半改回"各查各的"都会红。
  5. 优先级:governance 锁定 > ``nous:`` 直选 > override > 出厂。
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.repositories.agent_repository import AGENT_OVERRIDE_FIELDS
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.prompts.prompt_composer import (
    ComposerInput,
    background_composer_input,
)
from app.services.ai.providers import ai_provider_helpers as helpers

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_AGENT_ID = "22222222-2222-2222-2222-222222222222"

# 出厂 agent 行上的模型 vs 用户在 AI Library 里改成的模型。
FACTORY_MODEL = "doubao-seed-2-0-lite-260428"
USER_MODEL = "deepseek-chat"

# 五个后台模块的 (task_key, 默认 slug) —— 与各 workflow 真正传给
# resolve_task_ai_config 的字符串一致(漂移了这张表就该红)。
BG_MODULES = [
    ("summarization", helpers.DEFAULT_SUMMARIZE_AGENT_SLUG),
    ("caption", helpers.DEFAULT_CAPTION_AGENT_SLUG),
    ("visual_analysis", helpers.DEFAULT_ANALYZE_AGENT_SLUG),
    ("classification", helpers.DEFAULT_CLASSIFY_AGENT_SLUG),
    ("translation", helpers.DEFAULT_TRANSLATE_AGENT_SLUG),
]


def _agent(
    slug: str,
    *,
    model: str = FACTORY_MODEL,
    is_system_preset: bool = True,
    fallback_models: Optional[list[str]] = None,
) -> dict:
    return {
        "id": _AGENT_ID,
        "slug": slug,
        "model": model,
        "is_system_preset": is_system_preset,
        "identity_md": "FACTORY IDENTITY",
        "temperature": 0.2,
        "max_tokens": 1024,
        "fallback_models": fallback_models or ["factory-fallback"],
    }


def _repo(agent: Optional[dict], override_row: Optional[dict] = None) -> MagicMock:
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=agent)
    repo.get_override = AsyncMock(return_value=override_row)
    return repo


async def _resolve(
    *,
    task_key: str,
    default_slug: str,
    repo: MagicMock,
    ai_settings: Optional[dict] = None,
    governance: Optional[AIModuleGovernance] = None,
    mediahub: Any = None,
    user_id: Optional[str] = _USER,
):
    """跑真解析器,只假掉它自己拥有的 DB 接缝(不假任何被测逻辑)。"""
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=governance or AIModuleGovernance(allowed=True)),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=repo,
        ),
        patch.object(
            helpers,
            "get_ai_settings",
            new=AsyncMock(
                return_value=(
                    ai_settings
                    if ai_settings is not None
                    else {"task_assignment": {}, "ai_providers": {}}
                )
            ),
        ),
        patch.object(
            helpers, "resolve_mediahub_model", new=AsyncMock(return_value=mediahub)
        ),
        patch.object(
            helpers, "resolve_platform_model", new=AsyncMock(return_value=None)
        ),
    ):
        return await helpers.resolve_task_ai_config(user_id, task_key, default_slug)


# ── 1. 正向:五个模块逐一跟随 agent_overrides.model ────────────────────


@pytest.mark.parametrize("task_key,default_slug", BG_MODULES)
async def test_background_task_follows_the_users_model_override(
    task_key: str, default_slug: str
) -> None:
    repo = _repo(_agent(default_slug), override_row={"model": USER_MODEL})

    cfg = await _resolve(task_key=task_key, default_slug=default_slug, repo=repo)

    assert cfg.model == USER_MODEL
    # 单源:provider_config 里那份也必须是 override 的值 —— 它和 api_key /
    # base_url 一起被下游 build_fallback_llm 拨号。
    assert cfg.provider_config["model"] == USER_MODEL
    repo.get_override.assert_awaited_once_with(UUID(_AGENT_ID), user_id=UUID(_USER))


# ── 2. 守恒:没设 override 就是出厂值 ──────────────────────────────────


@pytest.mark.parametrize("task_key,default_slug", BG_MODULES)
async def test_no_override_row_keeps_the_factory_model(
    task_key: str, default_slug: str
) -> None:
    repo = _repo(_agent(default_slug), override_row=None)

    cfg = await _resolve(task_key=task_key, default_slug=default_slug, repo=repo)

    assert cfg.model == FACTORY_MODEL
    assert cfg.provider_config["model"] == FACTORY_MODEL


async def test_override_row_without_a_model_column_keeps_the_factory_model() -> None:
    """行存在但只定制了提示词 —— 后台仍拿出厂模型(不是 None、不是空串)。"""
    repo = _repo(
        _agent("summarize"), override_row={"identity_md": "MINE", "model": None}
    )

    cfg = await _resolve(task_key="summarization", default_slug="summarize", repo=repo)

    assert cfg.model == FACTORY_MODEL


async def test_non_system_preset_agent_never_reads_the_override_table() -> None:
    """自建 agent 没有 override 行(mig 341 只给系统预设),不该白查一次 DB。"""
    repo = _repo(_agent("my-own", is_system_preset=False))

    cfg = await _resolve(task_key="summarization", default_slug="my-own", repo=repo)

    assert cfg.model == FACTORY_MODEL
    repo.get_override.assert_not_awaited()


async def test_anonymous_run_never_reads_the_override_table() -> None:
    repo = _repo(_agent("summarize"))

    cfg = await _resolve(
        task_key="summarization", default_slug="summarize", repo=repo, user_id=None
    )

    assert cfg.model == FACTORY_MODEL
    repo.get_override.assert_not_awaited()


async def test_override_lookup_failure_degrades_to_the_factory_model() -> None:
    """查 override 炸了不该让整个后台任务失败 —— 与 _apply_overrides 同款 fail-soft。"""
    repo = _repo(_agent("summarize"))
    repo.get_override = AsyncMock(side_effect=RuntimeError("db down"))

    cfg = await _resolve(task_key="summarization", default_slug="summarize", repo=repo)

    assert cfg.model == FACTORY_MODEL


# ── 3. 反向:只有 model 越过边界,提示词与其余参数留在出厂 ──────────────


async def test_only_the_model_column_crosses_into_a_background_run() -> None:
    """override 行把七个字段全定制了,后台只认 model 那一个。"""
    full_override = {
        "identity_md": "USER IDENTITY",
        "soul_md": "USER SOUL",
        "agent_md": "USER AGENT",
        "model": USER_MODEL,
        "temperature": 1.9,
        "max_tokens": 99,
        "fallback_models": ["user-fallback"],
    }
    # 这一行就是"七个字段"的地面真值 —— 仓库层加字段而这里没跟,直接红。
    assert set(full_override) == set(AGENT_OVERRIDE_FIELDS)

    repo = _repo(_agent("summarize"), override_row=full_override)
    cfg = await _resolve(task_key="summarization", default_slug="summarize", repo=repo)

    assert cfg.model == USER_MODEL
    # fallback 池仍是出厂 agent 行的(口径 A:除 model 外都不跟随)。
    assert cfg.fallback_models == ("factory-fallback",)
    # 提示词那一半不在这里断言 —— ``ResolvedAIConfig`` 是 dataclass,写
    # ``assert not hasattr(cfg, "identity_md")`` 之类是恒真的,看着像守卫却
    # 什么都不守。真钉子是 test_background_run_composes_the_factory_prompt:
    # 它拿真 composer + 真的存在 override 行的仓库,断言渲染出来的
    # system_message 仍是出厂文本。


class _OverrideAwareAgentRepo:
    """A repo that really behaves like ``AgentRepository`` w.r.t. overrides.

    ``get_by_slug()`` returns the pristine system row; the SAME call with
    ``override_user_id=`` returns the user-merged row (that is exactly
    ``_apply_overrides``' contract). So whether the factory prompt or the
    user's prompt comes back is decided by *what the caller passes* — which is
    the thing under test. A fake that ignored the kwargs would make this用例
    pass no matter what the service did.
    """

    def __init__(self) -> None:
        self.seen_override_kwargs: list[dict] = []

    async def get_by_slug(self, slug: str, **kwargs):
        self.seen_override_kwargs.append(kwargs)
        agent = {
            "id": _AGENT_ID,
            "slug": slug,
            "model": FACTORY_MODEL,
            "is_system_preset": True,
            "identity_md": "FACTORY IDENTITY TEXT",
            "soul_md": "FACTORY SOUL TEXT",
            "agent_md": "FACTORY AGENT TEXT",
            "temperature": 0.2,
            "max_tokens": 1024,
        }
        if kwargs.get("override_user_id") or kwargs.get("override_team_id"):
            agent = {
                **agent,
                "identity_md": "USER IDENTITY TEXT",
                "soul_md": "USER SOUL TEXT",
                "agent_md": "USER AGENT TEXT",
                "model": USER_MODEL,
                "temperature": 1.9,
                "max_tokens": 99,
            }
        return agent

    async def get_skill_ids(self, _agent_id):
        return []

    async def list_persistent(self):
        return []


class _EmptySkillRepo:
    async def list_by_ids(self, _ids):
        return []


async def test_background_run_composes_the_factory_prompt() -> None:
    """端到端钉住口径 A 的提示词那一半:override 行里改了三个 *_md,后台跑出来
    的 system_message 仍是**出厂**文本。

    用的是真 ``PromptComposer``(不是 mock),仓库按真实 override 语义作答 ——
    带 override 身份问它就给用户版,不带就给出厂版。所以这条用例真正测的是
    ``background_composer_input`` 到底传没传那两个 id。把它接上去(=让提示词
    也吃 override)立刻转红。
    """
    from app.services.ai.prompts.prompt_composer import PromptComposer

    repo = _OverrideAwareAgentRepo()
    composer = PromptComposer(repo, _EmptySkillRepo())

    composed = await composer.compose(
        background_composer_input(
            agent_slug="summarize",
            request_instructions="do it",
            resolved_model=USER_MODEL,
        )
    )

    # 提示词:出厂,三段都不许出现用户版本。
    assert "FACTORY IDENTITY TEXT" in composed.system_message
    assert "FACTORY SOUL TEXT" in composed.system_message
    assert "FACTORY AGENT TEXT" in composed.system_message
    assert "USER" not in composed.system_message
    # 其余出厂参数同样不跟随。
    assert composed.temperature == 0.2
    assert composed.max_tokens == 1024
    # 而模型跟随 —— 口径 A 的另一半,由 resolver 解析后显式传进来。
    assert composed.model == USER_MODEL
    # 结构性证据:后台这一跳压根没给仓库任何 override 身份。
    assert repo.seen_override_kwargs == [{}]


async def test_the_same_repo_would_return_the_user_prompt_if_asked() -> None:
    """上一条的正向对照 —— 证明那个仓库**确实**能返回用户版提示词。

    少了这条,上一条就可能是在一个永远只会吐出厂文本的假体上通过的(那样它
    对任何实现都绿,等于没测)。
    """
    from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer

    composer = PromptComposer(_OverrideAwareAgentRepo(), _EmptySkillRepo())

    composed = await composer.compose(
        ComposerInput(
            agent_slug="summarize",
            request_instructions="do it",
            override_user_id=_USER,
        )
    )

    assert "USER IDENTITY TEXT" in composed.system_message
    assert "FACTORY IDENTITY TEXT" not in composed.system_message


async def test_background_composer_input_never_carries_override_identity() -> None:
    """口径 A 的另一半:提示词一律出厂 → 不给 composer 任何 override 身份。

    突变把 ``override_user_id`` 接上去(让提示词也吃 override),这条即红。
    """
    inp = background_composer_input(
        agent_slug="summarize",
        request_instructions="do it",
        resolved_model=USER_MODEL,
    )

    assert isinstance(inp, ComposerInput)
    assert inp.override_user_id is None
    assert inp.override_team_id is None
    assert inp.model_override == USER_MODEL


async def test_background_composer_input_empty_model_leaves_the_row_value() -> None:
    """裸 / smoke 路径没解析出模型时,不能拿空串把 agent 行的模型顶掉。"""
    inp = background_composer_input(
        agent_slug="summarize", request_instructions="x", resolved_model=""
    )
    assert inp.model_override is None


# ── 4. 单源:override 在 provider 推导之前生效 ─────────────────────────


async def test_override_is_applied_before_the_platform_catalog_lookup() -> None:
    """平台目录查的必须是 override 后的模型名,否则就是"用 A 的 key 打 B 的 id"。"""
    repo = _repo(_agent("summarize"), override_row={"model": USER_MODEL})
    seen: list[str] = []

    async def _catalog(model_name: str, module: str):
        seen.append(model_name)
        return None

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=repo,
        ),
        patch.object(
            helpers,
            "get_ai_settings",
            new=AsyncMock(return_value={"task_assignment": {}, "ai_providers": {}}),
        ),
        patch.object(helpers, "resolve_mediahub_model", new=_catalog),
        patch.object(
            helpers, "resolve_platform_model", new=AsyncMock(return_value=None)
        ),
    ):
        cfg = await helpers.resolve_task_ai_config(_USER, "summarization", "summarize")

    assert seen == [USER_MODEL], "目录查询看到的是出厂模型 → override 加晚了"
    assert cfg.model == USER_MODEL


async def test_override_drives_provider_key_and_credentials() -> None:
    """override 换了 provider 前缀,拿到的就必须是那个 provider 的 BYOK 卡。"""
    repo = _repo(
        _agent("summarize", model="qwen-max"), override_row={"model": USER_MODEL}
    )

    cfg = await _resolve(
        task_key="summarization",
        default_slug="summarize",
        repo=repo,
        ai_settings={
            "task_assignment": {},
            "ai_providers": {
                "qwen": {"api_key": "qwen-key"},
                "deepseek": {"api_key": "deepseek-key"},
            },
        },
    )

    assert cfg.model == USER_MODEL
    assert cfg.provider_key == "deepseek"
    assert cfg.provider_config["api_key"] == "deepseek-key"


# ── 5. 优先级:governance > nous: 直选 > override > 出厂 ───────────────


async def test_governance_lock_short_circuits_before_any_override() -> None:
    repo = _repo(_agent("summarize"), override_row={"model": USER_MODEL})

    cfg = await _resolve(
        task_key="summarization",
        default_slug="summarize",
        repo=repo,
        governance=AIModuleGovernance(
            allowed=False,
            base_url="https://admin/v1",
            model="admin-model",
            api_key="admin-key",
        ),
    )

    assert cfg.origin == "governance"
    assert cfg.model != USER_MODEL
    repo.get_override.assert_not_awaited()
    repo.get_by_slug.assert_not_awaited()


async def test_nous_direct_pick_wins_over_the_stored_override() -> None:
    """``nous:<model>`` 是用户在同一个下拉里刚做的显式选择,而 override 是对
    另一个对象(agent 行)的存量定制 —— 更具体、更晚的那个赢,且直选分支压根
    不读 agent 行,所以也读不到它的 override。"""
    repo = _repo(_agent("summarize"), override_row={"model": USER_MODEL})

    cfg = await _resolve(
        task_key="summarization",
        default_slug="summarize",
        repo=repo,
        ai_settings={
            "task_assignment": {"summarization": "nous:mediahub-pick"},
            "ai_providers": {},
        },
        mediahub=("doubao", {"api_key": "plat", "model": "picked"}, "picked"),
    )

    assert cfg.origin == "platform"
    assert cfg.model == "picked"
    repo.get_override.assert_not_awaited()


async def test_override_follows_the_agent_actually_resolved() -> None:
    """指派的 agent 不存在 → 回落默认 agent;override 要挂在真正用上的那行。"""
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(side_effect=[None, _agent("summarize")])
    repo.get_override = AsyncMock(return_value={"model": USER_MODEL})

    cfg = await _resolve(
        task_key="summarization",
        default_slug="summarize",
        repo=repo,
        ai_settings={
            "task_assignment": {"summarization": "gone-agent"},
            "ai_providers": {},
        },
    )

    assert cfg.agent_slug == "summarize"
    assert cfg.model == USER_MODEL
    repo.get_override.assert_awaited_once_with(UUID(_AGENT_ID), user_id=UUID(_USER))


# ── 6. 全链:解析器的 model 一路走到 composer,composer 不自己查 ────────


def _composed(model: str) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug="x",
        model=model,
        temperature=0.0,
        max_tokens=256,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


class _StopAfterCompose(RuntimeError):
    """compose 之后立刻收工 —— 这组用例只关心送进 composer 的那份输入。"""


def _capture_composer() -> tuple[MagicMock, dict]:
    captured: dict = {}

    async def _compose(inp):
        captured["input"] = inp
        return _composed(inp.model_override or "ROW-MODEL")

    composer = MagicMock()
    composer.compose = AsyncMock(side_effect=_compose)
    return composer, captured


async def _drive(service_call, module: str, composer: MagicMock) -> None:
    """跑到 build_fallback_llm 就停 —— ComposerInput 那时已经构造完了。"""
    with (
        patch(f"{module}.PromptComposer", return_value=composer),
        patch(f"{module}.get_agent_repository", return_value=MagicMock()),
        patch(f"{module}.get_skill_repository", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(side_effect=_StopAfterCompose("stop")),
        ),
    ):
        with pytest.raises(_StopAfterCompose):
            await service_call()


async def test_summarize_threads_the_resolved_model_into_the_composer() -> None:
    from app.services.ai.summarize.summarize_service import SummarizeService

    svc = SummarizeService(
        provider_key="deepseek",
        provider_config={"model": USER_MODEL, "api_key": "k"},
    )
    composer, captured = _capture_composer()

    await _drive(
        lambda: svc.summarize(transcript="hello world", user_id=None),
        "app.services.ai.summarize.summarize_service",
        composer,
    )

    inp = captured["input"]
    assert inp.model_override == USER_MODEL
    assert inp.override_user_id is None and inp.override_team_id is None


async def test_caption_threads_the_resolved_model_into_the_composer(tmp_path) -> None:
    from app.services.ai.caption.caption_service import CaptionService

    svc = CaptionService(
        provider_key="deepseek",
        provider_config={"model": USER_MODEL, "api_key": "k"},
    )
    composer, captured = _capture_composer()
    img = tmp_path / "x.jpg"
    img.write_bytes(b"jpeg")

    with patch(
        "app.services.ai.caption.caption_service._encode_image_sync",
        return_value="data:image/jpeg;base64,ZmFrZQ==",
    ):
        await _drive(
            lambda: svc.caption(file_path=str(img), user_id=None, resource_id="r1"),
            "app.services.ai.caption.caption_service",
            composer,
        )

    inp = captured["input"]
    assert inp.model_override == USER_MODEL
    assert inp.override_user_id is None and inp.override_team_id is None


async def test_classify_threads_the_resolved_model_into_the_composer(tmp_path) -> None:
    from app.services.ai.classify.classify_service import ClassifyService

    svc = ClassifyService(
        provider_key="deepseek",
        provider_config={"model": USER_MODEL, "api_key": "k"},
    )
    composer, captured = _capture_composer()
    img = tmp_path / "x.jpg"
    img.write_bytes(b"jpeg")

    with patch(
        "app.services.ai.classify.classify_service._encode_image_sync",
        return_value="data:image/jpeg;base64,ZmFrZQ==",
    ):
        await _drive(
            lambda: svc.classify(file_path=str(img), user_id=None, resource_id="r1"),
            "app.services.ai.classify.classify_service",
            composer,
        )

    assert captured["input"].model_override == USER_MODEL


async def test_translate_threads_the_resolved_model_into_the_composer() -> None:
    from app.services.ai.translate.translate_service import TranslateService

    svc = TranslateService(
        provider_key="deepseek",
        provider_config={"model": USER_MODEL, "api_key": "k"},
    )
    composer, captured = _capture_composer()

    await _drive(
        lambda: svc.translate(text="hello", target_lang="zh", user_id=None),
        "app.services.ai.translate.translate_service",
        composer,
    )

    assert captured["input"].model_override == USER_MODEL


async def test_visual_analysis_threads_the_resolved_model_into_the_composer() -> None:
    from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

    svc = VisualAnalysisService(
        provider_key="deepseek",
        provider_config={"model": USER_MODEL, "api_key": "k"},
    )
    composer, captured = _capture_composer()

    await _drive(
        lambda: svc._run_multimodal(
            instruction="look",
            image_content_blocks=[],
            input_summary="s",
            metadata={},
            user_id=None,
            trigger="test",
        ),
        "app.services.ai.visual.visual_analysis_service",
        composer,
    )

    assert captured["input"].model_override == USER_MODEL


async def test_every_background_service_builds_input_through_the_shared_helper() -> (
    None
):
    """五个服务必须走同一个构造器 —— 谁改回裸 ``ComposerInput(...)``(等于
    "composer 侧自己查"的入口重新打开),这条即红。"""
    import inspect

    from app.services.ai.caption import caption_service
    from app.services.ai.classify import classify_service
    from app.services.ai.summarize import summarize_service
    from app.services.ai.translate import translate_service
    from app.services.ai.visual import visual_analysis_service

    for mod in (
        summarize_service,
        caption_service,
        classify_service,
        translate_service,
        visual_analysis_service,
    ):
        src = inspect.getsource(mod)
        assert "background_composer_input(" in src, mod.__name__
        assert "ComposerInput(" not in src, (
            f"{mod.__name__} 直接构造了 ComposerInput —— 后台模块必须经由 "
            "background_composer_input,否则 model_override / 提示词出厂 "
            "两条口径会各自漂移"
        )
        assert "resolved_model=self._resolved_model" in src, mod.__name__


async def test_resolver_reads_only_the_model_column_of_the_override_row() -> None:
    """解析器只碰 override 行的 ``model``。突变它去读别的字段(例如把
    identity_md 也带出去)会让这条断言失效。"""
    import inspect

    src = inspect.getsource(helpers.resolve_agent_model_override)
    for field in AGENT_OVERRIDE_FIELDS:
        if field == "model":
            continue
        # 两种引号都挡 —— 只挡双引号的话,写成 row.get('identity_md') 就绕过去了。
        for quote in ('"', "'"):
            assert (
                f"{quote}{field}{quote}" not in src
            ), f"后台解析器读了 {field} —— 违反口径 A"


# ── 7. 服务侧再断言一次:解析器的 model 上线,composer 说什么都不算 ──────


def _stubborn_composer() -> MagicMock:
    """一个"顽固"的 composer —— 无视 model_override,永远吐 agent 行的值。

    真 composer 不会这样(它认 ``model_override``),这里刻意让它不认,是为了
    证明"解析出的模型上线"这条保证**不依赖协作者的善意**:哪天 composer 被
    改坏 / 被替换,服务侧仍然拨对号码。这就是 #622/#623 的守卫。
    """
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed("ROW-MODEL"))
    return composer


async def _primary_model_dialed(service_call, module: str) -> str:
    captured: dict = {}

    async def _build(*, primary_model, **_kw):
        captured["primary_model"] = primary_model
        raise _StopAfterCompose("stop")

    with (
        patch(f"{module}.PromptComposer", return_value=_stubborn_composer()),
        patch(f"{module}.get_agent_repository", return_value=MagicMock()),
        patch(f"{module}.get_skill_repository", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=_build,
        ),
    ):
        with pytest.raises(_StopAfterCompose):
            await service_call()
    return captured["primary_model"]


async def test_summarize_dials_the_resolved_model_not_the_composers() -> None:
    from app.services.ai.summarize.summarize_service import SummarizeService

    svc = SummarizeService(
        provider_key="deepseek", provider_config={"model": USER_MODEL, "api_key": "k"}
    )
    dialed = await _primary_model_dialed(
        lambda: svc.summarize(transcript="hello", user_id=None),
        "app.services.ai.summarize.summarize_service",
    )
    assert dialed == USER_MODEL


async def test_caption_dials_the_resolved_model_not_the_composers(tmp_path) -> None:
    from app.services.ai.caption.caption_service import CaptionService

    svc = CaptionService(
        provider_key="deepseek", provider_config={"model": USER_MODEL, "api_key": "k"}
    )
    img = tmp_path / "x.jpg"
    img.write_bytes(b"jpeg")
    with patch(
        "app.services.ai.caption.caption_service._encode_image_sync",
        return_value="data:image/jpeg;base64,ZmFrZQ==",
    ):
        dialed = await _primary_model_dialed(
            lambda: svc.caption(file_path=str(img), user_id=None, resource_id="r1"),
            "app.services.ai.caption.caption_service",
        )
    assert dialed == USER_MODEL


async def test_classify_dials_the_resolved_model_not_the_composers(tmp_path) -> None:
    from app.services.ai.classify.classify_service import ClassifyService

    svc = ClassifyService(
        provider_key="deepseek", provider_config={"model": USER_MODEL, "api_key": "k"}
    )
    img = tmp_path / "x.jpg"
    img.write_bytes(b"jpeg")
    with patch(
        "app.services.ai.classify.classify_service._encode_image_sync",
        return_value="data:image/jpeg;base64,ZmFrZQ==",
    ):
        dialed = await _primary_model_dialed(
            lambda: svc.classify(file_path=str(img), user_id=None, resource_id="r1"),
            "app.services.ai.classify.classify_service",
        )
    assert dialed == USER_MODEL


async def test_translate_dials_the_resolved_model_not_the_composers() -> None:
    from app.services.ai.translate.translate_service import TranslateService

    svc = TranslateService(
        provider_key="deepseek", provider_config={"model": USER_MODEL, "api_key": "k"}
    )
    dialed = await _primary_model_dialed(
        lambda: svc.translate(text="hello", target_lang="zh", user_id=None),
        "app.services.ai.translate.translate_service",
    )
    assert dialed == USER_MODEL


async def test_visual_analysis_dials_the_resolved_model_not_the_composers() -> None:
    from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

    svc = VisualAnalysisService(
        provider_key="deepseek", provider_config={"model": USER_MODEL, "api_key": "k"}
    )
    dialed = await _primary_model_dialed(
        lambda: svc._run_multimodal(
            instruction="look",
            image_content_blocks=[],
            input_summary="s",
            metadata={},
            user_id=None,
            trigger="test",
        ),
        "app.services.ai.visual.visual_analysis_service",
    )
    assert dialed == USER_MODEL
