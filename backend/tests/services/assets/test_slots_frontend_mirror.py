"""The frontend slot table is a SECOND copy of ``app.services.assets.slots``.

``frontend/components/assets/assetSlots.ts`` says so in its own header and
tells the next editor to re-copy by hand. Nothing enforced it: a one-sided
edit shipped as a picker offering a slot the backend rejects with a 400
``invalid_slot`` — visible only when a user clicked it. Its own
``assetSlots.test.ts`` hardcodes the same values a third time, so it stays
green while diverging from Python; only a test that reads BOTH files can see
the drift.

This is a text parse, not an execution: the assertion is over exactly what a
reader of the TypeScript file sees, and it needs no node toolchain in the
backend test run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.assets.slot_generation import _slot_priority
from app.services.assets.slots import (
    _AUDIO_SUBTYPE_FOR_RELATION,
    LINK_RULES,
    PRIMARY_SLOT,
    SLOTS,
    UNSORTED,
)

# tests/services/assets/<this file> → tests → backend → repo root
MIRROR = (
    Path(__file__).resolve().parents[4]
    / "frontend"
    / "components"
    / "assets"
    / "assetSlots.ts"
)


def _object_literal(source: str, name: str) -> str:
    """The ``{ ... }`` body of ``export const <name> ... = { ... };``.

    Brace-counted rather than regex-matched to the first ``}``: a lazy match
    would stop inside a nested literal and silently compare a PREFIX of the
    table, which is exactly the "looks checked, checks nothing" failure this
    file exists to prevent.
    """
    start = re.search(rf"export const {name}\b[^=]*=\s*\{{", source)
    assert start, f"{name} not found in {MIRROR.name} — was it renamed?"
    i = start.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[i : j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _parse_ts_object(literal: str) -> dict:
    """TS object literal → dict. Handles the two shapes this file uses:
    ``key: 'value' | null`` and ``key: ['a', 'b']``.
    """
    body = re.sub(r"/\*.*?\*/", "", literal, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    body = body.replace("'", '"')
    body = re.sub(r",(\s*[}\]])", r"\1", body)  # trailing commas
    body = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', body)
    return json.loads(body)


@pytest.fixture(scope="module")
def mirror_source() -> str:
    if not MIRROR.exists():
        pytest.skip(f"frontend mirror not checked out: {MIRROR} (backend-only tree)")
    return MIRROR.read_text(encoding="utf-8")


def test_primary_slot_matches(mirror_source):
    ts = _parse_ts_object(_object_literal(mirror_source, "PRIMARY_SLOT"))
    assert ts == PRIMARY_SLOT


def test_slots_match_including_order(mirror_source):
    """Order is part of the contract: the pickers render the named slots in
    table order with the primary first, so a reordered mirror moves the
    default a user sees without changing any value.
    """
    ts = _parse_ts_object(_object_literal(mirror_source, "SLOTS"))
    assert {k: tuple(v) for k, v in ts.items()} == SLOTS


def test_unsorted_matches(mirror_source):
    m = re.search(r"export const UNSORTED\s*=\s*'([^']+)'", mirror_source)
    assert m, "UNSORTED not found in the mirror"
    assert m.group(1) == UNSORTED


def test_asset_types_list_matches_the_table_keys(mirror_source):
    """``ASSET_TYPES`` is what the dialog's type segmented control offers. A
    type present there but absent from the tables would render a picker with
    no slots; the reverse hides a type the backend accepts.
    """
    m = re.search(
        r"export const ASSET_TYPES = \[(.*?)\] as const;", mirror_source, re.S
    )
    assert m, "ASSET_TYPES not found in the mirror"
    types = [x.strip().strip("'\",") for x in m.group(1).split("\n") if x.strip()]
    assert set(types) == set(PRIMARY_SLOT) == set(SLOTS)


# ── the link half (P2 Task 7) ──────────────────────────────────────────────
#
# `assetSlots.ts` grew a second mirror for the entity sheet's relation
# sections, and its own header said it was NOT pinned here. That is the same
# state the slot tables were in before this file existed: the TS-side test
# (`assetSlots.test.ts`) hardcodes the same pairs a third time, so a
# Python-side edit leaves it green while the two sides disagree — and the
# symptom is a picker that offers a relation the backend answers 422
# `link_not_allowed` to, after the user has already chosen something.


def test_link_rules_match(mirror_source):
    """relation → (from type, to type), on both sides.

    This table decides which assets the "Add" dialog even SEARCHES, so a
    drifted entry does not fail loudly: it quietly offers the wrong type.
    """
    ts = _parse_ts_object(_object_literal(mirror_source, "LINK_RULES"))
    assert {k: tuple(v) for k, v in ts.items()} == LINK_RULES


def test_audio_subtype_conditions_match(mirror_source):
    """The audio-subtype condition `link_allowed` applies on top of the pair.

    Compared as SETS: Python holds frozensets and the mirror holds arrays, so
    order is not part of this contract (unlike SLOTS, where it is). A relation
    ABSENT from either table means "no subtype condition" — so a one-sided
    deletion is drift too, and the key sets are compared, not just the values.
    """
    ts = _parse_ts_object(_object_literal(mirror_source, "AUDIO_SUBTYPE_FOR_RELATION"))
    assert {k: set(v) for k, v in ts.items()} == {
        k: set(v) for k, v in _AUDIO_SUBTYPE_FOR_RELATION.items()
    }


def test_link_relations_list_matches_the_rule_keys(mirror_source):
    """`LINK_RELATIONS` is what the sheet iterates to build its sections. A
    relation there but not in `LINK_RULES` renders a section whose Add button
    can never find a legal target."""
    m = re.search(
        r"export const LINK_RELATIONS = \[(.*?)\] as const", mirror_source, re.S
    )
    assert m, "LINK_RELATIONS not found in the mirror"
    relations = [x.strip().strip("'\",") for x in m.group(1).split("\n") if x.strip()]
    assert set(relations) == set(LINK_RULES)


def test_the_parser_would_notice_a_changed_link_rule():
    """Guard on the guard, for the link half specifically: the LINK_RULES
    literal carries a generic type annotation containing `[` and `]`, which a
    less careful extractor would trip over."""
    mutated = (
        "export const LINK_RULES: Record<AssetLinkRelation, "
        "readonly [AssetType, AssetType]> = {\n"
        "  wears: ['character', 'WRONG'],\n};"
    )
    parsed = _parse_ts_object(_object_literal(mutated, "LINK_RULES"))
    assert parsed == {"wears": ["character", "WRONG"]}
    assert {k: tuple(v) for k, v in parsed.items()} != LINK_RULES


def test_the_parser_would_notice_a_changed_value():
    """A guard on the guard: if ``_parse_ts_object`` silently returned ``{}``
    for anything it did not understand, every assertion above would pass on a
    mirror that says nothing at all.
    """
    mutated = "export const SLOTS: X = {\n  character: ['sheet', 'WRONG'],\n};"
    parsed = _parse_ts_object(_object_literal(mutated, "SLOTS"))
    assert parsed == {"character": ["sheet", "WRONG"]}
    assert {k: tuple(v) for k, v in parsed.items()} != SLOTS


# ── the reference-priority half (P4 Task 5, fix round 1) ───────────────────
#
# `_slot_priority` is NOT `SLOTS`: it hoists `worn` and `stills` ahead of the
# declaration order (spec §6.3), so for `character` the two disagree —
# declaration puts `worn` LAST, priority puts it second.
#
# The canvas asset card ranks its reference checklist by this order to decide
# which rows the provider's `max_refs` will trim, and the bundle endpoint walks
# `reference_order`, which walks `_slot_priority`. Ranking by the DECLARATION
# order instead (what the card did before this fix) dimmed a file that was in
# fact sent while leaving un-dimmed the one that was dropped — the pre-run hint
# and the post-run badge answering the same question differently.
#
# Mirrored as a literal on the TS side rather than recomputed there, so this
# parse can pin it row for row.


def test_reference_priority_matches_including_order(mirror_source):
    """Order IS the contract here — it decides which references survive the cap.

    Compared against `_slot_priority`'s output, not against a second hand-copy
    of the rule: the hoist is an algorithm on the Python side and a table on the
    TS side, and only the computed values can say the two agree.
    """
    ts = _parse_ts_object(_object_literal(mirror_source, "REFERENCE_SLOT_PRIORITY"))
    assert set(ts) == set(SLOTS), "the mirror covers a different set of types"
    for asset_type in SLOTS:
        assert ts[asset_type] == _slot_priority(asset_type), (
            f"{asset_type}: the card would rank its references "
            f"{ts[asset_type]} while the bundle sends "
            f"{_slot_priority(asset_type)}"
        )


def test_the_priority_really_differs_from_the_declaration_order():
    """A control for the test above: if `_slot_priority` ever collapsed into
    `SLOTS`, the mirror check would keep passing while the property it exists
    to protect had quietly stopped existing.
    """
    declaration = [*SLOTS["character"], UNSORTED]
    assert _slot_priority("character") != declaration, (
        "the reference priority no longer hoists worn/stills — if that is "
        "deliberate, this mirror is now redundant and should be removed, not "
        "left as a check of nothing"
    )


def test_the_parser_would_notice_a_changed_priority_row():
    """Guard on the guard, same posture as the SLOTS one above."""
    mutated = (
        "export const REFERENCE_SLOT_PRIORITY: X = {\n"
        "  character: ['sheet', 'WRONG'],\n};"
    )
    parsed = _parse_ts_object(_object_literal(mutated, "REFERENCE_SLOT_PRIORITY"))
    assert parsed == {"character": ["sheet", "WRONG"]}
    assert parsed["character"] != _slot_priority("character")
