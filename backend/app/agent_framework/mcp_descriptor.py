"""MCP tool descriptor builders.

Sprint 8 primitive. The Model Context Protocol (MCP) is Anthropic's
JSON-RPC protocol for advertising tools / resources / prompts to LLM
clients (Claude Desktop, Claude Code, Cursor, Cline, ...). Exposing
mediahub agents and skills over MCP would let an external Claude
client invoke them directly without routing through the chat panel.

This module is the SHAPE-ONLY layer:
  - dataclasses for the protocol payloads (Tool, ToolInputSchema,
    ToolDescriptor, ToolListResponse, ToolCallResult)
  - adapters that turn an internal Skill / Agent row into a Tool
  - a registry for grouping descriptors per server instance

It does NOT implement the JSON-RPC transport (stdio / websocket /
HTTP). That comes in a follow-up — see DEFERRED in commit message.
Reasons to land the shape layer first:
  1. Forces us to define what we'd actually expose (just tools? also
     resources? prompts?). Easier to iterate on dataclasses than on a
     half-built server.
  2. The descriptor construction logic is the part that's most likely
     to break on schema migrations, so locking it in with tests is
     high-leverage.
  3. The registry can be reused by an in-process "MCP-shaped" agent
     tool harness even before we ship a real MCP server.

Spec reference: https://modelcontextprotocol.io/specification — the
2024-11-05 revision is the basis for these shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class ToolInputSchema:
    """JSONSchema fragment describing a tool's parameters.

    MCP requires ``type='object'`` at the top level. Per-property schemas
    are passed through verbatim — this dataclass just wraps the most
    common case (object with named properties)."""

    properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = field(default_factory=tuple)
    additional_properties: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": "object",
            "properties": dict(self.properties),
            "additionalProperties": self.additional_properties,
        }
        if self.required:
            out["required"] = list(self.required)
        return out


@dataclass(frozen=True)
class Tool:
    """One advertised MCP tool. ``handler`` is internal — never serialized
    over the wire — and is what the dispatcher invokes when the client
    calls this tool."""

    name: str  # e.g. "skill.script-outline" / "agent.script_ai"
    description: str
    input_schema: ToolInputSchema
    handler: Callable[..., Any]

    def to_descriptor(self) -> dict[str, Any]:
        """JSON-serializable form for `tools/list` responses."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema.to_dict(),
        }


@dataclass(frozen=True)
class ToolCallResult:
    """Return value from a tool invocation. ``isError`` flag mirrors
    the MCP spec's distinction between protocol errors (transport-level)
    and tool errors (handler returned an error result)."""

    content: list[dict[str, Any]]
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": list(self.content),
            "isError": self.is_error,
        }

    @classmethod
    def text(cls, message: str, *, is_error: bool = False) -> "ToolCallResult":
        return cls(
            content=[{"type": "text", "text": message}],
            is_error=is_error,
        )


class DuplicateToolError(ValueError):
    """A tool name was registered twice in the same registry."""


class MCPToolRegistry:
    """Per-server registry of MCP-exposed tools.

    Surfaces (skills, agents, custom utilities) register their adapter
    on startup. The transport layer (added later) calls list/get to
    serve `tools/list` and `tools/call`.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("tool.name must be non-empty")
        if tool.name in self._tools:
            raise DuplicateToolError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def list_descriptors(self) -> list[dict[str, Any]]:
        """All descriptors as a JSON-friendly list (tools/list response body)."""
        return [self._tools[name].to_descriptor() for name in sorted(self._tools)]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools


# ─── Adapters: internal types → MCP Tool ──────────────────────────────


def skill_to_tool(
    *,
    slug: str,
    description: str,
    handler: Callable[..., Any],
    input_schema: Optional[ToolInputSchema] = None,
) -> Tool:
    """Wrap a skill as an MCP tool. Name conventionally prefixed
    'skill.' so a Claude Desktop user can tell agents from skills."""
    return Tool(
        name=f"skill.{slug}",
        description=description,
        input_schema=input_schema or ToolInputSchema(),
        handler=handler,
    )


def agent_to_tool(
    *,
    slug: str,
    description: str,
    handler: Callable[..., Any],
    input_schema: Optional[ToolInputSchema] = None,
) -> Tool:
    """Wrap an agent as an MCP tool. Name prefixed 'agent.'."""
    schema = input_schema or ToolInputSchema(
        properties={
            "prompt": {
                "type": "string",
                "description": "User instruction for this agent.",
            }
        },
        required=("prompt",),
    )
    return Tool(
        name=f"agent.{slug}",
        description=description,
        input_schema=schema,
        handler=handler,
    )


__all__ = [
    "DuplicateToolError",
    "MCPToolRegistry",
    "Tool",
    "ToolCallResult",
    "ToolInputSchema",
    "agent_to_tool",
    "skill_to_tool",
]
