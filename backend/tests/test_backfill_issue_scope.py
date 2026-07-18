"""Unit tests for the pure helpers of the issue-scope backfill workflow.

The repair decisions (which origin parses, which rounded id is safely
recoverable) live in pure functions precisely so they can be pinned here
without a database.
"""

import pytest

from app.workflows.backfill_issue_scope import parse_canvas_origin, rounded_candidates


class TestParseCanvasOrigin:
    def test_plain_canvas_origin(self):
        assert parse_canvas_origin("canvas:99887766554433") == "99887766554433"

    def test_snowflake_stays_a_string(self):
        big = "9007199254740993"  # 2^53 + 1 — must never be numeric-coerced
        assert parse_canvas_origin(f"canvas:{big}") == big

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "scene:123",  # different surface — not this backfill's business
            "12345",  # routine schedule id (bare, no prefix)
            "canvas:",  # empty id
            "canvas:abc",  # non-digits would crash a SQL CAST
            "canvas:12x3",
            "Canvas:123",  # case-sensitive, matching the writer
        ],
    )
    def test_rejects_everything_else(self, value):
        assert parse_canvas_origin(value) is None


class TestRoundedCandidates:
    def test_exact_single_match(self):
        real = 9007199254740993  # 2^53 + 1: float64 rounds it to 2^53
        stored = int(float(real))
        assert stored != real  # precondition: rounding actually happened
        assert rounded_candidates(stored, [real, 42]) == [real]

    def test_small_ids_survive_rounding_unchanged(self):
        # Below 2^53 nothing rounds; the "bad" value simply matches itself —
        # relevant because such rows can only be orphans (id not in teams).
        assert rounded_candidates(42, [42]) == [42]

    def test_no_candidates_for_orphan(self):
        assert rounded_candidates(123456, [9007199254740993]) == []

    def test_two_snowflakes_rounding_to_same_float_are_ambiguous(self):
        # 2^53+1 and 2^53 both round to 2^53 → caller must refuse to guess.
        a, b = 9007199254740992, 9007199254740993
        stored = int(float(b))
        got = rounded_candidates(stored, [a, b])
        assert len(got) == 2
