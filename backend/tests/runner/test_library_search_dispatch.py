"""LibrarySearch dispatch in BOTH runner loops (PR 5).

The handler is injected per turn on ``runner.library_search_handler`` (the
ResourceFetch pattern): None -> a clear error result; a raising handler ->
``{"error": "LibrarySearch failed: <Cls>"}``; otherwise the handler's dict is
the tool message the model reads."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner.agent_runner import SUPPORTED_TOOLS, AgentRunner

pytestmark = pytest.mark.unit

ARGS = {"query": "rain at night", "limit": 3}
RESULT = {"query": "rain at night", "hits": [], "total": 0, "vector_leg": "ok"}


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="unit-model",
        temperature=0.0,
        max_tokens=64,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


def _tool_message(messages):
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1, messages
    return json.loads(tool_msgs[0]["content"])


def _buffered_adapter():
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
                                    "name": "LibrarySearch",
                                    "arguments": json.dumps(ARGS),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    return adapter


class _StreamingAdapter:
    def __init__(self):
        self.iter = 0
        self.last_messages = None

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        self.last_messages = list(messages)
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {
                                "name": "LibrarySearch",
                                "arguments": json.dumps(ARGS),
                            },
                        }
                    ]
                }
            )
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_text="FINAL", finish_reason="stop")


def test_library_search_is_a_supported_tool():
    assert "LibrarySearch" in SUPPORTED_TOOLS


async def _run_buffered(handler):
    adapter = _buffered_adapter()
    runner = AgentRunner(adapter=adapter, skill_tool=None)
    runner.library_search_handler = handler
    out = await runner.run_turn(_composed(), [{"role": "user", "content": "find"}])
    assert out["content"] == "FINAL"
    second_call_messages = adapter.call.call_args_list[1].args[1]
    return _tool_message(second_call_messages)


async def _run_stream(handler):
    adapter = _StreamingAdapter()
    runner = AgentRunner(adapter=adapter, skill_tool=None)
    runner.library_search_handler = handler
    async for _ in runner.stream_turn(
        _composed(), [{"role": "user", "content": "find"}]
    ):
        pass
    return _tool_message(adapter.last_messages)


@pytest.mark.parametrize("run", [_run_buffered, _run_stream])
async def test_handler_result_reaches_the_model(run):
    seen = {}

    async def _handler(args):
        seen["args"] = args
        return RESULT

    assert await run(_handler) == RESULT
    assert seen["args"] == ARGS


@pytest.mark.parametrize("run", [_run_buffered, _run_stream])
async def test_unwired_handler_is_a_clear_error(run):
    out = await run(None)
    assert "LibrarySearch is not available" in out["error"]


@pytest.mark.parametrize("run", [_run_buffered, _run_stream])
async def test_raising_handler_is_contained(run):
    async def _boom(args):
        raise ValueError("nope")

    assert await run(_boom) == {"error": "LibrarySearch failed: ValueError"}


def test_library_chat_road_mounts_it_unconditionally_and_clears_it():
    """Same indentation as the unconditional AskUser injection (not inside
    the resource_refs / issue blocks), and dropped in the per-turn finally so
    a reused runner never carries another caller's binding."""
    from pathlib import Path

    src = Path("app/services/ai/chat/ai_library_chat_service.py").read_text(
        encoding="utf-8"
    )

    def _indent(marker: str) -> int:
        line = src[: src.index(marker)].rsplit("\n", 1)[-1]
        return len(line) - len(line.lstrip())

    assert _indent("[library_search_spec()]") == _indent("[ask_user_spec()]")
    assert "make_library_search_handler(str(user_id))" in src
    assert src.count("runner.library_search_handler = None") == 1
