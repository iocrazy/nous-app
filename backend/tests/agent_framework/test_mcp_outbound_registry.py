"""Q4 — outbound MCP registry tests.

We mock MCPClient via dependency-substitution so the registry's logic
(namespacing, parallel listing, failure isolation) is tested without
hitting any HTTP server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_framework.mcp_client import MCPClientError, MCPServerConfig
from app.agent_framework.mcp_outbound_registry import (
    MCPOutboundRegistry,
)


def _stub_client(tools_payload, *, raise_on_call=None, call_result=None):
    """Build a fake MCPClient that returns canned data."""
    c = AsyncMock()
    c.list_tools = AsyncMock(return_value=tools_payload)
    if raise_on_call:
        c.call_tool = AsyncMock(side_effect=raise_on_call)
    else:
        c.call_tool = AsyncMock(
            return_value=call_result
            or {
                "content": [{"type": "text", "text": "ok"}],
                "isError": False,
            }
        )
    c.aclose = AsyncMock()
    return c


@pytest.fixture
def reg():
    return MCPOutboundRegistry()


# ─── add_server validation ───────────────────────────────────────────


@pytest.mark.unit
def test_add_server_rejects_empty_name(reg):
    with pytest.raises(ValueError, match="non-empty"):
        reg.add_server(MCPServerConfig(name="", url="https://x"))


@pytest.mark.unit
def test_add_server_rejects_dot_in_name(reg):
    with pytest.raises(ValueError, match="must not contain"):
        reg.add_server(MCPServerConfig(name="bad.name", url="https://x"))


@pytest.mark.unit
def test_add_server_rejects_duplicate(reg):
    reg.add_server(MCPServerConfig(name="srv1", url="https://x"))
    with pytest.raises(ValueError, match="already registered"):
        reg.add_server(MCPServerConfig(name="srv1", url="https://y"))


@pytest.mark.unit
def test_server_names_sorted(reg):
    reg.add_server(MCPServerConfig(name="zebra", url="https://x"))
    reg.add_server(MCPServerConfig(name="alpha", url="https://x"))
    assert reg.server_names() == ["alpha", "zebra"]
    assert len(reg) == 2


# ─── all_tools aggregation + namespacing ─────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_tools_namespaces_with_server_name(reg):
    reg.add_server(MCPServerConfig(name="notion", url="https://x"))
    reg.add_server(MCPServerConfig(name="linear", url="https://x"))

    # Stub each client's list_tools
    reg._clients["notion"] = _stub_client(
        [
            {"name": "create_page", "description": "Create page", "inputSchema": {}},
        ]
    )
    reg._clients["linear"] = _stub_client(
        [
            {"name": "create_issue", "description": "New issue", "inputSchema": {}},
        ]
    )

    tools = await reg.all_tools()
    qualified = sorted(t.qualified_name for t in tools)
    assert qualified == ["linear.create_issue", "notion.create_page"]

    # Each tool retains its raw_name and server_name
    notion_tool = next(t for t in tools if t.qualified_name == "notion.create_page")
    assert notion_tool.raw_name == "create_page"
    assert notion_tool.server_name == "notion"
    assert notion_tool.description == "Create page"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_server_failure_does_not_block_others(reg):
    """Failure isolation — broken server's list returns [] but others work."""
    reg.add_server(MCPServerConfig(name="ok_server", url="https://x"))
    reg.add_server(MCPServerConfig(name="broken", url="https://x"))

    reg._clients["ok_server"] = _stub_client(
        [
            {"name": "good_tool", "description": "", "inputSchema": {}},
        ]
    )
    broken = AsyncMock()
    broken.list_tools = AsyncMock(side_effect=MCPClientError("server down"))
    broken.aclose = AsyncMock()
    reg._clients["broken"] = broken

    tools = await reg.all_tools()
    qualified = [t.qualified_name for t in tools]
    assert qualified == ["ok_server.good_tool"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_malformed_descriptors_skipped(reg):
    """A descriptor missing 'name' is dropped, not crashed on."""
    reg.add_server(MCPServerConfig(name="srv", url="https://x"))
    reg._clients["srv"] = _stub_client(
        [
            {"name": "valid", "description": "", "inputSchema": {}},
            {"description": "no name field"},  # missing name
            "not a dict",  # wrong type
            {"name": "", "description": ""},  # empty name
        ]
    )

    tools = await reg.all_tools()
    assert [t.qualified_name for t in tools] == ["srv.valid"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_tools_empty_when_no_servers(reg):
    assert await reg.all_tools() == []


# ─── call routing ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_routes_to_matching_server(reg):
    reg.add_server(MCPServerConfig(name="notion", url="https://x"))
    reg.add_server(MCPServerConfig(name="linear", url="https://x"))

    notion_client = _stub_client(
        [],
        call_result={
            "content": [{"type": "text", "text": "from notion"}],
            "isError": False,
        },
    )
    linear_client = _stub_client(
        [],
        call_result={
            "content": [{"type": "text", "text": "from linear"}],
            "isError": False,
        },
    )
    reg._clients["notion"] = notion_client
    reg._clients["linear"] = linear_client

    res = await reg.call("notion.create_page", {"title": "x"})
    assert res["content"][0]["text"] == "from notion"
    notion_client.call_tool.assert_awaited_once_with("create_page", {"title": "x"})
    linear_client.call_tool.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_unknown_server_raises(reg):
    reg.add_server(MCPServerConfig(name="notion", url="https://x"))
    reg._clients["notion"] = _stub_client([])

    with pytest.raises(MCPClientError, match="no MCP server registered"):
        await reg.call("missing.tool", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_missing_namespace_raises(reg):
    reg.add_server(MCPServerConfig(name="notion", url="https://x"))
    reg._clients["notion"] = _stub_client([])

    with pytest.raises(MCPClientError, match="missing namespace"):
        await reg.call("no_dot", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_empty_tool_part_raises(reg):
    reg.add_server(MCPServerConfig(name="notion", url="https://x"))
    reg._clients["notion"] = _stub_client([])

    with pytest.raises(MCPClientError, match="empty tool part"):
        await reg.call("notion.", {})


# ─── Lifecycle ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aclose_calls_each_client_aclose(reg):
    reg.add_server(MCPServerConfig(name="a", url="https://x"))
    reg.add_server(MCPServerConfig(name="b", url="https://x"))
    a_stub = _stub_client([])
    b_stub = _stub_client([])
    reg._clients["a"] = a_stub
    reg._clients["b"] = b_stub

    await reg.aclose()
    a_stub.aclose.assert_awaited_once()
    b_stub.aclose.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aclose_swallows_per_client_failure(reg):
    """One client.aclose() raising shouldn't block the others."""
    reg.add_server(MCPServerConfig(name="ok", url="https://x"))
    reg.add_server(MCPServerConfig(name="bad", url="https://x"))
    ok_stub = _stub_client([])
    bad_stub = AsyncMock()
    bad_stub.aclose = AsyncMock(side_effect=RuntimeError("nope"))
    reg._clients["ok"] = ok_stub
    reg._clients["bad"] = bad_stub

    # Should NOT raise
    await reg.aclose()
    ok_stub.aclose.assert_awaited_once()
