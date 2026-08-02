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

import json
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
    # Attribution regression guard: the issue has no origin_kind, so the
    # main turn's own attribution_from_origin_kind(None) == "direct_human" —
    # the SAME value must reach the forced-declare call, not be dropped.
    assert kwargs["attribution"] == "direct_human"

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


async def test_forced_declaration_threads_rule_owner_attribution(monkeypatch):
    """Sibling of the origin_kind=None case above — a routine/pipeline issue
    must forward attribution='rule_owner', not silently default to
    direct_human."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1b")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "did the routine thing"}}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    forced = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    await m.run_issue_agent(
        issue={"id": 59, "title": "t", "origin_kind": "routine"},
        agent_id="a",
        user_id="u",
    )

    forced.assert_awaited_once()
    assert forced.await_args.kwargs["attribution"] == "rule_owner"


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
    pinned to FinishIssue, a needs_input tool call is parsed into
    (outcome, reason), the response's real token counts reach
    record_usage, and the recorder is attached to the issue's own
    conversation (not left as an orphan card)."""
    from app.services.ai.tools import forced_finish_declaration as fd

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {
        "agent_slug": "issue_agent",
        "team_id": None,
        "project_id": None,
        "store_kind": "conversations",
    }

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(return_value=(agent_record, _FakeAdapter(), session)),
    )

    recorded_kwargs: dict[str, Any] = {}
    recorder_holder: dict[str, _RecorderCM] = {}

    def _recorder_factory(**kwargs: Any) -> _RecorderCM:
        recorded_kwargs.update(kwargs)
        rec = _RecorderCM(**kwargs)
        recorder_holder["rec"] = rec
        return rec

    monkeypatch.setattr(fd, "RunRecorder", _recorder_factory)

    # A conversations-backed session's id is a numeric Snowflake string
    # (ai_sessions.id) in production — using a real digit string here
    # matters: the implementation does int(session_id) on this exact
    # branch, and a non-numeric placeholder would raise ValueError and
    # silently fail the whole call open, masking the assertions below.
    outcome, reason = await fd.attempt_forced_finish_declaration(
        session_id="555555",
        user_id=str(uuid4()),
        assistant_text="I finished the draft but need a decision on tone.",
        issue_id=101,
        trigger="issue_dispatch_auto",
        attribution="rule_owner",
    )

    assert outcome == "needs_input"
    assert reason == "need a decision on tone"

    # Quota safety: the recorded trigger must NOT equal the exact string the
    # autopilot daily-quota counter filters on.
    assert recorded_kwargs["trigger"] != "issue_dispatch_auto"
    assert recorded_kwargs["trigger"] == "issue_dispatch_auto_finish_declare"
    assert recorded_kwargs["issue_id"] == 101

    # Attribution regression: the caller's attribution must reach THIS
    # recorder, not silently default to direct_human.
    assert recorded_kwargs["attribution"] == "rule_owner"

    # Orphan-card fix: a conversations-backed session links via
    # conversation_id (session_id=None), mirroring
    # ai_library_chat_service.py's own store_kind dispatch — so this row
    # groups under the SAME conversation card instead of standing alone.
    assert recorded_kwargs["session_id"] is None
    assert recorded_kwargs["conversation_id"] == 555555

    # Test gap the reviewer named: record_usage must receive the response's
    # ACTUAL token counts (_fake_resp's default usage), not go unasserted.
    recorder_holder["rec"].record_usage.assert_called_once_with(
        prompt_tokens=11, completion_tokens=4
    )


async def test_forced_declare_turn_uses_legacy_session_id_when_not_conversations_store(
    monkeypatch,
):
    """Sibling of the conversation-linkage assertion above — a legacy
    (non-conversations) session must keep the byte-identical session_id
    path, never conversation_id."""
    from app.services.ai.tools import forced_finish_declaration as fd

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {
        "agent_slug": "issue_agent",
        "team_id": None,
        "project_id": None,
        "store_kind": "legacy",
    }

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

    await fd.attempt_forced_finish_declaration(
        session_id="sess-legacy-1",
        user_id=str(uuid4()),
        assistant_text="draft ready",
        issue_id=105,
        trigger="issue_dispatch",
    )

    assert recorded_kwargs["session_id"] == "sess-legacy-1"
    assert recorded_kwargs["conversation_id"] is None


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


async def test_forced_declare_turn_narrows_forced_continue_to_none(monkeypatch):
    """Controller decision (review round 2): a FORCED 'continue' must be
    narrowed to no-declaration, never propagated — unlocking
    issue_lifecycle.py's continuation branch from the forced path (each
    continuation = a full quota-counted run_issue_agent) is a separate,
    observable change the autopilot quota wasn't calibrated for."""
    from app.services.ai.tools import forced_finish_declaration as fd

    class _ContinueAdapter:
        async def call(self, composed, messages, *, tool_choice=None):
            args = json.dumps({"outcome": "continue", "reason": "more to do"})
            return _fake_resp(
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "FinishIssue", "arguments": args},
                    }
                ]
            )

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {"agent_slug": "issue_agent", "team_id": None, "project_id": None}

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(return_value=(agent_record, _ContinueAdapter(), session)),
    )
    monkeypatch.setattr(fd, "RunRecorder", lambda **kw: _RecorderCM(**kw))

    outcome, reason = await fd.attempt_forced_finish_declaration(
        session_id="sess-continue",
        user_id=str(uuid4()),
        assistant_text="working on it",
        issue_id=106,
        trigger="issue_dispatch_auto",
    )

    assert outcome is None
    assert reason is None


class _NoToolChoiceAdapter:
    """Simulates an adapter whose call() signature genuinely has no
    tool_choice parameter (e.g. ClaudeAdapter) — the capability PROBE
    (inspect.signature, not except TypeError) must detect this up front and
    call it without tool_choice, rather than risk swallowing a real
    TypeError raised from inside a working adapter."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, composed, messages):
        self.calls.append({"composed": composed, "messages": messages})
        return _fake_resp(tool_calls=[])


async def test_forced_declare_turn_falls_back_when_adapter_lacks_tool_choice_support(
    monkeypatch,
):
    from app.services.ai.tools import forced_finish_declaration as fd

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {"agent_slug": "issue_agent", "team_id": None, "project_id": None}
    adapter = _NoToolChoiceAdapter()

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
    assert len(adapter.calls) == 1  # the un-forced call actually happened


async def test_forced_declare_turn_times_out_and_fails_open(monkeypatch):
    """No total-deadline protection (the forced call bypasses
    LLMFallbackChain) — a hung adapter must still be bounded and degrade to
    (None, None) rather than block the @DBOS.step for up to the adapter's
    own 60s client default."""
    import asyncio

    from app.services.ai.tools import forced_finish_declaration as fd

    class _HangingAdapter:
        async def call(self, composed, messages, *, tool_choice=None):
            await asyncio.sleep(10)
            return _fake_resp(tool_calls=[])

    agent_id = uuid4()
    agent_record = {"id": str(agent_id), "slug": "issue_agent", "model": "qwen-max"}
    session = {"agent_slug": "issue_agent", "team_id": None, "project_id": None}

    monkeypatch.setattr(
        fd,
        "_resolve_agent_and_adapter",
        AsyncMock(return_value=(agent_record, _HangingAdapter(), session)),
    )
    monkeypatch.setattr(fd, "RunRecorder", lambda **kw: _RecorderCM(**kw))
    monkeypatch.setattr(fd, "FORCED_DECLARE_TIMEOUT_S", 0.05)

    outcome, reason = await fd.attempt_forced_finish_declaration(
        session_id="sess-hang",
        user_id=str(uuid4()),
        assistant_text="draft ready",
        issue_id=107,
        trigger="issue_dispatch",
    )

    assert outcome is None
    assert reason is None


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
