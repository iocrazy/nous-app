"""Q5 — AgentRunner ↔ MCPOutboundRegistry integration tests.

Verifies:
  - composed.tools is augmented with MCP tool descriptors
  - tool_calls with mcp-prefixed names route to mcp_registry.call
  - non-MCP tools (Skill/Delegate) still work
  - MCP transport failure converts to a tool-result error (no crash)
  - mcp_registry=None → original behavior (no MCP tools, no dispatch)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.agent_framework.mcp_client import MCPClientError
from app.agent_framework.mcp_outbound_registry import QualifiedTool
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import (
    AgentRunner,
    _is_mcp_tool_name,
    _mcp_tools_to_openai_format,
)


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="test",
        model="qwen-max",
        temperature=0.0,
        max_tokens=512,
        system_message="sys",
        tools=[
            {"type": "function", "function": {"name": "Skill", "parameters": {}}}
        ],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


# ─── Helpers ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_is_mcp_tool_name_matches_registered_server():
    reg = MagicMock()
    reg.server_names = MagicMock(return_value=["notion", "linear"])
    assert _is_mcp_tool_name("notion.create_page", reg) is True
    assert _is_mcp_tool_name("linear.create_issue", reg) is True


@pytest.mark.unit
def test_is_mcp_tool_name_rejects_unregistered_prefix():
    reg = MagicMock()
    reg.server_names = MagicMock(return_value=["notion"])
    assert _is_mcp_tool_name("github.create_repo", reg) is False


@pytest.mark.unit
def test_is_mcp_tool_name_rejects_no_dot():
    reg = MagicMock()
    reg.server_names = MagicMock(return_value=["notion"])
    assert _is_mcp_tool_name("Skill", reg) is False


@pytest.mark.unit
def test_is_mcp_tool_name_rejects_empty_tool_part():
    reg = MagicMock()
    reg.server_names = MagicMock(return_value=["notion"])
    assert _is_mcp_tool_name("notion.", reg) is False


@pytest.mark.unit
def test_is_mcp_tool_name_returns_false_when_registry_none():
    assert _is_mcp_tool_name("notion.foo", None) is False


@pytest.mark.unit
def test_mcp_tools_to_openai_format_round_trip():
    tools = [
        QualifiedTool(
            qualified_name="notion.create_page",
            server_name="notion",
            raw_name="create_page",
            description="Create a new page",
            input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        ),
    ]
    result = _mcp_tools_to_openai_format(tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "notion.create_page"
    assert result[0]["function"]["description"] == "Create a new page"
    assert result[0]["function"]["parameters"]["properties"]["title"]["type"] == "string"


@pytest.mark.unit
def test_mcp_tools_to_openai_format_handles_empty_schema():
    tools = [
        QualifiedTool(
            qualified_name="srv.tool", server_name="srv", raw_name="tool",
            description="", input_schema={},
        ),
    ]
    result = _mcp_tools_to_openai_format(tools)
    assert result[0]["function"]["parameters"] == {"type": "object"}


# ─── End-to-end run_turn dispatch ────────────────────────────────────


def _adapter_with_responses(*responses):
    """Build a fake adapter that returns canned response dicts in order."""
    a = MagicMock()
    iter_resp = iter(responses)
    a.call = AsyncMock(side_effect=lambda *_a, **_k: next(iter_resp))
    return a


def _tool_call_msg(tool_name, args, call_id="c1"):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(args),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _final_msg(text):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_tool_call_routed_through_registry():
    """Happy path: LLM emits notion.create_page, runner dispatches via
    mcp_registry.call, result is appended to messages, second iteration
    returns final text."""
    qualified = [QualifiedTool(
        qualified_name="notion.create_page", server_name="notion",
        raw_name="create_page", description="Create page", input_schema={},
    )]
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(return_value=qualified)
    mcp_reg.server_names = MagicMock(return_value=["notion"])
    mcp_reg.call = AsyncMock(return_value={
        "content": [{"type": "text", "text": "page created!"}], "isError": False,
    })

    adapter = _adapter_with_responses(
        _tool_call_msg("notion.create_page", {"title": "hi"}),
        _final_msg("Done — page created."),
    )
    runner = AgentRunner(adapter=adapter, skill_tool=None, mcp_registry=mcp_reg)

    result = await runner.run_turn(_composed(), [{"role": "user", "content": "make a page"}])

    assert result["content"] == "Done — page created."
    mcp_reg.call.assert_awaited_once_with("notion.create_page", {"title": "hi"})
    # Trace records the dispatch
    assert any(t["name"] == "notion.create_page" for t in result["tool_calls"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_tools_injected_into_composed_tools():
    """run_turn enriches composed.tools before adapter.call so the LLM
    knows MCP tools exist."""
    qualified = [QualifiedTool(
        qualified_name="srv.foo", server_name="srv", raw_name="foo",
        description="d", input_schema={"type": "object"},
    )]
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(return_value=qualified)
    mcp_reg.server_names = MagicMock(return_value=["srv"])

    captured_tools = []

    async def _capture_call(composed, _messages):
        captured_tools.append(list(composed.tools))
        return _final_msg("ok")

    adapter = MagicMock()
    adapter.call = _capture_call
    runner = AgentRunner(adapter=adapter, skill_tool=None, mcp_registry=mcp_reg)

    await runner.run_turn(_composed(), [{"role": "user", "content": "x"}])

    # Original Skill tool + 1 MCP tool
    names = [t["function"]["name"] for t in captured_tools[0]]
    assert "Skill" in names
    assert "srv.foo" in names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_transport_failure_yields_tool_error_not_crash():
    """MCPClientError on dispatch → result dict with error, turn continues."""
    qualified = [QualifiedTool(
        qualified_name="srv.broken", server_name="srv", raw_name="broken",
        description="", input_schema={},
    )]
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(return_value=qualified)
    mcp_reg.server_names = MagicMock(return_value=["srv"])
    mcp_reg.call = AsyncMock(side_effect=MCPClientError("server down"))

    adapter = _adapter_with_responses(
        _tool_call_msg("srv.broken", {}),
        _final_msg("noted"),
    )
    runner = AgentRunner(adapter=adapter, skill_tool=None, mcp_registry=mcp_reg)

    result = await runner.run_turn(_composed(), [{"role": "user", "content": "go"}])

    # Run completed (didn't crash)
    assert result["content"] == "noted"
    # Trace shows the error result
    mcp_trace = [t for t in result["tool_calls"] if t["name"] == "srv.broken"]
    assert len(mcp_trace) == 1
    assert "MCP transport failure" in mcp_trace[0]["result"]["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_registry_means_no_mcp_tools_no_routing():
    """mcp_registry=None: composed.tools unchanged + .* tool names
    are unknown (skipped). Back-compat verified."""
    captured_tools = []

    async def _capture_call(composed, _messages):
        captured_tools.append(list(composed.tools))
        return _final_msg("hi")

    adapter = MagicMock()
    adapter.call = _capture_call
    runner = AgentRunner(adapter=adapter, skill_tool=None)  # no mcp_registry

    result = await runner.run_turn(_composed(), [{"role": "user", "content": "x"}])

    # Only the original Skill tool — MCP tools never injected
    names = [t["function"]["name"] for t in captured_tools[0]]
    assert names == ["Skill"]
    assert result["content"] == "hi"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_discovery_failure_isolated_run_continues():
    """If all_tools() raises, the turn proceeds without MCP tools rather
    than failing."""
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(side_effect=RuntimeError("registry boom"))
    mcp_reg.server_names = MagicMock(return_value=[])
    mcp_reg.call = AsyncMock()

    adapter = _adapter_with_responses(_final_msg("ok"))
    runner = AgentRunner(adapter=adapter, skill_tool=None, mcp_registry=mcp_reg)

    result = await runner.run_turn(_composed(), [{"role": "user", "content": "x"}])
    assert result["content"] == "ok"
    mcp_reg.call.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_call_still_works_alongside_mcp():
    """Built-in Skill tool dispatch unaffected when MCP is also registered."""
    qualified = [QualifiedTool(
        qualified_name="notion.x", server_name="notion", raw_name="x",
        description="", input_schema={},
    )]
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(return_value=qualified)
    mcp_reg.server_names = MagicMock(return_value=["notion"])
    mcp_reg.call = AsyncMock()

    skill = MagicMock()
    skill.execute = AsyncMock(return_value={"output": "skill ran"})

    adapter = _adapter_with_responses(
        _tool_call_msg("Skill", {"skill": "outline"}),
        _final_msg("done"),
    )
    runner = AgentRunner(adapter=adapter, skill_tool=skill, mcp_registry=mcp_reg)

    result = await runner.run_turn(_composed(), [{"role": "user", "content": "go"}])
    skill.execute.assert_awaited_once_with({"skill": "outline"})
    mcp_reg.call.assert_not_called()
    assert result["content"] == "done"
