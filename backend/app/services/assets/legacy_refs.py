"""The provenance labels the P3 migration stamped into ``assets.attrs``.

``backfill_assets_from_project_entities`` writes, for every asset it created
from a legacy project entity::

    attrs.legacy_ids = [["project_characters", 12], ["project_lib_entities", 7]]

Each pair is ``[table_label, legacy_row_id]``. **The table labels keep the
PRE-RENAME spelling on purpose** — mig 447 renamed the physical tables to
``_legacy_project_characters`` / ``_legacy_project_lib_entities``, but the
2026-09-02 production run had already written the old names into every row, and
these strings are provenance LABELS, not SQL identifiers (nothing resolves them
to a table; every read goes through the ORM models). Re-spelling them would make
the persisted data and the lookup disagree, which reads as "this legacy card has
no asset" rather than as an error.

This module exists so the READ side (``GET /assets/resolve-legacy``, which a
canvas uses to find the asset a legacy character/location/prop card became) does
not have to import the DBOS workflow that does the writing. The two tables are
pinned equal by ``tests/services/assets/test_legacy_refs_mirror.py``, so a
future edit to either one fails rather than silently splitting the vocabulary
in half.
"""

from __future__ import annotations

from typing import Dict, Optional

# Canvas card kind → the legacy table label stamped in ``attrs.legacy_ids``.
# Location and prop share one table (``project_lib_entities`` held both, keyed
# by its own ``kind`` column), which is why the kind alone cannot be used as the
# label and why resolving a location id can, in principle, land on a prop's
# asset if the caller passes the wrong kind — the id is the discriminator here,
# and legacy ids were unique across that table.
LEGACY_TABLE_BY_KIND: Dict[str, str] = {
    "character": "project_characters",
    "location": "project_lib_entities",
    "prop": "project_lib_entities",
}

# The kinds the resolve endpoint accepts, in one place so the router pattern and
# the service check cannot drift.
LEGACY_KINDS = tuple(LEGACY_TABLE_BY_KIND)


def legacy_table_for_kind(kind: Optional[str]) -> Optional[str]:
    """The label for a card kind, or ``None`` for a kind we cannot map.

    ``None`` is "we cannot say which legacy table this was" — the caller turns
    it into a typed refusal rather than into an empty result, because an empty
    result reads as "that entity was never migrated".
    """
    return LEGACY_TABLE_BY_KIND.get((kind or "").strip())
