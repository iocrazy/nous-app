"""``LEGACY_TABLE_BY_KIND`` must equal what the P3 migration actually wrote.

The read side (``GET /assets/resolve-legacy``) and the write side
(``backfill_assets_from_project_entities``) hold the same three-entry
vocabulary in two modules — the read side does not import the DBOS workflow.
Two copies of a vocabulary is exactly the drift that would make a lookup answer
"never migrated" for every row, silently, with no error anywhere.

The labels also carry a trap of their own: mig 447 renamed the physical tables
to ``_legacy_*``, and the labels deliberately did NOT follow, because the
2026-09-02 production run had already written the old spelling into every
``attrs.legacy_ids``. A well-meaning "fix" on either side is what this file
exists to fail on.
"""

from __future__ import annotations

from app.services.assets.legacy_refs import (
    LEGACY_KINDS,
    LEGACY_TABLE_BY_KIND,
    legacy_table_for_kind,
)
from app.workflows.backfill_assets_from_project_entities import (
    _ENTITY_KIND_TO_LEGACY_TABLE,
    legacy_ref_for_entity,
)


def test_the_two_tables_are_the_same_mapping():
    assert LEGACY_TABLE_BY_KIND == _ENTITY_KIND_TO_LEGACY_TABLE


def test_the_labels_keep_the_pre_rename_spelling():
    """The control for the test above: equal-but-both-renamed would still pass
    it while matching nothing in the database."""
    assert set(LEGACY_TABLE_BY_KIND.values()) == {
        "project_characters",
        "project_lib_entities",
    }
    assert not any(v.startswith("_legacy") for v in LEGACY_TABLE_BY_KIND.values())


def test_location_and_prop_share_one_table():
    """They were two ``kind`` values of ONE table, so the pair must not be
    split — a lookup keyed on the wrong label matches nothing."""
    assert LEGACY_TABLE_BY_KIND["location"] == LEGACY_TABLE_BY_KIND["prop"]
    assert LEGACY_TABLE_BY_KIND["character"] != LEGACY_TABLE_BY_KIND["prop"]


def test_lookup_agrees_with_the_writers_own_helper():
    """Same question, asked through each side's public helper."""
    for kind in LEGACY_KINDS:
        ref = legacy_ref_for_entity(kind, "12")
        assert ref is not None
        assert ref == (legacy_table_for_kind(kind), 12)


def test_an_unknown_kind_maps_to_nothing_rather_than_guessing():
    for kind in ("costume", "prompt", "audio", "", None, "CHARACTER"):
        assert legacy_table_for_kind(kind) is None
