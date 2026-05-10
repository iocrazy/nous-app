"""P1-7 — MCP registry built from real skill/agent rows."""

from __future__ import annotations

import pytest

from app.agent_framework.mcp_descriptor import (
    MCPToolRegistry,
    ToolCallResult,
)
from app.services.ai.skills.mcp_tool_registration import build_mcp_registry


class _StubSkillRepo:
    def __init__(self, skills):
        self._skills = skills

    async def list_skills(self):
        return self._skills


class _StubAgentRepo:
    def __init__(self, agents):
        self._agents = agents

    async def list_persistent(self):
        return self._agents


@pytest.mark.asyncio
async def test_skill_registration_basic():
    skills = [
        {
            "slug": "script-outline",
            "description": "outline generator",
            "body_md": "Step 1: ...",
        },
        {
            "slug": "summarize",
            "description": "summarize text",
            "body_md": "Read carefully then ...",
        },
    ]
    reg = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo([]),
    )
    assert isinstance(reg, MCPToolRegistry)
    assert "skill.script-outline" in reg
    assert "skill.summarize" in reg


@pytest.mark.asyncio
async def test_skill_handler_returns_body_md():
    skills = [
        {"slug": "test", "description": "d", "body_md": "the playbook"},
    ]
    reg = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo([]),
    )
    tool = reg.get("skill.test")
    result = await tool.handler()
    assert isinstance(result, ToolCallResult)
    assert result.content[0]["text"] == "the playbook"
    assert result.is_error is False


@pytest.mark.asyncio
async def test_skill_handler_error_when_body_missing():
    skills = [{"slug": "empty", "description": "d", "body_md": ""}]
    reg = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo([]),
    )
    tool = reg.get("skill.empty")
    result = await tool.handler()
    assert result.is_error is True


@pytest.mark.asyncio
async def test_skill_without_slug_skipped():
    skills = [
        {"description": "d", "body_md": "x"},  # no slug
        {"slug": "ok", "description": "d", "body_md": "y"},
    ]
    reg = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo([]),
    )
    assert "skill.ok" in reg
    assert len(reg) == 1


@pytest.mark.asyncio
async def test_agent_registration_persona_handler():
    agents = [
        {
            "slug": "script_ai",
            "name": "Script AI",
            "identity_md": "I write scripts",
            "soul_md": "Tone: lively",
            "agent_md": "Always think first",
        },
    ]
    reg = await build_mcp_registry(
        skill_repo=_StubSkillRepo([]),
        agent_repo=_StubAgentRepo(agents),
    )
    assert "agent.script_ai" in reg
    tool = reg.get("agent.script_ai")
    result = await tool.handler(prompt="write a 30s script")
    assert isinstance(result, ToolCallResult)
    text = result.content[0]["text"]
    assert "I write scripts" in text
    assert "Tone: lively" in text
    assert "write a 30s script" in text


@pytest.mark.asyncio
async def test_skill_repo_failure_does_not_crash():
    """Best-effort: DB error in skill pass leaves agent pass intact."""

    class _BrokenSkillRepo:
        async def list_skills(self):
            raise RuntimeError("DB exploded")

    reg = await build_mcp_registry(
        skill_repo=_BrokenSkillRepo(),
        agent_repo=_StubAgentRepo([{"slug": "x", "name": "X"}]),
    )
    # Skill pass failed silently; agent pass succeeded
    assert "skill.anything" not in reg
    assert "agent.x" in reg


@pytest.mark.asyncio
async def test_include_flags():
    skills = [{"slug": "s1", "description": "d", "body_md": "x"}]
    agents = [{"slug": "a1", "name": "A1"}]

    reg_skills_only = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo(agents),
        include_agents=False,
    )
    assert "skill.s1" in reg_skills_only
    assert "agent.a1" not in reg_skills_only

    reg_agents_only = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo(agents),
        include_skills=False,
    )
    assert "skill.s1" not in reg_agents_only
    assert "agent.a1" in reg_agents_only


@pytest.mark.asyncio
async def test_skill_input_schema_permissive():
    """Skills accept arbitrary kwargs — schema must allow that."""
    skills = [{"slug": "s", "description": "d", "body_md": "x"}]
    reg = await build_mcp_registry(
        skill_repo=_StubSkillRepo(skills),
        agent_repo=_StubAgentRepo([]),
    )
    tool = reg.get("skill.s")
    desc = tool.to_descriptor()
    assert desc["inputSchema"]["additionalProperties"] is True
