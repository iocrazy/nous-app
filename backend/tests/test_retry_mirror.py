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
            c = stmt.compile()
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
        },
    )
    mirrors = [(s, p) for s, p in executed if "jsonb_set" in s]
    assert len(mirrors) == 1, executed
    sql, params = mirrors[0]
    assert "{last_retry}" in params.values()
    blob = next(v for v in params.values() if isinstance(v, str) and "attempt" in v)
    assert '"attempt": 2' in blob and '"delay_ms": 3200' in blob
    assert (
        "policy_key" not in blob and "failure" not in blob
    ), "only what the card renders"
