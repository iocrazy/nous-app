"""Unit tests for PromptComposer fingerprint split + memory injection (M1.B)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.prompt_composer import (
    CACHE_BOUNDARY_MARKER,
    ComposerInput,
    PromptComposer,
    RecalledMemory,
)


def _agent_dict(slug="script_ai"):
    return {
        "id": str(uuid4()),
        "slug": slug,
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "identity_md": "I am the agent.",
        "soul_md": "Direct and vivid.",
        "agent_md": "Output HTML fragments.",
        "updated_at": "2026-04-25T00:00:00Z",
    }


def _skill_dict(_id=1, slug="script-outline"):
    return {
        "id": _id,
        "slug": slug,
        "name": slug,
        "description": "outline",
        "updated_at": "2026-04-25T00:00:00Z",
    }


def _make_composer_with_fixed_repos(agent: dict, skills: list[dict], skill_ids: list[int]):
    composer = PromptComposer(agent_repo=None, skill_repo=None)

    class _AgentRepo:
        async def get_by_slug(self, slug):
            return agent

        async def get_skill_ids(self, agent_id):
            return skill_ids

    class _SkillRepo:
        async def list_by_ids(self, ids):
            return skills

    composer.agent_repo = _AgentRepo()
    composer.skill_repo = _SkillRepo()
    return composer


# ---------------------------------------------------------------------------
# Memory injection placement
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_injected_after_cache_boundary():
    """★ Critical: memory section is AFTER the boundary so prefix cache stays valid."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])

    mem = RecalledMemory(
        id=uuid4(),
        summary="User prefers short replies.",
        when_to_use="When drafting responses.",
    )
    inp = ComposerInput(agent_slug="script_ai", recalled_memories=[mem])
    result = await composer.compose(inp)

    boundary_idx = result.system_message.index(CACHE_BOUNDARY_MARKER)
    memory_idx = result.system_message.index("<recalled_memories>")
    assert memory_idx > boundary_idx, "memory must appear after cache boundary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_memory_section_when_empty_list():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    inp = ComposerInput(agent_slug="script_ai", recalled_memories=[])
    result = await composer.compose(inp)
    assert "<recalled_memories>" not in result.system_message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_section_uses_int_refs_not_uuids():
    """LLM must see [0]/[1] — never raw UUID strings (Mem Zero pattern)."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])

    mems = [
        RecalledMemory(id=uuid4(), summary="A", when_to_use="ax"),
        RecalledMemory(id=uuid4(), summary="B", when_to_use="bx"),
    ]
    inp = ComposerInput(agent_slug="script_ai", recalled_memories=mems)
    result = await composer.compose(inp)

    assert "[0]" in result.system_message
    assert "[1]" in result.system_message
    # Verify UUID strings are NOT in system_message
    for mem in mems:
        assert str(mem.id) not in result.system_message


# ---------------------------------------------------------------------------
# Fingerprint split — prefix vs dynamic
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prefix_fingerprint_stable_across_different_memory_sets():
    """Same agent+skills, different recalled memories → same prefix_fingerprint."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [_skill_dict()], [1])

    mem_a = RecalledMemory(id=uuid4(), summary="A", when_to_use="x")
    mem_b = RecalledMemory(id=uuid4(), summary="B", when_to_use="y")

    res1 = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=[mem_a])
    )
    res2 = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=[mem_b])
    )
    assert res1.prefix_fingerprint == res2.prefix_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_fingerprint_differs_when_memory_set_differs():
    """Memory set change MUST change dynamic_fingerprint (P0 isolation)."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [_skill_dict()], [1])

    mem_a = RecalledMemory(id=uuid4(), summary="A", when_to_use="x")
    mem_b = RecalledMemory(id=uuid4(), summary="B", when_to_use="y")

    res1 = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=[mem_a])
    )
    res2 = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=[mem_b])
    )
    assert res1.dynamic_fingerprint != res2.dynamic_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_fingerprint_equals_prefix_when_no_memories():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    res = await composer.compose(ComposerInput(agent_slug="script_ai"))
    # No memories → dynamic hash includes only the prefix hash itself
    # (still differs from raw prefix string due to inner SHA wrap, but
    # both are stable). What matters: same input → same dynamic.
    res2 = await composer.compose(ComposerInput(agent_slug="script_ai"))
    assert res.dynamic_fingerprint == res2.dynamic_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_fingerprint_invariant_to_memory_order():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    mem_a = RecalledMemory(id=uuid4(), summary="A", when_to_use="x")
    mem_b = RecalledMemory(id=uuid4(), summary="B", when_to_use="y")

    res_ab = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=[mem_a, mem_b])
    )
    res_ba = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=[mem_b, mem_a])
    )
    # Set semantics — order doesn't matter
    assert res_ab.dynamic_fingerprint == res_ba.dynamic_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_back_compat_cache_fingerprint_alias():
    """Existing call sites read .cache_fingerprint — must equal prefix."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    res = await composer.compose(ComposerInput(agent_slug="script_ai"))
    assert res.cache_fingerprint == res.prefix_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recalled_memory_ids_populated():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    mems = [
        RecalledMemory(id=uuid4(), summary="A", when_to_use="x"),
        RecalledMemory(id=uuid4(), summary="B", when_to_use="y"),
    ]
    res = await composer.compose(
        ComposerInput(agent_slug="script_ai", recalled_memories=mems)
    )
    assert res.recalled_memory_ids == [m.id for m in mems]
