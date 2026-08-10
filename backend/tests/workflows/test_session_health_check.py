"""session_health_check — 调度接线 + 巡检写回语义。

这个模块存在的理由本身就是一条教训：spec §4.4 的巡检写了 repo 方法和两个
单测，**但没有任何生产调用方**，于是"测试全绿 + 功能不存在"并存了很久。所以
本文件的第一组测试测的不是逻辑而是**接线**：
``_scheduled_bundle`` 里有没有真的 import 它、DBOS 有没有真的给它挂上 cron。
把那行 import 注释掉，这两个测试必须红。
"""

from __future__ import annotations

import importlib
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.workflows.session_health_check import (
    MIN_RECHECK_INTERVAL_S,
    SESSION_CHECK_CRON,
    VERDICT_BUSY,
    VERDICT_ERROR,
    VERDICT_HEALTHY,
    VERDICT_INFRA,
    VERDICT_INVALID,
    VERDICT_MISSING,
    _check_one_account,
    classify_validation,
    select_due,
)

NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


# ── 接线（本任务的全部意义） ─────────────────────────────────────────────
def test_scheduled_bundle_imports_the_sweep():
    """生产 import 必须在 ``_scheduled_bundle``（worker-only）里。

    读源码而不是读 sys.modules：测试自己 import 了这个模块，sys.modules 里
    必然有它 —— 那个断言对"有没有接线"完全不敏感，正是这次要防的假绿。
    """
    src = importlib.util.find_spec(
        "app.workflows._scheduled_bundle"
    ).origin  # type: ignore[union-attr]
    text = open(src, encoding="utf-8").read()
    assert "from app.workflows.session_health_check import" in text
    assert "session_health_check_workflow" in text
    # 必须是 scheduled bundle 而不是 dispatch bundle：后者 gateway 也会导入，
    # 放错地方等于让 gateway 也起一份调度线程（2026-05-27 重启循环那次的病根）。
    dispatch = importlib.util.find_spec(
        "app.workflows._dispatch_bundle"
    ).origin  # type: ignore[union-attr]
    assert "session_health_check" not in open(dispatch, encoding="utf-8").read()


def _reload_workflows(role: str):
    """以 ``role`` 重新加载 ``app.workflows``，并把 DBOS 的进程级注册表清空。

    清空是这两个测试**能证伪**的前提：本文件顶部 import 了
    ``session_health_check``，那次 import 自己就会往 ``registry.pollers`` 里挂一
    条 cron。不清掉的话，即使 ``_scheduled_bundle`` 一行都没接，poller 断言照样
    绿 —— 那正是本任务要消灭的那种假绿（实测过：摘掉接线后它仍然通过）。

    ``pollers`` 必须一起清（``test_role_aware_imports`` 的 helper 只清四张
    map，对本测试不够）。安全边界同那边：有活的 DBOS 实例时不碰。
    """
    import dbos._dbos as dbos_internals

    if getattr(dbos_internals, "_dbos_global_instance", None) is not None:
        pytest.skip("a live DBOS instance is present; registry reset unsafe")
    for name in [n for n in sys.modules if n.startswith("app.workflows")]:
        del sys.modules[name]
    registry = dbos_internals._get_or_create_dbos_registry()
    for attr in (
        "queue_info_map",
        "workflow_info_map",
        "function_type_map",
        "instance_info_map",
        "class_info_map",
    ):
        getattr(registry, attr, {}).clear()
    registry.pollers.clear()
    os.environ["MEDIAHUB_ROLE"] = role
    importlib.import_module("app.workflows")
    return registry


def test_dbos_registers_the_sweep_on_a_cron(monkeypatch):
    """DBOS 的 poller 表里必须有它 —— import 到了但没挂上 cron 等于没接线。"""
    monkeypatch.setenv("MEDIAHUB_ROLE", "worker")
    registry = _reload_workflows("worker")
    scheduled = {
        (args[0].__name__, args[1])
        for (_evt, _loop, args, _kw) in registry.pollers
        if args and callable(args[0])
    }
    assert ("session_health_check_workflow", SESSION_CHECK_CRON) in scheduled


def test_sweep_is_not_loaded_on_the_gateway(monkeypatch):
    """gateway 角色不得加载它 —— 网关起浏览器巡检是明确不想要的行为。"""
    monkeypatch.setenv("MEDIAHUB_ROLE", "gateway")
    _reload_workflows("gateway")
    assert "app.workflows._scheduled_bundle" not in sys.modules
    assert "app.workflows.session_health_check" not in sys.modules


# ── 节奏（select_due） ───────────────────────────────────────────────────
def test_select_due_prefers_never_checked_and_honours_the_interval():
    candidates = [
        {"id": "1", "session_checked_at": None},  # 从没查过 → 必查
        {"id": "2", "session_checked_at": NOW - timedelta(days=1)},  # 早过期
        {"id": "3", "session_checked_at": NOW - timedelta(minutes=5)},  # 太新
    ]
    due = select_due(candidates, now=NOW, max_accounts=10)
    assert [c["id"] for c in due] == ["1", "2"]


def test_select_due_boundary_is_the_configured_interval():
    just_under = {
        "id": "a",
        "session_checked_at": NOW - timedelta(seconds=MIN_RECHECK_INTERVAL_S - 1),
    }
    just_over = {
        "id": "b",
        "session_checked_at": NOW - timedelta(seconds=MIN_RECHECK_INTERVAL_S + 1),
    }
    due = select_due([just_under, just_over], now=NOW, max_accounts=10)
    assert [c["id"] for c in due] == ["b"]


def test_select_due_does_not_rely_on_input_ordering():
    """乱序输入不得让"太新"的那行提前截断后面该查的账号。"""
    candidates = [
        {"id": "fresh", "session_checked_at": NOW},
        {"id": "stale", "session_checked_at": NOW - timedelta(days=3)},
    ]
    assert [c["id"] for c in select_due(candidates, now=NOW, max_accounts=10)] == [
        "stale"
    ]


def test_select_due_caps_the_batch():
    candidates = [{"id": str(i), "session_checked_at": None} for i in range(50)]
    assert len(select_due(candidates, now=NOW, max_accounts=20)) == 20


def test_cron_is_at_least_half_hourly():
    """节奏是刻意慢的：每次校验都要开真浏览器登一次平台，太频繁本身是风控信号。"""
    assert SESSION_CHECK_CRON == "*/30 * * * *"
    assert MIN_RECHECK_INTERVAL_S >= 6 * 3600


# ── 结论映射（classify_validation） ──────────────────────────────────────
def test_classify_infra_failure_wins_over_success_false():
    """容器宕机的信封里 success 也是 False。先看 success 就会把一次宕机
    记成"全部账号掉线"，让用户白扫一堆二维码（§7.8）。"""
    result = {
        "success": False,
        "status": "failed",
        "detail": {"error_kind": "unreachable"},
    }
    assert classify_validation(result) == VERDICT_INFRA


def test_classify_session_invalid_is_a_real_verdict():
    result = {
        "success": False,
        "status": "session_invalid",
        "detail": {"reason": "no_session_state"},
    }
    assert classify_validation(result) == VERDICT_INVALID


def test_classify_success_is_healthy():
    assert classify_validation({"success": True, "detail": {}}) == VERDICT_HEALTHY


# ── 写回语义（_check_one_account） ───────────────────────────────────────
class _FakeRepo:
    """记录写回动作 —— 断言的是"动了哪些列"，那正是巡检唯一的副作用。"""

    row: dict | None = {"id": "1", "platform": "douyin", "session_state": "{}"}

    def __init__(self) -> None:
        _FakeRepo.calls = []

    async def get_with_session(self, account_id):
        return dict(_FakeRepo.row) if _FakeRepo.row is not None else None

    async def mark_needs_relogin(self, account_id):
        _FakeRepo.calls.append(("mark_needs_relogin", account_id))

    async def update_profile(self, account_id, *, username=None, avatar_url=None):
        _FakeRepo.calls.append(("update_profile", username, avatar_url))

    async def update_session_state(
        self, account_id, session_state=None, *, status=None
    ):
        _FakeRepo.calls.append(("update_session_state", session_state, status))


def _patch(monkeypatch, *, result=None, acquired=True, raises=None, row=...):
    import app.repositories.social_accounts_repository as repo_mod
    import app.services.distribution.registry as registry_mod
    import app.services.distribution.session_lock as lock_mod

    if row is not ...:
        _FakeRepo.row = row
    else:
        _FakeRepo.row = {"id": "1", "platform": "douyin", "session_state": "{}"}
    monkeypatch.setattr(repo_mod, "SocialAccountsRepository", _FakeRepo)

    class _Adapter:
        async def validate_session(self, account):
            if raises is not None:
                raise raises
            return result

    monkeypatch.setattr(
        registry_mod, "get_session_adapter", lambda platform: _Adapter()
    )

    @asynccontextmanager
    async def _lock(account_id, *, attempts=1, retry_seconds=0.0):
        yield acquired

    monkeypatch.setattr(lock_mod, "account_session_lock", _lock)
    return _FakeRepo


@pytest.mark.asyncio
async def test_healthy_session_bumps_checked_at_and_repairs_profile(monkeypatch):
    _patch(
        monkeypatch,
        result={
            "success": True,
            "status": "session_valid",
            "detail": {"profile": {"username": "MioPoo", "avatar_url": "https://a"}},
        },
    )
    assert await _check_one_account("1", "douyin") == VERDICT_HEALTHY
    assert ("update_profile", "MioPoo", "https://a") in _FakeRepo.calls
    # session_state=None：只推进 session_checked_at，不把活会话抹成空。
    assert ("update_session_state", None, "active") in _FakeRepo.calls


@pytest.mark.asyncio
async def test_dead_session_is_marked_needs_relogin(monkeypatch):
    _patch(
        monkeypatch,
        result={
            "success": False,
            "status": "session_invalid",
            "detail": {"reason": "no_session_state"},
        },
    )
    assert await _check_one_account("1", "douyin") == VERDICT_INVALID
    assert ("mark_needs_relogin", 1) in _FakeRepo.calls
    # session_checked_at 不能前进：那一列的含义是"最后一次成功确认"，用它记
    # 失败会让下一轮以为刚查过，把重扫码提示推迟一整个间隔。
    assert not any(c[0] == "update_session_state" for c in _FakeRepo.calls)


@pytest.mark.asyncio
async def test_infra_failure_touches_nothing(monkeypatch):
    """容器宕机 == 没拿到结论。既不标 needs_relogin，也不刷新时间戳。"""
    _patch(
        monkeypatch,
        result={
            "success": False,
            "status": "failed",
            "detail": {"error_kind": "unreachable"},
        },
    )
    assert await _check_one_account("1", "douyin") == VERDICT_INFRA
    assert _FakeRepo.calls == []


@pytest.mark.asyncio
async def test_decrypt_failure_is_infra_not_a_dead_session(monkeypatch):
    """密钥错配时平台会话八成好得很，标 needs_relogin 是全量误伤。"""
    _patch(
        monkeypatch,
        result={"success": True, "detail": {}},
        row={
            "id": "1",
            "platform": "douyin",
            "session_state": None,
            "session_state_decrypt_failed": True,
        },
    )
    assert await _check_one_account("1", "douyin") == VERDICT_INFRA
    assert _FakeRepo.calls == []


@pytest.mark.asyncio
async def test_busy_account_is_skipped_not_failed(monkeypatch):
    """账号正被发布占用：同一账号两个 context 会互相踢下线（§7.5）。
    巡检等下一轮就行，绝不能硬开第二个。"""
    _patch(monkeypatch, result={"success": True, "detail": {}}, acquired=False)
    assert await _check_one_account("1", "douyin") == VERDICT_BUSY
    assert _FakeRepo.calls == []


@pytest.mark.asyncio
async def test_unexpected_error_is_contained_to_one_account(monkeypatch):
    """单账号炸了不该带走整批 —— 返回 verdict，让循环继续跑其余账号。"""
    _patch(monkeypatch, raises=RuntimeError("boom"))
    assert await _check_one_account("1", "douyin") == VERDICT_ERROR
    assert _FakeRepo.calls == []


@pytest.mark.asyncio
async def test_account_deleted_between_scan_and_check(monkeypatch):
    _patch(monkeypatch, result={"success": True, "detail": {}}, row=None)
    assert await _check_one_account("1", "douyin") == VERDICT_MISSING
