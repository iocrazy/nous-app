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


# ─── explicit rename table (nous-qwen3-llm → nous-qwen3-8-27b) ─────────────
#
# Not a prefix swap, so the prefix rule cannot derive it. Two-step like the
# prefix rename: this code ships first; the data migration renames the row
# later. Until then only the OLD row exists, afterwards only the NEW one —
# so the table must work in BOTH directions. It is kept indefinitely: external
# callers may hard-code the old name and there is no way to enumerate them.

OLD_QWEN = "nous-qwen3-llm"
NEW_QWEN = "nous-qwen3-8-27b"


def test_rename_alias_both_directions() -> None:
    assert catalog_name_alias(OLD_QWEN) == NEW_QWEN
    assert catalog_name_alias(NEW_QWEN) == OLD_QWEN


def test_rename_candidates_put_exact_first() -> None:
    assert catalog_name_candidates(OLD_QWEN) == (OLD_QWEN, NEW_QWEN)
    assert catalog_name_candidates(NEW_QWEN) == (NEW_QWEN, OLD_QWEN)


def test_rename_does_not_add_a_legacy_prefix_third_candidate() -> None:
    # ``mediahub-qwen3-llm`` never existed in production; do not invent it.
    assert "mediahub-qwen3-llm" not in catalog_name_candidates(OLD_QWEN)
    assert "mediahub-qwen3-8-27b" not in catalog_name_candidates(NEW_QWEN)


def test_rename_old_name_resolves_renamed_row() -> None:
    rows = [{"name": "other"}, {"name": NEW_QWEN, "id": 9}]
    assert find_row_by_catalog_name(rows, OLD_QWEN) == rows[1]


def test_rename_new_name_resolves_unrenamed_row() -> None:
    rows = [{"name": OLD_QWEN, "id": 4}]
    assert find_row_by_catalog_name(rows, NEW_QWEN) == rows[0]


def test_rename_exact_wins_when_both_rows_exist() -> None:
    rows = [{"name": NEW_QWEN, "id": 1}, {"name": OLD_QWEN, "id": 2}]
    assert find_row_by_catalog_name(rows, OLD_QWEN)["id"] == 2
    rows = [{"name": OLD_QWEN, "id": 2}, {"name": NEW_QWEN, "id": 1}]
    assert find_row_by_catalog_name(rows, NEW_QWEN)["id"] == 1


@pytest.mark.parametrize(
    ("name", "alias"),
    [
        # siblings of the renamed row keep the plain prefix rule
        ("nous-qwen3-8-27b-huihui", "mediahub-qwen3-8-27b-huihui"),
        ("nous-qwen3-8-27b-orcarouter", "mediahub-qwen3-8-27b-orcarouter"),
        ("nous-qwen3-llm-x", "mediahub-qwen3-llm-x"),
        # exact match only: no case folding, no fuzz
        ("NOUS-QWEN3-LLM", None),
        ("qwen3-llm", None),
        ("qwen3-8-27b", None),
    ],
)
def test_rename_table_leaves_unrelated_names_alone(
    name: str, alias: str | None
) -> None:
    assert catalog_name_alias(name) == alias


def test_rename_table_is_a_consistent_bijection() -> None:
    from app.core.catalog_names import _RENAMES

    olds = set(_RENAMES)
    news = set(_RENAMES.values())
    assert len(news) == len(_RENAMES), "two old names map to one new name"
    assert not olds & news, "a name is both an old and a new spelling"
    for old, new in _RENAMES.items():
        assert old != new
        assert catalog_name_alias(old) == new
        assert catalog_name_alias(new) == old
