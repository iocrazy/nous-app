"""Unit tests for ``app.services.script.scene_numbering`` — pure functions,
no DB. Covers the invariants from plan A3 / agent-layer spec §4:

  - writing-phase numbers derive consistently from position
  - the (base, suffix) sort key is total and correct across 3 / 3A / 3B /
    4 / 9 / 10 (the base MUST compare as int, not string, or "10" sorts
    before "9")
  - a lock-then-insert between two locked neighbours gets a letter suffix
    and does not touch either neighbour's number
  - a tail append past the last locked scene continues the plain integer
    sequence (no letter — nothing to protect)
  - a second insert near the same spot does not collide with the first
"""

from __future__ import annotations

import pytest

from app.services.script.scene_numbering import (
    compute_locked_insert_number,
    derive_scene_number,
    parse_scene_number,
    scene_number_sort_key,
)

# ── derive_scene_number (writing phase) ──────────────────────────────────


def test_derive_scene_number_is_1_indexed():
    assert derive_scene_number(0) == "1"
    assert derive_scene_number(1) == "2"
    assert derive_scene_number(9) == "10"


def test_derive_scene_number_rejects_negative_index():
    with pytest.raises(ValueError):
        derive_scene_number(-1)


def test_derive_scene_number_consistent_across_repeated_calls():
    """Unlocked scenes derive their number purely from position — the same
    index always yields the same number (no hidden state)."""
    for _ in range(3):
        assert derive_scene_number(4) == "5"


# ── parse_scene_number ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", (1, "")),
        ("3A", (3, "A")),
        ("12AB", (12, "AB")),
        ("100", (100, "")),
    ],
)
def test_parse_scene_number(value, expected):
    assert parse_scene_number(value) == expected


@pytest.mark.parametrize("bad", ["", "A3", "3-A", "3a", " ", "3 A", None])
def test_parse_scene_number_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_scene_number(bad)


# ── scene_number_sort_key: total order ────────────────────────────────────


def test_sort_key_orders_the_required_set_correctly():
    """The exact set called out in the plan: 3 / 3A / 3B / 4 / 9 / 10."""
    numbers = ["10", "3B", "9", "3", "4", "3A"]
    ordered = sorted(numbers, key=scene_number_sort_key)
    assert ordered == ["3", "3A", "3B", "4", "9", "10"]


def test_sort_key_base_compares_as_int_not_string():
    """The classic string-sort trap: "10" must NOT sort before "9"."""
    assert scene_number_sort_key("9") < scene_number_sort_key("10")
    assert scene_number_sort_key("99") < scene_number_sort_key("100")


def test_sort_key_plain_number_before_its_own_letter_children():
    assert scene_number_sort_key("3") < scene_number_sort_key("3A")
    assert scene_number_sort_key("3A") < scene_number_sort_key("3B")
    assert scene_number_sort_key("3Z") < scene_number_sort_key("4")


def test_sort_key_double_letter_sorts_after_every_single_letter_at_base():
    """3AA is a well-defined, later position than any single-letter suffix
    at the same base (Excel-column enumeration: length is the primary
    tie-break after base) — exercised even though normal insertion never
    produces it for a realistic scene count."""
    assert scene_number_sort_key("3Z") < scene_number_sort_key("3AA")
    assert scene_number_sort_key("3AA") < scene_number_sort_key("4")
    assert scene_number_sort_key("3AA") < scene_number_sort_key("3AB")


def test_sort_key_is_a_strict_total_order_via_python_sorted():
    """Sanity: feeding a scrambled, larger set through Python's sort (which
    requires a valid strict weak ordering) round-trips to the same stable
    order regardless of input order."""
    numbers = ["4", "3B", "3", "10", "9", "3A", "3AA", "3Z"]
    once = sorted(numbers, key=scene_number_sort_key)
    twice = sorted(list(reversed(numbers)), key=scene_number_sort_key)
    assert once == twice


# ── compute_locked_insert_number ──────────────────────────────────────────


def test_insert_between_plain_neighbours_gets_first_letter():
    """Insert after 3 (a scene between locked scenes 3 and 4, per plan A3's
    example) -> 3A. Existing numbers are passed through untouched."""
    existing = ["1", "2", "3", "4", "5"]
    assert compute_locked_insert_number("3", "4", existing) == "3A"


def test_second_insert_at_same_spot_gets_next_letter_not_a_collision():
    """A second insert between (now) 3A and 4 must not collide with 3A —
    this is the 'leaves existing numbers untouched, no colliding shot ids'
    requirement in a nutshell."""
    existing = ["1", "2", "3", "3A", "4", "5"]
    assert compute_locked_insert_number("3A", "4", existing) == "3B"


def test_third_insert_skips_both_taken_letters():
    existing = ["1", "2", "3", "3A", "3B", "4", "5"]
    assert compute_locked_insert_number("3B", "4", existing) == "3C"


def test_insert_does_not_mutate_or_require_reassigning_neighbours():
    """The defining invariant: computing a new number never implies
    changing prev/next's own number — the function doesn't even have the
    ability to (it only returns ONE new string)."""
    existing = ["1", "2", "3", "4", "5"]
    new_number = compute_locked_insert_number("3", "4", existing)
    assert new_number not in existing
    assert "3" in existing and "4" in existing  # untouched, still present


def test_tail_append_after_plain_number_continues_plain_sequence():
    """Appending past the LAST locked scene has nothing to protect — no
    letter needed, just the next plain integer."""
    existing = ["1", "2", "3"]
    assert compute_locked_insert_number("3", None, existing) == "4"


def test_tail_append_after_a_lettered_scene_still_continues_plain_sequence():
    """Even if the current tail is itself lettered (an earlier insert-after-
    lock happened near the end), appending further keeps using the base's
    own next plain integer, not another letter — there is still nothing
    after it to protect."""
    existing = ["1", "2", "3", "3A"]
    assert compute_locked_insert_number("3A", None, existing) == "4"


def test_head_insert_before_the_first_scene_nests_under_base_zero():
    existing = ["1", "2", "3"]
    assert compute_locked_insert_number(None, "1", existing) == "0A"


def test_both_neighbours_none_on_an_empty_script_starts_at_one():
    assert compute_locked_insert_number(None, None, []) == "1"


def test_insert_result_sorts_correctly_relative_to_its_neighbours():
    """End-to-end sanity: whatever compute_locked_insert_number returns must
    actually sort between prev and next per scene_number_sort_key (except
    the documented head/tail edges, which have no such bound on one side)."""
    existing = ["1", "2", "3", "4", "5"]
    new_number = compute_locked_insert_number("3", "4", existing)
    assert scene_number_sort_key("3") < scene_number_sort_key(new_number)
    assert scene_number_sort_key(new_number) < scene_number_sort_key("4")
