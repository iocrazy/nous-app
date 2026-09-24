"""A re-executed issue turn does not append the task text a second time.

Prod conversation 352662635985085 holds the identical ``Task: Probe
2026-09-23 S7b…`` user row at 12:27:56.449 AND 12:28:16.103: DBOS recovery
re-ran ``run_issue_agent_step`` and ``run_session_turn`` appended again. The
user message now carries the step key; on a re-execution the turn finds it in
history, reuses it, and the model sees the task once.
"""

from unittest.mock import patch
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


class _HistoryStore(_FakeStore):
    def __init__(self, session_row, history):
        super().__init__(session_row)
        self._history = history

    @property
    def user_rows(self):
        """``appended`` also records the assistant reply; only user rows count."""
        return [r for r in self.appended if r["role"] == "user"]

    async def get_messages(self, *, session_id, limit=200, newest=False):
        return [dict(m) for m in self._history]


async def _turn(history, **turn_kwargs):
    user_id, agent_id = uuid4(), uuid4()
    store = _HistoryStore(_session_row(user_id, agent_id), history)
    seen: dict = {}
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as env_recorder:

        def _rr(**kw):
            seen.update(kw)
            return _RunRecorderCM(env_recorder)

        with patch.object(svc_mod, "RunRecorder", side_effect=_rr):
            out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="Task: probe",
                trigger="issue_dispatch",
                issue_id=9,
                **turn_kwargs,
            )
        runner = svc_mod.build_agent_runner_stack.return_value.runner
        sent = runner.run_turn.await_args.kwargs.get("user_messages")
        if sent is None:
            sent = runner.run_turn.await_args.args[1]
    return store, seen, out, sent


def _user(content, *, key=None, mid=1):
    return {
        "id": mid,
        "role": "user",
        "content": content,
        "metadata_json": {"dbos_step_key": key} if key else None,
    }


def _task_count(sent):
    return sum(1 for m in sent if m.get("role") == "user" and "Task: probe" in str(m))


async def test_first_execution_stamps_the_key_on_the_user_message():
    store, seen, _, sent = await _turn([], dbos_step_key="wf:7")
    assert len(store.user_rows) == 1
    assert store.user_rows[0]["metadata"] == {"dbos_step_key": "wf:7"}
    assert seen["dbos_step_key"] == "wf:7"
    assert _task_count(sent) == 1


async def test_reexecution_reuses_the_message_and_the_model_sees_it_once():
    prior = [
        _user("earlier question", mid=1),
        {"id": 2, "role": "assistant", "content": "earlier answer"},
        _user("Task: probe", key="wf:7", mid=3),
    ]
    store, seen, out, sent = await _turn(prior, dbos_step_key="wf:7")
    assert store.user_rows == []  # no second copy of the task
    assert out["user_message"]["id"] == 3
    assert seen["dbos_step_key"] == "wf:7"
    assert _task_count(sent) == 1
    assert any("earlier answer" in str(m) for m in sent)  # older history kept


async def test_a_different_step_key_is_a_new_turn():
    prior = [_user("Task: probe", key="wf:5", mid=3)]
    store, _, _, _ = await _turn(prior, dbos_step_key="wf:7")
    assert len(store.user_rows) == 1


async def test_no_key_keeps_todays_message_shape():
    """Negative control: plain turns stay metadata-free, and an old keyed
    message in history does not suppress the append."""
    prior = [_user("Task: probe", key="wf:7", mid=3)]
    store, seen, _, _ = await _turn(prior)
    assert len(store.user_rows) == 1
    assert store.user_rows[0]["metadata"] is None
    assert seen["dbos_step_key"] is None


async def test_the_key_rides_along_with_a_message_source():
    source = {"kind": "schedule", "schedule_id": 1, "created_by": "agent"}
    store, _, _, _ = await _turn([], dbos_step_key="wf:7", message_source=source)
    assert store.user_rows[0]["metadata"] == {"source": source, "dbos_step_key": "wf:7"}
