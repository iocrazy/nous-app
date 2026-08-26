"""Every tool_call the assistant emits gets exactly one tool result — always.

Providers enforce this on the NEXT request: OpenAI rejects an assistant
`tool_calls` message unless every id is answered by a following `tool`
message; Anthropic rejects an orphaned `tool_use`.

The runner had one way to send an orphan: an unsupported / hallucinated tool
name was skipped with `continue` — no result appended — and the loop went
straight back to the adapter. `test_unknown_tool_name_skipped` proves that
second adapter call happens; its mock does not validate pairing, a real
endpoint 400s. dsh's rule (tools subsystem): a call that never started still
gets a synthetic error result so the log stays replay-legal. Applied here.

Checked and NOT a problem: the pre-hook abort / await_approval pop. The
runner works on an internal copy of the message list and returns the moment
it aborts, so nothing after the popped stub is ever sent or persisted — a
first version of this file asserted pairing on the caller's list there and
was vacuous (the caller's list is never mutated at all).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import AgentRunner


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="t",
        model="qwen-max",
        temperature=0.0,
        max_tokens=64,
        system_message="s",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )


class _SkillTool:
    async def execute(self, args):
        return {"prompt": f"resolved:{args.get('skill')}"}


def _call(cid, name, args=None):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


def _assistant(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(calls),
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _final(text="DONE"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _pairing_ok(messages):
    """Every tool_call id answered, every tool message answers a call."""
    open_ids, answered = set(), set()
    for m in messages:
        if m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                open_ids.add(c["id"])
        elif m.get("role") == "tool":
            answered.add(m.get("tool_call_id"))
    return open_ids == answered


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_tool_gets_a_synthetic_error_result_not_silence():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _assistant(_call("c1", "UnknownTool")),
        _final(),
    ]
    runner = AgentRunner(adapter=adapter, skill_tool=_SkillTool())
    out = await runner.run_turn(_composed(), [{"role": "user", "content": "hi"}])
    assert out["content"] == "DONE"

    # The second request must carry a paired, replay-legal history.
    _, messages = adapter.call.await_args_list[1].args
    assert _pairing_ok(messages), messages
    reply = next(m for m in messages if m.get("role") == "tool")
    assert reply["tool_call_id"] == "c1"
    body = json.loads(reply["content"])
    assert "UnknownTool" in body.get("error", ""), body
    assert body.get("synthetic") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mixed_known_and_unknown_calls_all_get_answered():
    """The realistic shape: one hallucinated name next to a real Skill call."""
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _assistant(_call("c1", "Skill", {"skill": "x"}), _call("c2", "Nope")),
        _final(),
    ]
    runner = AgentRunner(adapter=adapter, skill_tool=_SkillTool())
    await runner.run_turn(_composed(), [{"role": "user", "content": "hi"}])
    _, messages = adapter.call.await_args_list[1].args
    assert _pairing_ok(messages), messages
    ids = [m["tool_call_id"] for m in messages if m.get("role") == "tool"]
    assert sorted(ids) == ["c1", "c2"]
