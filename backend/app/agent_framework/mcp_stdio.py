"""Minimal MCP stdio transport.

Sprint 8.5 wire-up. Sprint 8 landed MCPToolRegistry + descriptor shapes
but no transport. This module is a JSON-RPC-over-stdio loop you can run
as a sidecar process to expose registered tools to MCP clients
(Claude Desktop, Cline, Cursor's MCP wiring, etc.).

Spec subset implemented (2024-11-05):
  - initialize       — handshake, returns serverInfo + capabilities
  - tools/list       — list_descriptors() from the registry
  - tools/call       — dispatch to Tool.handler, wrap result
  - shutdown / exit  — clean exit via stdin EOF or matching method
  - notifications/*  — accepted, no-op (we don't push)

Things deliberately NOT implemented in this minimal version:
  - resources/* (no resource adapters yet — Sprint 9 candidate)
  - prompts/*    (we don't pre-write prompts; the LLM does)
  - logging/*    (operational; deferred)
  - completion/* (advanced; not used by typical clients)
  - sampling/*   (lets servers ask the client to invoke an LLM —
                  inverts the trust direction; defer until needed)

Run via:

    python -m app.agent_framework.mcp_stdio

When launched, the parent (Claude Desktop config, etc.) talks JSON-RPC
2.0 over our stdin/stdout. Logging goes to stderr — never stdout, that
would corrupt the protocol stream.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from typing import Any, Optional

from app.agent_framework.mcp_descriptor import (
    MCPToolRegistry,
    Tool,
    ToolCallResult,
)


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mediahub"
SERVER_VERSION = "0.1"


# ─── JSON-RPC primitives ──────────────────────────────────────────────


def _ok(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str, data: Optional[Any] = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ─── Method dispatch ──────────────────────────────────────────────────


async def _dispatch(
    request: dict[str, Any], registry: MCPToolRegistry
) -> Optional[dict[str, Any]]:
    """Return a JSON-RPC response dict, or None for notifications.

    Handles malformed-but-parseable requests (missing method, bad params)
    by returning an error response. Truly malformed JSON is caught upstream
    before this is called.
    """
    rid = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    is_notification = "id" not in request

    if not isinstance(method, str):
        if is_notification:
            return None
        return _err(rid, INVALID_REQUEST, "method missing or not a string")

    # Notifications: accept silently.
    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _ok(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "tools/list":
        return _ok(rid, {"tools": registry.list_descriptors()})

    if method == "tools/call":
        return await _call_tool(rid, params, registry)

    if method == "shutdown":
        # Per spec the client follows up with `exit` notification; we
        # acknowledge the shutdown request and let the main loop exit
        # on the next stdin EOF or `exit` notification.
        return _ok(rid, {})

    return _err(rid, METHOD_NOT_FOUND, f"unknown method: {method}")


async def _call_tool(
    rid: Any, params: dict[str, Any], registry: MCPToolRegistry
) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        return _err(rid, INVALID_PARAMS, "tools/call: 'name' must be a string")
    if not isinstance(arguments, dict):
        return _err(rid, INVALID_PARAMS, "tools/call: 'arguments' must be an object")

    tool: Optional[Tool] = registry.get(name)
    if tool is None:
        return _err(rid, METHOD_NOT_FOUND, f"unknown tool: {name}")

    try:
        result = tool.handler(**arguments)
        if inspect.isawaitable(result):
            result = await result
    except TypeError as exc:
        # Most likely a schema mismatch (caller passed wrong kwargs).
        return _err(rid, INVALID_PARAMS, f"argument mismatch: {exc}")
    except Exception as exc:  # noqa: BLE001 — tool errors must not crash the server
        # Spec distinction: protocol errors (transport-level) vs tool
        # errors (handler returned an error). Tool errors come back
        # as a successful JSON-RPC response with isError=true.
        wrapped = ToolCallResult.text(
            f"{type(exc).__name__}: {exc}", is_error=True
        )
        return _ok(rid, wrapped.to_dict())

    if isinstance(result, ToolCallResult):
        return _ok(rid, result.to_dict())
    # Convenience: bare string → wrap as a text content block.
    if isinstance(result, str):
        return _ok(rid, ToolCallResult.text(result).to_dict())
    # Anything else → JSON-stringify into a text block.
    return _ok(rid, ToolCallResult.text(json.dumps(result, default=str)).to_dict())


# ─── Stdio loop ───────────────────────────────────────────────────────


async def serve(
    registry: MCPToolRegistry,
    *,
    reader: Optional[asyncio.StreamReader] = None,
    writer: Optional[Any] = None,
) -> None:
    """Run the JSON-RPC loop until stdin EOF or 'exit' notification.

    ``reader`` / ``writer`` are injectable for tests. Default uses
    real stdin/stdout via asyncio bridges.
    """
    if reader is None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    if writer is None:
        writer = sys.stdout

    while True:
        line = await reader.readline()
        if not line:
            break  # EOF
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            request = json.loads(text)
        except json.JSONDecodeError as exc:
            response = _err(None, PARSE_ERROR, f"parse error: {exc.msg}")
            _write(writer, response)
            continue

        if request.get("method") == "exit":
            break

        response = await _dispatch(request, registry)
        if response is not None:
            _write(writer, response)


def _write(writer: Any, payload: dict[str, Any]) -> None:
    """Write one JSON-RPC line + newline to ``writer``. Flushes so the
    parent process sees responses promptly even with line buffering."""
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    writer.write(line)
    flush = getattr(writer, "flush", None)
    if callable(flush):
        flush()


# ─── Entry point ──────────────────────────────────────────────────────


def _build_default_registry() -> MCPToolRegistry:
    """Construct the registry the standalone entry point publishes.

    Empty by default — concrete tool registration (skill.* / agent.*)
    happens via app.state when running embedded in FastAPI, and via
    a not-yet-written CLI flag when running as a stdio sidecar. Keeping
    the default empty means `python -m app.agent_framework.mcp_stdio`
    is safe to run for protocol-level smoke testing.
    """
    return MCPToolRegistry()


def main() -> None:  # pragma: no cover — entry point shim
    asyncio.run(serve(_build_default_registry()))


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["serve"]
