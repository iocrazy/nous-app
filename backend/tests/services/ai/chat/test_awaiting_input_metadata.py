"""Phase 2a Task 4: the chat side of a typed question mirrors awaiting_approval.

The runner parks (``awaiting_input`` + ``question``) → the assistant message
carries ``metadata_json.awaiting_input`` (question payload + run_id) so a
page reload still renders the QuestionCard. Answering is the next user
message with ``answer_to``; a plain next message supersedes the question."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tests.test_parity_gap_coverage import _chat_env, _FakeStore, _session_row

pytestmark = pytest.mark.unit

_SVC = "app.services.ai.chat.ai_library_chat_service"


class _QStore(_FakeStore):
    """FakeStore + the two phase-2a methods."""

    def __init__(self, session_row, open_question: Optional[Dict[str, Any]] = None):
        super().__init__(session_row)
        self.open_question = open_question
        self.answered: List[Dict[str, Any]] = []
        self.lookups = 0

    async def latest_assistant_open_question(self, *, session_id: Any):
        self.lookups += 1
        return self.open_question

    async def mark_question_answered(self, *, message_id, value, superseded=False):
        self.answered.append(
            {"message_id": message_id, "value": value, "superseded": superseded}
        )


QUESTION = {
    "question_id": "q:77:4",
    "kind": "user",
    "prompt": "Which ending?",
    "options": [
        {"label": "Twist", "description": None},
        {"label": "Quiet", "description": None},
    ],
    "allow_free_text": False,
    "asked_at": "2026-09-08T00:00:00Z",
    "run_id": "77",
}
OPEN = {"message_id": 5001, "question": QUESTION}


def _writer_patch(appended: list):
    class _W:
        async def append(self, event_type, payload, *, turn=None, step=None):
            appended.append((event_type, payload))
            return 9

    async def _for_run(run_id):
        appended.append(("_for_run", run_id))
        return _W()

    return patch(f"{_SVC}._event_writer_for_run", new=_for_run)


async def test_awaiting_input_result_lands_in_assistant_metadata():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id))
    q = {k: v for k, v in QUESTION.items() if k != "run_id"}
    with _chat_env(
        run_turn_result={
            "content": "\n\n[awaiting input: Which ending?]",
            "awaiting_input": True,
            "stop_reason": "awaiting_input",
            "question": q,
            "tool_calls": [],
        },
        agent_id=agent_id,
    ) as recorder:
        svc = AILibraryChatService(store=store)
        out = await svc.chat(uuid4(), user_id=user_id, content="write the ending")

    asst = [m for m in store.appended if m["role"] == "assistant"][0]
    meta = asst["metadata_json"]["awaiting_input"]
    assert meta["question_id"] == "q:77:4"
    assert meta["options"][0]["label"] == "Twist"
    assert meta["run_id"] == str(recorder.run_id)
    assert out["awaiting_input"] is True and out["question"]["question_id"] == "q:77:4"


async def test_answer_to_with_matching_label_records_and_runs_the_turn():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id), open_question=OPEN)
    appended: list = []
    with (
        _chat_env(
            run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
        ),
        _writer_patch(appended),
    ):
        svc = AILibraryChatService(store=store)
        out = await svc.chat(
            uuid4(), user_id=user_id, content="Twist", answer_to="q:77:4"
        )

    assert out["assistant_message"]["content"] == "ok"
    user = [m for m in store.appended if m["role"] == "user"][0]
    assert user["content"] == "Twist"  # the label IS the next user message
    assert appended == [
        ("_for_run", "77"),
        (
            "question_answered",
            {"question_id": "q:77:4", "value": "Twist", "superseded": False},
        ),
    ]
    assert store.answered == [
        {"message_id": 5001, "value": "Twist", "superseded": False}
    ]


async def test_answer_to_with_wrong_label_is_400_and_persists_nothing():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id), open_question=OPEN)
    with _chat_env(run_turn_result={"content": "ok"}, agent_id=agent_id):
        svc = AILibraryChatService(store=store)
        with pytest.raises(HTTPException) as ei:
            await svc.chat(uuid4(), user_id=user_id, content="Sad", answer_to="q:77:4")
    assert ei.value.status_code == 400 and ei.value.detail["code"] == "answer_shape"
    assert store.appended == [] and store.answered == []


async def test_answer_to_without_an_open_question_is_409():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id), open_question=None)
    with _chat_env(run_turn_result={"content": "ok"}, agent_id=agent_id):
        svc = AILibraryChatService(store=store)
        with pytest.raises(HTTPException) as ei:
            await svc.chat(
                uuid4(), user_id=user_id, content="Twist", answer_to="q:77:4"
            )
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "no_open_question"
    # and a stale id against a different open question is a 409 too
    store.open_question = OPEN
    with _chat_env(run_turn_result={"content": "ok"}, agent_id=agent_id):
        svc = AILibraryChatService(store=store)
        with pytest.raises(HTTPException) as ei:
            await svc.chat(
                uuid4(), user_id=user_id, content="Twist", answer_to="q:77:9"
            )
    assert ei.value.status_code == 409


async def test_plain_message_supersedes_the_open_question():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id), open_question=OPEN)
    appended: list = []
    with (
        _chat_env(
            run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
        ),
        _writer_patch(appended),
    ):
        svc = AILibraryChatService(store=store)
        await svc.chat(uuid4(), user_id=user_id, content="forget it, do something else")

    assert appended[-1] == (
        "question_answered",
        {"question_id": "q:77:4", "value": None, "superseded": True},
    )
    assert store.answered == [{"message_id": 5001, "value": None, "superseded": True}]


async def test_plain_message_equal_to_a_label_counts_as_the_answer():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id), open_question=OPEN)
    appended: list = []
    with (
        _chat_env(
            run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
        ),
        _writer_patch(appended),
    ):
        svc = AILibraryChatService(store=store)
        await svc.chat(uuid4(), user_id=user_id, content="Quiet")

    assert appended[-1][1] == {
        "question_id": "q:77:4",
        "value": "Quiet",
        "superseded": False,
    }


async def test_on_answer_rejection_blocks_the_turn():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.runner import question as q

    async def _reject(target, value, ctx):
        raise q.AnswerRejected(409, "budget_still_exhausted")

    q.register_kind("chat_rejecting_kind", _reject)
    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(
        _session_row(user_id, agent_id),
        open_question={
            "message_id": 1,
            "question": {**QUESTION, "kind": "chat_rejecting_kind"},
        },
    )
    try:
        with _chat_env(run_turn_result={"content": "ok"}, agent_id=agent_id):
            svc = AILibraryChatService(store=store)
            with pytest.raises(HTTPException) as ei:
                await svc.chat(
                    uuid4(), user_id=user_id, content="Twist", answer_to="q:77:4"
                )
    finally:
        q._unregister_kind_for_tests("chat_rejecting_kind")
    assert (
        ei.value.status_code == 409
        and ei.value.detail["code"] == "budget_still_exhausted"
    )
    assert store.appended == []


async def test_issue_triggers_leave_the_chat_answer_channel_alone():
    """Issue turns answer through the marker + message endpoint (Task 3);
    running the chat-side detection there would record every answer twice."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id, agent_id = uuid4(), uuid4()
    store = _QStore(_session_row(user_id, agent_id), open_question=OPEN)
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ):
        svc = AILibraryChatService(store=store)
        await svc.run_session_turn(
            uuid4(), user_id=user_id, content="Twist", trigger="issue_reply"
        )
    assert store.lookups == 0 and store.answered == []
