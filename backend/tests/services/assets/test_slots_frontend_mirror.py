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

from app.services.assets.slots import PRIMARY_SLOT, SLOTS, UNSORTED

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


def test_the_parser_would_notice_a_changed_value():
    """A guard on the guard: if ``_parse_ts_object`` silently returned ``{}``
    for anything it did not understand, every assertion above would pass on a
    mirror that says nothing at all.
    """
    mutated = "export const SLOTS: X = {\n  character: ['sheet', 'WRONG'],\n};"
    parsed = _parse_ts_object(_object_literal(mutated, "SLOTS"))
    assert parsed == {"character": ["sheet", "WRONG"]}
    assert {k: tuple(v) for k, v in parsed.items()} != SLOTS
