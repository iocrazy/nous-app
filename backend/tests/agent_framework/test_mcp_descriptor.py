"""Sprint 8 — MCP tool descriptor + registry."""

from __future__ import annotations

import pytest

from app.agent_framework.mcp_descriptor import (
    DuplicateToolError,
    MCPToolRegistry,
    Tool,
    ToolCallResult,
    ToolInputSchema,
    agent_to_tool,
    skill_to_tool,
)


def _noop_handler(**_kwargs):  # pragma: no cover - test fixture
    return None


# ─── ToolInputSchema serialization ────────────────────────────────────


@pytest.mark.unit
def test_input_schema_minimal():
    schema = ToolInputSchema()
    out = schema.to_dict()
    assert out["type"] == "object"
    assert out["properties"] == {}
    assert out["additionalProperties"] is False
    assert "required" not in out  # omitted when empty


@pytest.mark.unit
def test_input_schema_with_required():
    schema = ToolInputSchema(
        properties={"q": {"type": "string"}},
        required=("q",),
    )
    out = schema.to_dict()
    assert out["properties"] == {"q": {"type": "string"}}
    assert out["required"] == ["q"]


@pytest.mark.unit
def test_input_schema_additional_properties_passthrough():
    schema = ToolInputSchema(additional_properties=True)
    assert schema.to_dict()["additionalProperties"] is True


# ─── Tool descriptor ──────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_to_descriptor_omits_handler():
    """Handler is internal — must NOT appear in wire format."""
    t = Tool(
        name="x.y",
        description="test",
        input_schema=ToolInputSchema(),
        handler=_noop_handler,
    )
    desc = t.to_descriptor()
    assert "handler" not in desc
    assert desc["name"] == "x.y"
    assert desc["description"] == "test"
    assert desc["inputSchema"]["type"] == "object"


# ─── ToolCallResult ───────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_call_result_text_helper():
    r = ToolCallResult.text("hello")
    assert r.content == [{"type": "text", "text": "hello"}]
    assert r.is_error is False


@pytest.mark.unit
def test_tool_call_result_text_error():
    r = ToolCallResult.text("fail", is_error=True)
    assert r.is_error is True
    assert r.to_dict()["isError"] is True


# ─── Registry ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_register_and_get():
    reg = MCPToolRegistry()
    t = Tool(
        name="a", description="", input_schema=ToolInputSchema(), handler=_noop_handler
    )
    reg.register(t)
    assert reg.get("a") is t
    assert "a" in reg
    assert len(reg) == 1


@pytest.mark.unit
def test_register_duplicate_rejected():
    reg = MCPToolRegistry()
    reg.register(
        Tool(
            name="dup",
            description="",
            input_schema=ToolInputSchema(),
            handler=_noop_handler,
        )
    )
    with pytest.raises(DuplicateToolError, match="dup"):
        reg.register(
            Tool(
                name="dup",
                description="",
                input_schema=ToolInputSchema(),
                handler=_noop_handler,
            )
        )


@pytest.mark.unit
def test_register_empty_name_rejected():
    reg = MCPToolRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register(
            Tool(
                name="",
                description="",
                input_schema=ToolInputSchema(),
                handler=_noop_handler,
            )
        )


@pytest.mark.unit
def test_get_returns_none_for_missing():
    reg = MCPToolRegistry()
    assert reg.get("nope") is None


@pytest.mark.unit
def test_list_descriptors_sorted_and_serializable():
    """tools/list response — must be deterministic order + no handler."""
    reg = MCPToolRegistry()
    reg.register(
        Tool(
            name="b",
            description="bee",
            input_schema=ToolInputSchema(),
            handler=_noop_handler,
        )
    )
    reg.register(
        Tool(
            name="a",
            description="ay",
            input_schema=ToolInputSchema(),
            handler=_noop_handler,
        )
    )
    descs = reg.list_descriptors()
    assert [d["name"] for d in descs] == ["a", "b"]
    assert all("handler" not in d for d in descs)


# ─── Adapters ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_skill_to_tool_prefixes_name():
    """Skills get 'skill.' prefix so Claude Desktop user can distinguish."""
    t = skill_to_tool(
        slug="script-outline",
        description="generate outline",
        handler=_noop_handler,
    )
    assert t.name == "skill.script-outline"
    assert t.description == "generate outline"


@pytest.mark.unit
def test_skill_to_tool_default_input_schema_empty():
    t = skill_to_tool(slug="x", description="", handler=_noop_handler)
    assert t.input_schema.properties == {}
    assert t.input_schema.required == ()


@pytest.mark.unit
def test_skill_to_tool_custom_schema_passes_through():
    schema = ToolInputSchema(
        properties={"topic": {"type": "string"}}, required=("topic",)
    )
    t = skill_to_tool(
        slug="x", description="", handler=_noop_handler, input_schema=schema
    )
    assert t.input_schema is schema


@pytest.mark.unit
def test_agent_to_tool_prefixes_name():
    t = agent_to_tool(
        slug="script_ai", description="script writer", handler=_noop_handler
    )
    assert t.name == "agent.script_ai"


@pytest.mark.unit
def test_agent_to_tool_default_schema_requires_prompt():
    """Agents need a 'prompt' input by default — it's the user instruction."""
    t = agent_to_tool(slug="x", description="", handler=_noop_handler)
    assert "prompt" in t.input_schema.properties
    assert t.input_schema.required == ("prompt",)
    assert t.input_schema.properties["prompt"]["type"] == "string"


@pytest.mark.unit
def test_agent_to_tool_default_descriptor_round_trips():
    """End-to-end: adapter → descriptor → JSON-friendly dict."""
    t = agent_to_tool(
        slug="script_ai", description="script writer", handler=_noop_handler
    )
    desc = t.to_descriptor()
    assert desc["name"] == "agent.script_ai"
    assert desc["inputSchema"]["required"] == ["prompt"]
