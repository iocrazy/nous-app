"""AgentRunsRepository.list_transcript_events / list_forks (phase 2b-1) — the
compiled SQL each read emits, off a stubbed read_scope."""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from app.repositories.agent_runs_repository import AgentRunsRepository

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, rows):
        self.stmts, self._rows = [], rows

    async def execute(self, stmt):
        self.stmts.append(stmt)
        rows = self._rows

        class _R:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _R()


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


def _binds(stmt) -> dict:
    return dict(stmt.compile().params)


async def test_list_transcript_events_binds_run_upto_and_types_and_normalises_rows():
    sess = _Session([{"seq": 3, "event_type": "user", "payload": {"content": "x"}}])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().list_transcript_events(
            42, upto_seq=9, event_types=["user", "fork"]
        )
    assert out == [{"seq": 3, "event_type": "user", "payload": {"content": "x"}}]
    binds = _binds(sess.stmts[0])
    assert binds["run_id_1"] == 42 and binds["seq_1"] == 9
    in_values = [v for k, v in binds.items() if k.startswith("event_type_1")]
    assert in_values and sorted(in_values[0]) == ["fork", "user"]
    assert "ORDER BY" in str(sess.stmts[0]) and "seq ASC" in str(sess.stmts[0])


async def test_list_transcript_events_without_filters_has_only_the_run_bind():
    sess = _Session([])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        assert await AgentRunsRepository().list_transcript_events(42) == []
    assert _binds(sess.stmts[0]) == {"run_id_1": 42}


async def test_list_forks_filters_on_fork_of_run_id_oldest_first():
    sess = _Session([{"id": 7, "fork_at_seq": 4, "created_at": "t", "status": "s"}])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().list_forks(42)
    assert out == [{"id": 7, "fork_at_seq": 4, "created_at": "t", "status": "s"}]
    sql = str(sess.stmts[0])
    assert "fork_of_run_id = " in sql and _binds(sess.stmts[0]) == {
        "fork_of_run_id_1": 42
    }
    assert "ORDER BY public.agent_runs.created_at ASC" in sql
