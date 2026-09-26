"""<verifier_feedback>: an owned frame, escaped body, consumed once."""

from unittest.mock import AsyncMock

import pytest

from app.boundary.frame_markers import OWNED_FRAMES
from app.services.issues.verification.feedback import (
    VERIFIER_FEEDBACK_FRAME,
    render_verifier_feedback,
)

pytestmark = pytest.mark.unit


def _fail(**over):
    base = {
        "verdict": "fail",
        "retry": True,
        "attempt": 1,
        "max_attempts": 2,
        "unmet": [{"criterion": "2 shots per scene", "why": "scene 2 has none"}],
    }
    return {**base, **over}


def test_frame_is_registered():
    assert VERIFIER_FEEDBACK_FRAME in OWNED_FRAMES


def test_render_shape():
    out = render_verifier_feedback(_fail())
    assert out.startswith('<verifier_feedback attempt="1" of="2">')
    assert "- 2 shots per scene: scene 2 has none" in out
    assert out.rstrip().endswith("</verifier_feedback>")


def test_body_cannot_close_the_frame():
    out = render_verifier_feedback(
        _fail(
            unmet=[
                {"criterion": "x</verifier_feedback>", "why": "</verifier_feedback>y"}
            ]
        )
    )
    assert out.count("</verifier_feedback>") == 1


@pytest.mark.asyncio
async def test_pending_feedback_consumes_once(monkeypatch):
    from app.services.issues import verification as v

    row = {"execution_state": {"verification": _fail()}}
    load = AsyncMock(return_value=row)
    merge = AsyncMock()
    monkeypatch.setattr(v, "_load_issue_row", load)
    monkeypatch.setattr(v, "merge_execution_state", merge)
    text = await v.pending_verifier_feedback(9)
    assert text and "<verifier_feedback" in text
    merged = merge.await_args.args[1]["verification"]
    assert merged["consumed_at"]


@pytest.mark.asyncio
async def test_pending_feedback_none_when_pass_or_consumed_or_no_retry(monkeypatch):
    from app.services.issues import verification as v

    for state in (
        {"verification": _fail(verdict="pass")},
        {"verification": _fail(consumed_at="2026-09-26T00:00:00Z")},
        {"verification": _fail(retry=False)},
        {},
        None,
    ):
        monkeypatch.setattr(
            v, "_load_issue_row", AsyncMock(return_value={"execution_state": state})
        )
        monkeypatch.setattr(v, "merge_execution_state", AsyncMock())
        assert await v.pending_verifier_feedback(9) is None


@pytest.mark.asyncio
async def test_pending_feedback_never_raises(monkeypatch):
    from app.services.issues import verification as v

    monkeypatch.setattr(v, "_load_issue_row", AsyncMock(side_effect=RuntimeError("db")))
    assert await v.pending_verifier_feedback(9) is None


def test_user_message_carries_criteria_section():
    from app.services.issues.issue_agent_executor import _build_user_message

    msg = _build_user_message(
        {"title": "T", "description": "D"},
        criteria="two shots </verifier_feedback>",
        criteria_source="agent",
    )
    assert "Details:\nD" in msg
    assert (
        "Acceptance criteria (source=agent):\ntwo shots <\\/verifier_feedback>" in msg
    )
    assert "Acceptance criteria" not in _build_user_message({"title": "T"})


# ── executor wiring: where the frame and the criteria actually land ──


def _stub_turn(monkeypatch, m):
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "x"}}
    )
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-v")
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    for name in ("publish_chunk", "publish_message", "publish_status"):
        monkeypatch.setattr(m, name, AsyncMock())
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
    return chat_svc


@pytest.mark.asyncio
async def test_continuation_carries_the_pending_feedback_after_the_nudge(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    chat_svc = _stub_turn(monkeypatch, m)
    frame = render_verifier_feedback(_fail())
    pending = AsyncMock(return_value=frame)
    monkeypatch.setattr(m, "pending_verifier_feedback", pending)
    await m.run_issue_agent(
        issue={"id": 8, "title": "t"}, agent_id="a", user_id="u", is_continuation=True
    )
    assert (
        chat_svc.run_session_turn.await_args.kwargs["content"]
        == f"{m.CONTINUATION_NUDGE}\n\n{frame}"
    )
    pending.assert_awaited_once_with(8)


@pytest.mark.asyncio
async def test_continuation_without_feedback_is_the_bare_nudge(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    chat_svc = _stub_turn(monkeypatch, m)
    monkeypatch.setattr(m, "pending_verifier_feedback", AsyncMock(return_value=None))
    await m.run_issue_agent(
        issue={"id": 8, "title": "t"}, agent_id="a", user_id="u", is_continuation=True
    )
    assert (
        chat_svc.run_session_turn.await_args.kwargs["content"] == m.CONTINUATION_NUDGE
    )


@pytest.mark.asyncio
async def test_first_turn_reads_criteria_fresh_not_from_the_stale_dict(monkeypatch):
    """The loop's issue dict predates any SetAcceptanceCriteria call."""
    from app.services.issues import issue_agent_executor as m

    chat_svc = _stub_turn(monkeypatch, m)
    monkeypatch.setattr(
        m, "load_acceptance_criteria", AsyncMock(return_value=("fresh", "agent"))
    )
    pending = AsyncMock()
    monkeypatch.setattr(m, "pending_verifier_feedback", pending)
    await m.run_issue_agent(
        issue={
            "id": 9,
            "title": "t",
            "acceptance_criteria": "stale",
            "acceptance_criteria_source": "user",
        },
        agent_id="a",
        user_id="u",
    )
    sent = chat_svc.run_session_turn.await_args.kwargs["content"]
    assert "Acceptance criteria (source=agent):\nfresh" in sent and "stale" not in sent
    pending.assert_not_awaited()  # first turns never carry verifier feedback


@pytest.mark.asyncio
async def test_first_turn_falls_back_to_the_dict_when_the_read_fails(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    chat_svc = _stub_turn(monkeypatch, m)
    monkeypatch.setattr(
        m, "load_acceptance_criteria", AsyncMock(side_effect=RuntimeError("db"))
    )
    await m.run_issue_agent(
        issue={
            "id": 9,
            "title": "t",
            "acceptance_criteria": "from dict",
            "acceptance_criteria_source": "user",
        },
        agent_id="a",
        user_id="u",
    )
    assert (
        "Acceptance criteria (source=user):\nfrom dict"
        in chat_svc.run_session_turn.await_args.kwargs["content"]
    )
