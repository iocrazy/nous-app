"""M2 — memory kind classifier (declarative / procedural / episodic)."""
from __future__ import annotations

import pytest

from app.services.memory.kind_classifier import (
    KIND_WEIGHT_MODIFIERS,
    MemoryKind,
    classify_heuristic,
    classify_with_llm,
    score_modifier_for,
)


# ─── Heuristic ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_default_is_declarative():
    assert classify_heuristic("user prefers Vue") == MemoryKind.DECLARATIVE


@pytest.mark.unit
def test_procedural_how_to():
    assert classify_heuristic("How to deploy: run make build") == MemoryKind.PROCEDURAL


@pytest.mark.unit
def test_procedural_to_verb():
    assert classify_heuristic("To deploy the backend, push to master") == MemoryKind.PROCEDURAL


@pytest.mark.unit
def test_procedural_chinese():
    assert classify_heuristic("部署步骤: 1. 打包") == MemoryKind.PROCEDURAL


@pytest.mark.unit
def test_episodic_yesterday():
    assert classify_heuristic("Yesterday the build failed") == MemoryKind.EPISODIC


@pytest.mark.unit
def test_episodic_iso_date():
    assert classify_heuristic("On 2026-04-25 we shipped v0.7") == MemoryKind.EPISODIC


@pytest.mark.unit
def test_episodic_past_tense_failure():
    assert classify_heuristic("The deploy crashed at startup") == MemoryKind.EPISODIC


@pytest.mark.unit
def test_episodic_chinese():
    assert classify_heuristic("昨天部署崩了") == MemoryKind.EPISODIC


@pytest.mark.unit
def test_both_cues_picks_longer_match():
    """'How to debug yesterday's crash' — both fire; longer wins."""
    # 'how to' (6) vs 'yesterday' (9) → episodic
    text = "How to debug yesterday's crash"
    result = classify_heuristic(text)
    assert result == MemoryKind.EPISODIC


@pytest.mark.unit
def test_empty_returns_declarative():
    assert classify_heuristic("") == MemoryKind.DECLARATIVE
    assert classify_heuristic(None) == MemoryKind.DECLARATIVE  # type: ignore[arg-type]


# ─── LLM classifier ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_returns_canonical_value():
    async def _stub(prompt):
        return "procedural"

    result = await classify_with_llm("how to do X", _stub)
    assert result == MemoryKind.PROCEDURAL


@pytest.mark.asyncio
async def test_llm_tolerates_punctuation_and_case():
    async def _stub(prompt):
        return "EPISODIC."

    result = await classify_with_llm("text", _stub)
    assert result == MemoryKind.EPISODIC


@pytest.mark.asyncio
async def test_llm_takes_first_token():
    async def _stub(prompt):
        return "declarative — because it's a stable fact"

    result = await classify_with_llm("text", _stub)
    assert result == MemoryKind.DECLARATIVE


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_heuristic():
    async def _broken(prompt):
        raise RuntimeError("LLM down")

    # Heuristic recognizes "how to" → procedural
    result = await classify_with_llm("how to deploy", _broken)
    assert result == MemoryKind.PROCEDURAL


@pytest.mark.asyncio
async def test_llm_garbled_falls_back_to_heuristic():
    async def _confused(prompt):
        return "I'm not sure about this one"

    result = await classify_with_llm("user uses Python", _confused)
    assert result == MemoryKind.DECLARATIVE


@pytest.mark.asyncio
async def test_llm_empty_summary_returns_declarative():
    async def _stub(prompt):
        raise AssertionError("should not be called for empty input")

    result = await classify_with_llm("", _stub)
    assert result == MemoryKind.DECLARATIVE


# ─── Score modifier ──────────────────────────────────────────────────


@pytest.mark.unit
def test_score_modifier_per_kind():
    assert score_modifier_for(MemoryKind.DECLARATIVE) == 1.0
    assert score_modifier_for(MemoryKind.PROCEDURAL) == 1.1
    assert score_modifier_for(MemoryKind.EPISODIC) == 0.9


@pytest.mark.unit
def test_score_modifier_none_default_one():
    """Legacy rows without kind classification — neutral weighting."""
    assert score_modifier_for(None) == 1.0


@pytest.mark.unit
def test_kind_modifiers_all_present():
    """Sanity: every MemoryKind has a weight modifier defined."""
    for kind in MemoryKind:
        assert kind in KIND_WEIGHT_MODIFIERS
