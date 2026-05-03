"""Q4 — Outbound MCP client (JSON-RPC over HTTP).

The mcp_descriptor + mcp_stdio modules let mediahub *expose* its skills
to external MCP clients (Claude Desktop). This module is the inverse:
mediahub *consumes* tools advertised by an external MCP server, so
AgentRunner can call those tools mid-turn.

Why HTTP first (not stdio):
  - Stdio MCP requires us to spawn + manage a long-lived child process
    per session — adds a process lifecycle problem layer that R2 just
    fought with. HTTP is one fetch per call, no PID to track.
  - Most production MCP servers (Notion, Linear, GitHub) ship HTTP
    transport now; stdio is dev-time convenience.
  - Stdio is a separate follow-up if we ever need it — the descriptor
    returned from tools/list is identical, so the registry layer is
    transport-agnostic.

Spec subset (2024-11-05):
  - initialize  → returns serverInfo + capabilities + protocolVersion
  - tools/list  → returns [Tool descriptor] (we cache for ~5 min)
  - tools/call  → returns ToolCallResult { content[], isError }

Intentionally NOT implemented:
  - resources/* — separate descriptor type; defer until needed
  - prompts/*   — same
  - sampling/*  — inverts trust direction; defer
  - notifications/* — server pushes; only meaningful for stateful conn

Auth: per-server bearer token via Authorization header. Token comes from
a config row keyed by server name. Tokens are NEVER logged.

Rate limit + billing: caller (AgentRunner) increments
``mcp_calls_total`` metric. RunRecorder accumulates the external-call
count into agent_runs.metadata_json.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

import httpx
from loguru import logger


PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOLS_CACHE_TTL_SECONDS = 300  # 5 minutes


@dataclass(frozen=True)
class MCPServerConfig:
    """One outbound MCP server registration."""

    name: str
    """Stable identifier (alphanumeric + underscore). Becomes the
    namespace prefix on tool names: e.g. tool 'create_page' from server
    'notion' is exposed to the agent as 'notion.create_page'."""

    url: str
    """JSON-RPC HTTP endpoint."""

    bearer_token: Optional[str] = None
    """Authorization: Bearer <token>. None = no auth header sent."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    """Per-request HTTP timeout. Tools that need longer should be
    delegated to a workflow, not run inline."""


@dataclass
class _ToolsCacheEntry:
    """Cached tools/list response. Stored per server."""
    fetched_at: float
    descriptors: list[dict[str, Any]]


class MCPClientError(RuntimeError):
    """Raised on transport / protocol errors. Tool errors (isError=true
    in the response) are NOT raised — they're returned in the result."""


class MCPClient:
    """Outbound MCP client. One instance per MCP server (per agent's
    config); reusable across calls.

    Construction does NOT eagerly initialize the connection — the first
    call to ``list_tools`` or ``call_tool`` lazy-handshakes. This keeps
    startup fast even when many servers are configured but only some
    used per turn.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        tools_cache_ttl_seconds: float = DEFAULT_TOOLS_CACHE_TTL_SECONDS,
    ) -> None:
        self.config = config
        self._owned_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=config.timeout_seconds
        )
        self._initialized = False
        self._cache_ttl = tools_cache_ttl_seconds
        self._tools_cache: Optional[_ToolsCacheEntry] = None

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    # ─── JSON-RPC framing ────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.config.bearer_token:
            h["Authorization"] = f"Bearer {self.config.bearer_token}"
        return h

    async def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        """One JSON-RPC 2.0 request → response. Raises MCPClientError on
        transport or protocol errors. Returns the ``result`` field on
        success."""
        request_id = uuid4().hex
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            body["params"] = params

        try:
            resp = await self._client.post(
                self.config.url,
                content=json.dumps(body),
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise MCPClientError(
                f"transport error to {self.config.name}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise MCPClientError(
                f"http {resp.status_code} from {self.config.name}: "
                f"{resp.text[:300]}"
            )

        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise MCPClientError(
                f"non-JSON response from {self.config.name}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise MCPClientError(
                f"non-object JSON-RPC response from {self.config.name}"
            )

        if "error" in payload:
            err = payload["error"]
            code = err.get("code") if isinstance(err, dict) else "?"
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise MCPClientError(
                f"jsonrpc error {code} from {self.config.name}: {msg}"
            )

        if "result" not in payload:
            raise MCPClientError(
                f"jsonrpc response missing 'result' from {self.config.name}"
            )

        return payload["result"]

    # ─── Lifecycle: initialize handshake ─────────────────────────────

    async def initialize(self) -> dict[str, Any]:
        """Handshake. Returns server info + capabilities. Idempotent."""
        if self._initialized:
            return {}
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mediahub", "version": "1.0"},
            },
        )
        self._initialized = True
        return result if isinstance(result, dict) else {}

    # ─── Tools ────────────────────────────────────────────────────────

    async def list_tools(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return cached or freshly-fetched tool descriptors. Each is a
        dict with at least name + description + inputSchema (per MCP spec).
        Cache TTL is configurable; default 5 minutes."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._tools_cache is not None
            and (now - self._tools_cache.fetched_at) < self._cache_ttl
        ):
            return list(self._tools_cache.descriptors)

        await self.initialize()
        result = await self._rpc("tools/list")
        if not isinstance(result, dict) or "tools" not in result:
            raise MCPClientError(
                f"tools/list response missing 'tools' from {self.config.name}"
            )
        tools = result["tools"]
        if not isinstance(tools, list):
            raise MCPClientError(
                f"tools/list 'tools' is not a list from {self.config.name}"
            )

        self._tools_cache = _ToolsCacheEntry(fetched_at=now, descriptors=tools)
        return list(tools)

    async def call_tool(
        self,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Invoke a tool on the server. Returns the raw ToolCallResult
        dict ({content, isError}). Caller (AgentRunner) interprets
        isError=true as a recoverable tool failure — distinct from
        transport errors which raise MCPClientError.

        ``tool_name`` here is the *unprefixed* server-side name. The
        agent-facing name (with namespace prefix) is resolved by the
        outbound registry before calling here.
        """
        await self.initialize()
        result = await self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        if not isinstance(result, dict):
            raise MCPClientError(
                f"tools/call non-object result from {self.config.name}"
            )
        # Defensive defaults — some servers omit isError on success
        result.setdefault("isError", False)
        result.setdefault("content", [])
        return result


__all__ = [
    "MCPClient",
    "MCPClientError",
    "MCPServerConfig",
    "PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_TOOLS_CACHE_TTL_SECONDS",
]
