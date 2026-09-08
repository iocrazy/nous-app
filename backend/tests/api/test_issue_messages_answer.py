"""Phase 2a Task 3: answering a typed question is a normal comment carrying
``answer_to`` — label-checked, ``question_answered`` written to the asking
run, ``on_answer`` for the kind, then the existing wake path."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.schemas.issue_message import IssueMessagePost

pytestmark = pytest.mark.unit


def _router():
    importlib.import_module("app.api.issue_messages_router")
    return sys.modules["app.api.issue_messages_router"]


ISSUE = {
    "id": 1,
    "dbos_workflow_id": "wf-1",
    "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "created_by_user_id": "11111111-1111-1111-1111-111111111111",
    "assignee_user_id": None,
}
AUTH = SimpleNamespace(user_id=UUID("11111111-1111-1111-1111-111111111111"))


@pytest.fixture
def patched(monkeypatch):
    r = _router()
    p = SimpleNamespace(
        marker=AsyncMock(
            return_value={
                "question_id": "q:1:2",
                "kind": "user",
                "prompt": "?",
                "options": [{"label": "A"}],
                "allow_free_text": False,
                "run_id": "7",
            }
        ),
        writer=SimpleNamespace(append=AsyncMock(return_value=9)),
        wake=AsyncMock(return_value=True),
        divert=AsyncMock(return_value=None),
        dispatch=MagicMock(),
        mark_answered=AsyncMock(),
        order=[],
    )
    p.wake.side_effect = lambda *a, **k: p.order.append("wake") or True
    p.writer.append.side_effect = lambda *a, **k: p.order.append("append") or 9

    async def _for_run(run_id):
        p.writer.run_id = run_id
        return p.writer

    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=ISSUE))
    monkeypatch.setattr(r, "get_or_create_issue_session", AsyncMock(return_value="55"))
    monkeypatch.setattr(r, "_load_awaiting_marker", p.marker)
    monkeypatch.setattr(r, "_event_writer_for_run", _for_run)
    monkeypatch.setattr(r, "_try_wake_waiting_workflow", p.wake)
    monkeypatch.setattr(r, "_divert_to_inbox_if_running", p.divert)
    monkeypatch.setattr(r, "_dispatch_respond_to_issue_reply", p.dispatch)
    monkeypatch.setattr(r.input_gate, "mark_question_answered", p.mark_answered)
    return p


async def _post(body, answer_to=None):
    r = _router()
    return await r.post_issue_message(
        1, IssueMessagePost(body=body, answer_to=answer_to), AUTH
    )


async def test_answer_to_with_wrong_label_is_400_answer_shape(patched):
    with pytest.raises(HTTPException) as ei:
        await _post("B", answer_to="q:1:2")
    assert ei.value.status_code == 400 and ei.value.detail["code"] == "answer_shape"
    patched.writer.append.assert_not_awaited()
    patched.wake.assert_not_awaited()


async def test_answer_to_unknown_question_is_409(patched):
    with pytest.raises(HTTPException) as ei:
        await _post("A", answer_to="q:1:9")
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "no_open_question"


async def test_answer_to_without_any_marker_is_409(patched):
    patched.marker.return_value = None
    with pytest.raises(HTTPException) as ei:
        await _post("A", answer_to="q:1:2")
    assert ei.value.status_code == 409


async def test_matching_answer_emits_question_answered_then_wakes(patched):
    resp = await _post("A", answer_to="q:1:2")
    assert resp.agent_dispatched is True
    patched.writer.append.assert_awaited_once_with(
        "question_answered",
        {"question_id": "q:1:2", "value": "A", "superseded": False},
        turn=None,
        step=None,
    )
    assert patched.writer.run_id == "7"
    patched.wake.assert_awaited_once()
    # the answer text is what the workflow receives
    assert patched.wake.await_args.args[2] == "A"
    # recorded only AFTER delivery, and the marker is stamped so a retry is a 409
    assert patched.order == ["wake", "append"]
    patched.mark_answered.assert_awaited_once_with(
        workflow_id="wf-1", question_id="q:1:2", value="A"
    )
    patched.divert.assert_not_awaited()  # an answer never goes to the inbox


async def test_answer_already_delivered_is_409_on_retry(patched):
    patched.marker.return_value["answered_at"] = "2026-09-08T00:00:00Z"
    with pytest.raises(HTTPException) as ei:
        await _post("A", answer_to="q:1:2")
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "no_open_question"
    patched.wake.assert_not_awaited()
    # and the bare-label compat path no longer treats it as an answer either
    await _post("A")
    patched.writer.append.assert_not_awaited()


async def test_wake_failure_falls_back_to_dispatch_and_still_records(patched):
    patched.wake.side_effect = None
    patched.wake.return_value = False
    resp = await _post("A", answer_to="q:1:2")
    assert resp.agent_dispatched is True
    patched.dispatch.assert_called_once()
    patched.writer.append.assert_awaited_once()
    patched.mark_answered.assert_awaited_once()


async def test_dispatch_failure_is_500_and_nothing_is_recorded(patched):
    patched.wake.side_effect = None
    patched.wake.return_value = False
    patched.dispatch.side_effect = RuntimeError("dbos down")
    with pytest.raises(HTTPException) as ei:
        await _post("A", answer_to="q:1:2")
    assert ei.value.status_code == 500
    patched.writer.append.assert_not_awaited()
    patched.mark_answered.assert_not_awaited()


async def test_answer_to_on_an_issue_without_agent_is_409_not_a_legacy_comment(
    patched, monkeypatch
):
    r = _router()
    monkeypatch.setattr(
        r,
        "_assert_issue_visible",
        AsyncMock(return_value={**ISSUE, "assignee_agent_id": None}),
    )
    legacy = AsyncMock()
    monkeypatch.setattr(r, "_insert_legacy_comment", legacy)
    with pytest.raises(HTTPException) as ei:
        await _post("A", answer_to="q:1:2")
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "no_open_question"
    legacy.assert_not_awaited()


async def test_plain_comment_equal_to_a_label_counts_as_an_answer(patched):
    """Spec §1 compat: an old client that sends the label as a bare comment."""
    await _post("A")
    patched.writer.append.assert_awaited_once()


async def test_plain_comment_that_is_not_a_label_is_not_an_answer(patched):
    await _post("something else entirely")
    patched.writer.append.assert_not_awaited()
    patched.wake.assert_awaited_once()  # still the ordinary wake path


async def test_free_text_answer_accepted_when_allowed(patched):
    patched.marker.return_value["allow_free_text"] = True
    await _post("my own words", answer_to="q:1:2")
    payload = patched.writer.append.await_args.args[1]
    assert payload["value"] == "my own words"


async def test_on_answer_rejection_maps_to_http(patched, monkeypatch):
    from app.services.ai.runner import question as q

    async def _reject(issue, value, ctx):
        raise q.AnswerRejected(409, "budget_still_exhausted")

    q.register_kind("rejecting_kind", _reject)
    try:
        patched.marker.return_value["kind"] = "rejecting_kind"
        with pytest.raises(HTTPException) as ei:
            await _post("A", answer_to="q:1:2")
    finally:
        q._unregister_kind_for_tests("rejecting_kind")
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "budget_still_exhausted"
    patched.wake.assert_not_awaited()
    patched.writer.append.assert_not_awaited()  # on_answer refused → nothing recorded
