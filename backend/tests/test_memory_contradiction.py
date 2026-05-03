"""D3 — memory contradiction resolution: writer-time conflict checks."""
from __future__ import annotations

import pytest

from app.services.memory.contradiction import (
    ELITE_SIMILARITY,
    HIGH_SIMILARITY,
    SUPERSEDING_VERDICTS,
    ContradictionDecision,
    ContradictionVerdict,
    build_contradiction_prompt,
    classify_pair,
    parse_verdict,
    select_supersede_targets,
)


# ─── parse_verdict ────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_verdict_canonical_values():
    assert parse_verdict("replaces") == ContradictionVerdict.REPLACES
    assert parse_verdict("contradicts") == ContradictionVerdict.CONTRADICTS
    assert parse_verdict("supplements") == ContradictionVerdict.SUPPLEMENTS
    assert parse_verdict("unrelated") == ContradictionVerdict.UNRELATED


@pytest.mark.unit
def test_parse_verdict_tolerates_whitespace_and_case():
    assert parse_verdict("  REPLACES  ") == ContradictionVerdict.REPLACES
    assert parse_verdict("Replaces.") == ContradictionVerdict.REPLACES


@pytest.mark.unit
def test_parse_verdict_takes_first_token():
    """LLMs sometimes add commentary even when told not to."""
    assert (
        parse_verdict("replaces. Because the user said...")
        == ContradictionVerdict.REPLACES
    )


@pytest.mark.unit
def test_parse_verdict_unknown_falls_back_to_unrelated():
    """Safest default: keep both memories rather than wrongly supersede."""
    assert parse_verdict("maybe") == ContradictionVerdict.UNRELATED
    assert parse_verdict("") == ContradictionVerdict.UNRELATED
    assert parse_verdict("   ") == ContradictionVerdict.UNRELATED


# ─── build_contradiction_prompt ──────────────────────────────────────


@pytest.mark.unit
def test_prompt_includes_both_memories():
    p = build_contradiction_prompt(
        old_summary="user prefers Vue", new_summary="user now uses React"
    )
    assert "user prefers Vue" in p
    assert "user now uses React" in p
    # All four verdicts named
    for v in ("replaces", "contradicts", "supplements", "unrelated"):
        assert v in p


# ─── classify_pair ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_decision():
    async def _classifier(prompt):
        return "replaces"

    d = await classify_pair(
        old_summary="user prefers Vue",
        old_id="m-1",
        new_summary="user now uses React",
        classifier=_classifier,
    )
    assert d is not None
    assert d.verdict == ContradictionVerdict.REPLACES
    assert d.old_memory_id == "m-1"
    assert d.raw_llm_output == "replaces"


@pytest.mark.asyncio
async def test_classify_classifier_failure_returns_none():
    async def _broken(prompt):
        raise RuntimeError("LLM down")

    d = await classify_pair(
        old_summary="x", old_id="m-1", new_summary="y", classifier=_broken
    )
    assert d is None


@pytest.mark.asyncio
async def test_classify_unparseable_output_keeps_both():
    """LLM returns garbage → verdict UNRELATED → both memories kept."""
    async def _confused(prompt):
        return "I'm not sure"

    d = await classify_pair(
        old_summary="x", old_id="m-1", new_summary="y", classifier=_confused
    )
    assert d is not None
    assert d.verdict == ContradictionVerdict.UNRELATED


# ─── select_supersede_targets ────────────────────────────────────────


@pytest.mark.unit
def test_select_includes_replaces_and_contradicts():
    decisions = [
        ContradictionDecision("m-1", ContradictionVerdict.REPLACES, "replaces"),
        ContradictionDecision("m-2", ContradictionVerdict.CONTRADICTS, "contradicts"),
        ContradictionDecision("m-3", ContradictionVerdict.SUPPLEMENTS, "supplements"),
        ContradictionDecision("m-4", ContradictionVerdict.UNRELATED, "unrelated"),
    ]
    targets = select_supersede_targets(decisions)
    assert set(targets) == {"m-1", "m-2"}


@pytest.mark.unit
def test_select_empty_when_no_conflicts():
    decisions = [
        ContradictionDecision("m-1", ContradictionVerdict.UNRELATED, "unrelated"),
    ]
    assert select_supersede_targets(decisions) == []


@pytest.mark.unit
def test_select_empty_input():
    assert select_supersede_targets([]) == []


# ─── Constants ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_thresholds_are_documented():
    assert 0 < HIGH_SIMILARITY < ELITE_SIMILARITY < 1.0


@pytest.mark.unit
def test_superseding_verdicts_documented():
    """Sanity: REPLACES + CONTRADICTS are the supersede triggers."""
    assert ContradictionVerdict.REPLACES in SUPERSEDING_VERDICTS
    assert ContradictionVerdict.CONTRADICTS in SUPERSEDING_VERDICTS
    assert ContradictionVerdict.SUPPLEMENTS not in SUPERSEDING_VERDICTS
    assert ContradictionVerdict.UNRELATED not in SUPERSEDING_VERDICTS
