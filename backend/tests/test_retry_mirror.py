"""``llm_retry`` events mirror ``metadata_json.last_retry`` so the Task Center
can render "Retry 2/4 · waiting 3.2s" off the agent_runs row it already has."""

import contextlib

import pytest

pytestmark = pytest.mark.unit


async def test_llm_retry_mirrors_last_retry(monkeypatch):
    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    executed = []

    class _S:
        async def execute(self, stmt, *a, **k):
            from sqlalchemy.dialects import postgresql

            c = stmt.compile(dialect=postgresql.dialect())  # the real target
            executed.append((str(c), dict(getattr(c, "params", {}))))

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = 7
    rec._event_seq = 0
    await rec.record_event(
        "llm_retry",
        {
            "attempt": 2,
            "max_retries": 4,
            "model": "m",
            "delay_ms": 3200,
            "policy_key": "k",
            "failure": "x" * 300,
            "at": "2026-09-05T00:00:00+00:00",  # stamped by make_retry_observer
        },
    )
    mirrors = [(s, p) for s, p in executed if "jsonb_set" in s]
    assert len(mirrors) == 1, executed
    sql, params = mirrors[0]
    assert "last_retry" in params.values()
    # jsonb_set(jsonb, text[], jsonb, bool): a bare string binds as varchar
    # and PG finds no matching function. Seen live 2026-08-27 — the mock
    # boundary hid it. The path must be cast to text[].
    assert "AS TEXT[]" in sql, sql
    blob = next(v for v in params.values() if isinstance(v, str) and "attempt" in v)
    assert '"attempt": 2' in blob and '"delay_ms": 3200' in blob
    assert (
        "policy_key" not in blob and "failure" not in blob
    ), "only what the card renders"
    assert '"at": "20' in blob, "the card needs a timestamp to age the wait"
