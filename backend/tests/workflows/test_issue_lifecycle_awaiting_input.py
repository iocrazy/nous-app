"""Phase 2a Task 3: an AskUser-parked turn (or a FinishIssue needs_input with
options) parks the issue with a typed question in the marker."""

from unittest.mock import AsyncMock

import pytest

from app.workflows import issue_lifecycle as il

pytestmark = pytest.mark.unit


def _load_issue(status="in_progress"):
    async def load(issue_id):
        return {"id": issue_id, "status": status}

    return load


async def test_awaiting_input_result_parks_with_question_marker(monkeypatch):
    marks = []
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append((status, kw.get("agent_outcome")))

    async def mark_waiting(issue_id, prompt, *, question=None):
        marks.append((prompt, question))

    async def wait_for_input(issue_id, *, ttl_seconds):
        return None  # timeout → stays parked

    async def clear_waiting(issue_id):
        pass

    async def run_reply(issue_id, payload):
        raise AssertionError("no reply expected")

    qd = {
        "question_id": "q:9:2",
        "kind": "user",
        "prompt": "Which ending?",
        "options": [{"label": "A", "description": None}],
        "allow_free_text": True,
        "asked_at": "2026-09-08T00:00:00Z",
    }

    async def run_turn(*a, **k):
        return {
            "content": "",
            "outcome": "needs_input",
            "reason": "Which ending?",
            "run_id": "9",
            "awaiting_input": True,
            "question": qd,
        }

    monkeypatch.setattr(il, "_backfill_run_issue_id", AsyncMock())
    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(),
        wait_for_input=wait_for_input,
        mark_waiting=mark_waiting,
        clear_waiting=clear_waiting,
        run_reply=run_reply,
    )
    assert res["outcome"] == "needs_input"
    assert marks == [("Which ending?", {**qd, "run_id": "9"})]
    assert ("needs_followup", "needs_input") in statuses


async def test_finish_issue_options_become_a_question_and_a_question_asked_event(
    monkeypatch,
):
    marks = []
    appended = []

    class _Writer:
        def __init__(self, run_id):
            self.run_id = run_id

        async def append(self, event_type, payload, *, turn=None, step=None):
            appended.append((self.run_id, event_type, payload))
            return 5

    async def _for_run(run_id):
        return _Writer(run_id)

    monkeypatch.setattr(il, "_event_writer_for_run", _for_run)
    monkeypatch.setattr(il, "_backfill_run_issue_id", AsyncMock())

    async def set_status(issue_id, status, **kw):
        pass

    async def mark_waiting(issue_id, prompt, *, question=None):
        marks.append((prompt, question))

    async def wait_for_input(issue_id, *, ttl_seconds):
        return None

    async def clear_waiting(issue_id):
        pass

    async def run_reply(issue_id, payload):
        raise AssertionError("no reply expected")

    async def run_turn(*a, **k):
        return {
            "content": "I need a decision.",
            "outcome": "needs_input",
            "reason": "Which one?",
            "run_id": "11",
            "options": [{"label": "Left"}, {"label": "Right", "description": "d"}],
        }

    await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(),
        wait_for_input=wait_for_input,
        mark_waiting=mark_waiting,
        clear_waiting=clear_waiting,
        run_reply=run_reply,
    )
    prompt, question = marks[0]
    assert prompt == "Which one?"
    assert question["question_id"] == "q:11:0" and question["run_id"] == "11"
    assert [o["label"] for o in question["options"]] == ["Left", "Right"]
    assert question["allow_free_text"] is True
    assert appended and appended[0][:2] == ("11", "question_asked")
    assert appended[0][2]["question_id"] == "q:11:0"


async def test_plain_needs_input_without_options_keeps_the_old_marker_call():
    """No question → mark_waiting is called exactly as before (2-arg form), so
    the pre-2a wiring and its tests keep working."""
    marks = []

    async def set_status(issue_id, status, **kw):
        pass

    async def mark_waiting(issue_id, prompt):  # old signature, no kwarg
        marks.append(prompt)

    async def wait_for_input(issue_id, *, ttl_seconds):
        return None

    async def clear_waiting(issue_id):
        pass

    async def run_reply(issue_id, payload):
        raise AssertionError

    async def run_turn(*a, **k):
        return {"content": "x", "outcome": "needs_input", "reason": "why?"}

    await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(),
        wait_for_input=wait_for_input,
        mark_waiting=mark_waiting,
        clear_waiting=clear_waiting,
        run_reply=run_reply,
    )
    assert marks == ["why?"]
