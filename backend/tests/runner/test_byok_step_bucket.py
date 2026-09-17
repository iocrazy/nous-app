"""BYOK 免扣（用户裁定 2）的第一段：一步 LLM 调用的钱是不是用户自己的 key 付的，
必须在 ``step_end`` 落事件的那一刻就定下来，因为那是唯一同时知道「这一步花了多少」
与「这一步由谁的 adapter 服务」的地方。

三条 runner 路径都要覆盖，其中「adapter 无 ``stream`` 属性 → stream_turn 委托
run_turn」那条是生产上每个带 chunk_callback 回合的**唯一**路径（CLAUDE.md 血泪：
2026-09-08 stop_reason 事故就是只测了另外两条）。
"""

from unittest.mock import AsyncMock

import pytest

from app.services.ai.billing.byok_step import step_byok_cents

pytestmark = pytest.mark.unit


# ── 纯函数：四态 origin × 三态 served_by_platform ────────────────────────


def test_only_byok_origin_counts_as_the_users_own_money():
    """``env`` 是「BYOK 形状但没有 api_key」，adapter factory 回落平台凭证 ——
    平台真付了钱，绝不能当成用户自己付（裁定 ①）。"""
    assert step_byok_cents(1.5, "byok", False) == 1.5
    for origin in ("platform", "governance", "env", None, ""):
        assert step_byok_cents(1.5, origin, False) is None, origin


def test_a_step_served_by_the_platform_catalog_is_never_byok():
    """半程 BYOK：主模型是用户的、fallback 落到平台目录行 —— 那一步的钱平台
    真付了（裁定 ②）。"""
    assert step_byok_cents(1.5, "byok", True) is None


def test_an_unknown_server_falls_back_to_the_run_level_origin():
    """``None`` = 这条链根本没有 fallback 机制（直连 adapter、子 agent 栈），
    一个凭证服务整轮，run 级 origin 就是精确答案 —— 这也是裁定 ② 的路 B。"""
    assert step_byok_cents(1.5, "byok", None) == 1.5
    assert step_byok_cents(1.5, "platform", None) is None


def test_a_step_with_no_known_price_reports_nothing_rather_than_zero():
    """价钱未知时 ``cost_cents`` 是 None。写 0 会把「不知道」伪装成「没花钱」，
    而下游是拿它去减平台额的 —— 减掉一个假的 0 不痛，减掉一个假的数才痛。
    ``True`` 是 ``int`` 的子类，同样不是价钱。"""
    assert step_byok_cents(None, "byok", False) is None
    assert step_byok_cents("1.5", "byok", False) is None
    assert step_byok_cents(True, "byok", False) is None


# ── 三条 runner 路径 ────────────────────────────────────────────────────


class _Rec:
    """``tests/runner/test_turn_end_reasons.py`` 的 ``_Rec`` 加两样：BYOK 判据要
    读的 ``credential_origin``，以及 ``_step_ended`` 算价要调的 ``cost_of``。"""

    def __init__(self, credential_origin=None):
        self.events = []
        self.views = {"view": {}}
        self.credential_origin = credential_origin

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def cost_of(self, prompt, completion, cached):
        return 2.0

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def step_ends(self):
        return [p for t, p in self.events if t == "step_end"]


def _composed():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(adapter=adapter, skill_tool=_Tool())


def _resp(*, served_by_platform=None):
    """真实 wire 形状：``LLMFallbackChain.call`` 成功时在响应体顶层注入
    ``_actual_model`` / ``_served_by_platform``，choices 与 usage 是 provider 原样。"""
    out = {
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "_actual_model": "m",
    }
    if served_by_platform is not None:
        out["_served_by_platform"] = served_by_platform
    return out


@pytest.mark.asyncio
async def test_run_turn_marks_the_step_when_the_users_own_key_served_it():
    adapter = AsyncMock()
    adapter.call = AsyncMock(return_value=_resp(served_by_platform=False))
    rec = _Rec(credential_origin="byok")
    await _runner(adapter).run_turn(
        _composed(), user_messages=[{"role": "user", "content": "go"}], recorder=rec
    )
    (step,) = rec.step_ends()
    assert step["cost_cents"] == 2.0
    assert step["byok_cents"] == 2.0


@pytest.mark.asyncio
async def test_run_turn_leaves_the_key_off_a_platform_served_step():
    """键不存在，不是等于 0 —— fold 靠「有没有这个键」判断，0 与缺席在账上
    同值但在语义上不同（缺席 = 平台付的，0 = 用户付了 0 分）。"""
    adapter = AsyncMock()
    adapter.call = AsyncMock(return_value=_resp(served_by_platform=True))
    rec = _Rec(credential_origin="byok")
    await _runner(adapter).run_turn(
        _composed(), user_messages=[{"role": "user", "content": "go"}], recorder=rec
    )
    (step,) = rec.step_ends()
    assert step["cost_cents"] == 2.0
    assert "byok_cents" not in step


class _NoStreamAdapter:
    """没有 ``stream`` 属性 —— 生产上 chunk_callback 回合走的就是它，
    ``stream_turn`` 委托 ``run_turn``（CLAUDE.md「生产的唯一路径」）。"""

    async def call(self, *a, **k):
        return _resp(served_by_platform=False)


@pytest.mark.asyncio
async def test_the_buffered_fallback_path_carries_the_byok_mark():
    rec = _Rec(credential_origin="byok")
    async for _chunk in _runner(_NoStreamAdapter()).stream_turn(
        _composed(),
        [{"role": "user", "content": "go"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    (step,) = rec.step_ends()
    assert step["byok_cents"] == 2.0, rec.events


class _StreamAdapter:
    """有 ``stream``：流式分块里没有响应体，拿不到 ``_served_by_platform``，
    于是退到 run 级 origin（路 B 的那一支）。"""

    async def call(self, *a, **k):
        raise AssertionError("must not fall back to call()")

    async def stream(self, composed, messages, **kw):
        from app.services.ai.adapters.base import StreamChunk

        yield StreamChunk(delta_text="ok")
        yield StreamChunk(
            finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 4}
        )


@pytest.mark.asyncio
async def test_the_streaming_path_falls_back_to_the_run_level_origin():
    rec = _Rec(credential_origin="byok")
    async for _chunk in _runner(_StreamAdapter()).stream_turn(
        _composed(),
        [{"role": "user", "content": "go"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    (step,) = rec.step_ends()
    assert step["byok_cents"] == 2.0, rec.events


# ── fold ────────────────────────────────────────────────────────────────


def _views():
    from app.services.ai.runner.run_projection import empty_views

    return empty_views()


def test_the_fold_accumulates_a_parallel_byok_lane():
    from app.services.ai.runner.folds.step import fold_step_end

    views = _views()
    fold_step_end(views, {"turn": 1, "step": 1, "cost_cents": 2.0, "byok_cents": 2.0})
    fold_step_end(views, {"turn": 1, "step": 2, "cost_cents": 3.0})
    cost = views["cost"]
    assert cost["own_cents"] == 5.0
    assert cost["own_byok_cents"] == 2.0
    # 真花了 5 分：BYOK 的钱用户真付了，预算门禁与 UI 读的 spent_cents 不许缩水。
    assert cost["spent_cents"] == 5.0
