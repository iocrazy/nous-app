"""A step re-executed by DBOS recovery opens a run that points at its predecessor.

Prod issue 352662630815921: the killed attempt's row (352662637910207) and the
recovery's row (352662719416522) had nothing linking them — two roots, two
bills, no way to read "this is the same turn, retried". RunRecorder now stamps
``metadata_json.dbos_step_key`` on every issue-turn row; a second row with the
same key gets ``recovered_from`` and the old row gets ``superseded_by``.
Status is untouched (the old row still closes through heartbeat_lost), and
per ruling 1 both runs keep their real spend.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.runner import step_recovery
from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


def _rec(**kw) -> RunRecorder:
    return RunRecorder(
        agent_id=uuid4(), user_id=uuid4(), trigger="issue_dispatch", issue_id=9, **kw
    )


@pytest.fixture
def db(monkeypatch):
    """Stub the pre-flight + insert; spy the recovery lookup and backlink."""
    state: dict = {"prior": None, "stamped": [], "inserted_meta": None, "lookups": []}

    async def _noop(self):
        return None

    async def _insert(self):
        state["inserted_meta"] = dict(self.metadata)
        self.run_id = "777"

    async def _find(step_key, *, issue_id, user_id):
        state["lookups"].append((step_key, issue_id))
        return state["prior"]

    async def _stamp(old_id, new_id):
        state["stamped"].append((old_id, new_id))

    monkeypatch.setattr(RunRecorder, "_pre_flight_check_paused", _noop)
    monkeypatch.setattr(RunRecorder, "_snapshot_price", _noop)
    monkeypatch.setattr(RunRecorder, "_insert_row", _insert)
    monkeypatch.setattr(step_recovery, "find_prior_run", _find)
    monkeypatch.setattr(step_recovery, "stamp_superseded", _stamp)
    return state


async def test_first_execution_stamps_the_key_only(db):
    rec = _rec(dbos_step_key="wf:7", metadata={"full_input": "Task: x"})
    await rec._start_once()
    assert db["inserted_meta"] == {"full_input": "Task: x", "dbos_step_key": "wf:7"}
    assert db["lookups"] == [("wf:7", 9)]
    assert db["stamped"] == []


async def test_reexecution_links_both_rows(db):
    db["prior"] = {"id": 555, "status": "running"}
    rec = _rec(dbos_step_key="wf:7", metadata={"full_input": "Task: x"})
    await rec._start_once()
    assert db["inserted_meta"]["recovered_from"] == "555"
    assert db["inserted_meta"]["dbos_step_key"] == "wf:7"
    assert db["stamped"] == [(555, "777")]


async def test_an_already_closed_predecessor_is_still_linked(db):
    """With graceful shutdown the old row is normally heartbeat_lost by the time
    recovery runs; the backlink is written anyway (metadata only)."""
    db["prior"] = {"id": 555, "status": "heartbeat_lost"}
    await _rec(dbos_step_key="wf:7")._start_once()
    assert db["stamped"] == [(555, "777")]


async def test_no_key_is_todays_row(db):
    rec = _rec(metadata={"full_input": "hi"})
    await rec._start_once()
    assert db["inserted_meta"] == {"full_input": "hi"}
    assert db["lookups"] == []


async def test_a_failed_lookup_still_opens_the_run(db, monkeypatch):
    async def _boom(step_key, *, issue_id, user_id):
        raise RuntimeError("db blip")

    monkeypatch.setattr(step_recovery, "find_prior_run", _boom)
    rec = _rec(dbos_step_key="wf:7")
    await rec._start_once()
    assert rec.run_id == "777"
    assert db["inserted_meta"] == {"dbos_step_key": "wf:7"}


async def test_caller_metadata_dict_is_not_mutated(db):
    db["prior"] = {"id": 555, "status": "running"}
    caller_meta = {"full_input": "Task: x"}
    await _rec(dbos_step_key="wf:7", metadata=caller_meta)._start_once()
    assert caller_meta == {"full_input": "Task: x"}


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_prior_run_lookup_statement():
    sql = _sql(step_recovery.prior_run_stmt("wf:7", issue_id=9, user_id=None))
    assert "agent_runs.metadata_json ->> CAST('dbos_step_key' AS TEXT)) = 'wf:7'" in sql
    assert "agent_runs.issue_id = 9" in sql  # rides idx_agent_runs_issue
    assert "ORDER BY public.agent_runs.started_at DESC" in sql
    assert "LIMIT 1" in sql


def test_supersede_statement_merges_not_replaces():
    sql = _sql(step_recovery.supersede_stmt(555, "777"))
    assert "UPDATE public.agent_runs SET metadata_json=" in sql
    assert "||" in sql and "superseded_by" in sql
    assert "agent_runs.id = 555" in sql
    assert "status" not in sql.split("WHERE")[0]  # status left to heartbeat_lost


@pytest.mark.parametrize("name", ["find_prior_run", "stamp_superseded"])
def test_helpers_are_not_dbos_steps(name):
    fn = getattr(step_recovery, name)
    assert not hasattr(fn, "dbos_function_name")
    assert inspect.unwrap(fn) is fn
