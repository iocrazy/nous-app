"""Write-side bind coercion for the asyncpg/ORM path.

asyncpg refuses ISO strings for timestamp/timestamptz binds
(``DataError: invalid input for query argument``) — and because the whole
UPDATE rolls back, one bad bind silently drops every other column in the
statement. The 2026-07-05 download incident: ``download_time`` arrived as
``datetime.now().isoformat()`` (a legacy PostgREST habit, where strings were
fine), so ``download_path`` never persisted and every download looked
un-downloaded to the UI.

Call sites should bind real ``datetime`` objects; this module is the
defense layer for the ones that slip through.

Related per-repo variants that predate this module:
``issue_repository._coerce_temporal`` and ``projects_repository.
_coerce_temporal`` (kinds-map based). New call sites should use THIS
model-driven helper; migrating those two onto it is a welcome follow-up.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, FrozenSet, Type

from sqlalchemy import DateTime


@lru_cache(maxsize=None)
def _datetime_cols(model: Type[Any]) -> FrozenSet[str]:
    """DateTime-typed column keys of ``model`` (cached — pure function of
    the class, and this runs on every write of adopting repositories)."""
    return frozenset(
        col.key for col in model.__table__.columns if isinstance(col.type, DateTime)
    )


def coerce_datetime_strings(model: Type[Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a NEW dict with ISO strings parsed to datetimes for every
    DateTime-typed column of ``model``.

    Non-string / non-DateTime-column values pass through untouched. A string
    that does not parse raises ``ValueError`` naming the column — failing
    loudly at the boundary beats asyncpg's opaque bind error after the
    statement is already doomed.
    """
    datetime_cols = _datetime_cols(model)
    out = dict(data)
    for key, value in out.items():
        if key not in datetime_cols or not isinstance(value, str):
            continue
        try:
            out[key] = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"Column {model.__tablename__}.{key} expects a datetime; "
                f"got unparseable string {value[:64]!r}"
            ) from exc
    return out
