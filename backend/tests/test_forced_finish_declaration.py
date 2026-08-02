"""Forced FinishIssue declaration fallback.

Production evidence (2026-08-01 E2E probe): the issue-execution protocol
depends on the agent calling ``FinishIssue`` to declare its outcome, but no
agent run in production ever calls it — not even when the model's own prose
IS the declaration. ``run_issue_agent`` (issue_agent_executor.py) now makes
exactly ONE bounded follow-up call — ``attempt_forced_finish_declaration``
(forced_finish_declaration.py) — with ``tool_choice`` pinned to FinishIssue,
whenever a turn produced content but declared no outcome.

This file covers two layers:
  1. The WIRING in ``run_issue_agent`` (when the fallback fires / doesn't,
     and that it never lets an exception escape) — the seam is mocked.
  2. The fallback function itself (``attempt_forced_finish_declaration`` /
     ``_run_forced_declare_turn``) — the seam's OWN internals, with the
     adapter/agent-repo/RunRecorder mocked at a lower level. Covers the
     tool_choice-forcing call, the distinct (quota-safe) trigger value, and
     fail-open behavior on an internal failure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


# ─── Layer 1: run_issue_agent wiring ────────────────────────────────────


async def test_forced_declaration_fires_when_content_but_no_outcome(monkeypatch):
    """Content produced, no FinishIssue call — the fallback fires exactly
    once, and its declaration (including 'needs_input' + reason) becomes the
    turn's outcome."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {
                "content": (
                    "I finished the layout but I'm not sure which color "
                    "scheme you want — can you pick one?"
                )
            },
            "tool_calls": [],
            "run_id": "r-1",
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    forced = AsyncMock(return_value=("needs_input", "which color scheme should I use?"))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    out = await m.run_issue_agent(
        issue={"id": 55, "title": "build the layout"}, agent_id="a", user_id="u"
    )

    forced.assert_awaited_once()
    kwargs = forced.await_args.kwargs
    assert kwargs["issue_id"] == 55
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["user_id"] == "u"
    assert kwargs["trigger"] == "issue_dispatch"
    assert "color scheme" in kwargs["assistant_text"]

    assert out["outcome"] == "needs_input"
    assert out["reason"] == "which color scheme should I use?"


async def test_forced_declaration_skipped_when_outcome_already_declared(monkeypatch):
    """The agent DID call FinishIssue — the fallback must never fire (zero
    added cost on the healthy path)."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-2")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {"content": "done"},
            "tool_calls": [
                {
                    "name": "FinishIssue",
                    "args": {"outcome": "completed"},
                    "result": {
                        "acknowledged": True,
                        "outcome": "completed",
                        "reason": "shipped",
                    },
                }
            ],
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    forced = AsyncMock()
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    out = await m.run_issue_agent(
        issue={"id": 56, "title": "t"}, agent_id="a", user_id="u"
    )

    forced.assert_not_awaited()
    assert out["outcome"] == "completed"
    assert out["reason"] == "shipped"


async def test_forced_declaration_skipped_when_content_empty(monkeypatch):
    """Zero-content, no-outcome turn is the EMPTY_OUTPUT path — the fallback
    must not fire there (route_finish_outcome's own typed branch owns it)."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-3")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": ""}, "tool_calls": []}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    forced = AsyncMock()
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    out = await m.run_issue_agent(
        issue={"id": 57, "title": "t"}, agent_id="a", user_id="u"
    )

    forced.assert_not_awaited()
    assert out["outcome"] is None
    assert out["content"] == ""


async def test_forced_declaration_raise_is_swallowed_by_run_issue_agent(monkeypatch):
    """Defense in depth: even if the fallback seam itself raises (a bug in
    that module), run_issue_agent must not propagate it — outcome stays
    None, exactly today's in_review-default behavior."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-4")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "did some work"}}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    forced = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    out = await m.run_issue_agent(
        issue={"id": 58, "title": "t"}, agent_id="a", user_id="u"
    )

    forced.assert_awaited_once()
    assert out["outcome"] is None
    assert out["content"] == "did some work"


# ─── Layer 2: attempt_forced_finish_declaration internals ──────────────


def _fake_resp(tool_calls=None, usage=None) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "tool_calls": tool_calls or []}}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 4},
    }


class _RecorderCM:
    """Minimal async-context-manager RunRecorder stand-in that captures the
    kwargs it was constructed with (for the quota/trigger assertions) and
    exposes the small surface _run_forced_declare_turn touches."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.run_id = "999"
        self.record_usage = MagicMock()
        self.set_summaries = MagicMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAdapter:
    """Records the tool_choice it was called with and returns a canned
    FinishIssue(needs_input) tool call."""

    def __init__(self) -> None:
        self.last_tool_choice = None

    async def call(self, composed, messages, *, tool_choice=None):
        self.last_tool_choice = tool_choice
        import json

        args = json.dumps(
            {
                "outcome": "needs_input",
                "reason": "need a decision on tone",
            }
        )
        return _fake_resp(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "FinishIssue", "arguments": args},
                }
            ]
        )


async def test_forced_declare_turn_forces_tool_choice_and_parses_needs_input(
    monkeypatch,
):
    """Happy path: the forced call reaches the adapter with tool_choice
    pinned to FinishIssue, and a needs_input tool call is parsed into
    (outcome, reason)."""
    from app.services.ai.tools import forced_finish_declaration as fd

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {"agent_slug": "issue_agent", "team_id": None, "project_id": None}

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(return_value=(agent_record, _FakeAdapter(), session)),
    )

    recorded_kwargs: dict[str, Any] = {}

    def _recorder_factory(**kwargs: Any) -> _RecorderCM:
        recorded_kwargs.update(kwargs)
        return _RecorderCM(**kwargs)

    monkeypatch.setattr(fd, "RunRecorder", _recorder_factory)

    outcome, reason = await fd.attempt_forced_finish_declaration(
        session_id="sess-x",
        user_id=str(uuid4()),
        assistant_text="I finished the draft but need a decision on tone.",
        issue_id=101,
        trigger="issue_dispatch_auto",
    )

    assert outcome == "needs_input"
    assert reason == "need a decision on tone"

    # Quota safety: the recorded trigger must NOT equal the exact string the
    # autopilot daily-quota counter filters on.
    assert recorded_kwargs["trigger"] != "issue_dispatch_auto"
    assert recorded_kwargs["trigger"] == "issue_dispatch_auto_finish_declare"
    assert recorded_kwargs["issue_id"] == 101


async def test_forced_declare_turn_passes_the_forcing_tool_choice(monkeypatch):
    from app.services.ai.tools import forced_finish_declaration as fd

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {"agent_slug": "issue_agent", "team_id": None, "project_id": None}
    adapter = _FakeAdapter()

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(return_value=(agent_record, adapter, session)),
    )
    monkeypatch.setattr(fd, "RunRecorder", lambda **kw: _RecorderCM(**kw))

    await fd.attempt_forced_finish_declaration(
        session_id="sess-y",
        user_id=str(uuid4()),
        assistant_text="draft ready",
        issue_id=102,
        trigger="issue_dispatch",
    )

    assert adapter.last_tool_choice == fd.FORCED_DECLARE_TOOL_CHOICE
    assert adapter.last_tool_choice["function"]["name"] == "FinishIssue"


class _TypeErrorOnceAdapter:
    """Simulates an adapter whose call() doesn't accept tool_choice at all
    (e.g. a bespoke adapter that never added the kwarg) — the forced-declare
    turn must retry once without it rather than blow up."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, composed, messages, **kwargs):
        if "tool_choice" in kwargs:
            raise TypeError("call() got an unexpected keyword argument 'tool_choice'")
        self.calls.append({"composed": composed, "messages": messages})
        return _fake_resp(tool_calls=[])


async def test_forced_declare_turn_falls_back_when_adapter_rejects_tool_choice(
    monkeypatch,
):
    from app.services.ai.tools import forced_finish_declaration as fd

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {"agent_slug": "issue_agent", "team_id": None, "project_id": None}
    adapter = _TypeErrorOnceAdapter()

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(return_value=(agent_record, adapter, session)),
    )
    monkeypatch.setattr(fd, "RunRecorder", lambda **kw: _RecorderCM(**kw))

    outcome, reason = await fd.attempt_forced_finish_declaration(
        session_id="sess-z",
        user_id=str(uuid4()),
        assistant_text="draft ready",
        issue_id=103,
        trigger="issue_dispatch",
    )

    # No FinishIssue call in the fallback response — degrades cleanly.
    assert outcome is None
    assert reason is None
    assert len(adapter.calls) == 1  # the un-forced retry actually happened


async def test_attempt_forced_finish_declaration_fails_open_on_internal_error(
    monkeypatch,
):
    """Requirement #4: any internal failure (session lookup, adapter
    resolution, etc.) must degrade to (None, None) — never raise."""
    from app.services.ai.tools import forced_finish_declaration as fd

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(side_effect=RuntimeError("session not found")),
    )

    outcome, reason = await fd.attempt_forced_finish_declaration(
        session_id="sess-missing",
        user_id=str(uuid4()),
        assistant_text="whatever the agent said",
        issue_id=104,
        trigger="issue_dispatch",
    )

    assert outcome is None
    assert reason is None
