"""Build the MCP registry from real skills + agents.

P1-7 wire-up. Sprint 8/8.5 landed MCPToolRegistry + stdio transport
but no concrete tool registration. Without this module, Claude
Desktop / Cursor can connect but see an empty tools/list.

This module is the bridge:
  - Read skills from SkillRepository → wrap as Tool via skill_to_tool
  - Read persistent agents from AgentRepository → wrap via agent_to_tool
  - Each tool's handler returns body_md (skills) or a placeholder
    "agent invocation deferred" message (agents — full agent execution
    over MCP needs the Workforce-via-MCP design which is bigger work).

The handler indirection is deliberate: today a Skill executed via
chat goes through SkillToolService which uses the in-process
adapter / RunRecorder / hooks chain. Replicating that over MCP would
mean exposing the agent runner over a remote-call shape, which is
a larger design (auth, rate limit, billing). For Sprint 8.5+ we ship
"tools/list shows what's available + tools/call returns the skill
description and instruction body" — useful as a knowledge surface
to external Claude clients, not yet a full execution surface.

Design notes:
  - Builds a fresh registry each call (no module-level caching) so
    `python -m app.agent_framework.mcp_stdio` reflects current DB
    state on every spawn.
  - Skill input schemas are intentionally permissive (additionalProperties
    True) — skills accept arbitrary kwargs and we don't have schema
    annotations stored for them yet. Tighten when skill_files grows
    a `parameters_json` column.
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.agent_framework.mcp_descriptor import (
    MCPToolRegistry,
    ToolCallResult,
    ToolInputSchema,
    agent_to_tool,
    skill_to_tool,
)
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository


def _make_skill_handler(skill_row: dict[str, Any]):
    """Build the (async) handler closure for one skill.

    Returns the skill's body_md so MCP clients can read the playbook
    even when remote execution isn't wired. ``slug`` and ``description``
    are echoed in the response so the client can verify it got what
    it asked for.
    """
    body = skill_row.get("body_md") or ""
    slug = skill_row.get("slug") or "(unknown)"

    async def _handler(**_kw):
        if not body:
            return ToolCallResult.text(
                f"skill '{slug}' has no body_md — "
                "check seed loading or DB row.",
                is_error=True,
            )
        return ToolCallResult.text(body)

    return _handler


def _make_agent_handler(agent_row: dict[str, Any]):
    """Placeholder agent handler: returns the agent's identity + soul +
    instruction so the client knows what would happen. Full execution
    over MCP is a bigger work item (auth / rate limit / billing)."""
    slug = agent_row.get("slug") or "(unknown)"
    identity = agent_row.get("identity_md") or ""
    soul = agent_row.get("soul_md") or ""
    instruction = agent_row.get("agent_md") or ""

    async def _handler(**kwargs):
        prompt = kwargs.get("prompt", "")
        return ToolCallResult.text(
            f"# Agent: {slug}\n"
            f"(MCP-side execution not yet wired — returning agent persona.)\n\n"
            f"## Identity\n{identity}\n\n"
            f"## Soul\n{soul}\n\n"
            f"## Instructions\n{instruction}\n\n"
            f"## Caller's prompt\n{prompt}"
        )

    return _handler


async def build_mcp_registry(
    *,
    skill_repo: Optional[SkillRepository] = None,
    agent_repo: Optional[AgentRepository] = None,
    include_skills: bool = True,
    include_agents: bool = True,
) -> MCPToolRegistry:
    """Build a fresh MCPToolRegistry populated from current DB state.

    Repos default to standard instances; tests inject fakes. Failures
    in either pass log + skip — empty registry is preferable to a
    crashed stdio process.
    """
    registry = MCPToolRegistry()
    skill_repo = skill_repo or SkillRepository()
    agent_repo = agent_repo or AgentRepository()

    if include_skills:
        try:
            skills = await skill_repo.list_skills()
            for s in skills:
                slug = s.get("slug")
                if not slug:
                    continue
                description = s.get("description") or s.get("name") or slug
                tool = skill_to_tool(
                    slug=slug,
                    description=description,
                    handler=_make_skill_handler(s),
                    input_schema=ToolInputSchema(additional_properties=True),
                )
                try:
                    registry.register(tool)
                except Exception as reg_exc:
                    logger.warning(
                        f"MCP: skill '{slug}' registration skipped: {reg_exc}"
                    )
            logger.info(f"MCP: registered {len(skills)} skill tools")
        except Exception as exc:
            logger.warning(f"MCP: skill registration pass failed: {exc}")

    if include_agents:
        try:
            agents = await _list_persistent_agents(agent_repo)
            for a in agents:
                slug = a.get("slug")
                if not slug:
                    continue
                description = a.get("name") or slug
                tool = agent_to_tool(
                    slug=slug,
                    description=description,
                    handler=_make_agent_handler(a),
                )
                try:
                    registry.register(tool)
                except Exception as reg_exc:
                    logger.warning(
                        f"MCP: agent '{slug}' registration skipped: {reg_exc}"
                    )
            logger.info(f"MCP: registered {len(agents)} agent tools")
        except Exception as exc:
            logger.warning(f"MCP: agent registration pass failed: {exc}")

    return registry


async def _list_persistent_agents(agent_repo: Any) -> list[dict[str, Any]]:
    """Persistent agents (worker-style, exposed for delegation) — these
    are the ones with Delegate-as-tool semantics. Skip ephemeral / chat-
    only agents from MCP exposure."""
    if hasattr(agent_repo, "list_persistent"):
        return await agent_repo.list_persistent()
    # Fallback: read all agents.
    try:
        client = await agent_repo._get_client()  # noqa: SLF001
        result = await client.table("ai_agents").select("*").execute()
        return result.data or []
    except Exception:
        return []


__all__ = ["build_mcp_registry"]
