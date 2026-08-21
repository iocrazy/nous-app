"""榜单定时刷新：闸门、结论映射、以及那条不能被"优化"掉的订阅语义。

这个 sweeper 与其它 housekeeping 有一点不同：**它每跑一次就在一个真实平台账号
上留下一条草稿**（够到「选择音乐」面板必须先上传）。所以下面的测试里，真正重要
的不是"它会跑"，而是**它在什么时候坚决不跑**。
"""

from __future__ import annotations

import pytest

import app.workflows.music_charts_sweep as sweep

pytestmark = pytest.mark.unit


# ── 闸门：什么时候一个账号都不碰 ──────────────────────────────────────


async def test_a_disabled_module_scans_nothing_at_all(monkeypatch):
    """关掉 distribution 的人的意思是"别再碰这些平台账号了"。

    这里是少数几条**不需要用户点任何按钮**就会去登平台的路径之一，所以它必须
    服从同一个 fail-closed 开关。断言的是"连扫描都没发生"——只在采集前判一次
    是不够的，扫描本身就要读跨租户的数据。
    """
    scanned = []
    monkeypatch.setattr(sweep, "_music_module_enabled", _const(False))
    monkeypatch.setattr(sweep, "_scan_stale_accounts", _record(scanned, []))

    counts = await sweep.music_charts_sweep_workflow.__wrapped__.__wrapped__(None, None)
    assert counts == {"due": 0, "harvested": 0}
    assert scanned == []


async def test_an_unhealthy_browser_touches_no_account(monkeypatch):
    """容器宕机时这一轮无论如何都采不到，逐个试只是白等一堆超时 —— 而每一次
    超时都可能在账号上留下一条**半成品**草稿。所以整轮让开。"""
    harvested = []
    monkeypatch.setattr(sweep, "_music_module_enabled", _const(True))
    monkeypatch.setattr(
        sweep,
        "_scan_stale_accounts",
        _const([{"account_id": "10", "platform": "douyin"}]),
    )
    monkeypatch.setattr(sweep, "_music_browser_is_healthy", _const(False))
    monkeypatch.setattr(sweep, "_harvest_one", _record(harvested, sweep.VERDICT_STORED))

    counts = await sweep.music_charts_sweep_workflow.__wrapped__.__wrapped__(None, None)
    assert counts["due"] == 1
    assert counts["skipped_unhealthy"] == 1
    assert counts["harvested"] == 0
    assert harvested == []


async def test_nothing_due_ends_the_tick_before_the_browser_probe(monkeypatch):
    """没有过期账号时连健康探针都不发。探针本身是一次 HTTP 调用，为一个注定
    什么都不做的 tick 去打它，是把"每小时一次"变成"每小时至少一次请求"。"""
    probed = []
    monkeypatch.setattr(sweep, "_music_module_enabled", _const(True))
    monkeypatch.setattr(sweep, "_scan_stale_accounts", _const([]))
    monkeypatch.setattr(sweep, "_music_browser_is_healthy", _record(probed, True))

    counts = await sweep.music_charts_sweep_workflow.__wrapped__.__wrapped__(None, None)
    assert counts == {"due": 0, "harvested": 0}
    assert probed == []


# ── 结论映射 ────────────────────────────────────────────────────────


async def test_a_busy_account_is_not_counted_as_a_failure(monkeypatch):
    """账号正在发布/巡检不是错误，是"下一轮再来"。

    而且它**不推进任何时间戳**（由 harvest 侧保证），所以下一轮它仍排在队首。
    把 busy 记成 error 会让一个正常发布中的账号看起来像坏了。
    """
    out = await _harvest_with(
        monkeypatch, {"detail": {"reason": sweep_reason_busy()}, "stored": {}}
    )
    assert out == sweep.VERDICT_BUSY


async def test_a_run_that_read_nothing_is_not_reported_as_stored(monkeypatch):
    """跑完了却一个榜都没读到:缓存**原样保留**(repository 的 keep 规则),
    所以这既不是数据丢失也不是成功 —— 它有自己的计数键。"""
    out = await _harvest_with(
        monkeypatch,
        {
            "success": False,
            "message": "no chart could be read",
            "stored": {"stored": 0},
        },
    )
    assert out == sweep.VERDICT_NOTHING


async def test_a_stored_run_counts_by_charts_actually_written(monkeypatch):
    out = await _harvest_with(monkeypatch, {"success": True, "stored": {"stored": 12}})
    assert out == sweep.VERDICT_STORED


async def test_one_account_blowing_up_does_not_abort_the_tick(monkeypatch):
    """一行坏数据不该饿死其余账号 —— 与 session_health_check 的解密失败同族。"""
    monkeypatch.setattr(
        "app.services.distribution.music_charts.harvest_account_charts", _raise
    )
    out = await sweep._harvest_one.__wrapped__("10")
    assert out == sweep.VERDICT_ERROR


# ── 节奏 ────────────────────────────────────────────────────────────


def test_the_tick_takes_one_account_by_default():
    """每次采集约 2 分钟、要独占该账号的浏览器会话、并留一条草稿。默认 1 个
    不是保守，是这三件事的乘积。"""
    assert sweep._max_per_tick() == 1


def test_the_ttl_defaults_to_a_day():
    """榜单是天级变化的。更频繁买不到任何东西,只是多付草稿。"""
    assert sweep._ttl_hours() == 24


def test_the_cron_is_hourly_not_daily():
    """小时级 tick + 24h TTL,而不是"每天 03:00 跑一次"。

    差别在**自愈**:固定日程错过一次(worker 重启、容器不健康)就要再等一天;
    小时级 tick 会在下一个整点自己把它捡回来。
    """
    assert sweep.MUSIC_CHARTS_CRON.split()[1] == "*"


def test_the_step_names_are_prefixed_so_dbos_can_register_them():
    """⚠️ 这条钉住的是一次**真实撞上的**启动期崩溃。

    DBOS 要求注册的函数名全局唯一,而 `session_health_check` 已经有
    `_module_enabled` / `_browser_is_healthy`。重名会抛
    "Duplicate registration of function" —— 炸掉的不是这一个 workflow,是整个
    scheduled bundle 的 import,于是**所有**定时任务一起起不来。

    照抄邻居模块时最容易踩到,而且症状(所有定时任务消失)离原因很远。
    """
    import app.workflows.session_health_check as neighbour

    assert hasattr(sweep, "_music_module_enabled")
    assert hasattr(sweep, "_music_browser_is_healthy")
    # 正向对照:邻居确实占着那两个名字,所以上面的前缀不是多余的。
    assert hasattr(neighbour, "_module_enabled")
    assert hasattr(neighbour, "_browser_is_healthy")


# ── helpers ─────────────────────────────────────────────────────────


def _const(value):
    async def _fn(*_a, **_k):
        return value

    return _fn


def _record(sink, value):
    async def _fn(*a, **_k):
        sink.append(a)
        return value

    return _fn


async def _raise(*_a, **_k):
    raise RuntimeError("kaboom")


def sweep_reason_busy() -> str:
    from app.services.distribution.music_charts import REASON_ACCOUNT_BUSY

    return REASON_ACCOUNT_BUSY


async def _harvest_with(monkeypatch, envelope: dict) -> str:
    async def _fake(*_a, **_k):
        return envelope

    monkeypatch.setattr(
        "app.services.distribution.music_charts.harvest_account_charts", _fake
    )
    return await sweep._harvest_one.__wrapped__("10")
