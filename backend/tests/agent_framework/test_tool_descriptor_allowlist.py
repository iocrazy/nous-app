"""`Tool.to_descriptor()` is an allowlist, and stays one when Tool grows.

Today the projection names three fields explicitly, so nothing internal can
reach a model request. That property is worth a test not because it is subtle
now, but because the usual next edit — "just add the new field to the dataclass"
— silently widens what the model sees if the projection ever becomes
``asdict(self)``. A field like ``handler`` is a live callable; a future
``timeout_ms`` or ``required_capability`` is scheduler/permission metadata that
tells a model what to try to talk its way around.
"""

import dataclasses

import pytest

from app.agent_framework.mcp_descriptor import Tool, ToolInputSchema

MODEL_FACING_KEYS = {"name", "description", "inputSchema"}


def _tool(**kw):
    return Tool(
        name=kw.get("name", "t"),
        description=kw.get("description", "d"),
        input_schema=kw.get("input_schema", ToolInputSchema()),
        handler=kw.get("handler", lambda **_: None),
    )


@pytest.mark.unit
def test_descriptor_exposes_exactly_the_model_facing_keys():
    assert set(_tool().to_descriptor()) == MODEL_FACING_KEYS


@pytest.mark.unit
def test_handler_never_appears_in_the_descriptor():
    """The callable is the sharpest thing on the object; it must not serialize."""
    sentinel = object()

    def handler(**_):
        return sentinel

    assert "handler" not in _tool(handler=handler).to_descriptor()


@pytest.mark.unit
def test_descriptor_is_json_serializable():
    """A leaked callable shows up here too — json.dumps refuses it."""
    import json

    json.dumps(_tool().to_descriptor())


@pytest.mark.unit
def test_every_tool_field_is_either_allowlisted_or_deliberately_internal():
    """The guard that actually catches the regression.

    Adding a field to ``Tool`` forces a decision here: name it model-facing
    (and project it) or name it internal. A new field that is neither fails
    this test rather than quietly reaching a model request.
    """
    internal = {"handler"}
    fields = {f.name for f in dataclasses.fields(Tool)}
    # `input_schema` is the python-side name for the wire key `inputSchema`.
    model_facing = {"name", "description", "input_schema"}
    unclassified = fields - internal - model_facing
    assert not unclassified, (
        f"Tool grew {sorted(unclassified)} — decide whether each belongs in "
        "to_descriptor() (model-facing) or stays host-only, then record it here"
    )


@pytest.mark.unit
def test_registry_list_descriptors_uses_the_same_projection():
    """The registry is the path the wire actually takes — check it, not just Tool."""
    from app.agent_framework.mcp_descriptor import MCPToolRegistry

    reg = MCPToolRegistry()
    reg.register(_tool(name="a"))
    for d in reg.list_descriptors():
        assert set(d) == MODEL_FACING_KEYS
