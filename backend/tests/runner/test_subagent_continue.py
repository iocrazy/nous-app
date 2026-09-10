"""``Skill(skill="task", child_run_id=…)`` — continue an earlier sub-run.

Two things have to hold: the named run really is this parent's child (a
model-supplied id must not reach another user's transcript), and the history
handed to the new turn is the child's own, rebuilt from its events.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService

pytestmark = pytest.mark.unit


def _svc(**kw):
    base = dict(caller_agent_id=uuid4(), caller_user_id=uuid4(), parent_run_id="900")
    return SubAgentTaskService(**{**base, **kw})


async def _ok(value):
    return value


async def test_a_child_that_is_not_ours_is_refused(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_child_chain_ok", lambda cid: _ok(False))
    out = await svc.spawn(
        {"subagent_type": "a", "prompt": "more", "child_run_id": "51"}
    )
    assert out["status"] == "failed" and out["error"] == "not_your_child"


async def test_continue_messages_rebuild_history_then_append_the_prompt(monkeypatch):
    events = [
        {"seq": 1, "event_type": "user", "payload": {"content": "first"}},
        {"seq": 2, "event_type": "tool_call", "payload": {"tool": "Skill"}},
        {"seq": 3, "event_type": "assistant", "payload": {"content": "answer"}},
        {"seq": 9, "event_type": "assistant", "payload": {"content": "after cut"}},
    ]
    svc = _svc()
    monkeypatch.setattr(svc, "_load_child_events", lambda rid: _ok((events, 3)))

    msgs, last_seq = await svc._continue_messages("51", "keep going")

    assert last_seq == 3
    # seq 9 proves the slice is by SEQ, tool_call proves tool rows never
    # enter cross-turn history.
    assert msgs == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "keep going"},
    ]


async def test_round_increments_from_the_continued_run(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_load_child_metadata", lambda rid: _ok({"round": 3}))
    assert await svc._round_of("51") == 4


async def test_round_defaults_to_two_when_the_run_never_recorded_one(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_load_child_metadata", lambda rid: _ok({}))
    assert await svc._round_of("51") == 2
