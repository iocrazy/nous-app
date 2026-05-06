"""R2 — memory replay testing primitive."""
from __future__ import annotations

import pytest

from app.services.ai.memory.replay import (
    categorize_influence,
    diff_response,
    filter_excluding,
    score_influence,
)


# ─── filter_excluding ────────────────────────────────────────────────


@pytest.mark.unit
def test_filter_removes_excluded_ids():
    out = filter_excluding(["a", "b", "c", "d"], ["b", "d"])
    assert out == ["a", "c"]


@pytest.mark.unit
def test_filter_no_op_when_excluded_empty():
    assert filter_excluding(["a", "b"], []) == ["a", "b"]


@pytest.mark.unit
def test_filter_handles_unknown_excluded_id():
    """Excluding an id not in the list is a no-op."""
    assert filter_excluding(["a", "b"], ["nope"]) == ["a", "b"]


@pytest.mark.unit
def test_filter_empty_input():
    assert filter_excluding([], ["x"]) == []


# ─── diff_response ───────────────────────────────────────────────────


@pytest.mark.unit
def test_identical_responses_perfect_similarity():
    diff = diff_response("hello world", "hello world")
    assert diff.char_similarity == 1.0
    assert diff.word_similarity == 1.0
    assert diff.differing_tokens == ()


@pytest.mark.unit
def test_completely_different_responses_low_similarity():
    diff = diff_response("alpha bravo charlie", "delta echo foxtrot")
    # char_sim picks up incidental letter overlap (a/r/l/e common)
    # so threshold is generous; word_sim is strict 0
    assert diff.char_similarity < 0.5
    assert diff.word_similarity == 0.0
    # All 6 tokens differ
    assert len(diff.differing_tokens) >= 4


@pytest.mark.unit
def test_one_word_change_high_similarity():
    """Single token swap → high similarity but not 1.0."""
    diff = diff_response(
        "the user prefers Vue framework",
        "the user prefers React framework",
    )
    assert diff.char_similarity > 0.7
    assert diff.word_similarity > 0.7
    assert "Vue" in diff.differing_tokens
    assert "React" in diff.differing_tokens


@pytest.mark.unit
def test_diff_handles_empty_strings():
    diff = diff_response("", "")
    assert diff.char_similarity == 1.0


@pytest.mark.unit
def test_diff_caps_differing_tokens_sample():
    """Don't return 1000 differing tokens — cap at 10 for display."""
    base = " ".join(f"a{i}" for i in range(50))
    abl = " ".join(f"b{i}" for i in range(50))
    diff = diff_response(base, abl)
    assert len(diff.differing_tokens) <= 10


# ─── score_influence ───────────────────────────────────────────────


@pytest.mark.unit
def test_score_zero_for_identical():
    diff = diff_response("hello", "hello")
    assert score_influence(diff) == 0.0


@pytest.mark.unit
def test_score_high_for_very_different():
    diff = diff_response("alpha bravo", "xyz pqr")
    assert score_influence(diff) > 0.5


@pytest.mark.unit
def test_score_clamped_to_unit_interval():
    """Even with degenerate inputs, score stays in [0, 1]."""
    diff = diff_response("a", "")
    s = score_influence(diff)
    assert 0.0 <= s <= 1.0


# ─── categorize_influence ────────────────────────────────────────────


@pytest.mark.unit
def test_categorize_thresholds():
    assert categorize_influence(0.0) == "negligible"
    assert categorize_influence(0.04) == "negligible"
    assert categorize_influence(0.10) == "minor"
    assert categorize_influence(0.30) == "moderate"
    assert categorize_influence(0.80) == "major"
    assert categorize_influence(1.0) == "major"
