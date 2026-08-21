"""每个 agent 任务的 ``agent_slug`` 都必须从解析器一路走到 Service(F2)。

`resolve_task_ai_config` 解析出的 slug 决定**用谁的提示词**;它的 model 决定
**打哪个模型**。两者必须来自同一行 agent —— 否则就是 #622/#623 那个缺陷:
用 A 的模型 + B 的提示词,产出与调用方的契约对不上(摘要侧的真实后果见
`test_summarize_output_contract.py` 的 F1 用例:视觉分析 agent 让摘要变空)。

2026-08-20 审查复做发现:caption / analyze / classify 的 step→Service 这一跳
**全部零覆盖** —— 把三处 ``agent_slug=`` 同时改成一个假 slug,后端全量
9370 仍然全绿。这个文件就是补那道缺口(摘要自己的两跳在
`test_summarize_output_contract.py`)。
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_RID = "9000000000000000001"
_CFG = {"model": "doubao-seed-1-6-250615", "api_key": "k"}


def _service_returning(result: Any) -> tuple[MagicMock, MagicMock]:
    """(factory, instance) —— factory 记录构造参数,instance 返回给定结果。"""
    instance = MagicMock()
    return MagicMock(return_value=instance), instance


async def test_call_caption_threads_agent_slug_into_caption_service():
    from app.workflows import caption_asset as m

    factory, service = _service_returning(None)
    service.caption = AsyncMock(return_value={"en": "a cat", "zh": "一只猫"})

    with patch("app.services.ai.caption.CaptionService", factory):
        await inspect.unwrap(m.call_caption)(
            abs_path="/tmp/x.png",
            user_id=_USER,
            resource_id=_RID,
            provider_key="doubao",
            provider_config=_CFG,
            agent_slug="my-caption",
        )

    assert factory.call_args.kwargs["agent_slug"] == "my-caption"


async def test_call_caption_empty_slug_falls_back_to_the_preset():
    from app.workflows import caption_asset as m

    factory, service = _service_returning(None)
    service.caption = AsyncMock(return_value={"en": "a cat", "zh": "一只猫"})

    with patch("app.services.ai.caption.CaptionService", factory):
        await inspect.unwrap(m.call_caption)(
            abs_path="/tmp/x.png",
            user_id=_USER,
            resource_id=_RID,
            provider_key="doubao",
            provider_config=_CFG,
            agent_slug="",
        )

    assert factory.call_args.kwargs["agent_slug"] == "caption"


async def _run_classify_step(agent_slug: str) -> MagicMock:
    """构造参数在 classify() 被调用前就已捕获,所以让 classify 抛个哨兵
    提前结束 —— 免得为了走完返回值归一化去伪造 tag 对象。"""
    from app.workflows import classify_asset as m

    factory, service = _service_returning(None)
    service.classify = AsyncMock(side_effect=RuntimeError("stop here"))

    with patch("app.services.ai.classify.ClassifyService", factory):
        with pytest.raises(RuntimeError, match="stop here"):
            await inspect.unwrap(m.call_classify)(
                abs_path="/tmp/x.png",
                user_id=_USER,
                resource_id=_RID,
                provider_key="doubao",
                provider_config=_CFG,
                agent_slug=agent_slug,
            )
    return factory


async def test_call_classify_threads_agent_slug_into_classify_service():

    factory = await _run_classify_step("my-classifier")
    assert factory.call_args.kwargs["agent_slug"] == "my-classifier"


async def test_call_classify_empty_slug_falls_back_to_the_preset():

    factory = await _run_classify_step("")
    assert factory.call_args.kwargs["agent_slug"] == "classify"


class _FakeSession:
    async def execute(self, _stmt: Any) -> Any:
        return MagicMock(rowcount=1)

    async def scalar(self, _stmt: Any) -> int:
        # analyze_l1 先把 media_id 解析成 resources.id 再往下走。
        return 4242


@asynccontextmanager
async def _fake_scope():
    yield _FakeSession()


async def _run_analyze_step(agent_slug: str) -> MagicMock:
    """analyze_l1 的 step 在构造 Service 前先读一次 resources.id、再写一次
    status,所以 read/write scope 都要假掉;拿到构造参数后让 analyze_l1 抛错
    提前结束(re-raise 路径只会再写一次同样被假掉的 status)。"""
    from app.workflows import analyze_l1 as m

    factory, service = _service_returning(None)
    service.analyze_l1 = AsyncMock(side_effect=RuntimeError("stop here"))

    with (
        patch(
            "app.services.ai.visual.visual_analysis_service.VisualAnalysisService",
            factory,
        ),
        patch("app.db.session.write_scope", _fake_scope),
        patch("app.db.session.read_scope", _fake_scope),
    ):
        with pytest.raises(RuntimeError, match="stop here"):
            await inspect.unwrap(m.call_analyze_l1)(
                media_id=1,
                cover_url="https://example.com/c.jpg",
                title="T",
                description="D",
                user_id=_USER,
                provider_key="doubao",
                provider_config=_CFG,
                agent_model="doubao-seed-1-6-250615",
                agent_slug=agent_slug,
            )
    return factory


async def test_call_analyze_l1_threads_agent_slug_into_visual_service():
    factory = await _run_analyze_step("my-analyzer")
    assert factory.call_args.kwargs["agent_slug"] == "my-analyzer"


async def test_call_analyze_l1_empty_slug_falls_back_to_the_preset():
    factory = await _run_analyze_step("")
    assert factory.call_args.kwargs["agent_slug"] == "analyze"
