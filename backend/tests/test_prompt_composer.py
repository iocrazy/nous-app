"""Unit tests for PromptComposer (AI Library Phase 1).

These exercise the pure helpers (_assemble_system_message, _build_tools,
_fingerprint) directly — no Supabase, no async repos needed. We pass
``None`` in for the repos since the helpers do not touch them.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.ai.prompts.prompt_composer import (
    CACHE_BOUNDARY_MARKER,
    PromptComposer,
)


@pytest.fixture(autouse=True)
def _enable_delegate(monkeypatch):
    """Audit #4: Delegate is gated off by default in prod
    (FEATURE_WORKFORCE_DELEGATE). These tests pin the feature-ON advertisement
    contract; default-off is covered by test_delegate_not_advertised_by_default."""
    monkeypatch.setenv("FEATURE_WORKFORCE_DELEGATE", "1")


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
    msg = composer._assemble_system_message(fake_agent, [], request_instructions=None)
    assert "<available_skills>" not in msg


@pytest.mark.unit
def test_available_workers_section_rendered_when_workers_present(
    fake_agent, fake_skills
):
    """M3: persistent agents listed as Delegate targets in `<available_workers>`."""
    workers = [
        {
            "id": str(uuid4()),
            "slug": "summarize",
            "name": "Summarize",
            "description": "Summarises long text into bullet points",
            "model": "doubao-seed-2-0-pro-260215",
        },
        {
            "id": str(uuid4()),
            "slug": "analyze",
            "name": "Analyze",
            "description": "Visual analysis of frames",
            "model": "doubao-seed-2-0-pro-260215",
        },
    ]
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions=None, workers=workers
    )
    assert "## Available Workers" in msg
    assert "<available_workers>" in msg
    assert "<slug>summarize</slug>" in msg
    assert "<slug>analyze</slug>" in msg
    # Workers section should sit BEFORE cache boundary (stable in prefix).
    assert msg.index("<available_workers>") < msg.index(CACHE_BOUNDARY_MARKER)


@pytest.mark.unit
def test_available_workers_section_absent_when_empty(fake_agent, fake_skills):
    """No persistent workers → no `<available_workers>` block at all."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions=None, workers=[]
    )
    assert "<available_workers>" not in msg
    assert "## Available Workers" not in msg


@pytest.mark.unit
def test_workers_html_escaped_in_description(fake_agent, fake_skills):
    """Worker description with raw < > must be escaped in the XML."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    workers = [
        {
            "id": str(uuid4()),
            "slug": "evil",
            "name": "Evil",
            "description": "uses <tag> & >more<",
            "model": "x",
        }
    ]
    msg = composer._assemble_system_message(
        fake_agent, fake_skills, request_instructions=None, workers=workers
    )
    assert "&lt;tag&gt;" in msg
    # Make sure raw <tag> didn't leak (sandwich check around the worker's
    # description so we don't catch the legitimate <slug> / <description>
    # XML markers).
    desc_block = msg[msg.index("<slug>evil</slug>") :]
    assert "<tag>" not in desc_block


@pytest.mark.unit
def test_prefix_fingerprint_includes_workers(fake_agent, fake_skills):
    """Adding/removing a persistent worker MUST change the fingerprint
    so the prompt cache invalidates."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    fp_no_workers = composer._prefix_fingerprint(fake_agent, fake_skills, [])
    fp_one_worker = composer._prefix_fingerprint(
        fake_agent,
        fake_skills,
        [{"id": str(uuid4()), "slug": "summarize"}],
    )
    assert fp_no_workers != fp_one_worker


@pytest.mark.unit
def test_prefix_fingerprint_stable_across_worker_order(fake_agent, fake_skills):
    """Workers sorted internally → caller's list order doesn't affect fp."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    w1 = {"id": "1111", "slug": "a"}
    w2 = {"id": "2222", "slug": "b"}
    fp_a = composer._prefix_fingerprint(fake_agent, fake_skills, [w1, w2])
    fp_b = composer._prefix_fingerprint(fake_agent, fake_skills, [w2, w1])
    assert fp_a == fp_b


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
    fp1 = composer._prefix_fingerprint(fake_agent, fake_skills)
    fp2 = composer._prefix_fingerprint(fake_agent, fake_skills)
    assert fp1 == fp2


@pytest.mark.unit
def test_cache_fingerprint_changes_when_agent_changes(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    fp1 = composer._prefix_fingerprint(fake_agent, fake_skills)
    fake_agent["agent_md"] = "CHANGED"
    fp2 = composer._prefix_fingerprint(fake_agent, fake_skills)
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
    # The built-in todo skill's arguments are part of the advertised schema:
    # a model cannot use an argument it was never shown (2026-09-06 — doubao
    # lite jammed "?op=replace&items=" into `file` and never produced a todo
    # snapshot). Tokenize-style assertions, not a full pin.
    props = params["properties"]
    assert set(props["op"]["enum"]) == {
        "replace",
        "complete",
        "in_progress",
        "pending",
        "show",
    }
    assert props["items"]["type"] == "array"
    assert props["items"]["items"]["required"] == ["content"]
    assert props["id"]["type"] == "integer"
    assert "todo" in skill_tool["function"]["description"]
    assert params["required"] == ["skill"]  # todo args stay optional
    assert params["required"] == ["skill"]


@pytest.mark.unit
def test_empty_skills_still_advertises_delegate(fake_agent):
    """G milestone: Delegate is independent of skills — coordinator-style
    agents (no skills, just routing) still need it."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools([])
    names = [t["function"]["name"] for t in tools]
    assert names == ["Delegate"]


@pytest.mark.unit
def test_delegate_not_advertised_by_default(fake_agent, fake_skills, monkeypatch):
    """Audit #4: with FEATURE_WORKFORCE_DELEGATE off, Delegate is NOT
    advertised — the execution chain isn't wired, so the LLM must not be
    able to queue tasks that orphan. Skill still advertised when bound."""
    monkeypatch.delenv("FEATURE_WORKFORCE_DELEGATE", raising=False)
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    names = [t["function"]["name"] for t in composer._build_tools(fake_skills)]
    assert "Delegate" not in names
    assert "Skill" in names
    # No skills + gated off → empty tool list.
    assert composer._build_tools([]) == []


@pytest.mark.unit
def test_delegate_tool_advertised_alongside_skill(fake_agent, fake_skills):
    """M2.5: agents must see both Skill and Delegate in their tool list so
    the LLM can choose to dispatch sub-tasks to other persistent agents."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools(fake_skills)
    names = [t["function"]["name"] for t in tools]
    assert "Skill" in names
    assert "Delegate" in names


@pytest.mark.unit
def test_delegate_tool_schema_required_params(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools(fake_skills)
    delegate = next(t for t in tools if t["function"]["name"] == "Delegate")
    params = delegate["function"]["parameters"]
    assert "agent_slug" in params["properties"]
    assert "prompt" in params["properties"]
    # Optional knobs documented but not required
    assert "title" in params["properties"]
    assert "priority" in params["properties"]
    assert "dedup_key" in params["properties"]
    assert set(params["required"]) == {"agent_slug", "prompt"}


@pytest.mark.unit
def test_skill_tool_omitted_when_no_skills(fake_agent):
    """G milestone: when an agent has no bound skills, the Skill tool is
    omitted (loading nothing makes no sense). Delegate stays — coordinators
    without skills still need it."""
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    names = [t["function"]["name"] for t in composer._build_tools([])]
    assert "Skill" not in names
    assert "Delegate" in names


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
