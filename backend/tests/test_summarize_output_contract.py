"""摘要的**产出契约**守卫 + agent_slug 端到端接线(2026-08-20 审查 F1/F2)。

F1 — 空产出必须失败,不许 completed 空内容
    模型来源收口到 agent 配置之后,``task_assignment.summarization`` 可以指向
    任何合法 agent。生产存量值就是 ``test-analyze``(一个真实存在、合法、但
    输出契约完全不同的**视觉分析** agent):它返回
    ``{"category":…, "objects":…, "people":…}`` —— 合法 JSON、``_parse_json``
    解析成功、``summary`` 字段不存在 → 收口前的代码会把空摘要写库并把任务标成
    completed。**绿着成功、内容为空**比现在红着失败更糟:用户不会再报障。
    与"DBOS 失败必须 raise"同族,产出不满足契约就是失败。

F2 — ``agent_slug`` 的两跳接线各有一条守卫
    workflow → ``run_summarize_agent`` → ``SummarizeService``。审查复做证明这条
    链此前**零覆盖**(删掉 workflow 里那行,后端全量 9360 仍全绿),而它正是
    #622/#623「解析 model 的 agent 与复合 prompt 的 agent 必须同一个」要防的
    缺陷 —— 也正是 F1 那条故障链得以成立的环节。
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.summarize.summarize_service import (
    EmptySummaryError,
    SummarizeService,
)

pytestmark = pytest.mark.asyncio

_MOD = "app.services.ai.summarize.summarize_service"

# 生产存量指派 test-analyze 的真实输出形状(视觉分析 agent 的 JSON 契约)。
_VISUAL_ANALYZE_OUTPUT = (
    '{"category":"Food","objects":["bowl","chopsticks"],'
    '"people":[{"gender":"female","clothing":"apron","action":"cooking"}]}'
)
_GOOD_OUTPUT = '{"summary":"A recap.","key_points":["a"],"topics":["t"]}'


def _composed(slug: str = "summarize") -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug=slug,
        model="doubao-seed-2-0-lite-260428",
        temperature=0.0,
        max_tokens=512,
        system_message="x",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


def _patched_service(content: str, *, slug: str = "summarize"):
    """Run the real ``SummarizeService.summarize`` with composer/runner stubbed
    so only the output-contract handling is under test."""
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed(slug))
    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": content, "raw": {}})

    async def _build(*, primary_model, **_kw):
        return MagicMock()

    return (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm", side_effect=_build
        ),
    )


async def _summarize(content: str, *, slug: str = "summarize"):
    svc = SummarizeService(
        provider_key="doubao",
        provider_config={"model": "doubao-seed-2-0-lite-260428", "api_key": "k"},
        agent_slug=slug,
    )
    composer, runner, skills, build = _patched_service(content, slug=slug)
    with composer, runner, skills, build:
        # user_id=None → bare path;下面另有一条用例覆盖带 RunRecorder 的真实路径。
        return await svc.summarize(
            transcript="hello world", user_id=None, parsed_media_id=1, title="T"
        )


# ── F1:事故形态 —— 指派了视觉分析 agent ────────────────────────────────


async def test_visual_analyze_output_fails_instead_of_saving_an_empty_summary():
    with pytest.raises(EmptySummaryError) as err:
        await _summarize(_VISUAL_ANALYZE_OUTPUT, slug="test-analyze")

    msg = str(err.value)
    # 原因可读:是谁、错在哪、怎么修。
    assert "test-analyze" in msg
    assert "produced no summary field" in msg
    assert "prompt contract" in msg
    # 带上模型真的返回了哪些键 —— 一眼看出是契约不匹配,不是模型坏了。
    assert "category" in msg and "objects" in msg
    # 只带键名,不带值:转写内容不该漏进错误信息/日志。
    assert "chopsticks" not in msg


async def test_empty_content_fails_too():
    """模型什么都没返回 —— 同一条守卫接住(_parse_json 会给出空 summary)。"""
    with pytest.raises(EmptySummaryError):
        await _summarize("")


async def test_blank_summary_field_fails():
    with pytest.raises(EmptySummaryError):
        await _summarize('{"summary":"   ","key_points":[],"topics":[]}')


async def test_recorder_path_also_raises_so_the_agent_run_is_red():
    """带 user_id 的真实路径:异常必须穿过 RunRecorder 传出去(run 记失败),
    而不是被吞成 None。"""
    svc = SummarizeService(
        provider_key="doubao",
        provider_config={"model": "doubao-seed-2-0-lite-260428", "api_key": "k"},
        agent_slug="test-analyze",
    )
    recorder = MagicMock()
    recorder.set_summaries = MagicMock()
    recorder_cm = MagicMock()
    recorder_cm.__aenter__ = AsyncMock(return_value=recorder)
    recorder_cm.__aexit__ = AsyncMock(return_value=False)

    composer, runner, skills, build = _patched_service(
        _VISUAL_ANALYZE_OUTPUT, slug="test-analyze"
    )
    with (
        composer,
        runner,
        skills,
        build,
        patch(f"{_MOD}.RunRecorder", return_value=recorder_cm),
    ):
        with pytest.raises(EmptySummaryError):
            await svc.summarize(
                transcript="hello world",
                user_id="11111111-1111-1111-1111-111111111111",
                parsed_media_id=1,
                title="T",
            )
    recorder_cm.__aexit__.assert_awaited()


async def test_valid_summary_still_passes():
    result = await _summarize(_GOOD_OUTPUT)
    assert result is not None
    assert result.summary == "A recap."


async def test_summary_without_key_points_is_accepted():
    """只校验 ``summary``:给了正文但没拆要点仍是可用结果,不该判失败。"""
    result = await _summarize('{"summary":"Just prose.","key_points":[],"topics":[]}')
    assert result is not None
    assert result.summary == "Just prose."
    assert result.key_points == []


async def test_non_json_content_is_still_accepted_as_prose():
    """``_parse_json`` 的兜底把裸文本当作 summary —— 有正文就不算空产出。"""
    result = await _summarize("The video is about cooking.")
    assert result is not None
    assert result.summary == "The video is about cooking."


# ── F2:agent_slug 两跳接线 ───────────────────────────────────────────


async def test_workflow_threads_agent_slug_into_the_step():
    """第一跳:``ai_summary_workflow`` → ``run_summarize_agent``。"""
    from app.workflows import ai_summary as m

    inputs = {
        "transcript": "t",
        "title": "T",
        "resource_id": "999",
        "provider_key": "doubao",
        "provider_config": {"model": "doubao-seed-2-0-lite-260428"},
        "agent_slug": "my-summarizer",
        "fallback_models": [],
    }
    run_step = AsyncMock(
        return_value={
            "summary": "s",
            "key_points": [],
            "topics": [],
            "llm_model": "m",
            "llm_provider": "doubao",
        }
    )

    with (
        patch.object(m, "load_summary_inputs", AsyncMock(return_value=inputs)),
        patch.object(m, "run_summarize_agent", run_step),
        patch.object(m, "persist_summary", AsyncMock(return_value={"summary_len": 1})),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=AsyncMock(),
        ),
    ):
        await inspect.unwrap(m.ai_summary_workflow)(parsed_media_id=1, user_id="u-1")

    assert run_step.await_args.kwargs["agent_slug"] == "my-summarizer"


async def test_step_threads_agent_slug_into_the_service():
    """第二跳:``run_summarize_agent`` → ``SummarizeService(agent_slug=…)``。"""
    from app.workflows import ai_summary as m

    svc = MagicMock()
    svc.summarize = AsyncMock(
        return_value=MagicMock(summary="s", key_points=[], topics=[], llm_model="m")
    )
    factory = MagicMock(return_value=svc)

    with patch(f"{_MOD}.SummarizeService", factory):
        await inspect.unwrap(m.run_summarize_agent)(
            transcript="t",
            title="T",
            user_id="u-1",
            parsed_media_id=1,
            provider_key="doubao",
            provider_config={"model": "doubao-seed-2-0-lite-260428"},
            agent_slug="my-summarizer",
        )

    assert factory.call_args.kwargs["agent_slug"] == "my-summarizer"


async def test_step_without_agent_slug_falls_back_to_the_builtin_preset():
    """DBOS 重放:收口前缓存的 step 入参没有这个键 → 内置 ``summarize`` 预设。"""
    from app.workflows import ai_summary as m

    svc = MagicMock()
    svc.summarize = AsyncMock(
        return_value=MagicMock(summary="s", key_points=[], topics=[], llm_model="m")
    )
    factory = MagicMock(return_value=svc)

    with patch(f"{_MOD}.SummarizeService", factory):
        await inspect.unwrap(m.run_summarize_agent)(
            transcript="t",
            title="T",
            user_id="u-1",
            parsed_media_id=1,
            provider_key="doubao",
            provider_config={"model": "doubao-seed-2-0-lite-260428"},
        )

    assert factory.call_args.kwargs["agent_slug"] == ""
    assert SummarizeService(agent_slug="").AGENT_SLUG == "summarize"
