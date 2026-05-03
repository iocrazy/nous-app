# MCP Servers (Outbound)

This document explains how to register an external MCP (Model Context
Protocol) server so your MediaHub agents can call its tools — and what
the security model is.

## What is "outbound MCP"?

MCP is JSON-RPC over HTTP. An MCP server advertises a list of `tools`
(name + JSON-Schema params). MediaHub's agents can call those tools
during a chat turn, mixing them in with the built-in `Skill` and
`Delegate` tools.

When you register a server in **Settings → AI → MCP Servers**:

1. MediaHub's chat backend lists your enabled servers at chat-start
2. Calls `tools/list` against each (cached 5 min)
3. Injects each tool into the LLM's `tools` array as
   `{server_name}.{tool_name}` (e.g. `notion.create_page`)
4. When the LLM calls `notion.create_page`, MediaHub routes the
   `tools/call` JSON-RPC request to your server
5. Returns the response as the tool result for the next LLM iteration

## Registering a server

Settings → AI → MCP Servers → Add Server.

| Field | Notes |
|---|---|
| **Name (namespace)** | Alphanumeric + underscore. Becomes the prefix on every tool. **Cannot be changed later** — pick something stable like `notion`, `linear`, `gh`. No dots allowed (`bad.name` would clash with the namespace separator). |
| **URL** | The HTTPS JSON-RPC endpoint of your MCP server. Must start with `http://` or `https://`. `localhost` is fine for self-hosted dev. |
| **Bearer Token** | Optional. Sent as `Authorization: Bearer <token>` on every request. **Stored in plain text in `user_mcp_servers.bearer_token`** — see Security below. |
| **Description** | Free-form label, shown in the list. |
| **Enabled** | Tools from disabled servers are not advertised to your agents. |

## Compatible MCP servers (verified)

These ship MCP HTTP transport that works with our outbound client:

- **`mcp-server-filesystem`** (Anthropic reference impl) — local-only,
  good for testing
- **Notion MCP** (`@modelcontextprotocol/server-notion`) — needs an
  integration token from notion.so/my-integrations
- **GitHub MCP** (`@modelcontextprotocol/server-github`) — needs PAT
- **Linear MCP** — query issues, create issues, transition state

> ⚠️ Many community MCP servers ship **stdio transport only** (designed
> for Claude Desktop). MediaHub's outbound client is **HTTP only**
> right now. If a server only does stdio, you'll need to wrap it in
> an HTTP adapter (e.g. `mcp-proxy`).

## Security model

### What MediaHub trusts the server to do

When the LLM calls `srv.create_page`, that call **executes immediately
on your MCP server with whatever credentials the server was configured
to use**. There is no per-tool approval prompt.

This means: **only register servers you trust to act on your behalf**.

PlanMode (chat → Mode → Plan First) gives you a workaround — the LLM
emits a structured plan first, you approve, then execution runs. Worth
enabling if you're testing a new server.

### What we protect

- ✅ Per-user isolation: `user_mcp_servers` is RLS-policy scoped (one
  user's servers are invisible to another)
- ✅ Bearer token never returned in API responses (only
  `has_bearer_token: true/false`)
- ✅ Bearer token never logged (httpx headers redacted by
  `boundary/log_redact.py`)
- ✅ Repo-layer SQL filter on `user_id` in update/delete (defense in
  depth — even if endpoint forgets ownership check, SQL clause blocks
  cross-user mutation)
- ✅ Server names cannot contain `.` (prevents namespace ambiguity)

### What we **don't** protect (current limitations)

- ❌ Bearer tokens are stored **plain text** in Postgres. Any DBA / NAS
   admin who can `SELECT * FROM user_mcp_servers` sees all tokens.
   See `mig 194` TODO for pgsodium encryption.
- ❌ MCP tool descriptors are passed to the LLM as-is. A malicious
   server could advertise misleading descriptions to trick the LLM
   into calling the wrong tool.
- ❌ MCP `tools/call` happens inside MediaHub's request — a slow MCP
   server eats your chat-turn timeout (default 30s per request).
- ❌ No per-tool rate limiting (yet). A spammy LLM could call your
   `create_issue` tool 50 times in a row.

## Debugging a server that doesn't work

Symptoms map:

| Symptom | Probable cause | Check |
|---|---|---|
| Tools never appear in chat | Server unreachable | `curl -v https://your-mcp/jsonrpc -H 'Authorization: Bearer xxx' -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{}}'` |
| Tools appear but call always 401s | Token expired / wrong scope | Rotate token in MCP server's admin → update in Settings |
| Agent says "tool not found" | Server changed its tool name post-cache | Wait 5 min OR delete + re-add the server (forces refresh) |
| Slow chat turns whenever this server is enabled | MCP server slow / hanging | Disable it; check `agent_mcp_tool_call_transport_error` metric for transport timeouts |
| `mcp_tool_call_error` metric climbs | LLM is calling your tools wrong | Check `agent_runs.tool_calls` JSON in admin telemetry; the tool result will include the server's error message |

### Telemetry counters

These show up in `/metrics`:

| Counter | Meaning |
|---|---|
| `agent_chat_mcp_registry_built` | Chat turns where ≥1 MCP server was loaded (cumulative server count) |
| `agent_mcp_tools_injected` | Tool descriptors added to the LLM tools list (cumulative) |
| `agent_mcp_tool_call` | MCP tool calls dispatched |
| `agent_mcp_tool_call_error` | MCP responses with `isError: true` |
| `agent_mcp_tool_call_transport_error` | Network/4xx/5xx failures (server unreachable) |

See [PROMETHEUS_ALERTS.md](./PROMETHEUS_ALERTS.md) for suggested
alerting thresholds.

## Roadmap

In rough priority order:

1. **pgsodium encryption** for `bearer_token` (mig 194 TODO)
2. **Per-tool approval** (extend PlanMode to apply per-call, not just
   per-turn)
3. **stdio transport** so we can wrap any community MCP server
4. **Per-user rate limiting** at MCPClient layer
