"""Q4 — Outbound MCP registry: aggregate multiple servers into one
namespaced tool surface.

When an agent has been configured with N MCP servers, the AgentRunner
needs a single object to ask:

    "what tools are available, including from all MCP servers?"
    "I'm calling tool 'notion.create_page' — dispatch it"

This registry is that object. It owns N MCPClient instances and
prefixes each server's tool names with its server name (so
'create_page' on the 'notion' server becomes 'notion.create_page' to
the LLM).

Lifecycle:
  - Constructed with a list of MCPServerConfig at agent boot
  - all_tools() lazily lists tools from each server (with cache)
  - call(qualified_name, args) routes to the right client
  - aclose() cleans up all owned http clients

Failure isolation: if server A is down, server B's tools still work.
Errors from one server are logged + that server's tool list returns
empty for the cache window.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.agent_framework.mcp_client import (
    MCPClient,
    MCPClientError,
    MCPServerConfig,
)


@dataclass(frozen=True)
class QualifiedTool:
    """One tool advertised by one of the registered MCP servers, with
    the server-prefixed agent-facing name."""

    qualified_name: str
    """e.g. 'notion.create_page'"""

    server_name: str
    """The server this came from."""

    raw_name: str
    """The unprefixed name as the server knows it. Used at dispatch."""

    description: str
    input_schema: dict


def _qualify(server_name: str, tool_name: str) -> str:
    """Build the agent-facing name. Server name + dot + raw tool name."""
    return f"{server_name}.{tool_name}"


class MCPOutboundRegistry:
    """Aggregates outbound MCP servers behind one namespaced surface.

    Tool names exposed to the agent take the form
    ``{server_name}.{raw_tool_name}``. Routing looks up the matching
    MCPClient from server_name, then calls raw_tool_name on it.

    Servers register at boot via add_server(). Tools are listed
    lazily — the first call to all_tools() reaches out to each
    server. Subsequent calls hit the per-client cache.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}

    def add_server(self, config: MCPServerConfig) -> None:
        """Register one server. Duplicate name raises ValueError."""
        if not config.name or not config.name.strip():
            raise ValueError("server name must be non-empty")
        if "." in config.name:
            # The dot is our namespace separator; reject ambiguity at boot
            raise ValueError(
                f"server name '{config.name}' must not contain '.' "
                "(reserved as namespace separator)"
            )
        if config.name in self._clients:
            raise ValueError(f"server '{config.name}' already registered")
        self._clients[config.name] = MCPClient(config)

    def server_names(self) -> list[str]:
        return sorted(self._clients)

    def __len__(self) -> int:
        return len(self._clients)

    async def aclose(self) -> None:
        """Close all owned HTTP clients."""
        for c in self._clients.values():
            try:
                await c.aclose()
            except Exception as exc:
                logger.warning(f"[MCPOutboundRegistry] aclose for "
                               f"'{c.config.name}' failed: {exc}")

    async def all_tools(self) -> list[QualifiedTool]:
        """Aggregate tools from every registered server.

        Failures from one server are isolated (log + skip). Each
        server's tool list is cached per-client via list_tools()'s
        TTL window.
        """
        if not self._clients:
            return []

        async def _list_one(name: str, client: MCPClient):
            try:
                descriptors = await client.list_tools()
            except MCPClientError as exc:
                logger.warning(
                    f"[MCPOutboundRegistry] '{name}' tools/list failed: {exc}"
                )
                return name, []
            return name, descriptors

        results = await asyncio.gather(
            *(_list_one(n, c) for n, c in self._clients.items()),
            return_exceptions=False,
        )

        out: list[QualifiedTool] = []
        for server_name, descriptors in results:
            for d in descriptors:
                if not isinstance(d, dict):
                    continue
                raw_name = d.get("name")
                if not raw_name or not isinstance(raw_name, str):
                    continue
                out.append(
                    QualifiedTool(
                        qualified_name=_qualify(server_name, raw_name),
                        server_name=server_name,
                        raw_name=raw_name,
                        description=d.get("description", "") or "",
                        input_schema=d.get("inputSchema") or {},
                    )
                )
        return out

    async def call(
        self,
        qualified_name: str,
        arguments: Optional[dict] = None,
    ) -> dict:
        """Route a qualified tool call to the right server.

        Returns the raw ToolCallResult dict from the server
        (includes 'content' + 'isError'). Raises MCPClientError on
        unknown server / tool / transport errors. Tool-level
        errors (server returned isError=true) are returned in
        the dict, not raised.
        """
        if "." not in qualified_name:
            raise MCPClientError(
                f"qualified tool name '{qualified_name}' missing namespace"
            )
        server_name, _, raw_name = qualified_name.partition(".")
        client = self._clients.get(server_name)
        if client is None:
            raise MCPClientError(
                f"no MCP server registered as '{server_name}' "
                f"(have: {sorted(self._clients)})"
            )
        if not raw_name:
            raise MCPClientError(
                f"qualified name '{qualified_name}' has empty tool part"
            )
        return await client.call_tool(raw_name, arguments)


__all__ = [
    "MCPOutboundRegistry",
    "QualifiedTool",
]
