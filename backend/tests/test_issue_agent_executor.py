"""run_issue_agent composes the assigned agent, runs it on the issue's
title+description, and records output_summary on an issue-linked run."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


async def test_run_issue_agent_builds_prompt_and_records_output(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    agent_uuid = uuid4()

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(
        return_value={"id": str(agent_uuid), "slug": "writer", "model": "qwen-max"}
    )
    monkeypatch.setattr(m, "AgentRepository", lambda: agent_repo)

    composed = MagicMock(agent_id=agent_uuid, model="qwen-max")
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)
    monkeypatch.setattr(m, "PromptComposer", lambda *a, **k: composer)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": "Here is the 200-word essay...", "tool_calls": []}
    )
    monkeypatch.setattr(m, "_build_runner", lambda composed_, settings_: runner)

    rec = MagicMock()
    rec.__aenter__ = AsyncMock(return_value=rec)
    rec.__aexit__ = AsyncMock(return_value=False)
    rec.set_summaries = MagicMock()
    captured = {}

    def _rec_factory(**kwargs):
        captured.update(kwargs)
        return rec

    monkeypatch.setattr(m, "RunRecorder", _rec_factory)

    issue = {"id": 409, "title": "写一篇 200 字的短文", "description": "关于春天"}
    out = await m.run_issue_agent(
        issue=issue, agent_id=str(agent_uuid), user_id=str(uuid4())
    )

    # prompt carries issue title + description
    call = runner.run_turn.call_args
    user_msgs = call.args[1] if len(call.args) > 1 else call.kwargs["user_messages"]
    joined = " ".join(msg["content"] for msg in user_msgs)
    assert "写一篇 200 字的短文" in joined and "关于春天" in joined
    # recorder is issue-linked + records output
    assert captured["issue_id"] == 409
    assert captured["trigger"] == "issue_dispatch"
    rec.set_summaries.assert_called_once()
    assert "essay" in rec.set_summaries.call_args.kwargs["output_summary"]
    assert out == "Here is the 200-word essay..."


async def test_run_issue_agent_raises_when_agent_missing(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=None)
    monkeypatch.setattr(m, "AgentRepository", lambda: agent_repo)

    with pytest.raises(RuntimeError, match="not found"):
        await m.run_issue_agent(
            issue={"id": 1, "title": "t", "description": None},
            agent_id=str(uuid4()),
            user_id=str(uuid4()),
        )
