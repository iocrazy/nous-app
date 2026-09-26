"""run_process — real children, every outcome reported on its own field.

Every case spawns a real ``python -c`` child (no fakes): the defects this
file pins were all invisible to the stubbed ``communicate()`` tests — a
trapped SIGTERM, an unreaped child, a grandchild holding the pipes open.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import pytest

from app.agent_framework import subprocess_registry as sr
from app.agent_framework.process_result import ProcessResult
from app.agent_framework.process_runner import run_process

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals"),
]

PY = sys.executable


def _pid_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture(autouse=True)
def _clean_registry():
    sr.clear_registry()
    yield
    sr.clear_registry()


async def test_process_result_trap_term_exit0_reports_timed_out_and_exit0():
    """The case CLAUDE.md names: the child traps our SIGTERM and exits 0.
    Timed out AND exit 0 — both reported, and ``ok`` is False."""
    code = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    res = await run_process([PY, "-c", code], timeout_s=0.5, grace_s=2.0)
    assert isinstance(res, ProcessResult)
    assert res.timed_out is True
    assert res.exit_code == 0
    assert res.signal is None
    assert res.cancelled is False
    assert res.ok is False
    # partial output survives the timeout
    assert b"ready" in res.stdout


async def test_ignore_term_escalates_to_sigkill_and_is_reaped():
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    t0 = time.monotonic()
    res = await run_process([PY, "-c", code], timeout_s=0.3, grace_s=0.3)
    assert res.timed_out is True
    assert res.signal == signal.SIGKILL
    assert res.exit_code is None
    assert time.monotonic() - t0 < 5


async def test_natural_signal_death_is_a_signal_not_a_timeout():
    code = "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"
    res = await run_process([PY, "-c", code], timeout_s=10)
    assert res.signal == signal.SIGSEGV
    assert res.exit_code is None
    assert res.timed_out is False


async def test_literal_exit_137_is_an_exit_code_not_a_signal():
    res = await run_process([PY, "-c", "import sys; sys.exit(137)"], timeout_s=10)
    assert res.exit_code == 137
    assert res.signal is None
    assert res.timed_out is False
    assert res.ok is False


async def test_clean_run_is_ok_and_captures_both_streams():
    code = "import sys; print('out'); print('err', file=sys.stderr)"
    res = await run_process([PY, "-c", code], timeout_s=10)
    assert res.ok is True
    assert res.stdout.strip() == b"out"
    assert res.stderr.strip() == b"err"
    assert res.duration_s >= 0


async def test_stdin_is_delivered():
    code = "import sys; sys.stdout.write(sys.stdin.read().upper())"
    res = await run_process([PY, "-c", code], timeout_s=10, stdin=b"abc")
    assert res.stdout == b"ABC"


async def test_child_env_is_scrubbed(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_leak")
    code = "import os; print(os.environ.get('SUPABASE_SERVICE_ROLE_KEY', 'ABSENT'))"
    res = await run_process([PY, "-c", code], timeout_s=10)
    assert res.stdout.strip() == b"ABSENT"


async def test_env_extra_reaches_the_child():
    code = "import os; print(os.environ['FH4_PROBE'])"
    res = await run_process(
        [PY, "-c", code], timeout_s=10, env_extra={"FH4_PROBE": "yes"}
    )
    assert res.stdout.strip() == b"yes"


async def test_timeout_kills_the_grandchild_too():
    """The child's own child (yt-dlp → ffmpeg shape) must not outlive us."""
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen(['sleep', '60'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    res = await run_process([PY, "-c", code], timeout_s=0.5, grace_s=0.5)
    assert res.timed_out is True
    gpid = int(res.stdout.split()[0])
    # no test-side sleep: run_process returned only once the tree was still
    deadline = time.monotonic() + 0.5
    while not _pid_gone(gpid) and time.monotonic() < deadline:
        # an orphaned grandchild is reaped by init/launchd, not us — allow
        # the reaper one beat, but it must already have been SIGKILLed/TERMed
        await asyncio.sleep(0.05)
    assert _pid_gone(gpid) or _is_zombie(gpid)


def _is_zombie(pid: int) -> bool:
    from app.agent_framework.kill_tree import _is_zombie as z

    return z(pid)


async def test_stdout_line_callback_streams_lines():
    seen: list[str] = []

    async def on_line(line: str) -> None:
        seen.append(line)

    code = "print('a', flush=True); print('b', flush=True)"
    res = await run_process([PY, "-c", code], timeout_s=10, on_stdout_line=on_line)
    assert res.ok
    assert seen == ["a", "b"]
    assert res.stdout == b"a\nb\n"


async def test_streaming_reads_are_bounded_by_the_timeout():
    """H6: reading until EOF used to happen OUTSIDE the deadline — a hung
    child with open pipes blocked forever."""
    seen: list[str] = []
    code = "import time; print('x', flush=True); time.sleep(60)"
    res = await asyncio.wait_for(
        run_process(
            [PY, "-c", code],
            timeout_s=0.4,
            grace_s=0.3,
            on_stdout_line=lambda s: seen.append(s),
        ),
        timeout=5,
    )
    assert res.timed_out is True
    assert seen == ["x"]


async def test_workflow_cancel_seen_in_task_tracking_kills_child(monkeypatch):
    """H2: cancel lands in another process; the wait loop polls
    task_tracking and kills the child itself."""
    from app.agent_framework import process_runner as pr

    calls: list[str] = []
    t0 = time.monotonic()

    async def fake_cancelled(wf: str) -> bool:
        calls.append(wf)
        return time.monotonic() - t0 > 0.3

    monkeypatch.setattr(pr, "is_workflow_cancelled", fake_cancelled)
    res = await run_process(
        [PY, "-c", "import time; time.sleep(60)"],
        timeout_s=30,
        grace_s=0.5,
        workflow_id="wf-cancel",
        cancel_poll_s=0.1,
    )
    assert res.cancelled is True
    assert res.timed_out is False
    assert res.signal == signal.SIGTERM
    assert calls and set(calls) == {"wf-cancel"}
    assert time.monotonic() - t0 < 5
    assert sr.registered_pids("wf-cancel") == []


async def test_registered_while_running_and_unregistered_after():
    seen: list[list[int]] = []

    async def probe():
        await asyncio.sleep(0.3)
        seen.append(sr.registered_pids("wf-reg"))

    task = asyncio.create_task(probe())
    res = await run_process(
        [PY, "-c", "import time; time.sleep(0.8)"],
        timeout_s=10,
        workflow_id="wf-reg",
        cancel_poll_s=60,
    )
    await task
    assert res.ok
    assert len(seen[0]) == 1
    assert sr.registered_pids("wf-reg") == []


async def test_in_process_registry_cancel_reports_cancelled():
    async def canceller():
        await asyncio.sleep(0.3)
        await sr.cancel_workflow_subprocesses("wf-inproc", grace_seconds=0.5)

    task = asyncio.create_task(canceller())
    res = await run_process(
        [PY, "-c", "import time; time.sleep(60)"],
        timeout_s=30,
        workflow_id="wf-inproc",
        cancel_poll_s=60,
    )
    await task
    assert res.cancelled is True
    assert res.timed_out is False
    assert res.signal == signal.SIGTERM


async def test_outer_task_cancel_kills_child_before_propagating():
    """Dispose must reach quiescence: cancelling the awaiting task must not
    orphan the child."""
    pids: list[int] = []

    async def body():
        await run_process(
            [
                PY,
                "-c",
                "import os, time; print(os.getpid(), flush=True); time.sleep(60)",
            ],
            timeout_s=30,
            grace_s=0.5,
            workflow_id="wf-outer",
            cancel_poll_s=60,
        )

    task = asyncio.create_task(body())
    for _ in range(50):
        await asyncio.sleep(0.05)
        pids = sr.registered_pids("wf-outer")
        if pids:
            break
    assert pids
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _pid_gone(pids[0]) or _is_zombie(pids[0])
    assert sr.registered_pids("wf-outer") == []


async def test_spawn_failure_raises_file_not_found():
    """Not a timeout — the binary is absent. Callers map this to their own
    typed error (cli_missing)."""
    with pytest.raises(FileNotFoundError):
        await run_process(["/nonexistent/fh4-binary"], timeout_s=1)


# ── is_workflow_cancelled: the column that reflects a cancel ──────────────


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row, sink):
        self._row = row
        self._sink = sink

    async def execute(self, stmt):
        self._sink.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        return _FakeResult(self._row)


def _fake_read_scope(row, sink):
    import contextlib

    @contextlib.asynccontextmanager
    async def _scope(*a, **kw):
        yield _FakeSession(row, sink)

    return _scope


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"phase": "cancelled", "status": "cancelled"}, True),
        # Task Center writes both; the DBOS trigger writes both. Either alone
        # is still a cancel (a half-applied write must not keep a child alive).
        ({"phase": "processing", "status": "cancelled"}, True),
        ({"phase": "cancelled", "status": "processing"}, True),
        ({"phase": "processing", "status": "processing"}, False),
        ({"phase": "failed", "status": "failed"}, False),
        (None, False),
    ],
)
async def test_is_workflow_cancelled_reads_task_tracking(monkeypatch, row, expected):
    from app.agent_framework import process_runner as pr
    from app.db import session as db_session

    sink: list[str] = []
    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope(row, sink))
    assert await pr.is_workflow_cancelled("wf-x") is expected
    (sql,) = sink
    assert "task_tracking" in sql
    assert "dbos_workflow_id = 'wf-x'" in sql


async def test_is_workflow_cancelled_fails_open_and_logs(monkeypatch):
    """A DB hiccup must not kill a healthy child; the deadline still bounds it."""
    import contextlib

    from app.agent_framework import process_runner as pr
    from app.db import session as db_session

    @contextlib.asynccontextmanager
    async def boom(*a, **kw):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(db_session, "read_scope", boom)
    assert await pr.is_workflow_cancelled("wf-x") is False
