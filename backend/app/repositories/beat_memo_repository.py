# app/repositories/beat_memo_repository.py

"""Beat Memo Repository — SQLAlchemy 2.0 ORM data access for timeline memos
(``beat_memos``), the laper-style note tier anchored to a script's Beats
arrangement.

ORM-only (read_scope / write_scope), matching ``script_beat_repository.py``:
reads swallow + return None/[] on failure; writes log + re-raise (write_scope
rolls back on any raise). Every bigint id/FK is ``_bigint``-coerced and the
INTEGER ``anchor_sec`` is int-coerced at the boundary (asyncpg binds strictly —
a str would silently miss / raise). Read dicts go through strategy-C value-type
parity (datetime → ISO str; bigint ids STAY native int; ``images`` JSONB stays a
list).

Ownership is enforced by the router guard (verify_memo_access → script → team),
not here — except ``list_by_script`` which scopes to one script.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import BeatMemos
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_MEMO_N2A: Dict[str, str] = _name_to_attr(BeatMemos)
_MEMO_ATTRS = {p.key for p in BeatMemos.__mapper__.column_attrs}

# update() whitelist — the editable memo fields. NEVER script_id or id.
_UPDATE_FIELDS = frozenset({"anchor_sec", "content", "images"})


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _int_or_none(v: Any) -> Optional[int]:
    """Coerce an INTEGER-column bind value to native int; None passes through."""
    return None if v is None else int(v)


def _coerce_images(value: Any) -> List[str]:
    """Normalize ``images`` to a list of strings (object-store path keys)."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(s) for s in value]


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime → ISO str.
    Bigint ids and JSONB (list) stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one BeatMemos row."""
    return _parity(_orm_obj_to_dict(obj, _MEMO_N2A))


def _memo_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the write ``values()`` dict: keep only mapped columns, bigint-coerce
    script_id, int-coerce anchor_sec, and string-coerce images."""
    known = {k: v for k, v in data.items() if k in _MEMO_ATTRS}
    if "script_id" in known and known["script_id"] is not None:
        known["script_id"] = _bigint(known["script_id"])
    if "anchor_sec" in known and known["anchor_sec"] is not None:
        known["anchor_sec"] = _int_or_none(known["anchor_sec"])
    if "images" in known:
        known["images"] = _coerce_images(known["images"])
    return known


class BeatMemoRepository:
    """Beat memo data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def list_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        """All memos for a script, ordered along the timeline (anchor_sec asc)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(BeatMemos)
                    .where(BeatMemos.script_id == _bigint(script_id))
                    .order_by(BeatMemos.anchor_sec.asc(), BeatMemos.id.asc())
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list memos for script {script_id}: {e}")
            return []

    async def get_by_id(self, memo_id: str) -> Optional[Dict[str, Any]]:
        """A single memo by id, or None (used by the ownership guard + serve)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(BeatMemos).where(BeatMemos.id == _bigint(memo_id)).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get memo {memo_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Writes — create / update / delete
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a memo under a script."""
        try:
            values = _memo_write_values(data)
            async with write_scope() as session:
                result = await session.execute(
                    insert(BeatMemos).values(**values).returning(BeatMemos)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created memo in script {data.get('script_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create memo: {e}")
            raise

    async def update(
        self, memo_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update anchor_sec / content / images only. NEVER touches script_id."""
        try:
            values = {k: v for k, v in data.items() if k in _UPDATE_FIELDS}
            if "anchor_sec" in values and values["anchor_sec"] is not None:
                values["anchor_sec"] = _int_or_none(values["anchor_sec"])
            if "images" in values:
                values["images"] = _coerce_images(values["images"])
            if not values:
                return await self.get_by_id(memo_id)
            values["updated_at"] = func.now()
            async with write_scope() as session:
                result = await session.execute(
                    update(BeatMemos)
                    .where(BeatMemos.id == _bigint(memo_id))
                    .values(**values)
                    .returning(BeatMemos)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated memo {memo_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update memo {memo_id}: {e}")
            raise

    async def delete(self, memo_id: str) -> bool:
        """Delete a memo."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(BeatMemos).where(BeatMemos.id == _bigint(memo_id))
                )
            logger.info(f"Deleted memo {memo_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete memo {memo_id}: {e}")
            raise


def get_beat_memo_repository() -> "BeatMemoRepository":
    """Return the BeatMemoRepository (ORM-only)."""
    return BeatMemoRepository()
