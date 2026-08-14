"""并发观察者：把"发布卡在验证码上"变成用户看得见的东西。

这一层最容易被写成"看起来对但什么也没做"，因为它的失败是**沉默的**：轮询没跑、
metadata 没写，发布照样按老路超时失败，日志里没有任何异常。所以这里断言的是
"metadata 里到底出现了什么"，而不是"函数被调用了几次"。

路线 C 第 2 条在本文件里是硬门禁：观察者**只许**写 metadata，
``test_the_watcher_never_touches_the_phase_columns`` 会在它碰 phase/status/progress
时直接红。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.distribution.publish_sms_watch import (
    PUBLISH_SMS_KEY,
    new_correlation_id,
    publish_with_sms_channel,
)

pytestmark = pytest.mark.unit


class FakeManager:
    """记录每一次 metadata patch。禁止任何 phase 列写入。"""

    def __init__(self, fail_on_patch: bool = False):
        self.patches: list[dict] = []
        self.fail_on_patch = fail_on_patch

    async def patch_metadata(self, workflow_id: str, patch: dict) -> None:
        if self.fail_on_patch:
            raise RuntimeError("task_tracking unreachable")
        self.patches.append(patch)

    def __getattr__(self, name):  # pragma: no cover - 防呆
        raise AssertionError(
            f"观察者调用了 manager.{name} —— 它只许 patch_metadata。"
            "phase/status/progress 由 mirror trigger 单向同步（路线 C 第 2 条）。"
        )


class FakeStatus:
    def __init__(self, waiting, outcome=None, message="", attempts_left=3, max_attempts=3):
        self.waiting = waiting
        self.outcome = outcome
        self.message = message
        self.attempts_left = attempts_left
        self.max_attempts = max_attempts
        self.seconds_remaining = 170.0


class FakeClient:
    """按脚本回答轮询。最后一个状态会一直重复。"""

    def __init__(self, states, raises: Exception | None = None):
        self.states = list(states)
        self.raises = raises
        self.calls: list[str] = []

    async def get_publish_sms(self, correlation_id: str):
        self.calls.append(correlation_id)
        if self.raises:
            raise self.raises
        return self.states[min(len(self.calls) - 1, len(self.states) - 1)]


class FakeAdapter:
    """一个"发布"：跑够久让观察者轮询到，然后返回。"""

    def __init__(self, duration_s: float = 0.25, boom: Exception | None = None):
        self.duration_s = duration_s
        self.boom = boom
        self.correlation_ids: list[str | None] = []

    async def publish(self, account, intent, *, correlation_id=None):
        self.correlation_ids.append(correlation_id)
        await asyncio.sleep(self.duration_s)
        if self.boom:
            raise self.boom
        return {"ok": True}


async def _run(adapter, client, manager, workflow_id="wf-1"):
    return await publish_with_sms_channel(
        adapter=adapter,
        account={"id": 1},
        intent=object(),
        workflow_id=workflow_id,
        account_id=42,
        platform="douyin",
        manager=manager,
        client=client,
        poll_interval_s=0.05,
    )


# --- 核心：等码这件事真的到达了 task_tracking ---------------------------------


async def test_a_parked_publish_shows_up_in_task_tracking_metadata():
    """没有这一条，整条通道对用户不可达 —— 后端全对，用户只看见一个转圈。"""
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=True)])
    await _run(FakeAdapter(0.25), client, manager)

    waiting = [p for p in manager.patches if (p.get(PUBLISH_SMS_KEY) or {}).get("waiting")]
    assert waiting, "发布停在验证码上，但 metadata 里什么都没写"
    block = waiting[0][PUBLISH_SMS_KEY]
    assert block["account_id"] == 42
    assert block["platform"] == "douyin"
    # correlation_id 必须落库：供码端点是从这里读回它的（客户端永远不传）。
    assert block["correlation_id"]
    assert block["attempts_left"] == 3


async def test_the_block_is_cleared_once_the_publish_returns():
    """屏幕上残留一个没人在等的输入框，用户输进去的每个码都会石沉大海 ——
    那是"绿灯但什么也没发生"的镜像版本。"""
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=True)])
    await _run(FakeAdapter(0.25), client, manager)

    assert manager.patches[-1] == {PUBLISH_SMS_KEY: None}


async def test_the_block_is_cleared_even_when_the_publish_raises():
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=True)])
    with pytest.raises(RuntimeError, match="publish blew up"):
        await _run(FakeAdapter(0.25, boom=RuntimeError("publish blew up")), client, manager)

    assert manager.patches[-1] == {PUBLISH_SMS_KEY: None}


async def test_a_challenge_that_ends_is_announced_as_ended():
    """挑战结束不说一声，输入框就会一直挂着。"""
    manager = FakeManager()
    client = FakeClient([
        FakeStatus(waiting=True),
        FakeStatus(waiting=False, outcome="expired", message="window closed"),
    ])
    await _run(FakeAdapter(0.4), client, manager)

    ended = [
        p[PUBLISH_SMS_KEY] for p in manager.patches
        if p.get(PUBLISH_SMS_KEY) and p[PUBLISH_SMS_KEY].get("outcome") == "expired"
    ]
    assert ended, "挑战结束了，但没有任何一次 patch 说明它是怎么结束的"


async def test_a_publish_nobody_challenged_writes_nothing_but_the_clear():
    """没撞验证码的发布不该在 metadata 里留下噪音。"""
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=False)])
    await _run(FakeAdapter(0.25), client, manager)

    assert all(p == {PUBLISH_SMS_KEY: None} for p in manager.patches)


async def test_the_state_is_written_on_transitions_not_on_every_tick():
    """一次发布可能等 3 分钟，每 3 秒 PATCH 一次 jsonb 就是 60 次没有新信息的写。"""
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=True)])
    await _run(FakeAdapter(0.5), client, manager)

    assert len(client.calls) >= 4, "轮询根本没跑几轮，这个测试证明不了去重"
    announcements = [
        p for p in manager.patches if (p.get(PUBLISH_SMS_KEY) or {}).get("waiting")
    ]
    assert len(announcements) == 1


# --- 观察者绝不许把发布搞挂 ---------------------------------------------------


async def test_a_broken_poll_does_not_break_the_publish():
    """它是装饰通道。轮询挂了，最坏结果是用户看不到输入框、发布按老路失败 ——
    与修复前一模一样；让它去打断一个已经传完几百 MB 的发布则是纯亏。"""
    manager = FakeManager()
    client = FakeClient([], raises=RuntimeError("browser unreachable"))
    result = await _run(FakeAdapter(0.2), client, manager)
    assert result == {"ok": True}


async def test_a_broken_task_tracking_does_not_break_the_publish():
    manager = FakeManager(fail_on_patch=True)
    client = FakeClient([FakeStatus(waiting=True)])
    result = await _run(FakeAdapter(0.2), client, manager)
    assert result == {"ok": True}


async def test_the_watcher_never_touches_the_phase_columns():
    """路线 C 第 2 条，结构性断言。

    `FakeManager.__getattr__` 会在观察者调用 `patch_metadata` 以外的任何方法时
    直接 AssertionError —— 包括 start / update_progress / fail / complete。
    """
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=True)])
    await _run(FakeAdapter(0.25), client, manager)

    for patch in manager.patches:
        assert set(patch) == {PUBLISH_SMS_KEY}, (
            f"观察者写了业务装饰字段以外的键: {set(patch)}"
        )


# --- correlation id 与降级 ----------------------------------------------------


async def test_every_publish_attempt_gets_a_fresh_correlation_id():
    """复用会让上一次尝试的残留挑战被新一次寻址到 —— 用户的码进错上下文，是这里
    唯一真正危险的错法。"""
    assert new_correlation_id() != new_correlation_id()

    manager = FakeManager()
    adapter = FakeAdapter(0.05)
    for _ in range(3):
        await _run(adapter, FakeClient([FakeStatus(waiting=False)]), manager)
    assert len(set(adapter.correlation_ids)) == 3


async def test_without_a_workflow_id_the_channel_still_exists():
    """降级是刻意的：让"能不能供码"和"能不能显示"各自独立地失败。

    没有 workflow_id（单测 / 无 DBOS runtime）时不镜像状态，但 correlation_id
    照传，浏览器侧的通道照常建立。
    """
    adapter = FakeAdapter(0.01)
    result = await publish_with_sms_channel(
        adapter=adapter, account={"id": 1}, intent=object(),
        workflow_id="", account_id=42, platform="douyin",
    )
    assert result == {"ok": True}
    assert adapter.correlation_ids[0], "降级路径把 correlation_id 一起丢了 —— 那就真的没有通道了"


async def test_the_watcher_stops_when_the_publish_does():
    """悬空的观察者会在发布早已返回之后继续写 metadata —— 正好把一个不存在的
    输入框重新画到用户屏幕上。"""
    manager = FakeManager()
    client = FakeClient([FakeStatus(waiting=True)])
    await _run(FakeAdapter(0.2), client, manager)

    calls_at_return = len(client.calls)
    await asyncio.sleep(0.3)
    assert len(client.calls) == calls_at_return
    assert manager.patches[-1] == {PUBLISH_SMS_KEY: None}
