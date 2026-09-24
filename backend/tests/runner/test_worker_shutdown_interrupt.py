"""Graceful worker shutdown closes the runs that are still in flight.

Before this, a SIGTERM abandoned every live ``RunRecorder``: its row stayed
``running`` with a frozen heartbeat until the sweeper flipped it two minutes
later (prod issue 352662630815921: ``heartbeat_at == started_at``, closed
2m05s after the deploy). ``interrupt_inflight_runs`` runs from
``shutdown_all`` BEFORE ``shutdown_dbos`` and closes them the way the sweeper
would, with ``error_code='worker_shutdown'`` so the two causes stay apart.
"""

from __future__ import annotations

import asyncio
import inspect
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.runner import live_runs
from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


def _recorder(run_id: str) -> RunRecorder:
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="issue_dispatch")
    rec.run_id = run_id
    return rec


async def _forever() -> None:
    await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def _empty_registry():
    live_runs.clear_for_tests()
    yield
    live_runs.clear_for_tests()


@pytest.fixture
def closers(monkeypatch):
    """Spy the three closing writes; the repository flip reports every id."""
    calls: dict[str, list] = {"flip": [], "close": [], "settle": []}

    class _Repo:
        async def mark_worker_shutdown_ids(self, run_ids):
            calls["flip"].append(sorted(run_ids))
            return sorted(run_ids)

    async def _close(run_id, *, detail=None):
        calls["close"].append((run_id, detail))
        return True

    async def _settle(*, run_id, force_stale_pending=False):
        calls["settle"].append(run_id)

    import app.repositories.agent_runs_repository as repo_mod
    import app.services.ai.billing.tree_charge as tc
    import app.services.ai.runner.interrupted_turn as it

    monkeypatch.setattr(repo_mod, "get_agent_runs_repository", lambda: _Repo())
    monkeypatch.setattr(it, "close_interrupted_run", _close)
    monkeypatch.setattr(tc, "settle_tree_if_closed", _settle)
    return calls


async def test_two_running_runs_are_closed_as_worker_shutdown(closers):
    a, b = _recorder("101"), _recorder("202")
    a._heartbeat_task = asyncio.get_running_loop().create_task(_forever())
    b._heartbeat_task = asyncio.get_running_loop().create_task(_forever())
    beat_a, beat_b = a._heartbeat_task, b._heartbeat_task
    live_runs.register(a)
    live_runs.register(b)

    closed = await live_runs.interrupt_inflight_runs()

    assert sorted(closed) == [101, 202]
    assert closers["flip"] == [[101, 202]]
    assert sorted(closers["close"]) == [
        (101, "worker_shutdown"),
        (202, "worker_shutdown"),
    ]
    assert sorted(closers["settle"]) == ["101", "202"]
    # heartbeats are stopped, so nothing re-freshens a row we just closed
    assert beat_a.cancelled() and beat_b.cancelled()
    assert a._heartbeat_task is None and b._heartbeat_task is None


async def test_only_the_rows_the_flip_actually_took_are_closed(closers, monkeypatch):
    """A run that finished on its own between the snapshot and the flip is not
    ``running`` any more; the guarded UPDATE skips it, and so must the close."""

    class _Repo:
        async def mark_worker_shutdown_ids(self, run_ids):
            return [101]

    import app.repositories.agent_runs_repository as repo_mod

    monkeypatch.setattr(repo_mod, "get_agent_runs_repository", lambda: _Repo())
    live_runs.register(_recorder("101"))
    live_runs.register(_recorder("202"))

    assert await live_runs.interrupt_inflight_runs() == [101]
    assert closers["close"] == [(101, "worker_shutdown")]
    assert closers["settle"] == ["101"]


async def test_nothing_in_flight_writes_nothing(closers):
    assert await live_runs.interrupt_inflight_runs() == []
    assert closers == {"flip": [], "close": [], "settle": []}


async def test_a_hung_flip_is_bounded_and_never_raises(monkeypatch):
    class _Repo:
        async def mark_worker_shutdown_ids(self, run_ids):
            await asyncio.sleep(3600)

    import app.repositories.agent_runs_repository as repo_mod

    monkeypatch.setattr(repo_mod, "get_agent_runs_repository", lambda: _Repo())
    live_runs.register(_recorder("101"))
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await live_runs.interrupt_inflight_runs(timeout_s=0.05) == []
    assert loop.time() - started < 1.0


async def test_a_failing_flip_is_logged_not_raised(monkeypatch):
    class _Repo:
        async def mark_worker_shutdown_ids(self, run_ids):
            raise RuntimeError("db gone")

    import app.repositories.agent_runs_repository as repo_mod

    monkeypatch.setattr(repo_mod, "get_agent_runs_repository", lambda: _Repo())
    live_runs.register(_recorder("101"))
    assert await live_runs.interrupt_inflight_runs() == []


async def test_one_failing_close_does_not_starve_the_rest(closers, monkeypatch):
    import app.services.ai.runner.interrupted_turn as it

    async def _close(run_id, *, detail=None):
        if run_id == 101:
            raise RuntimeError("boom")
        closers["close"].append((run_id, detail))
        return True

    monkeypatch.setattr(it, "close_interrupted_run", _close)
    live_runs.register(_recorder("101"))
    live_runs.register(_recorder("202"))
    assert sorted(await live_runs.interrupt_inflight_runs()) == [101, 202]
    assert closers["close"] == [(202, "worker_shutdown")]
    assert sorted(closers["settle"]) == ["101", "202"]


async def test_recorder_registers_on_enter_and_unregisters_on_exit(monkeypatch):
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")

    async def _start_once(self):
        self.run_id = "303"

    async def _finish(self, **kw):
        assert live_runs.live_recorders() == (self,)  # still live while closing

    monkeypatch.setattr(RunRecorder, "_start_once", _start_once)
    monkeypatch.setattr(RunRecorder, "_finish", _finish)
    monkeypatch.setattr(RunRecorder, "_maybe_export_langfuse", lambda self, **k: None)

    async with rec:
        assert live_runs.live_recorders() == (rec,)
    assert live_runs.live_recorders() == ()


async def test_a_failed_finish_still_unregisters(monkeypatch):
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")

    async def _start_once(self):
        self.run_id = "304"

    async def _finish(self, **kw):
        raise RuntimeError("finish blew up")

    monkeypatch.setattr(RunRecorder, "_start_once", _start_once)
    monkeypatch.setattr(RunRecorder, "_finish", _finish)
    monkeypatch.setattr(RunRecorder, "_maybe_export_langfuse", lambda self, **k: None)
    async with rec:
        pass
    assert live_runs.live_recorders() == ()


async def test_a_run_whose_row_never_landed_is_not_registered(monkeypatch):
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")

    async def _start_once(self):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(RunRecorder, "_start_once", _start_once)
    monkeypatch.setattr(RunRecorder, "_maybe_export_langfuse", lambda self, **k: None)
    async with rec:
        assert live_runs.live_recorders() == ()


def test_worker_shutdown_flip_statement_shape():
    from app.repositories.agent_runs_repository import worker_shutdown_stmt

    sql = str(
        worker_shutdown_stmt([101, 202]).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "UPDATE public.agent_runs" in sql
    assert "agent_runs.id IN (101, 202)" in sql
    assert "agent_runs.status = 'running'" in sql  # a finished run is never re-closed
    assert "status='heartbeat_lost'" in sql
    assert "error_code='worker_shutdown'" in sql
    assert "turn_end_reason='heartbeat_lost'" in sql
    assert "RETURNING public.agent_runs.id" in sql


async def test_shutdown_all_interrupts_runs_before_draining_dbos(monkeypatch):
    from app.startup import teardown

    order: list[str] = []

    async def _noop(*a, **k):
        return None

    async def _interrupt(*a, **k):
        order.append("interrupt")
        return []

    async def _dbos(*a, **k):
        order.append("shutdown_dbos")

    for name in (
        "stop_bounds_heartbeat",
        "stop_lifecycle_bus",
        "stop_prometheus_pusher",
        "stop_ssrf_proxy",
        "close_async_redis",
    ):
        monkeypatch.setattr(teardown, name, _noop)
    monkeypatch.setattr(teardown, "interrupt_inflight_runs", _interrupt)
    monkeypatch.setattr(teardown, "shutdown_dbos", _dbos)
    monkeypatch.setattr(
        teardown,
        "DrissionPageParser",
        lambda: type("P", (), {"close": lambda s: None})(),
    )
    import app.db.engine as eng
    import app.db.session as sess

    monkeypatch.setattr(eng, "dispose_engine", _noop)
    monkeypatch.setattr(sess, "dispose_sessionmaker", lambda: None)

    app = type("A", (), {"state": type("S", (), {})()})()
    await teardown.shutdown_all(app)
    assert order == ["interrupt", "shutdown_dbos"]


def test_interrupt_inflight_runs_is_not_a_dbos_step():
    fn = live_runs.interrupt_inflight_runs
    assert not hasattr(fn, "dbos_function_name")
    assert inspect.unwrap(fn) is fn


async def test_a_hung_close_after_the_flip_logs_the_unclosed_ids(closers, monkeypatch):
    """Flipped rows are no longer ``running``, so the sweeper never revisits
    them; a close that runs out of budget must name them, not claim the
    sweeper will pick them up."""
    from loguru import logger

    import app.services.ai.runner.interrupted_turn as it

    async def _close(run_id, *, detail=None):
        if run_id == 202:
            await asyncio.sleep(3600)
        closers["close"].append((run_id, detail))
        return True

    monkeypatch.setattr(it, "close_interrupted_run", _close)
    live_runs.register(_recorder("101"))
    live_runs.register(_recorder("202"))
    live_runs.register(_recorder("303"))
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="ERROR")
    try:
        closed = await live_runs.interrupt_inflight_runs(timeout_s=0.2)
    finally:
        logger.remove(sink)

    assert closed == [101, 202, 303]  # all three were flipped
    assert closers["close"] == [(101, "worker_shutdown")]
    unclosed = [line for line in lines if "not closed" in line]
    assert unclosed and "[202, 303]" in unclosed[0]
    assert not any("sweeper will close" in line for line in unclosed)
