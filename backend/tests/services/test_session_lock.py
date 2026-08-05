"""账号级串行锁 (spec §7.5) —— 同一账号同时只能有一个浏览器会话。

DB 全程用假 session 替身；这里验的是**锁的语义**：拿到才 yield True、
取不到不假装拿到、等待有上界、异常路径也一定释放（即事务一定结束）。

为什么锁本身是 ``pg_try_advisory_xact_lock`` 而不是 session 级的
``pg_advisory_lock``：连接走 Supavisor 事务级池，session 级锁跨事务持有时
下一条语句可能落到另一个 PG backend —— 锁既不在你以为的地方，也没人能解开。
"""

from __future__ import annotations

import pytest

from app.services.distribution.session_lock import (
    LOCK_NAMESPACE,
    account_lock_key,
    account_session_lock,
)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    def __init__(self, granted: bool, log: list):
        self._granted = granted
        self._log = log

    async def execute(self, stmt):
        self._log.append(str(stmt))
        return _FakeResult(self._granted)


def _fake_system_session(grants: list[bool], log: list, opened: list, closed: list):
    """按 ``grants`` 依次决定每一轮 try 的结果，并记录事务开/关的次数 ——
    "锁一定被释放"在这一层等价于"事务一定结束"。"""
    from contextlib import asynccontextmanager

    calls = iter(grants)

    @asynccontextmanager
    async def _session(reason: str):
        opened.append(reason)
        try:
            yield _FakeSession(next(calls), log)
        finally:
            closed.append(reason)

    return _session


@pytest.fixture
def patched(monkeypatch):
    def _apply(grants):
        log: list = []
        opened: list = []
        closed: list = []
        monkeypatch.setattr(
            "app.db.scope.system_session",
            _fake_system_session(grants, log, opened, closed),
        )
        return {"log": log, "opened": opened, "closed": closed}

    return _apply


async def test_lock_acquired_on_first_try(patched):
    state = patched([True])
    async with account_session_lock("900", retry_seconds=0) as acquired:
        assert acquired is True
        # 持锁期间事务仍然开着 —— 这正是 xact lock 的持有期
        assert state["closed"] == []
    assert len(state["closed"]) == 1  # 退出块 == 事务结束 == 锁释放


async def test_lock_uses_the_transaction_scoped_advisory_lock(patched):
    """必须是 xact 版本：事务结束（含 rollback / 连接死掉）自动释放，不存在
    忘记解锁或进程猝死留下死锁的路径。"""
    state = patched([True])
    async with account_session_lock("900", retry_seconds=0):
        pass
    assert "pg_try_advisory_xact_lock" in state["log"][0]


async def test_lock_retries_in_a_fresh_transaction_each_round(patched):
    """同一个事务里重试是无意义的：连接已被钉在一个 backend 上，锁状态不会变。"""
    state = patched([False, False, True])
    async with account_session_lock("900", attempts=5, retry_seconds=0) as acquired:
        assert acquired is True
        assert len(state["opened"]) == 3
        # 前两轮的事务都已结束（连接已还回池），只剩当前这把还开着
        assert len(state["closed"]) == 2
    assert len(state["closed"]) == 3


async def test_lock_gives_up_with_an_upper_bound(patched):
    """§7.2：不无限等。一个卡住的会话不得把整批任务连坐。"""
    state = patched([False, False, False])
    async with account_session_lock("900", attempts=3, retry_seconds=0) as acquired:
        assert acquired is False
    assert len(state["opened"]) == 3
    assert len(state["closed"]) == 3  # 没有事务被留在开着的状态


async def test_lock_released_when_the_body_raises(patched):
    state = patched([True])
    with pytest.raises(RuntimeError):
        async with account_session_lock("900", retry_seconds=0):
            raise RuntimeError("publish blew up")
    assert len(state["closed"]) == 1


def test_lock_key_fits_in_int4_for_snowflake_ids():
    """账号 id 是 Snowflake BIGINT，塞不进 advisory lock 的 int4 参数。"""
    for account_id in ("900", 900, "7312891273812731827", 2**62):
        key = account_lock_key(account_id)
        assert -(2**31) <= key <= 2**31 - 1


def test_lock_key_is_stable_and_distinguishes_accounts():
    assert account_lock_key("900") == account_lock_key(900)
    assert account_lock_key("900") != account_lock_key("901")


def test_lock_namespace_is_pinned():
    """namespace 变了等于把所有在跑的锁瞬间作废（新旧进程互相看不见对方的
    锁），所以它是契约的一部分，不是随手可改的常量。"""
    assert LOCK_NAMESPACE == 0x5E55
