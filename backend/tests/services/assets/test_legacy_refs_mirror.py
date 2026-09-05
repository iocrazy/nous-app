"""``LEGACY_TABLE_BY_KIND`` must keep saying what production actually wrote.

WHAT THIS FILE USED TO BE, AND WHY IT CHANGED. Until P6 it pinned two copies
of one vocabulary equal: the read side (``legacy_refs.py``, behind
``GET /assets/resolve-legacy``) against the write side
(``backfill_assets_from_project_entities``). Mig 451 dropped the two legacy
tables and that workflow went with them, so the comparison side no longer
exists — but the vocabulary still has to be right, because the OTHER side of
the equality never went away: it is the ``attrs.legacy_ids`` rows the
2026-09-02 production run already persisted. Those strings are frozen in the
database. This file is now what stops an edit here from drifting away from
them.

THE TRAP THESE ASSERTIONS EXIST FOR. The labels are provenance LABELS, not
SQL identifiers — nothing resolves them to a table. Mig 447 renamed the
physical tables to ``_legacy_*`` and mig 451 dropped them, and through both
the labels deliberately kept the PRE-rename spelling. A well-meaning "the
tables are called ``_legacy_*`` now" or "the tables are gone, clean this up"
edit would not raise anything: every lookup would simply match no row, and a
canvas card that WAS migrated would render as ``Unmigrated`` forever. That
silence is the failure mode; these assertions are the noise.
"""

from __future__ import annotations

from app.services.assets.legacy_refs import (
    LEGACY_KINDS,
    LEGACY_TABLE_BY_KIND,
    legacy_table_for_kind,
)


def test_the_labels_keep_the_pre_rename_spelling():
    """The literals below are the ones in production's ``attrs.legacy_ids``.

    Written out rather than derived, so this test cannot agree with the module
    by construction — it agrees with the database or it fails."""
    assert LEGACY_TABLE_BY_KIND == {
        "character": "project_characters",
        "location": "project_lib_entities",
        "prop": "project_lib_entities",
    }
    assert not any(v.startswith("_legacy") for v in LEGACY_TABLE_BY_KIND.values())


def test_location_and_prop_share_one_table():
    """They were two ``kind`` values of ONE table, so the pair must not be
    split — a lookup keyed on the wrong label matches nothing."""
    assert LEGACY_TABLE_BY_KIND["location"] == LEGACY_TABLE_BY_KIND["prop"]
    assert LEGACY_TABLE_BY_KIND["character"] != LEGACY_TABLE_BY_KIND["prop"]


def test_legacy_kinds_is_exactly_the_mapping_keys():
    """The router pattern and the service check both read ``LEGACY_KINDS``; a
    kind that falls out of it stops being resolvable without any error."""
    assert set(LEGACY_KINDS) == set(LEGACY_TABLE_BY_KIND)
    assert set(LEGACY_KINDS) == {"character", "location", "prop"}


def test_the_public_helper_answers_for_every_accepted_kind():
    for kind in LEGACY_KINDS:
        assert legacy_table_for_kind(kind) == LEGACY_TABLE_BY_KIND[kind]


def test_an_unknown_kind_maps_to_nothing_rather_than_guessing():
    for kind in ("costume", "prompt", "audio", "", None, "CHARACTER"):
        assert legacy_table_for_kind(kind) is None
