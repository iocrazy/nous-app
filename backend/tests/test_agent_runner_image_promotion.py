"""AgentRunner — image-bearing ResourceFetch results must reach the model
as user-message image parts.

OpenAI-compatible providers only process images inside user/assistant
multipart content; a base64 blob json.dumps'ed into a role:tool message
is (a) invisible to the vision pipeline and (b) megabytes of dead weight
in the transcript/event log.

Contract pinned here (both run_turn and stream_turn):
  - vision-capable turn: tool message carries a stripped placeholder,
    and a synthetic user message with {"type":"image_url"} parts follows;
  - non-vision turn: no synthetic user message; tool message notes the
    image was omitted; base64 never appears in messages or the trace.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import AgentRunner

DATA_URL = "data:image/png;base64,QUFB"


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
        return {"prompt": "unused"}


def _adapter_with_resource_fetch_then_final():
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
                                    "name": "ResourceFetch",
                                    "arguments": json.dumps({"resource_id": "42"}),
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


def _image_result():
    return {
        "content": [{"type": "image_url", "url": DATA_URL, "mime": "image/png"}],
        "meta": {"name": "shot.png", "kind": "image"},
    }


@pytest.mark.unit
async def test_vision_turn_promotes_image_to_user_message():
    adapter = _adapter_with_resource_fetch_then_final()
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    runner.resource_fetch_handler = AsyncMock(return_value=_image_result())
    runner.vision_capable = True

    result = await runner.run_turn(
        _composed(), [{"role": "user", "content": "look at @shot.png"}]
    )

    assert result["content"] == "FINAL"
    second_messages = adapter.call.await_args_list[1].args[1]

    tool_idx = next(i for i, m in enumerate(second_messages) if m.get("role") == "tool")
    # Base64 must NOT ride in the tool message.
    assert DATA_URL not in second_messages[tool_idx]["content"]

    # A synthetic user message with the image part must follow the tool msg.
    injected = second_messages[tool_idx + 1]
    assert injected["role"] == "user"
    parts = injected["content"]
    image_parts = [p for p in parts if p.get("type") == "image_url"]
    assert image_parts, parts
    assert image_parts[0]["image_url"]["url"] == DATA_URL


@pytest.mark.unit
async def test_non_vision_turn_omits_image_and_says_so():
    adapter = _adapter_with_resource_fetch_then_final()
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    runner.resource_fetch_handler = AsyncMock(return_value=_image_result())
    runner.vision_capable = False

    result = await runner.run_turn(
        _composed(), [{"role": "user", "content": "look at @shot.png"}]
    )

    assert result["content"] == "FINAL"
    second_messages = adapter.call.await_args_list[1].args[1]
    # No synthetic user message injected after the tool message.
    roles = [m.get("role") for m in second_messages]
    tool_idx = roles.index("tool")
    assert "user" not in roles[tool_idx + 1 :]
    # Tool message: no base64, but an explicit omission note.
    tool_content = second_messages[tool_idx]["content"]
    assert DATA_URL not in tool_content
    assert "vision" in tool_content or "omitted" in tool_content


@pytest.mark.unit
async def test_trace_never_carries_base64():
    """The tool_call trace / recorder payload must be stripped too —
    a 10MB base64 in every event row would bloat the transcript store."""
    adapter = _adapter_with_resource_fetch_then_final()
    runner = AgentRunner(adapter=adapter, skill_tool=FakeSkillTool())
    runner.resource_fetch_handler = AsyncMock(return_value=_image_result())
    runner.vision_capable = True

    result = await runner.run_turn(_composed(), [{"role": "user", "content": "look"}])
    trace = result.get("tool_calls") or result.get("tool_call_trace") or []
    assert trace, "expected a tool call trace"
    assert DATA_URL not in json.dumps(trace)
