"""Unit tests for PromptComposer (AI Library Phase 1).

These exercise the pure helpers (_assemble_system_message, _build_tools,
_fingerprint) directly — no Supabase, no async repos needed. We pass
``None`` in for the repos since the helpers do not touch them.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.prompt_composer import (
    CACHE_BOUNDARY_MARKER,
    PromptComposer,
)


@pytest.fixture
def fake_agent():
    return {
        "id": str(uuid4()),
        "slug": "script_ai",
        "name": "Script AI",
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "identity_md": "I am the Script AI.",
        "soul_md": "I write crisp cinematic prose.",
        "agent_md": "When asked, produce HTML script output.",
        "updated_at": "2026-04-21T00:00:00Z",
    }


@pytest.fixture
def fake_skills():
    return [
        {
            "id": 123,
            "slug": "script-outline",
            "name": "Script Outline",
            "description": "Produce a 3-act outline.",
            "updated_at": "2026-04-21T00:00:00Z",
        }
    ]


@pytest.mark.unit
def test_sections_appear_in_order(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions=None
    )
    identity_pos = msg.index("# Identity")
    soul_pos = msg.index("# Soul")
    agent_pos = msg.index("# Agent Instructions")
    skills_pos = msg.index("## Available Skills")
    assert identity_pos < soul_pos < agent_pos < skills_pos


@pytest.mark.unit
def test_missing_identity_and_soul_skip_sections(fake_agent, fake_skills):
    fake_agent["identity_md"] = None
    fake_agent["soul_md"] = "   "  # whitespace-only → skip
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions=None
    )
    assert "# Identity" not in msg
    assert "# Soul" not in msg
    assert "# Agent Instructions" in msg


@pytest.mark.unit
def test_skills_xml_section_present(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions=None
    )
    assert "<available_skills>" in msg
    assert "<name>script-outline</name>" in msg
    assert "<description>Produce a 3-act outline.</description>" in msg


@pytest.mark.unit
def test_empty_skills_skips_xml_section(fake_agent):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, [], request_instructions=None
    )
    assert "<available_skills>" not in msg


@pytest.mark.unit
def test_cache_boundary_position(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions="Do X"
    )
    boundary_pos = msg.index(CACHE_BOUNDARY_MARKER)
    skills_pos = msg.index("<available_skills>")
    req_pos = msg.index("# Request Instructions")
    assert skills_pos < boundary_pos < req_pos


@pytest.mark.unit
def test_cache_fingerprint_stable(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    fp1 = composer._fingerprint(fake_agent, fake_skills)
    fp2 = composer._fingerprint(fake_agent, fake_skills)
    assert fp1 == fp2


@pytest.mark.unit
def test_cache_fingerprint_changes_when_agent_changes(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    fp1 = composer._fingerprint(fake_agent, fake_skills)
    fake_agent["agent_md"] = "CHANGED"
    fp2 = composer._fingerprint(fake_agent, fake_skills)
    assert fp1 != fp2


@pytest.mark.unit
def test_skill_tool_schema_injected(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools(fake_skills)
    names = [t["function"]["name"] for t in tools]
    assert "Skill" in names


@pytest.mark.unit
def test_skill_tool_has_required_params(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools(fake_skills)
    skill_tool = next(t for t in tools if t["function"]["name"] == "Skill")
    params = skill_tool["function"]["parameters"]
    assert "skill" in params["properties"]
    assert "file" in params["properties"]
    assert params["required"] == ["skill"]


@pytest.mark.unit
def test_empty_skills_produces_no_tools(fake_agent):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    assert composer._build_tools([]) == []


@pytest.mark.unit
def test_description_html_escaped():
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    skills = [
        {
            "id": 1,
            "slug": "evil",
            "description": "uses <tag> & >more<",
            "updated_at": "x",
        }
    ]
    msg = composer._assemble_system_message(
        {"id": str(uuid4()), "slug": "a", "agent_md": "x", "model": "m"},
        skills,
        request_instructions=None,
    )
    assert "&lt;tag&gt;" in msg
    assert "<tag>" not in msg  # un-escaped original should not leak
