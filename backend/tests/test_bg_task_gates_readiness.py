"""后台任务的第三类:detached housekeeping(有跟踪、不当门禁、可以正常结束)。

回归的是 2026-08-03 的生产故障:`reap_stale_input_waits`(#1662)想表达"别让
就绪等我 30 秒",却用了 `long_running=True`。注册表把"long_running 任务结束"
定义为守护进程崩溃,于是该任务 sleep 30s 正常返回后:

  /readyz 永久 "degraded" + 503 → 两个容器永久 (unhealthy)
  → 部署 smoke 探的就是 readyz,只靠抢在 30 秒睡眠之前才侥幸通过

正确表达是 `gates_readiness=False`:finite、允许结束、结束不算崩溃。
"""

import asyncio

import pytest

from app.lifespan_helpers import BackgroundTaskRegistry


async def _instant() -> None:
    return None


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_finished_housekeeping_is_not_a_dead_daemon():
    """核心回归:detached 任务跑完 → 仍 ready,不进 dead_daemons。"""
    reg = BackgroundTaskRegistry()
    reg.spawn("housekeeping", _instant(), gates_readiness=False)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert reg.dead_daemons() == []
    assert reg.all_done() is True  # ← 老写法(long_running=True)在这里会 False


@pytest.mark.asyncio
async def test_housekeeping_does_not_hold_readiness_while_pending():
    """未跑完也不该把 /readyz 摁在 starting —— 这正是作者的原始意图。"""
    reg = BackgroundTaskRegistry()
    reg.spawn("slow_housekeeping", _forever(), gates_readiness=False)
    await asyncio.sleep(0)

    assert reg.all_done() is True
    await reg.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_finished_daemon_still_reads_as_dead():
    """守护进程的崩溃检测不能被这次改动削弱。"""
    reg = BackgroundTaskRegistry()
    reg.spawn("daemon", _instant(), long_running=True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert reg.dead_daemons() == ["daemon"]
    assert reg.all_done() is False


@pytest.mark.asyncio
async def test_startup_gate_still_gates():
    """默认仍是启动门禁:没跑完 → not ready。"""
    reg = BackgroundTaskRegistry()
    reg.spawn("gate", _forever())
    await asyncio.sleep(0)

    assert reg.all_done() is False
    await reg.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_snapshot_exposes_gates_readiness():
    """/readyz 载荷要能看出一个任务属于哪一类(排障可见性)。"""
    reg = BackgroundTaskRegistry()
    reg.spawn("housekeeping", _instant(), gates_readiness=False)
    await asyncio.sleep(0)

    entry = next(t for t in reg.status_snapshot() if t["name"] == "housekeeping")
    assert entry["gates_readiness"] is False
    assert entry["long_running"] is False


def test_reap_stale_input_waits_is_registered_as_housekeeping():
    """锁死接线:它必须是 detached housekeeping,不能再被写成 daemon。

    源码级断言 —— 起一个真 app 太重,而这一行的语义就是故障的全部成因。
    """
    import inspect

    from app.startup import bootstrap

    src = inspect.getsource(bootstrap)
    idx = src.index('"reap_stale_input_waits"')
    call = src[idx : idx + 220]
    assert "gates_readiness=False" in call
    assert "long_running=True" not in call


# ── 第四类:fatal gate(2026-09-07)────────────────────────────────────────────
# 有限门任务失败仍算 done,/readyz 照样 200——对「探到就该拒绝启动」的探针
# (work_dir_probe:中转卷没挂/不可写)这是错的:部署 smoke 探 readyz,200 就放行,
# 影子目录照样上线。fatal=True 的门失败 → not ready + degraded/503。


async def _boom() -> None:
    raise RuntimeError("work dir not mounted")


@pytest.mark.asyncio
async def test_failed_fatal_gate_blocks_readiness_as_degraded():
    reg = BackgroundTaskRegistry()
    reg.spawn("work_dir_probe", _boom(), fatal=True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert reg.failed_fatal_gates() == ["work_dir_probe"]
    assert reg.all_done() is False
    snap = {e["name"]: e for e in reg.status_snapshot()}
    assert snap["work_dir_probe"]["fatal"] is True
    assert "not mounted" in snap["work_dir_probe"]["error"]


@pytest.mark.asyncio
async def test_failed_non_fatal_gate_keeps_the_old_semantics():
    """未标 fatal 的门失败仍只是记录——不把每个 warn-only 探针都变成停机。"""
    reg = BackgroundTaskRegistry()
    reg.spawn("schema_probe", _boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert reg.failed_fatal_gates() == []
    assert reg.all_done() is True


@pytest.mark.asyncio
async def test_readyz_reports_a_failed_fatal_gate_as_degraded_503():
    from fastapi import Response

    from app.api import lifespan_router as lr

    reg = BackgroundTaskRegistry()
    reg.spawn("work_dir_probe", _boom(), fatal=True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    resp = Response()
    body = await lr._readyz_payload(reg, resp)
    assert resp.status_code == 503
    assert body["status"] == "degraded"


def test_work_dir_probe_is_registered_fatal():
    """把守卫接进启动链的那一行本身也要钉住,否则守卫写好了没人调用。"""
    import inspect

    from app.startup import bootstrap

    src = inspect.getsource(bootstrap.install_background_bootstrap)
    assert 'spawn("work_dir_probe", _bg_work_dir_probe(), fatal=True)' in src
