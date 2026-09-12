"""The search-scope vocabulary is a SECOND copy in the frontend.

``frontend/services/searchService.ts`` holds ``ALL_SEARCH_FIELDS`` (what the
scope dropdown renders) and ``DEFAULT_SEARCH_FIELDS`` (what a fresh install
ships with). ``backend/app/schemas/search.py`` holds the same two things as
``SearchField`` and ``DEFAULT_SEARCH_FIELDS``. The TS file's own comment tells
the next editor to keep them in sync by hand; nothing enforced it.

Both halves of the drift are silent, which is why a text-level mirror is worth
its weight (same posture as ``tests/services/assets/test_slots_frontend_mirror``):

  * a scope the picker offers but the backend's ``Literal`` rejects → the
    request 422s the moment the user ticks that checkbox;
  * a default that differs between the two → the backend applies its own list
    whenever a caller omits ``fields`` (``hybridSearch`` with no scope, every
    non-DownloadsView caller), so the same query answers differently depending
    on which surface asked. That is exactly the defect this change fixed on the
    backend side — leaving the two copies unpinned would let it come back from
    the other direction.

This is a text parse, not an execution: the assertion is over exactly what a
reader of the TypeScript file sees, and it needs no node toolchain in the
backend test run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, get_args

import pytest

from app.schemas.search import DEFAULT_SEARCH_FIELDS, SearchField

# tests/<this file> → backend → repo root
MIRROR = (
    Path(__file__).resolve().parents[2] / "frontend" / "services" / "searchService.ts"
)


def _string_array(source: str, name: str) -> List[str]:
    """The string literals of ``export const <name> ... = [ ... ];``.

    Bracket-counted rather than matched to the first ``]``: the declarations
    carry a ``SearchField[]`` type annotation whose own brackets would end a
    lazy match early and silently compare a PREFIX of the list — the "looks
    checked, checks nothing" failure this file exists to prevent.
    """
    start = re.search(rf"export const {name}\b[^=]*=\s*\[", source)
    assert start, f"{name} not found in {MIRROR.name} — was it renamed?"
    i = start.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "[":
            depth += 1
        elif source[j] == "]":
            depth -= 1
            if depth == 0:
                body = source[i + 1 : j]
                break
    else:  # pragma: no cover - unbalanced source would fail earlier
        raise AssertionError(f"unbalanced brackets in {name}")
    body = re.sub(r"//[^\n]*", "", body)
    return re.findall(r"'([^']+)'", body)


def _union_members(source: str, name: str) -> List[str]:
    """The members of ``export type <name> = | 'a' | 'b';``."""
    m = re.search(rf"export type {name}\s*=\s*((?:[^;]|\n)*?);", source)
    assert m, f"type {name} not found in {MIRROR.name}"
    return re.findall(r"'([^']+)'", m.group(1))


@pytest.fixture(scope="module")
def mirror_source() -> str:
    if not MIRROR.exists():
        pytest.skip(f"frontend mirror not checked out: {MIRROR} (backend-only tree)")
    return MIRROR.read_text(encoding="utf-8")


def test_all_search_fields_match_the_backend_literal(mirror_source):
    """Every scope the dropdown offers must be one the backend accepts.

    Compared as SETS: the TS order is the checkbox render order and the Python
    ``Literal`` order is arbitrary, so ordering is not part of this contract.
    Length is asserted too, so a duplicated entry cannot hide inside the set.
    """
    ts = _string_array(mirror_source, "ALL_SEARCH_FIELDS")
    backend = list(get_args(SearchField))
    assert len(ts) == len(set(ts)), f"duplicate entries in the mirror: {ts}"
    assert set(ts) == set(backend), (
        "the scope picker and the backend Literal disagree — a field only the "
        "picker knows 422s the request the moment the user ticks it"
    )


def test_the_search_field_type_alias_matches_too(mirror_source):
    """``SearchField`` on the TS side types every caller's ``fields`` argument.

    It is a third hand-written copy of the same vocabulary, so it can drift
    from ``ALL_SEARCH_FIELDS`` one file over as easily as from Python.
    """
    assert set(_union_members(mirror_source, "SearchField")) == set(
        get_args(SearchField)
    )


def test_default_search_fields_match_the_backend_default(mirror_source):
    """The default scope decides what a user who never opened the picker gets.

    Compared as SETS for the same reason as above — the RPC OR-matches the
    field list, and the picker's "is this the default?" tint compares as a set.
    """
    ts = _string_array(mirror_source, "DEFAULT_SEARCH_FIELDS")
    assert len(ts) == len(set(ts)), f"duplicate entries in the mirror: {ts}"
    assert set(ts) == set(DEFAULT_SEARCH_FIELDS), (
        "frontend and backend default scopes disagree — the backend applies "
        "its own copy whenever a caller omits ``fields``, so the same query "
        "would answer differently depending on which surface asked"
    )


def test_the_default_is_a_subset_of_the_offered_scopes(mirror_source):
    """A default naming a scope the picker cannot render is unbailable: the
    user sees a tinted "custom scope" funnel with no way back to it."""
    ts_all = set(_string_array(mirror_source, "ALL_SEARCH_FIELDS"))
    assert set(_string_array(mirror_source, "DEFAULT_SEARCH_FIELDS")) <= ts_all
    assert set(DEFAULT_SEARCH_FIELDS) <= set(get_args(SearchField))


def test_the_parser_would_notice_a_changed_value():
    """A guard on the guard: if ``_string_array`` silently returned ``[]`` for
    anything it did not understand, every assertion above would pass on a
    mirror that says nothing at all.
    """
    mutated = (
        "export const DEFAULT_SEARCH_FIELDS: SearchField[] = [\n"
        "  'title',\n  'WRONG',\n];"
    )
    parsed = _string_array(mutated, "DEFAULT_SEARCH_FIELDS")
    assert parsed == ["title", "WRONG"]
    assert set(parsed) != set(DEFAULT_SEARCH_FIELDS)


def test_the_parser_survives_the_type_annotation_brackets():
    """The ``SearchField[]`` annotation sits between the name and the ``=``.

    A regex that hunted for the first ``[`` would open its bracket count on the
    annotation and close it immediately, yielding an empty list for every
    declaration in the file.
    """
    mutated = "export const ALL_SEARCH_FIELDS: SearchField[] = ['title', 'notes'];"
    assert _string_array(mutated, "ALL_SEARCH_FIELDS") == ["title", "notes"]
