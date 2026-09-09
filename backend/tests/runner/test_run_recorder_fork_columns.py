"""RunRecorder writes fork_of_run_id / fork_at_seq when given (mig 453 columns);
an ordinary run's INSERT does not mention them (phase 2b-1 §2.3)."""

import contextlib
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


def _write_scope(captured: dict, first):
    class _R:
        def first(self):
            return first

    class _S:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _R()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    return _ws


async def _noop():
    return None


async def test_insert_row_carries_fork_columns(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("app.db.session.write_scope", _write_scope(captured, (777,)))
    rec = RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="issue_dispatch",
        fork_of_run_id=42,
        fork_at_seq=7,
    )
    monkeypatch.setattr(rec, "_link_task", _noop)
    await rec._insert_row()
    assert "fork_of_run_id" in captured["sql"] and "fork_at_seq" in captured["sql"]
    assert captured["params"]["fork_of_run_id"] == 42
    assert captured["params"]["fork_at_seq"] == 7
    assert rec.run_id == "777"


async def test_insert_row_omits_fork_columns_when_not_forked(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("app.db.session.write_scope", _write_scope(captured, None))
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    await rec._insert_row()
    assert "fork_of_run_id" not in captured["sql"]
    assert "fork_at_seq" not in captured["sql"]
