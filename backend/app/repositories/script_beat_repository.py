# app/repositories/script_beat_repository.py

"""Script Beat Repository — SQLAlchemy 2.0 ORM data access for the beat layer
(``script_beats``), the classic beat-sheet tier that hangs off a script.

ORM-only (read_scope / write_scope), matching the house idiom in
``script_shot_repository.py``: reads swallow + return None/[] on failure; writes
log + re-raise (write_scope rolls back on any raise — the silent-rollback P0
lesson). Every bigint id/FK is ``_bigint``-coerced at the boundary (the 5.3 trap
— a snowflake compared/bound as str silently misses), and read dicts go through
strategy-C value-type parity (uuid → str, datetime → ISO str; bigint ids STAY
native int; JSONB stays a list).

``scene_ids`` is an ordered JSONB array of linked scene ids. It is coerced to a
list of STRINGS on write (#1006 — a bigint scene id round-trips through the API
+ JS as a string; keeping the stored form string-typed avoids a native-int vs
str mismatch on read-back).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import ScriptBeats
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

# Sparse-ordering step. New beats land at MAX+STEP; moves bisect neighbours,
# renumbering the whole script's beat group to a fresh ladder when exhausted.
STEP = 1000

_BEATS_N2A: Dict[str, str] = _name_to_attr(ScriptBeats)
_BEATS_ATTRS = {p.key for p in ScriptBeats.__mapper__.column_attrs}

# bigint columns coerced on write. Ids/FKs stay native int on read (5.3 trap).
_BEAT_BIGINT_FIELDS = ("script_id",)

# INTEGER (non-id) columns coerced to native int on write — asyncpg binds an
# INTEGER column strictly, so a string at the API boundary must become int.
_BEAT_INT_FIELDS = ("start_sec", "duration_sec")

# update() whitelist — the editable beat fields. NEVER script_id, sort_order
# (move owns it), or id.
_UPDATE_FIELDS = frozenset(
    {"title", "summary", "scene_ids", "start_sec", "duration_sec", "beat_role", "color"}
)


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _int_or_none(v: Any) -> Optional[int]:
    """Coerce an INTEGER-column bind value to native int; None passes through."""
    return None if v is None else int(v)


def _coerce_scene_ids(value: Any) -> List[str]:
    """Normalize ``scene_ids`` to an ordered list of strings (#1006)."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(s) for s in value]


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime/date → ISO
    str. Bigint ids/FKs and JSONB (list) stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ScriptBeats row."""
    return _parity(_orm_obj_to_dict(obj, _BEATS_N2A))


def _beat_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the write ``values()`` dict: keep only mapped columns (graceful
    no-op for unknown keys, REST parity), bigint-coerce id/FK fields, and
    string-coerce scene_ids."""
    known = {k: v for k, v in data.items() if k in _BEATS_ATTRS}
    for field in _BEAT_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    for field in _BEAT_INT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _int_or_none(known[field])
    if "scene_ids" in known:
        known["scene_ids"] = _coerce_scene_ids(known["scene_ids"])
    return known


class ScriptBeatRepository:
    """Beat data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def list_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        """All beats for a script, ordered by sort_order (the beat-sheet order)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptBeats)
                    .where(ScriptBeats.script_id == _bigint(script_id))
                    .order_by(ScriptBeats.sort_order.asc())
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list beats for script {script_id}: {e}")
            return []

    async def get_by_id(self, beat_id: str) -> Optional[Dict[str, Any]]:
        """A single beat by id, or None."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptBeats)
                    .where(ScriptBeats.id == _bigint(beat_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get beat {beat_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Writes — create / update / delete
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a beat. When ``sort_order`` is not supplied, auto-assign it to
        ``MAX(sort_order) + STEP`` within the script (sparse ordering, so later
        moves can bisect without renumbering)."""
        try:
            values = _beat_write_values(data)
            async with write_scope() as session:
                if data.get("sort_order") is None:
                    script_id = values["script_id"]
                    current_max = await session.scalar(
                        select(func.max(ScriptBeats.sort_order)).where(
                            ScriptBeats.script_id == script_id
                        )
                    )
                    values["sort_order"] = (current_max or 0) + STEP
                result = await session.execute(
                    insert(ScriptBeats).values(**values).returning(ScriptBeats)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created beat in script {data.get('script_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create beat: {e}")
            raise

    async def update(
        self, beat_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update title / summary / scene_ids only. NEVER touches script_id or
        sort_order (move owns it)."""
        try:
            values = {k: v for k, v in data.items() if k in _UPDATE_FIELDS}
            if "scene_ids" in values:
                values["scene_ids"] = _coerce_scene_ids(values["scene_ids"])
            for field in _BEAT_INT_FIELDS:
                if field in values and values[field] is not None:
                    values[field] = _int_or_none(values[field])
            if not values:
                return await self.get_by_id(beat_id)
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptBeats)
                    .where(ScriptBeats.id == _bigint(beat_id))
                    .values(**values)
                    .returning(ScriptBeats)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated beat {beat_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update beat {beat_id}: {e}")
            raise

    async def delete(self, beat_id: str) -> bool:
        """Delete a beat."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ScriptBeats).where(ScriptBeats.id == _bigint(beat_id))
                )
            logger.info(f"Deleted beat {beat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete beat {beat_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Reorder — sparse insertion within the script, renumber on exhaustion.
    # ------------------------------------------------------------------ #

    async def move(
        self, beat_id: str, *, after_beat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Reorder a beat within its script.

        The beat lands just after ``after_beat_id`` (or at the front when it is
        ``None``). It is bisected into the gap; when the gap is exhausted the
        whole script's beat group is renumbered to a fresh STEP ladder. Same
        sparse-ordering semantics as ``move_scene`` / ``move_shot``."""
        bid = _bigint(beat_id)
        try:
            async with write_scope() as session:
                beat = (
                    (
                        await session.execute(
                            select(ScriptBeats).where(ScriptBeats.id == bid).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if beat is None:
                    raise ValueError(f"beat {beat_id} not found")

                script_id = beat.script_id
                sib_stmt = (
                    select(ScriptBeats.id, ScriptBeats.sort_order)
                    .where(ScriptBeats.script_id == script_id)
                    .where(ScriptBeats.id != bid)
                    .order_by(ScriptBeats.sort_order.asc())
                )
                siblings = [
                    (r[0], r[1]) for r in (await session.execute(sib_stmt)).all()
                ]

                lower, upper, insert_idx = self._resolve_slot(siblings, after_beat_id)
                new_order = self._sparse_between(lower, upper)
                if new_order is None:
                    await self._renumber_group(session, siblings, bid, insert_idx)
                else:
                    await session.execute(
                        update(ScriptBeats)
                        .where(ScriptBeats.id == bid)
                        .values(sort_order=new_order)
                    )

                moved = (
                    (
                        await session.execute(
                            select(ScriptBeats).where(ScriptBeats.id == bid).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                out = _row(moved) if moved else {}
            logger.info(f"Moved beat {beat_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to move beat {beat_id}: {e}")
            raise

    @staticmethod
    def _resolve_slot(siblings: List[tuple], after_beat_id: Optional[str]) -> tuple:
        """Return ``(lower, upper, insert_idx)`` for the slot the beat lands in.

        ``after_beat_id=None`` → front (before the first sibling). A named anchor
        → just after it. An unknown/absent anchor → append at the tail."""
        ids = [s[0] for s in siblings]
        orders = [s[1] for s in siblings]
        if after_beat_id is None:
            return None, (orders[0] if orders else None), 0
        aid = _bigint(after_beat_id)
        if aid in ids:
            i = ids.index(aid)
            return orders[i], (orders[i + 1] if i + 1 < len(orders) else None), i + 1
        return (orders[-1] if orders else None), None, len(ids)

    @staticmethod
    def _sparse_between(lower: Optional[int], upper: Optional[int]) -> Optional[int]:
        """Midpoint sort_order between neighbours. Returns None to signal the
        gap is exhausted (caller renumbers the whole group)."""
        if lower is None and upper is None:
            return STEP
        if lower is None:
            return upper - STEP
        if upper is None:
            return lower + STEP
        gap = upper - lower
        if gap < 2:
            return None
        return lower + gap // 2

    @staticmethod
    async def _renumber_group(
        session: Any, siblings: List[tuple], bid: int, insert_idx: int
    ) -> None:
        """Rewrite every beat in the script to a fresh STEP ladder, with the
        moved beat spliced in at ``insert_idx`` (one UPDATE per row)."""
        ids = [s[0] for s in siblings]
        ordered = ids[:insert_idx] + [bid] + ids[insert_idx:]
        for i, eid in enumerate(ordered):
            await session.execute(
                update(ScriptBeats)
                .where(ScriptBeats.id == eid)
                .values(sort_order=(i + 1) * STEP)
            )


def get_script_beat_repository() -> "ScriptBeatRepository":
    """Return the ScriptBeatRepository (ORM-only)."""
    return ScriptBeatRepository()
