"""Dispose must reach quiescence (CLAUDE.md 防御模式).

kill_process_tree used to SIGKILL and return at once, count a zombie as
alive (so a sync child burned the whole grace), and lose the group once the
leader died. The registry killed first and detached after; atexit read a
dict that does not exist; teardown never touched the registry at all. Each
test here failed on e4c3775c7 for exactly that reason.
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError

import pytest

from app.agent_framework import subprocess_registry as sr
from app.agent_framework.kill_tree import (
    KillOutcome,
    _is_dead,
    kill_process_tree,
    kill_process_tree_sync,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups"),
]

PY = sys.executable


def _gone_or_zombie(pid: int) -> bool:
    return _is_dead(pid)


@pytest.fixture(autouse=True)
def _clean_registry():
    sr.clear_registry()
    yield
    sr.clear_registry()


def _popen_sleeper(code: str = "import time; time.sleep(60)") -> subprocess.Popen:
    return subprocess.Popen(
        [PY, "-c", code],
        start_new_session=True,
        stdout=subprocess.PIPE,
    )


async def test_kill_tree_returns_only_after_quiescence():
    """No test-side wait: when kill_process_tree returns, the child is not
    running any more (dead or a zombie awaiting its owner's reap)."""
    p = _popen_sleeper(
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    try:
        time.sleep(0.2)
        out = await kill_process_tree(p.pid, grace_seconds=0.3)
        assert isinstance(out, KillOutcome)
        assert out.escalated is True
        assert out.quiesced is True
        assert _is_dead(p.pid)
        # we peeked, never reaped: the owner still gets the real status
        assert p.wait(timeout=2) == -signal.SIGKILL
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


async def test_kill_tree_grandchild_dead_after_return_when_leader_exits_first():
    """Leader dies on SIGTERM at once; its grandchild ignores SIGTERM. The
    group must still be escalated — the old getpgid(pid) fallback lost the
    group as soon as the leader was gone."""
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)'])\n"
        "print(g.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    p = _popen_sleeper(code)
    try:
        gpid = int(p.stdout.readline())
        time.sleep(0.3)  # grandchild installs its SIG_IGN
        out = await kill_process_tree(p.pid, grace_seconds=0.4)
        assert out.quiesced is True
        assert out.escalated is True
        assert _gone_or_zombie(gpid)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()


async def test_exits_on_term_is_reported():
    p = _popen_sleeper()
    try:
        time.sleep(0.2)
        out = await kill_process_tree(p.pid, grace_seconds=2.0)
        assert out == KillOutcome(
            already_dead=False, exited_on_term=True, escalated=False, quiesced=True
        )
    finally:
        p.wait()


async def test_already_dead_is_reported_and_no_signal_sent():
    p = subprocess.Popen([PY, "-c", "pass"], start_new_session=True)
    p.wait()
    out = await kill_process_tree(p.pid, grace_seconds=0.5)
    assert out.already_dead is True
    assert out.quiesced is True


def test_kill_outcome_is_frozen():
    out = KillOutcome(True, False, False, True)
    with pytest.raises(FrozenInstanceError):
        out.quiesced = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "platform",
    [
        pytest.param(
            "linux",
            marks=pytest.mark.skipif(
                not sys.platform.startswith("linux"), reason="/proc branch"
            ),
        ),
        pytest.param(
            "darwin",
            marks=pytest.mark.skipif(sys.platform != "darwin", reason="ps branch"),
        ),
    ],
)
async def test_is_dead_treats_zombie_as_dead(platform):
    """A sync child that exited but was never waited on is a zombie. It is
    not running; kill_process_tree must see that at once rather than burn
    the whole grace."""
    p = subprocess.Popen([PY, "-c", "pass"], start_new_session=True)
    try:
        time.sleep(0.5)  # exited, not reaped
        assert _is_dead(p.pid) is True
        t0 = time.monotonic()
        await kill_process_tree(p.pid, grace_seconds=3.0)
        assert time.monotonic() - t0 < 1.0
        assert p.wait(timeout=1) == 0  # status left for the owner
    finally:
        if p.poll() is None:
            p.wait()


def test_sync_kill_escalates_and_waits():
    p = _popen_sleeper(
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    try:
        time.sleep(0.2)
        out = kill_process_tree_sync(p.pid, grace_seconds=0.3)
        assert out.escalated and out.quiesced
        assert p.wait(timeout=2) == -signal.SIGKILL
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_kill_tree_never_signals_our_own_group():
    """A child spawned WITHOUT a new session shares our group; killpg on it
    would kill the test runner. Only the pid is signalled."""
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.2)
        out = kill_process_tree_sync(p.pid, grace_seconds=1.0)
        assert out.quiesced
        assert p.wait(timeout=2) == -signal.SIGTERM
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


# ── registry: detach first, then kill ─────────────────────────────────────


async def test_cancel_detaches_before_kill(monkeypatch):
    from app.agent_framework import kill_tree

    p = _popen_sleeper()
    snapshots: list[list[int]] = []
    real = kill_tree._send

    def spy(pid, pgid, sig):
        snapshots.append(sr.registered_pids("wf-order"))
        return real(pid, pgid, sig)

    monkeypatch.setattr(kill_tree, "_send", spy)
    try:
        time.sleep(0.2)
        sr.register_subprocess("wf-order", p.pid)
        outcomes = await sr.cancel_workflow_subprocesses("wf-order", grace_seconds=1.0)
        assert snapshots and snapshots[0] == []
        assert len(outcomes) == 1 and outcomes[0].quiesced
    finally:
        p.wait()


async def test_cancel_kills_concurrently_not_serially():
    ps = [
        _popen_sleeper(
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)"
        )
        for _ in range(3)
    ]
    try:
        time.sleep(0.3)
        for p in ps:
            sr.register_subprocess("wf-many", p.pid)
        t0 = time.monotonic()
        outcomes = await sr.cancel_workflow_subprocesses("wf-many", grace_seconds=0.5)
        # three graces serialised would be >= 1.5 s
        assert time.monotonic() - t0 < 1.4
        assert all(o.quiesced for o in outcomes)
    finally:
        for p in ps:
            if p.poll() is None:
                p.kill()
            p.wait()


async def test_cancel_all_subprocesses_empties_registry():
    p = _popen_sleeper()
    try:
        time.sleep(0.2)
        sr.register_subprocess("wf-a", p.pid)
        outcomes = await sr.cancel_all_subprocesses(grace_s=1.0)
        assert len(outcomes) == 1
        assert sr.snapshot_all_pids() == {}
    finally:
        p.wait()


# ── atexit / teardown ─────────────────────────────────────────────────────


def test_atexit_cleanup_kills_registered_pid():
    """H3: it read ``_workflow_pids`` (does not exist) and was a no-op."""
    from app.agent_framework.process_lifecycle import _cleanup_subprocess_registry

    p = _popen_sleeper(
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    try:
        time.sleep(0.2)
        sr.register_subprocess("wf-exit", p.pid)
        _cleanup_subprocess_registry()
        assert _is_dead(p.pid)
        assert p.wait(timeout=2) == -signal.SIGKILL
        assert sr.snapshot_all_pids() == {}
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


async def test_shutdown_all_kills_registered_subprocesses_before_dbos(monkeypatch):
    """Teardown never touched the registry; now it drains it before DBOS."""
    from app.startup import teardown

    order: list[str] = []

    async def _noop(*a, **kw):
        return None

    async def _dbos():
        order.append("dbos")

    real_cancel_all = sr.cancel_all_subprocesses

    async def _cancel_all(**kw):
        order.append("subprocesses")
        return await real_cancel_all(**kw)

    for name in (
        "stop_bounds_heartbeat",
        "stop_lifecycle_bus",
        "stop_prometheus_pusher",
        "interrupt_inflight_runs",
        "stop_ssrf_proxy",
        "close_async_redis",
    ):
        monkeypatch.setattr(teardown, name, _noop)
    monkeypatch.setattr(teardown, "shutdown_dbos", _dbos)
    monkeypatch.setattr(teardown, "cancel_all_subprocesses", _cancel_all)
    monkeypatch.setattr(
        teardown,
        "DrissionPageParser",
        lambda: type("P", (), {"close": lambda s: None})(),
    )

    class _App:
        class state:  # noqa: N801
            bg_tasks = None

    p = _popen_sleeper()
    try:
        await asyncio.sleep(0.2)
        sr.register_subprocess("wf-td", p.pid)
        await teardown.shutdown_all(_App())
        assert _is_dead(p.pid)
        assert order[:2] == ["subprocesses", "dbos"]
    finally:
        p.wait()
