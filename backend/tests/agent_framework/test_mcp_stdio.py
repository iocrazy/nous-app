"""Sprint 8.5 — minimal MCP JSON-RPC stdio transport."""
from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest

from app.agent_framework.mcp_descriptor import (
    MCPToolRegistry,
    Tool,
    ToolCallResult,
    ToolInputSchema,
)
from app.agent_framework.mcp_stdio import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    _dispatch,
    serve,
)


# ─── _dispatch (unit-level) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_returns_protocol_version_and_serverinfo():
    reg = MCPToolRegistry()
    resp = await _dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, reg
    )
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "mediahub"
    assert "tools" in resp["result"]["capabilities"]


@pytest.mark.asyncio
async def test_tools_list_returns_descriptors():
    reg = MCPToolRegistry()

    def _h(**_kw):
        return None

    reg.register(
        Tool(name="x", description="d", input_schema=ToolInputSchema(), handler=_h)
    )
    resp = await _dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, reg
    )
    assert resp["id"] == 2
    assert [t["name"] for t in resp["result"]["tools"]] == ["x"]
    # Descriptor must NOT include the internal handler reference
    assert all("handler" not in t for t in resp["result"]["tools"])


@pytest.mark.asyncio
async def test_tools_call_async_handler_returns_result():
    reg = MCPToolRegistry()

    async def _h(**kw):
        return ToolCallResult.text(f"got {kw.get('x')}")

    reg.register(
        Tool(
            name="echo",
            description="",
            input_schema=ToolInputSchema(properties={"x": {"type": "string"}}),
            handler=_h,
        )
    )
    resp = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"x": "hi"}},
        },
        reg,
    )
    assert resp["result"]["content"] == [{"type": "text", "text": "got hi"}]
    assert resp["result"]["isError"] is False


@pytest.mark.asyncio
async def test_tools_call_sync_handler_string_wrapped():
    """Bare string return → auto-wrapped as text content block."""
    reg = MCPToolRegistry()

    def _h(**_kw):
        return "plain string"

    reg.register(
        Tool(name="s", description="", input_schema=ToolInputSchema(), handler=_h)
    )
    resp = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "s"},
        },
        reg,
    )
    assert resp["result"]["content"][0]["text"] == "plain string"


@pytest.mark.asyncio
async def test_tools_call_handler_exception_returns_iserror():
    """Spec: tool errors come back as a successful response with isError=true,
    NOT a JSON-RPC protocol error. Crashes must not kill the server."""
    reg = MCPToolRegistry()

    def _h(**_kw):
        raise RuntimeError("oops the tool blew up")

    reg.register(
        Tool(name="boom", description="", input_schema=ToolInputSchema(), handler=_h)
    )
    resp = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "boom"},
        },
        reg,
    )
    assert "error" not in resp  # JSON-RPC successful
    assert resp["result"]["isError"] is True
    assert "RuntimeError" in resp["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_tools_call_unknown_tool_returns_method_not_found():
    reg = MCPToolRegistry()
    resp = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "nope"},
        },
        reg,
    )
    assert resp["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_tools_call_argument_mismatch_returns_invalid_params():
    reg = MCPToolRegistry()

    def _h(required_arg):  # only takes one positional
        return None

    reg.register(
        Tool(name="t", description="", input_schema=ToolInputSchema(), handler=_h)
    )
    resp = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "t", "arguments": {"wrong": 1}},
        },
        reg,
    )
    assert resp["error"]["code"] == INVALID_PARAMS


@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found():
    reg = MCPToolRegistry()
    resp = await _dispatch(
        {"jsonrpc": "2.0", "id": 8, "method": "fake/thing"}, reg
    )
    assert resp["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_notification_returns_none():
    """Notifications (no 'id') accepted silently — no response."""
    reg = MCPToolRegistry()
    resp = await _dispatch(
        {"jsonrpc": "2.0", "method": "notifications/cancelled"}, reg
    )
    assert resp is None


@pytest.mark.asyncio
async def test_shutdown_acks():
    reg = MCPToolRegistry()
    resp = await _dispatch(
        {"jsonrpc": "2.0", "id": 9, "method": "shutdown"}, reg
    )
    assert resp["result"] == {}


# ─── serve loop (integration with stream) ─────────────────────────────


class _CapturingWriter:
    def __init__(self) -> None:
        self.buf = io.StringIO()

    def write(self, s: str) -> None:
        self.buf.write(s)

    def flush(self) -> None:
        ...

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.buf.getvalue().splitlines() if line]


def _stream_reader(payload: bytes) -> asyncio.StreamReader:
    """Build a StreamReader pre-loaded with payload + EOF."""
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_serve_handshake_then_list_then_call():
    """Drive the loop end-to-end: initialize → tools/list → tools/call."""
    reg = MCPToolRegistry()

    def _h(**kw):
        return f"hello {kw.get('who', 'world')}"

    reg.register(
        Tool(name="greet", description="", input_schema=ToolInputSchema(), handler=_h)
    )
    requests = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        b'{"jsonrpc":"2.0","id":3,"method":"tools/call",'
        b'"params":{"name":"greet","arguments":{"who":"x"}}}\n'
    )
    writer = _CapturingWriter()
    reader = _stream_reader(requests)
    await serve(reg, reader=reader, writer=writer)

    responses = writer.lines()
    assert len(responses) == 3
    assert responses[0]["result"]["serverInfo"]["name"] == "mediahub"
    assert [t["name"] for t in responses[1]["result"]["tools"]] == ["greet"]
    assert responses[2]["result"]["content"][0]["text"] == "hello x"


@pytest.mark.asyncio
async def test_serve_handles_malformed_json():
    reg = MCPToolRegistry()
    requests = b"not json at all\n"
    writer = _CapturingWriter()
    reader = _stream_reader(requests)
    await serve(reg, reader=reader, writer=writer)
    responses = writer.lines()
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == PARSE_ERROR


@pytest.mark.asyncio
async def test_serve_exits_on_exit_notification():
    """'exit' is a notification per spec; loop terminates without
    responding to it but does respond to anything BEFORE it."""
    reg = MCPToolRegistry()
    requests = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        b'{"jsonrpc":"2.0","method":"exit"}\n'
        b'{"jsonrpc":"2.0","id":99,"method":"tools/list"}\n'  # never seen
    )
    writer = _CapturingWriter()
    reader = _stream_reader(requests)
    await serve(reg, reader=reader, writer=writer)
    responses = writer.lines()
    assert len(responses) == 1  # only the initialize response
    assert responses[0]["id"] == 1


@pytest.mark.asyncio
async def test_serve_skips_blank_lines():
    reg = MCPToolRegistry()
    requests = b"\n\n   \n" + b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
    writer = _CapturingWriter()
    reader = _stream_reader(requests)
    await serve(reg, reader=reader, writer=writer)
    assert len(writer.lines()) == 1


def test_internal_error_constant_present():
    """Sanity check the standard error-code constants didn't get
    accidentally renumbered."""
    assert INTERNAL_ERROR == -32603
    assert PARSE_ERROR == -32700
