"""A forked turn (phase 2b-1 §2.3): RunRecorder gets the fork columns and the
run's FIRST recorded event is fork{of_run_id, at_seq, steer}."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat import ai_library_chat_service as svc_mod
from tests.test_parity_gap_coverage import (
    _chat_env,
    _FakeStore,
    _RunRecorderCM,
    _session_row,
)

pytestmark = pytest.mark.unit


async def _turn(fork_of, fork_steer, seen, recorder):
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as env_recorder:
        env_recorder.record_event = recorder.record_event

        def _rr(**kw):
            seen.update(kw)
            return _RunRecorderCM(env_recorder)

        with patch.object(svc_mod, "RunRecorder", side_effect=_rr):
            await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="go",
                trigger="issue_dispatch",
                fork_of=fork_of,
                fork_steer=fork_steer,
            )


class _Rec:
    def __init__(self):
        self.events = []
        self.record_event = AsyncMock(side_effect=self._record)

    async def _record(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))


async def test_fork_of_reaches_recorder_and_emits_fork_first():
    seen: dict = {}
    rec = _Rec()
    await _turn((42, 7), True, seen, rec)
    assert seen["fork_of_run_id"] == 42 and seen["fork_at_seq"] == 7
    assert rec.events and rec.events[0] == (
        "fork",
        {"of_run_id": 42, "at_seq": 7, "steer": True},
    )


async def test_plain_turn_has_no_fork_columns_and_no_fork_event():
    seen: dict = {}
    rec = _Rec()
    await _turn(None, False, seen, rec)
    assert seen.get("fork_of_run_id") is None and seen.get("fork_at_seq") is None
    assert not any(t == "fork" for t, _ in rec.events)
