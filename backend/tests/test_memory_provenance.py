"""R1 — memory provenance verification."""
from __future__ import annotations

import pytest

from app.services.ai.memory.provenance import (
    VerificationDecision,
    VerificationVerdict,
    build_verification_prompt,
    parse_verdict,
    select_outdated,
    verify_one,
)


# ─── parse_verdict ────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_canonical():
    assert parse_verdict("still_true") == VerificationVerdict.STILL_TRUE
    assert parse_verdict("outdated") == VerificationVerdict.OUTDATED
    assert parse_verdict("unverifiable") == VerificationVerdict.UNVERIFIABLE


@pytest.mark.unit
def test_parse_tolerates_case_and_punct():
    assert parse_verdict("OUTDATED.") == VerificationVerdict.OUTDATED
    assert parse_verdict("Still_True") == VerificationVerdict.STILL_TRUE


@pytest.mark.unit
def test_parse_takes_first_token():
    assert (
        parse_verdict("outdated. Because user said...")
        == VerificationVerdict.OUTDATED
    )


@pytest.mark.unit
def test_parse_unknown_defaults_to_unverifiable():
    """Safety: ambiguous output never marks a memory outdated."""
    assert parse_verdict("hmm") == VerificationVerdict.UNVERIFIABLE
    assert parse_verdict("") == VerificationVerdict.UNVERIFIABLE
    assert parse_verdict(None) == VerificationVerdict.UNVERIFIABLE  # type: ignore[arg-type]


# ─── build_verification_prompt ───────────────────────────────────────


@pytest.mark.unit
def test_prompt_includes_memory_and_context():
    p = build_verification_prompt(
        memory="user prefers Vue",
        context="user said 'I now use React for everything'",
    )
    assert "user prefers Vue" in p
    assert "I now use React" in p


@pytest.mark.unit
def test_prompt_handles_empty_context():
    p = build_verification_prompt(memory="x", context="")
    assert "(none)" in p


@pytest.mark.unit
def test_prompt_caps_input_lengths():
    """Long memory / context truncated to keep cheap-LLM prompt small."""
    long_mem = "x" * 1000
    long_ctx = "y" * 5000
    p = build_verification_prompt(memory=long_mem, context=long_ctx)
    # No multi-MB explosion
    assert len(p) < 4000


# ─── verify_one ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_returns_decision_on_success():
    async def _classifier(prompt):
        return "outdated"

    d = await verify_one(
        memory_id="m1",
        memory_summary="user prefers Vue",
        context="user now uses React",
        classifier=_classifier,
    )
    assert d is not None
    assert d.verdict == VerificationVerdict.OUTDATED
    assert d.memory_id == "m1"


@pytest.mark.asyncio
async def test_verify_classifier_failure_returns_none():
    async def _broken(prompt):
        raise RuntimeError("LLM down")

    d = await verify_one(
        memory_id="m1",
        memory_summary="x",
        context="y",
        classifier=_broken,
    )
    assert d is None


@pytest.mark.asyncio
async def test_verify_garbled_output_unverifiable():
    """Tolerant parse → safe default."""
    async def _confused(prompt):
        return "I'm not sure"

    d = await verify_one(
        memory_id="m1", memory_summary="x", context="y", classifier=_confused
    )
    assert d is not None
    assert d.verdict == VerificationVerdict.UNVERIFIABLE


# ─── select_outdated ────────────────────────────────────────────────


@pytest.mark.unit
def test_select_outdated_only_includes_outdated():
    decisions = [
        VerificationDecision("m1", VerificationVerdict.STILL_TRUE, "still_true"),
        VerificationDecision("m2", VerificationVerdict.OUTDATED, "outdated"),
        VerificationDecision("m3", VerificationVerdict.UNVERIFIABLE, "unverifiable"),
        VerificationDecision("m4", VerificationVerdict.OUTDATED, "outdated"),
    ]
    targets = select_outdated(decisions)
    assert set(targets) == {"m2", "m4"}


@pytest.mark.unit
def test_select_unverifiable_is_NOT_marked_outdated():
    """Critical safety: UNVERIFIABLE means 'leave alone', NOT 'mark
    outdated'. A wrong outdated flag deletes a true memory."""
    decisions = [
        VerificationDecision("m1", VerificationVerdict.UNVERIFIABLE, "?"),
    ]
    assert select_outdated(decisions) == []


@pytest.mark.unit
def test_select_empty_input():
    assert select_outdated([]) == []
