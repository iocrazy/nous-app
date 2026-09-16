"""``duration_ms`` 量的是**工具执行本身**（3c §3.2 修复第 1 轮）。

发射点与派发之间还夹着图片提升 / `_strip_image_urls`、tool message 的
``json.dumps``、trace append —— 而 run_turn 那侧的起点一度还在 Phase L 缓存查找
之前。这些都是 harness 的开销，不是工具花的时间；混进去之后「哪个工具慢」这个
读面读到的是我们自己的裁剪成本。

手法：让工具本身睡 ``TOOL_SLEEP``，让裁剪睡 ``STRIP_SLEEP``（更久），断言量出来的
时长夹在两者之间 —— 既证明工具那段被量到了，也证明裁剪那段没被量进去。
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner import agent_runner as ar
from app.services.ai.runner.agent_runner import AgentRunner

pytestmark = pytest.mark.unit

DATA_URL = "data:image/png;base64,QUFB"
TOOL_SLEEP = 0.15
STRIP_SLEEP = 0.40


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="unit-model",
        temperature=0.0,
        max_tokens=64,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


class _Recorder:
    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def record_usage(self, **kw):
        return None

    def record_skill(self, slug):
        return None

    def cost_of(self, prompt, completion, cached=0):
        return 0.0

    async def heartbeat(self):
        return None

    async def check_cancelled(self):
        return False


def _durations(rec):
    return [p["duration_ms"] for t, p in rec.events if t == "tool_call"]


def _image_result():
    return {
        "content": [{"type": "image_url", "url": DATA_URL, "mime": "image/png"}],
        "meta": {"name": "shot.png", "kind": "image"},
    }


async def _slow_fetch(args):
    await asyncio.sleep(TOOL_SLEEP)
    return _image_result()


def _slow_strip(monkeypatch):
    """裁剪是同步的，所以用 ``time.sleep`` —— 如果它落在计时窗口内，量出来的
    时长必然 ≥ STRIP_SLEEP。"""
    real = ar._strip_image_urls

    def _impl(result, note):
        time.sleep(STRIP_SLEEP)
        return real(result, note)

    monkeypatch.setattr(ar, "_strip_image_urls", _impl)


def _runner_with_resource_fetch(adapter):
    runner = AgentRunner(adapter=adapter, skill_tool=None)
    runner.resource_fetch_handler = _slow_fetch
    runner.vision_capable = True
    return runner


def _assert_window(durations):
    assert len(durations) == 1, durations
    measured = durations[0]
    assert measured >= int(
        TOOL_SLEEP * 1000 * 0.8
    ), f"工具本身睡了 {TOOL_SLEEP}s，量出来只有 {measured}ms —— 派发没被量到"
    assert measured < int(
        STRIP_SLEEP * 1000
    ), f"量出来 {measured}ms ≥ 裁剪的 {STRIP_SLEEP}s —— 窗口把 harness 开销算了进去"


# ── run_turn ────────────────────────────────────────────────────────────


def _buffered_adapter():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "function": {
                                    "name": "ResourceFetch",
                                    "arguments": json.dumps({"resource_id": "42"}),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    return adapter


async def test_run_turn_measures_the_tool_not_the_image_stripping(monkeypatch):
    _slow_strip(monkeypatch)
    rec = _Recorder()
    runner = _runner_with_resource_fetch(_buffered_adapter())

    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "@shot.png"}], recorder=rec
    )

    assert out["content"] == "FINAL"
    _assert_window(_durations(rec))


# ── stream_turn ─────────────────────────────────────────────────────────


class _StreamingResourceFetchAdapter:
    def __init__(self):
        self.iter = 0

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "ResourceFetch", "arguments": ""},
                        }
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": '{"resource_id":"42"}'},
                        }
                    ]
                }
            )
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_text="FINAL", finish_reason="stop")


async def test_stream_turn_measures_the_tool_not_the_image_stripping(monkeypatch):
    _slow_strip(monkeypatch)
    rec = _Recorder()
    runner = _runner_with_resource_fetch(_StreamingResourceFetchAdapter())

    async for _ in runner.stream_turn(
        _composed(), [{"role": "user", "content": "@shot.png"}], recorder=rec
    ):
        pass

    _assert_window(_durations(rec))


# ── run_turn 的缓存命中 ──────────────────────────────────────────────────


class _CachingSkillTool:
    async def execute(self, args):
        await asyncio.sleep(TOOL_SLEEP)
        return {"ok": True}


def _two_skill_calls_adapter():
    def _call(name, cid):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": cid,
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps({"skill": "idem"}),
                                },
                            }
                        ],
                    }
                }
            ]
        }

    adapter = AsyncMock()
    adapter.call.side_effect = [
        _call("Skill", "a"),
        _call("Skill", "b"),
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    return adapter


def _slow_cache_key(monkeypatch):
    """Phase L 的缓存查找也在派发之前。把它拖慢，起点只要还在它之前就会被抓到。"""
    from app.agent_framework import ToolResultCache

    real = ToolResultCache.key

    @staticmethod
    def _impl(tool_name, args):
        time.sleep(STRIP_SLEEP)
        return real(tool_name, args)

    monkeypatch.setattr(ToolResultCache, "key", _impl)


async def test_a_cache_hit_is_not_charged_the_lookup(monkeypatch):
    """两件事一起钉：缓存查找不算进时长（起点在它之后），以及命中缓存的那次
    工具根本没跑、时长必须塌下来。"""
    _slow_cache_key(monkeypatch)
    rec = _Recorder()
    runner = AgentRunner(
        adapter=_two_skill_calls_adapter(), skill_tool=_CachingSkillTool()
    )
    composed = _composed()
    # ``idempotent_slugs`` 读的是 manifest 里的裸 dict（agent_runner.py:1748）。
    composed.skill_manifest = [{"slug": "idem", "idempotent": True}]

    out = await runner.run_turn(
        composed, [{"role": "user", "content": "go"}], recorder=rec
    )

    assert out["content"] == "FINAL"
    first, second = _durations(rec)
    assert first >= int(TOOL_SLEEP * 1000 * 0.8), first
    assert first < int(
        STRIP_SLEEP * 1000
    ), f"第一次量出 {first}ms ≥ 缓存查找的 {STRIP_SLEEP}s —— 起点还在查找之前"
    assert second < int(TOOL_SLEEP * 1000 * 0.5), (first, second)
