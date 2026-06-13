import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import MAX_TOOL_ITERATIONS, AgentRunner


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="script_ai",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


class FakeSkillTool:
    async def execute(self, args):
        return {"prompt": f"resolved:{args.get('skill')}"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_turn_no_tool_calls_returns_content():
    adapter = AsyncMock()
    adapter.call.return_value = {"choices": [{"message": {"content": "hello"}}]}
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    result = await runner.run_turn(_composed(), [{"role": "user", "content": "hi"}])
    assert result["content"] == "hello"
    adapter.call.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_call_resolved_then_final_content():
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
                                "id": "tc1",
                                "function": {
                                    "name": "Skill",
                                    "arguments": json.dumps(
                                        {"skill": "script-outline"}
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    result = await runner.run_turn(
        _composed(), [{"role": "user", "content": "outline please"}]
    )
    assert result["content"] == "FINAL"
    assert adapter.call.await_count == 2
    # Verify the second call carries the tool result as a tool-role message
    second_messages = adapter.call.await_args_list[1].args[1]
    tool_role_msg = next(m for m in second_messages if m.get("role") == "tool")
    assert tool_role_msg["tool_call_id"] == "tc1"
    assert "resolved:script-outline" in tool_role_msg["content"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_tool_name_skipped():
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
                                "function": {"name": "UnknownTool", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "DONE"}}]},
    ]
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    result = await runner.run_turn(_composed(), [])
    assert result["content"] == "DONE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_malformed_arguments_fall_back_to_empty_dict():
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
                                "function": {"name": "Skill", "arguments": "{not-json"},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "recovered"}}]},
    ]
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    result = await runner.run_turn(_composed(), [])
    assert result["content"] == "recovered"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_max_iterations_exceeded_returns_error():
    adapter = AsyncMock()
    # Always returns tool_calls → infinite loop guard
    adapter.call.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "x",
                            "function": {
                                "name": "Skill",
                                "arguments": json.dumps({"skill": "a"}),
                            },
                        }
                    ],
                }
            }
        ]
    }
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    result = await runner.run_turn(_composed(), [])
    assert result.get("error") == "max_tool_iterations_exceeded"
    assert adapter.call.await_count == MAX_TOOL_ITERATIONS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_sec_zero_or_none_means_no_cap():
    """mig 286: timeout_sec None/0 → no deadline; turn completes normally."""
    adapter = AsyncMock()
    adapter.call.return_value = {"choices": [{"message": {"content": "ok"}}]}
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    composed = _composed().model_copy(update={"timeout_sec": 0})
    result = await runner.run_turn(composed, [{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_sec_exceeded_returns_run_timeout(monkeypatch):
    """mig 286: when the wall-clock cap is exceeded between iterations the
    turn returns error_code='run_timeout' instead of calling the LLM again."""
    import time as _time

    adapter = AsyncMock()
    # First call returns a tool_call so the loop re-enters; the deadline
    # check on iteration 2 must fire before the second adapter.call.
    adapter.call.return_value = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "Skill",
                                "arguments": json.dumps({"skill": "x"}),
                            },
                        }
                    ],
                }
            }
        ]
    }
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    composed = _composed().model_copy(update={"timeout_sec": 30})

    # Freeze-then-jump monotonic clock: first call (deadline init) returns
    # t0; later calls return t0 + 120 so iteration 2 sees the cap exceeded.
    t0 = _time.monotonic()
    calls = {"n": 0}

    def fake_monotonic():
        # call 1 = deadline init, call 2 = iteration-1 check (still inside
        # budget so the first LLM call happens), call 3+ = iteration-2 check
        # (past the cap).
        calls["n"] += 1
        return t0 if calls["n"] <= 2 else t0 + 120

    monkeypatch.setattr(_time, "monotonic", fake_monotonic)

    result = await runner.run_turn(composed, [{"role": "user", "content": "hi"}])
    assert result.get("error_code") == "run_timeout"
    # LLM called once (iteration 1); iteration 2 was cut off by the cap.
    assert adapter.call.await_count == 1
