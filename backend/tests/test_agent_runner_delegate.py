"""Runner-level tests for the Delegate tool branch (M2).

Covers:
    - Delegate dispatched to delegate_tool when configured
    - Delegate returns "not configured" error when delegate_tool is None
    - tool_role message carries name='Delegate' (not 'Skill')
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.agent_runner import AgentRunner


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="planner",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


class _FakeSkillTool:
    async def execute(self, args):
        return {"prompt": "skill-result"}


class _FakeDelegateTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, args):
        self.calls.append(args)
        return {
            "delegated_to": args.get("agent_slug"),
            "status": "queued",
            "inbox_message_id": "fake-msg-id",
        }


def _delegate_call_payload(slug: str = "summary") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "delg-1",
                            "function": {
                                "name": "Delegate",
                                "arguments": json.dumps(
                                    {"agent_slug": slug, "prompt": "do X"}
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }


# ─── happy path ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegate_call_dispatched_to_delegate_tool_when_configured():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _delegate_call_payload("summary"),
        {"choices": [{"message": {"content": "DONE"}}]},
    ]
    delegate = _FakeDelegateTool()
    runner = AgentRunner(
        adapter=adapter,
        skill_tool=_FakeSkillTool(),
        delegate_tool=delegate,
    )
    result = await runner.run_turn(
        _composed(), [{"role": "user", "content": "delegate it"}]
    )
    assert result["content"] == "DONE"
    assert len(delegate.calls) == 1
    assert delegate.calls[0]["agent_slug"] == "summary"

    # The tool role message in the second call should be name='Delegate'.
    second_messages = adapter.call.await_args_list[1].args[1]
    tool_msg = next(m for m in second_messages if m.get("role") == "tool")
    assert tool_msg["name"] == "Delegate"
    assert "queued" in tool_msg["content"]


# ─── delegate_tool not configured ────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegate_call_returns_explicit_error_when_unconfigured():
    """Without a delegate_tool, the runner answers Delegate with an
    explanatory error so the LLM can reason about it (vs silent skip)."""
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _delegate_call_payload("summary"),
        {"choices": [{"message": {"content": "OK"}}]},
    ]
    runner = AgentRunner(
        adapter=adapter,
        skill_tool=_FakeSkillTool(),
        delegate_tool=None,
    )
    await runner.run_turn(
        _composed(), [{"role": "user", "content": "try delegate"}]
    )

    second_messages = adapter.call.await_args_list[1].args[1]
    tool_msg = next(m for m in second_messages if m.get("role") == "tool")
    assert tool_msg["name"] == "Delegate"
    body = json.loads(tool_msg["content"])
    assert "error" in body
    assert "not configured" in body["error"]


# ─── unknown tools still skipped ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unsupported_tool_still_skipped_after_delegate_added():
    """The new SUPPORTED_TOOLS frozenset must not regress the unknown-tool
    skip behaviour from M1."""
    adapter = AsyncMock()
    adapter.call.side_effect = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "x",
                                "function": {
                                    "name": "WeirdCustomTool",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    delegate = _FakeDelegateTool()
    runner = AgentRunner(
        adapter=adapter,
        skill_tool=_FakeSkillTool(),
        delegate_tool=delegate,
    )
    result = await runner.run_turn(_composed(), [{"role": "user", "content": "hi"}])
    assert result["content"] == "FINAL"
    # Delegate must NOT have been called for an unsupported tool.
    assert delegate.calls == []
