"""Catalog-name aliasing across ``nous_models`` row renames.

``nous_models`` rows are addressed by ``name``, and those names are persisted in
places we cannot fully enumerate (system settings, governance keys, per-user AI
settings, agent ``model`` fields, canvas node JSON, external callers that pass a
model name). Renaming a row is therefore done in two steps:

1. (this module) every by-name lookup accepts BOTH spellings, so a reference
   written with either one resolves to the row whichever way the row is
   currently named — independent of whether the rename migration or the code
   deploy lands first, and robust to references the migration misses;
2. a later migration renames the row and rewrites the known references.

Two kinds of rename are covered:

* the ``mediahub-`` → ``nous-`` prefix rename (mig 488), derived by rule;
* explicit one-off renames that no rule can derive, listed in :data:`_RENAMES`
  (``nous-qwen3-llm`` → ``nous-qwen3-8-27b``, mig 502). The table works in
  both directions: before the migration only the old row exists, afterwards
  only the new one. **It is kept indefinitely — no removal window.** External
  callers may hard-code an old name and there is no way to enumerate them.

Rules — deliberately narrow:

* the exact name is always tried first and always wins. If both spellings
  exist as distinct rows, each name resolves to its own row;
* a name has at most ONE alias. The explicit table is consulted first; a name
  it lists is not also prefix-swapped (``nous-qwen3-llm`` does not grow a
  ``mediahub-qwen3-llm`` candidate — that spelling never existed);
* only whole names match; nothing is lower-cased, trimmed or otherwise fuzzed
  (the unique index on ``nous_models.name`` is exact);
* a name that is in neither the table nor carries either prefix has no alias.

Every by-name lookup of a catalog row must go through
:func:`catalog_name_candidates` (SQL) or :func:`find_row_by_catalog_name`
(rows already in memory) — a second matcher is how the two spellings drift.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable, Mapping, TypeVar

LEGACY_CATALOG_PREFIX = "mediahub-"
CURRENT_CATALOG_PREFIX = "nous-"

_SWAP = (
    (LEGACY_CATALOG_PREFIX, CURRENT_CATALOG_PREFIX),
    (CURRENT_CATALOG_PREFIX, LEGACY_CATALOG_PREFIX),
)

# old name → new name. Kept forever (see module docstring); add, never remove.
_RENAMES: Mapping[str, str] = MappingProxyType(
    {
        "nous-qwen3-llm": "nous-qwen3-8-27b",
    }
)

_RENAMES_BOTH_WAYS: Mapping[str, str] = MappingProxyType(
    {**_RENAMES, **{new: old for old, new in _RENAMES.items()}}
)

RowT = TypeVar("RowT", bound=Mapping[str, Any])


def catalog_name_alias(name: str) -> str | None:
    """The other spelling of ``name`` across a rename, or ``None``.

    The explicit table (either direction) first, then ``mediahub-X`` ↔
    ``nous-X``. A bare prefix (empty rest) has no alias.
    """
    if not name:
        return None
    renamed = _RENAMES_BOTH_WAYS.get(name)
    if renamed is not None:
        return renamed
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
