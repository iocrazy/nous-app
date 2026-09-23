"""Catalog-name aliasing across the ``mediahub-`` → ``nous-`` row rename.

``nous_models`` rows are addressed by ``name``, and those names are persisted in
places we cannot fully enumerate (system settings, governance keys, per-user AI
settings, agent ``model`` fields, canvas node JSON). The rename of the legacy
``mediahub-*`` rows to ``nous-*`` is therefore done in two steps:

1. (this module) every by-name lookup accepts BOTH spellings, so a reference
   written with either prefix resolves to the row whichever way the row is
   currently named — independent of whether the rename migration or the code
   deploy lands first, and robust to references the migration misses;
2. a later migration renames the rows and rewrites the known references.

Rules — deliberately narrow:

* the exact name is always tried first and always wins. If both ``mediahub-X``
  and ``nous-X`` exist as distinct rows, each name resolves to its own row;
* only the two prefixes are swapped; nothing is lower-cased, trimmed or
  otherwise fuzzed (the unique index on ``nous_models.name`` is exact);
* a name carrying neither prefix has no alias.

Every by-name lookup of a catalog row must go through
:func:`catalog_name_candidates` (SQL) or :func:`find_row_by_catalog_name`
(rows already in memory) — a second matcher is how the two spellings drift.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, TypeVar

LEGACY_CATALOG_PREFIX = "mediahub-"
CURRENT_CATALOG_PREFIX = "nous-"

_SWAP = (
    (LEGACY_CATALOG_PREFIX, CURRENT_CATALOG_PREFIX),
    (CURRENT_CATALOG_PREFIX, LEGACY_CATALOG_PREFIX),
)

RowT = TypeVar("RowT", bound=Mapping[str, Any])


def catalog_name_alias(name: str) -> str | None:
    """The other spelling of ``name`` across the prefix rename, or ``None``.

    ``mediahub-X`` ↔ ``nous-X``. A bare prefix (empty rest) has no alias.
    """
    if not name:
        return None
    for src, dst in _SWAP:
        if name.startswith(src) and len(name) > len(src):
            return dst + name[len(src) :]
    return None


def catalog_name_candidates(name: str) -> tuple[str, ...]:
    """``name`` first, then its alias (if any). Order is precedence."""
    alias = catalog_name_alias(name)
    return (name,) if alias is None else (name, alias)


def find_row_by_catalog_name(
    rows: Iterable[RowT], name: str, *, key: str = "name"
) -> RowT | None:
    """The row whose ``key`` equals ``name``, else the row matching its alias.

    Exact beats alias regardless of list order; within one candidate the first
    row in list order wins (callers pass rows already ordered by precedence).
    """
    if not name:
        return None
    materialized = list(rows)
    for candidate in catalog_name_candidates(name):
        for row in materialized:
            if row.get(key) == candidate:
                return row
    return None
