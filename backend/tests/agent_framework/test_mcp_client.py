"""Q4 — outbound MCP client (HTTP JSON-RPC) tests.

We mock httpx.AsyncClient.post to feed canned responses; the goal is to
prove the JSON-RPC framing + error handling, not exercise a real MCP
server.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.agent_framework.mcp_client import (
    MCPClient,
    MCPClientError,
    MCPServerConfig,
)


def _ok_response(result):
    """Build a fake httpx.Response carrying a JSON-RPC success."""
    body = {"jsonrpc": "2.0", "id": "x", "result": result}
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=body)
    resp.text = json.dumps(body)
    return resp


def _err_response(code, message, http_status=200):
    body = {"jsonrpc": "2.0", "id": "x", "error": {"code": code, "message": message}}
    resp = MagicMock()
    resp.status_code = http_status
    resp.json = MagicMock(return_value=body)
    resp.text = json.dumps(body)
    return resp


def _http_status(status, body_text="something broke"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = body_text
    resp.json = MagicMock(side_effect=ValueError("not json"))
    return resp


@pytest.fixture
def cfg():
    return MCPServerConfig(name="testsrv", url="https://example.test/mcp")


@pytest.fixture
def mock_http():
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


# ─── Initialize handshake ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_sends_protocol_and_marks_initialized(cfg, mock_http):
    mock_http.post.return_value = _ok_response(
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "testsrv", "version": "1.0"},
        }
    )
    c = MCPClient(cfg, http_client=mock_http)

    info = await c.initialize()
    assert info["serverInfo"]["name"] == "testsrv"

    # Idempotent — second call should not re-issue the handshake
    await c.initialize()
    assert mock_http.post.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_includes_bearer_token_when_set(mock_http):
    cfg = MCPServerConfig(name="t", url="https://x", bearer_token="sekret123")
    mock_http.post.return_value = _ok_response({})
    c = MCPClient(cfg, http_client=mock_http)
    await c.initialize()

    _, kwargs = mock_http.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer sekret123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_no_auth_header_when_no_token(cfg, mock_http):
    mock_http.post.return_value = _ok_response({})
    c = MCPClient(cfg, http_client=mock_http)
    await c.initialize()

    _, kwargs = mock_http.post.call_args
    assert "Authorization" not in kwargs["headers"]


# ─── tools/list with caching ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tools_caches_within_ttl(cfg, mock_http):
    """Second call within TTL should hit cache, not the wire."""
    init = _ok_response({})
    listing = _ok_response(
        {
            "tools": [
                {"name": "t1", "description": "first", "inputSchema": {}},
            ]
        }
    )
    mock_http.post.side_effect = [init, listing]
    c = MCPClient(cfg, http_client=mock_http, tools_cache_ttl_seconds=300)

    first = await c.list_tools()
    second = await c.list_tools()
    assert first == second
    assert len(first) == 1
    # Init + 1 list = 2 calls, no additional list
    assert mock_http.post.call_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tools_force_refresh_bypasses_cache(cfg, mock_http):
    init = _ok_response({})
    listing = _ok_response({"tools": []})
    listing2 = _ok_response(
        {"tools": [{"name": "n2", "description": "", "inputSchema": {}}]}
    )
    mock_http.post.side_effect = [init, listing, listing2]
    c = MCPClient(cfg, http_client=mock_http)

    first = await c.list_tools()
    assert first == []
    second = await c.list_tools(force_refresh=True)
    assert len(second) == 1
    assert mock_http.post.call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tools_malformed_response_raises(cfg, mock_http):
    """Server returns object without 'tools' key → MCPClientError."""
    init = _ok_response({})
    listing = _ok_response({"wrong_key": []})
    mock_http.post.side_effect = [init, listing]
    c = MCPClient(cfg, http_client=mock_http)

    with pytest.raises(MCPClientError, match="missing 'tools'"):
        await c.list_tools()


# ─── tools/call ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_tool_returns_content(cfg, mock_http):
    init = _ok_response({})
    call = _ok_response({"content": [{"type": "text", "text": "ok"}], "isError": False})
    mock_http.post.side_effect = [init, call]
    c = MCPClient(cfg, http_client=mock_http)

    result = await c.call_tool("my_tool", {"arg": 1})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_tool_defaults_missing_fields(cfg, mock_http):
    """Some servers omit isError/content on success — we backfill."""
    init = _ok_response({})
    call = _ok_response({})  # no content, no isError
    mock_http.post.side_effect = [init, call]
    c = MCPClient(cfg, http_client=mock_http)

    result = await c.call_tool("any")
    assert result["isError"] is False
    assert result["content"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_tool_returns_is_error_in_dict_not_raise(cfg, mock_http):
    """isError=true is a tool-level failure, not a transport one;
    caller decides how to surface it. Don't raise."""
    init = _ok_response({})
    call = _ok_response({"content": [{"type": "text", "text": "no"}], "isError": True})
    mock_http.post.side_effect = [init, call]
    c = MCPClient(cfg, http_client=mock_http)

    result = await c.call_tool("broken")
    assert result["isError"] is True  # surfaced, not raised


# ─── Error handling ──────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_4xx_raises(cfg, mock_http):
    mock_http.post.return_value = _http_status(401, "no auth")
    c = MCPClient(cfg, http_client=mock_http)
    with pytest.raises(MCPClientError, match="http 401"):
        await c.initialize()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_jsonrpc_error_raises(cfg, mock_http):
    mock_http.post.return_value = _err_response(-32601, "method not found")
    c = MCPClient(cfg, http_client=mock_http)
    with pytest.raises(MCPClientError, match="jsonrpc error -32601"):
        await c.initialize()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_failure_wrapped(cfg, mock_http):
    mock_http.post.side_effect = httpx.ConnectError("connection refused")
    c = MCPClient(cfg, http_client=mock_http)
    with pytest.raises(MCPClientError, match="transport error"):
        await c.initialize()
