"""SQLAlchemy 2.0 ORM implementation of SystemSettingsRepository (admin wave).

REST → ORM successor for the ``system_settings`` admin CRUD.
``SystemSettingsRepositoryOrm`` subclasses ``SystemSettingsRepository`` and
overrides every data method; the ``TABLE`` constant is inherited. Call sites
route through ``get_system_settings_repository()`` (bottom of
``system_settings_repository.py``).

MODEL: ``app.models.SystemSettings`` (table ``system_settings``, PK = ``key``
text) — verified reflected.

★ UUID AUDIT ★
==============
``system_settings`` has ONE uuid column, ``updated_by`` (the admin who last wrote
the setting). It is READ (list / update return it) and WRITTEN (update sets it).

  updated_by (uuid) → **str** on READ. CONSUMED: ``_to_response`` in the settings
    router sets ``SystemSettingResponse.updated_by: Optional[str]`` from it. A
    native uuid would not match the declared str field shape; REST returned a str.
    No ``==``/dict-key consumer, but str() preserves exact REST parity.

NON-uuid type-sensitive columns
-------------------------------
  updated_at (timestamptz) → **.isoformat()** ALWAYS (SystemSettingResponse takes
    ``updated_at: datetime`` — pydantic parses the ISO str; REST returned a str).
  value / options (jsonb) → native dict (REST returned a parsed object).
  key / description / category / input_type (text/varchar) → native str.

Model-quirk scan: no SQLAlchemy Enum column, no renamed column. Reads route
through ``_name_to_attr`` + ``_orm_obj_to_dict`` then the uuid→str / datetime→ISO
sweep. NO date-range filters anywhere in this repo.

WRITE PATH (the silent-rollback P0 lesson)
==========================================
``update(key, value, updated_by)`` writes ``value`` + ``updated_by`` and MUST
COMMIT — it runs inside ``write_scope()`` (never a bare read_scope / connect()).
The bound columns (``value`` jsonb, ``updated_by`` uuid — a str user_id, which the
SQLAlchemy Uuid type processor accepts) are both real mapped columns: NO phantom
column. ``updated_at`` is left to the DB server default (the legacy did not set it
either). Returns the updated SELECT *-shaped row dict (parity-coerced) or None
when no row matched (REST-contract parity — the legacy returned None).

``exists(key)`` reproduces the legacy maybe_single semantics: a scalar existence
probe that swallows any error to False (the legacy wrapped it in try/except).
``list_non_transcode`` preserves the "exclude transcode_* keys" policy verbatim
and orders by ``key``.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import SystemSettings
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.system_settings_repository import (
    SystemSettingsRepository,
)

_SETTINGS_N2A: Dict[str, str] = _name_to_attr(SystemSettings)


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for a ``system_settings`` row:
    updated_by (uuid) → str, updated_at (timestamptz) → ISO str. value/options
    (jsonb) stay native dict. NULLs pass through."""
    out = _orm_obj_to_dict(obj, _SETTINGS_N2A)
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class SystemSettingsRepositoryOrm(SystemSettingsRepository):
    """ORM-backed SystemSettingsRepository (system_settings admin CRUD)."""

    async def list_non_transcode(self) -> List[dict[str, Any]]:
        """All settings ordered by ``key``, EXCLUDING transcode_* (managed
        elsewhere) — the exclusion policy is preserved verbatim."""
        async with read_scope() as session:
            result = await session.execute(
                select(SystemSettings).order_by(SystemSettings.key)
            )
            rows = [_row(r) for r in result.scalars().all()]
        return [row for row in rows if not row["key"].startswith("transcode_")]

    async def exists(self, key: str) -> bool:
        """True iff a setting with ``key`` exists. Swallows any error to False —
        parity with the legacy maybe_single try/except."""
        try:
            async with read_scope() as session:
                found = await session.scalar(
                    select(SystemSettings.key).where(SystemSettings.key == key).limit(1)
                )
            return found is not None
        except Exception as e:  # noqa: BLE001 — legacy parity (swallow → False)
            logger.warning(f"[SystemSettings] exists({key}) failed: {e}")
            return False

    async def update(
        self, key: str, value: Any, updated_by: str
    ) -> Optional[dict[str, Any]]:
        """Update value + updated_by for ``key``; COMMITS via write_scope().
        Returns the updated row dict (parity-coerced) or None if no row matched
        (REST-contract parity)."""
        async with write_scope() as session:
            result = await session.execute(
                sa_update(SystemSettings)
                .where(SystemSettings.key == key)
                .values(value=value, updated_by=updated_by)
                .returning(SystemSettings)
            )
            row = result.scalars().first()
            out = _row(row) if row else None
        return out

    async def upsert_setting(
        self, key: str, value: Any, updated_by: str
    ) -> dict[str, Any]:
        """Insert-or-update via ``INSERT ... ON CONFLICT DO UPDATE``.

        Safe for first-time writes where the row may not exist yet (no seed
        migration required).  Commits via ``write_scope()``.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        async with write_scope() as session:
            stmt = (
                pg_insert(SystemSettings)
                .values(key=key, value=value, updated_by=updated_by)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"value": value, "updated_by": updated_by},
                )
                .returning(SystemSettings)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
        return _row(row) if row else {"key": key, "value": value}


__all__ = ["SystemSettingsRepositoryOrm"]
