"""The footer's quality ramp is a SECOND copy of ``QUALITY_TIER_ORDER``.

``GenFooterControls.tsx`` writes the low→max ramp out by hand as ``QUALITIES``
and says in its own comment that it mirrors the backend constant. Nothing
enforced it. A tier added on one side only fails in the quiet direction:
adding a rung to Python leaves a picker that cannot offer it (the capability
is declared, the UI never shows it), and adding one to TypeScript offers a
value ``request.py`` drops before dispatch — the fake switch the whole
capability contract exists to end.

Same posture as ``tests/services/assets/test_slots_frontend_mirror.py``: a text
parse, not an execution, so the assertion is over exactly what a reader of the
TypeScript file sees and the backend test run needs no node toolchain.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.ai.provider_protocols.base import QUALITY_TIER_ORDER

# tests/<this file> → tests → backend → repo root
MIRROR = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "features"
    / "canvas-core"
    / "smart"
    / "nodes"
    / "GenFooterControls.tsx"
)


def _array_literal(source: str, name: str) -> str:
    """The ``[ ... ]`` body of ``const <name> ... = [ ... ];``.

    Bracket-counted rather than matched to the first ``]``: a lazy regex would
    stop inside a nested literal and compare a PREFIX of the ramp, which is
    the "looks checked, checks nothing" failure this file exists to prevent.
    ``QUALITIES`` is module-private, so no ``export`` is required.
    """
    start = re.search(rf"const {name}\b[^=]*=\s*\[", source)
    assert start, f"{name} not found in {MIRROR.name} — was it renamed?"
    i = start.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "[":
            depth += 1
        elif source[j] == "]":
            depth -= 1
            if depth == 0:
                return source[i : j + 1]
    raise AssertionError(f"unbalanced brackets in {name}")


def _tier_values(literal: str) -> list[str]:
    """The ``value:`` of each row, in order, minus the ``undefined`` one.

    ``{ label: 'Auto', value: undefined }`` is a frontend concept — "let the
    provider pick" — that the backend vocabulary never lists, exactly like the
    ratio grid's ``auto`` row. Everything else must be a real tier.
    """
    return [
        m.group(1)
        for m in re.finditer(r"value:\s*(?:'([^']*)'|undefined)", literal)
        if m.group(1) is not None
    ]


@pytest.fixture(scope="module")
def mirror_source() -> str:
    if not MIRROR.exists():
        pytest.skip(f"frontend mirror not checked out: {MIRROR} (backend-only tree)")
    return MIRROR.read_text(encoding="utf-8")


@pytest.mark.unit
def test_quality_ramp_matches_including_order(mirror_source):
    """Order is part of the contract: the popover renders the rungs in list
    order, so a reordered mirror shows a ramp that climbs the wrong way even
    though every value is legal."""
    ts = _tier_values(_array_literal(mirror_source, "QUALITIES"))
    assert ts, "the parse found no tiers at all — the extractor is broken"
    assert tuple(ts) == QUALITY_TIER_ORDER


@pytest.mark.unit
def test_the_parser_would_notice_a_changed_rung():
    """A guard on the guard: if ``_tier_values`` silently returned ``[]`` for
    anything it did not understand, the assertion above would pass against a
    mirror that says nothing at all."""
    mutated = (
        "const QUALITIES: Array<{ label: string; value: string | undefined }> = [\n"
        "  { label: 'Auto', value: undefined },\n"
        "  { label: 'Low', value: 'low' },\n"
        "  { label: 'Wrong', value: 'WRONG' },\n];"
    )
    parsed = _tier_values(_array_literal(mutated, "QUALITIES"))
    assert parsed == ["low", "WRONG"]  # `undefined` dropped, the rest kept
    assert tuple(parsed) != QUALITY_TIER_ORDER
