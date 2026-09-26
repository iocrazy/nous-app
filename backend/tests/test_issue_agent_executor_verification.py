"""run_issue_agent: completed → verification hook → result carries the verdict."""

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests

USER = "22222222-2222-2222-2222-222222222222"


def _turn(tool_calls, **extra):
    return {
        "assistant_message": {
            "id": "m",
            "role": "assistant",
            "content": "did it",
            "metadata_json": {},
        },
        "run_id": "r1",
        "tool_calls": tool_calls,
        **extra,
    }


def _finish(outcome):
    return [
        {
            "name": "FinishIssue",
            "args": {"outcome": outcome, "reason": "x"},
            "result": {"ok": True, "outcome": outcome, "reason": "x"},
        }
    ]


@pytest.fixture
def m(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    chat = AsyncMock()
    chat.run_session_turn = AsyncMock(return_value=_turn(_finish("completed")))
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat)
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    for name in ("publish_chunk", "publish_message", "publish_status"):
        monkeypatch.setattr(m, name, AsyncMock())
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
    return m, chat


async def test_completed_goes_through_verification_and_result_carries_it(
    m, monkeypatch
):
    mod, chat = m
    hook = AsyncMock(
        return_value=(
            "continue",
            "verifier_rejected: c",
            {"verdict": "fail", "attempt": 1},
        )
    )
    monkeypatch.setattr(mod, "apply_completion_verification", hook)
    out = await mod.run_issue_agent(
        issue={"id": 42, "title": "t"}, agent_id="a", user_id=USER
    )
    assert out["outcome"] == "continue" and out["reason"] == "verifier_rejected: c"
    assert out["verification"] == {"verdict": "fail", "attempt": 1}
    kw = hook.await_args.kwargs
    assert (
        kw["issue_id"] == 42
        and kw["outcome"] == "completed"
        and kw["content"] == "did it"
    )
    assert (
        kw["session_id"] == "sess-1"
        and kw["trigger"] == "issue_dispatch"
        and kw["result"]["run_id"] == "r1"
    )


async def test_forced_declaration_result_is_also_verified(m, monkeypatch):
    mod, chat = m
    chat.run_session_turn.return_value = _turn([])
    monkeypatch.setattr(
        mod,
        "attempt_forced_finish_declaration",
        AsyncMock(return_value=("completed", "forced")),
    )
    hook = AsyncMock(return_value=("completed", "forced", {"verdict": "pass"}))
    monkeypatch.setattr(mod, "apply_completion_verification", hook)
    out = await mod.run_issue_agent(
        issue={"id": 42, "title": "t"}, agent_id="a", user_id=USER
    )
    assert (
        out["verification"] == {"verdict": "pass"}
        and hook.await_args.kwargs["outcome"] == "completed"
    )


async def test_hook_exception_never_breaks_the_turn(m, monkeypatch):
    mod, chat = m
    monkeypatch.setattr(
        mod,
        "apply_completion_verification",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    out = await mod.run_issue_agent(
        issue={"id": 42, "title": "t"}, agent_id="a", user_id=USER
    )
    assert out["outcome"] == "completed" and out["verification"] is None
