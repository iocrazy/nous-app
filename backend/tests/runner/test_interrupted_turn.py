"""The sweeper closes a crashed run's transcript with ``turn_end
interrupted`` through the same writer the runner uses — idempotently."""

from __future__ import annotations

import contextlib

import pytest

from app.services.ai.runner import interrupted_turn as it


class _Row:
    def __init__(self, last_seq, ends):
        self._v = (last_seq, ends)

    def one(self):
        return self._v


def _patch_db(monkeypatch, *, last_seq, has_end, executed):
    from app.db import session as dbs

    class _Read:
        async def execute(self, stmt):
            return _Row(last_seq, 1 if has_end else 0)

    class _Write:
        async def execute(self, stmt):
            executed.append(str(stmt))

    @contextlib.asynccontextmanager
    async def _rs():
        yield _Read()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _Write()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    monkeypatch.setattr(dbs, "write_scope", _ws)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_appends_interrupted_turn_end_after_the_last_seq(monkeypatch):
    executed = []
    _patch_db(monkeypatch, last_seq=7, has_end=False, executed=executed)
    captured = {}

    from app.services.ai.runner import run_recorder as rr

    orig_append = rr.RunEventWriter.append

    async def _spy(self, event_type, payload, *, turn=None, step=None):
        captured.update(
            seq_before=self.seq, event_type=event_type, payload=payload, turn=turn
        )
        return await orig_append(self, event_type, payload, turn=turn, step=step)

    monkeypatch.setattr(rr.RunEventWriter, "append", _spy)
    assert await it.close_interrupted_run(11, detail="heartbeat_lost") is True
    assert captured["seq_before"] == 7 and captured["event_type"] == "turn_end"
    assert captured["payload"] == {"reason": "interrupted", "detail": "heartbeat_lost"}
    assert captured["turn"] == 1
    assert any("agent_run_transcript_events" in s for s in executed)
    assert any("jsonb_set" in s for s in executed)  # view.ended mirrored


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skips_a_run_that_already_ended(monkeypatch):
    executed = []
    _patch_db(monkeypatch, last_seq=3, has_end=True, executed=executed)
    assert await it.close_interrupted_run(11) is False
    assert executed == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_bad_run_does_not_block_the_rest(monkeypatch):
    calls = []

    async def _fake(run_id, *, detail=None):
        calls.append(run_id)
        if run_id == 2:
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr(it, "close_interrupted_run", _fake)
    assert await it.close_interrupted_runs([1, 2, 3]) == 2
    assert calls == [1, 2, 3]


@pytest.mark.unit
def test_interrupted_is_a_known_turn_end_reason():
    from app.services.ai.runner.turn_end import TurnEndReason

    assert TurnEndReason.INTERRUPTED.value == "interrupted"
    # and the fold treats it as ended (not paused / waiting)
    from app.services.ai.runner import run_projection as rp

    v = rp.apply(rp.empty_views(), "turn_end", {"reason": "interrupted"}, seq=1)
    assert (
        v["view"]["phase"] == "ended" and v["view"]["ended"]["reason"] == "interrupted"
    )
