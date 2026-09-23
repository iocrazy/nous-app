"""Catalog-name aliasing across the ``mediahub-`` → ``nous-`` row rename.

Step 1 of 2: lookups tolerate BOTH spellings before any row is renamed, so the
rename migration and the code deploy can land in either order, and a persisted
reference the migration misses still resolves. Rules pinned here:

* exact name always wins;
* only the two prefixes are swapped, never anything fuzzy;
* a name with neither prefix has no alias.
"""

from __future__ import annotations

import pytest

from app.core.catalog_names import (
    catalog_name_alias,
    catalog_name_candidates,
    find_row_by_catalog_name,
)


@pytest.mark.parametrize(
    ("name", "alias"),
    [
        ("mediahub-moss-asr", "nous-moss-asr"),
        ("nous-moss-asr", "mediahub-moss-asr"),
        ("jimeng-cli-image", None),
        ("codex-local-image", None),
        ("gpt-4o", None),
        ("", None),
        # prefix alone has no "rest" — never alias to a bare prefix
        ("mediahub-", None),
        ("nous-", None),
        # case-sensitive, like the unique index on nous_models.name
        ("Mediahub-x", None),
        # the prefix must lead; an infix is not a prefix
        ("x-mediahub-y", None),
    ],
)
def test_catalog_name_alias(name: str, alias: str | None) -> None:
    assert catalog_name_alias(name) == alias


def test_candidates_put_exact_first() -> None:
    assert catalog_name_candidates("mediahub-a") == ("mediahub-a", "nous-a")
    assert catalog_name_candidates("nous-a") == ("nous-a", "mediahub-a")
    assert catalog_name_candidates("jimeng-cli-image") == ("jimeng-cli-image",)


def test_find_row_old_name_resolves_renamed_row() -> None:
    rows = [{"name": "other"}, {"name": "nous-moss-asr", "id": 7}]
    assert find_row_by_catalog_name(rows, "mediahub-moss-asr") == rows[1]


def test_find_row_new_name_resolves_unrenamed_row() -> None:
    rows = [{"name": "mediahub-doubao-seed-2-0-lite", "id": 3}]
    assert find_row_by_catalog_name(rows, "nous-doubao-seed-2-0-lite") == rows[0]


def test_find_row_exact_wins_when_both_spellings_exist() -> None:
    # Alias row listed FIRST: list order must not beat an exact match.
    rows = [
        {"name": "nous-x", "id": 1},
        {"name": "mediahub-x", "id": 2},
    ]
    assert find_row_by_catalog_name(rows, "mediahub-x")["id"] == 2
    assert find_row_by_catalog_name(rows, "nous-x")["id"] == 1


def test_find_row_unprefixed_names_are_untouched() -> None:
    rows = [{"name": "nous-jimeng-cli-image"}, {"name": "mediahub-jimeng-cli-image"}]
    assert find_row_by_catalog_name(rows, "jimeng-cli-image") is None


def test_find_row_empty_name_matches_nothing() -> None:
    assert find_row_by_catalog_name([{"name": ""}], "") is None
