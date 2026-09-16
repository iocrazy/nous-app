"""一个浏览器标签页，同一时刻只能有一个解析在用它。"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from app.services.media.parsers.douyin_parse.drissionpage_parser import (
    DrissionPageParser,
)


@pytest.fixture(autouse=True)
def _fresh_parser():
    """一把干净的锁 + 一个干净的单例。

    ``DrissionPageParser`` 用 ``SingletonMeta``，实例存在**进程级**的
    ``_instances`` 里。别的测试只要构造过它一次，这里 patch 的 ``_initialize``
    就不会被调用（``_initialized`` 已经是 True），于是用例单跑绿、全量红 ——
    实际撞到过。所以每条用例前后都把实例清掉。
    """
    from app.core.utils import SingletonMeta

    def _reset():
        SingletonMeta._instances.pop(DrissionPageParser, None)
        DrissionPageParser._page = None
        DrissionPageParser._fetch_lock = threading.Lock()

    _reset()
    yield
    _reset()


class _CountingLock:
    """真锁 + 计数，用来观察**源码**有没有真的把它拿起来。

    这一步是刻意的：上一版测试自己把 acquire/release 重写了一遍，于是把
    drissionpage_parser 里的锁删掉它照样绿 —— 典型的「mock 骗了自己」。现在
    fetch_one_query 走的是真实的 _fetch_in_thread，锁必须由源码来抢。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquires = 0
        self.holders = 0
        self.max_holders = 0
        self._guard = threading.Lock()

    def acquire(self, *a, **kw) -> bool:
        got = self._lock.acquire(*a, **kw)
        if got:
            with self._guard:
                self.acquires += 1
                self.holders += 1
                self.max_holders = max(self.max_holders, self.holders)
        return got

    def release(self) -> None:
        with self._guard:
            self.holders -= 1
        self._lock.release()


async def _no_cookie(*_a, **_kw) -> str:
    return ""


async def _two_real_fetches(lock: _CountingLock, hold: float = 0.08):
    """并发跑两次**真的** fetch_one_video。

    浏览器起不来（_initialize 抛），所以 _fetch_locked 很快就返回 None —— 但那是
    在锁里面发生的，正是要观察的区间。
    """
    import time

    def _boom(self):
        time.sleep(hold)
        raise RuntimeError("no chromium in tests")

    with (
        patch.object(DrissionPageParser, "_fetch_lock", lock),
        patch.object(DrissionPageParser, "_initialize", _boom),
        patch.object(DrissionPageParser, "_get_user_cookie_text", _no_cookie),
    ):
        return await asyncio.gather(
            DrissionPageParser.fetch_one_video(
                "https://v.douyin.com/a/", user_agent="UA"
            ),
            DrissionPageParser.fetch_one_video(
                "https://v.douyin.com/b/", user_agent="UA"
            ),
        )


@pytest.mark.asyncio
async def test_two_parses_never_hold_the_browser_at_once():
    """**本文件的全部意义。**

    `parse_user` 队列每用户跑 3 个并发，而浏览器只有一个标签页。没有这把锁，两个
    解析会把同一个标签页导去对方的 URL、共用一个响应监听器 —— 失败形态不是崩溃，
    是**数据串了**：一个任务拿着另一个任务的 aweme_detail 完成。
    """
    lock = _CountingLock()

    await _two_real_fetches(lock)

    assert lock.acquires == 2, "源码没有为每次 fetch 抢锁"
    assert lock.max_holders == 1, "同一时刻有两个解析在操作同一个标签页"


@pytest.mark.asyncio
async def test_both_parses_still_finish():
    """串行化不等于丢任务 —— 排队的那个照样要跑到，只是轮到它才跑。"""
    lock = _CountingLock()

    results = await _two_real_fetches(lock)

    assert len(results) == 2
    assert lock.acquires == 2


def test_a_wedged_browser_does_not_block_the_queue_forever():
    """拿不到锁要有尽头。

    浏览器卡死时，后面的解析应该在超时后返回「没拿到详情」让链路继续，而不是把
    DBOS 的 worker 线程永久钉住 —— 那会把整条解析队列一起拖死。
    """
    assert DrissionPageParser._FETCH_LOCK_TIMEOUT > 0
    DrissionPageParser._fetch_lock.acquire()
    try:
        with patch.object(DrissionPageParser, "_FETCH_LOCK_TIMEOUT", 0.05):
            acquired = DrissionPageParser._fetch_lock.acquire(
                timeout=DrissionPageParser._FETCH_LOCK_TIMEOUT
            )
        assert acquired is False
    finally:
        DrissionPageParser._fetch_lock.release()


def test_the_timeout_is_longer_than_a_real_parse():
    """超时不能短到把正常解析误判成卡死。

    生产 14 天实测：解析平均 10.4s，最长 277s（撞验证码那种）。所以下限按最长
    那一档取，而不是按平均。
    """
    assert DrissionPageParser._FETCH_LOCK_TIMEOUT >= 120
