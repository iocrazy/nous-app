"""P1b: per-call agent_cost_events ledger flush."""

from uuid import UUID

import pytest

from app.services.ai.runner.run_recorder import RunRecorder


class _FakeInsert:
    def __init__(self, sink):
        self._sink = sink

    def insert(self, rows):
        self._sink.extend(rows)
        return self

    async def execute(self):
        return None


class _FakeClient:
    def __init__(self):
        self.inserted = []

    def table(self, name):
        assert name == "agent_cost_events"
        return _FakeInsert(self.inserted)


def _rec():
    r = RunRecorder.__new__(RunRecorder)
    r._prompt_tokens = 0
    r._completion_tokens = 0
    r._cached_input_tokens = 0
    r._per_call_usage = []
    r._prompt_rate = 10.0
    r._completion_rate = 30.0
    r._cached_rate = 2.0
    r.run_id = UUID(int=1)
    r.agent_id = UUID(int=2)
    r.user_id = UUID(int=3)
    r.session_id = None
    r.team_id = 42
    r.project_id = None
    r.issue_id = None
    r.provider = "qwen"
    r.model = "qwen-max"
    return r


def test_record_usage_buffers_each_call():
    r = _rec()
    r.record_usage(prompt_tokens=1000, completion_tokens=100, cached_input_tokens=200)
    r.record_usage(prompt_tokens=500, completion_tokens=50)
    assert r._per_call_usage == [
        {"prompt": 1000, "completion": 100, "cached": 200},
        {"prompt": 500, "completion": 50, "cached": 0},
    ]


@pytest.mark.asyncio
async def test_flush_writes_one_row_per_call():
    r = _rec()
    r.record_usage(prompt_tokens=1000, completion_tokens=100, cached_input_tokens=200)
    r.record_usage(prompt_tokens=500, completion_tokens=50)
    client = _FakeClient()
    await r._flush_cost_events(client)

    assert len(client.inserted) == 2
    row0 = client.inserted[0]
    assert row0["run_id"] == str(r.run_id)
    assert row0["team_id"] == 42
    assert row0["input_tokens"] == 1000
    assert row0["cached_input_tokens"] == 200
    assert row0["output_tokens"] == 100
    # billable 800*10/1000 + cached 200*2/1000 + completion 100*30/1000
    assert round(row0["cost_cents"], 4) == round(8.0 + 0.4 + 3.0, 4)


@pytest.mark.asyncio
async def test_flush_noop_when_empty():
    r = _rec()
    client = _FakeClient()
    await r._flush_cost_events(client)
    assert client.inserted == []
