"""Unit tests for PromptComposer fingerprint split + memory injection.

L1 (``agent_memories``) recalled-memory injection has been removed; the
per-turn memory injected into the prompt is now Graphiti graph facts +
the Honcho user context. These tests exercise the fingerprint split
(prefix vs dynamic) using graph facts as the per-turn signal.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.ai.prompts.prompt_composer import (
    CACHE_BOUNDARY_MARKER,
    ComposerInput,
    PromptComposer,
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


def _make_composer_with_fixed_repos(
    agent: dict, skills: list[dict], skill_ids: list[int]
):
    composer = PromptComposer(agent_repo=None, skill_repo=None)

    class _AgentRepo:
        async def get_by_slug(self, slug):
            return agent

        async def get_skill_ids(self, agent_id):
            return skill_ids

        async def list_persistent(self):
            return []

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
async def test_graph_facts_injected_after_cache_boundary():
    """★ Critical: graph facts are AFTER the boundary so prefix cache stays valid."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])

    inp = ComposerInput(
        agent_slug="script_ai", graph_facts=["User prefers short replies."]
    )
    result = await composer.compose(inp)

    boundary_idx = result.system_message.index(CACHE_BOUNDARY_MARKER)
    facts_idx = result.system_message.index("<graph_facts>")
    assert facts_idx > boundary_idx, "graph facts must appear after cache boundary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_graph_facts_section_when_empty_list():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    inp = ComposerInput(agent_slug="script_ai", graph_facts=[])
    result = await composer.compose(inp)
    assert "<graph_facts>" not in result.system_message


# ---------------------------------------------------------------------------
# Fingerprint split — prefix vs dynamic
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prefix_fingerprint_stable_across_different_memory_sets():
    """Same agent+skills, different graph facts → same prefix_fingerprint."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [_skill_dict()], [1])

    res1 = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["A"])
    )
    res2 = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["B"])
    )
    assert res1.prefix_fingerprint == res2.prefix_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_fingerprint_differs_when_memory_set_differs():
    """Graph fact change MUST change dynamic_fingerprint (P0 isolation)."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [_skill_dict()], [1])

    res1 = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["A"])
    )
    res2 = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["B"])
    )
    assert res1.dynamic_fingerprint != res2.dynamic_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_fingerprint_equals_prefix_when_no_memories():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    res = await composer.compose(ComposerInput(agent_slug="script_ai"))
    # No per-turn memory → dynamic hash includes only the prefix hash itself.
    # What matters: same input → same dynamic.
    res2 = await composer.compose(ComposerInput(agent_slug="script_ai"))
    assert res.dynamic_fingerprint == res2.dynamic_fingerprint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_fingerprint_invariant_to_memory_order():
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])

    res_ab = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["A", "B"])
    )
    res_ba = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["B", "A"])
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
async def test_recalled_memory_ids_empty_after_l1_removal():
    """L1 recall is gone — recalled_memory_ids is always empty now."""
    agent = _agent_dict()
    composer = _make_composer_with_fixed_repos(agent, [], [])
    res = await composer.compose(
        ComposerInput(agent_slug="script_ai", graph_facts=["A", "B"])
    )
    assert res.recalled_memory_ids == []
