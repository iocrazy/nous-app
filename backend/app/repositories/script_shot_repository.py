# app/repositories/script_shot_repository.py

"""Script Shot Repository — SQLAlchemy 2.0 ORM data access for the shot layer
(``script_shots``), the storyboard tier that hangs off a scene.

ORM-only (read_scope / write_scope), matching the house idiom in
``script_scene_repository.py``: reads swallow + return None/[] on failure; writes
log + re-raise (write_scope rolls back on any raise — the silent-rollback P0
lesson). Every bigint id/FK is ``_bigint``-coerced at the boundary (the 5.3 trap
— a snowflake compared/bound as str silently misses), and read dicts go through
strategy-C value-type parity (uuid → str, datetime → ISO str; bigint ids STAY
native int).

Two disjoint write lanes keep the status machine honest:
  - ``update`` writes ONLY the parameter-tag / description whitelist. It never
    touches ``status`` or the image/thumbnail/video URLs.
  - ``update_status`` is the ONLY writer of ``status`` + the URL columns (the
    generate workflow's lane).
``create_many`` lands a whole AI-generated shot list in ONE transaction so a
partial write can never leave a scene half-boarded.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import ScriptShots
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

# Sparse-ordering step. New shots land at MAX+STEP; moves bisect neighbours,
# renumbering the whole scene group to a fresh ladder when the gap is exhausted.
STEP = 1000

_SHOTS_N2A: Dict[str, str] = _name_to_attr(ScriptShots)
_SHOTS_ATTRS = {p.key for p in ScriptShots.__mapper__.column_attrs}

# bigint columns coerced on write. Ids/FKs stay native int on read (5.3 trap).
_SHOT_BIGINT_FIELDS = ("scene_id",)

# update() whitelist — parameter tags + description only. NEVER status, the
# image/thumbnail/video URLs (update_status owns those), scene_id, sort_order
# (move_shot owns it), or id.
_UPDATE_FIELDS = frozenset(
    {
        "shot_number",
        "shot_type",
        "camera_angle",
        "camera_movement",
        "focal_length",
        "lighting",
        "description",
    }
)

# update_status() whitelist — the status machine + its produced media URLs.
_STATUS_FIELDS = ("status", "image_url", "thumbnail_url", "video_url")


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime/date → ISO
    str. Bigint ids/FKs stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ScriptShots row."""
    return _parity(_orm_obj_to_dict(obj, _SHOTS_N2A))


def _shot_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the write ``values()`` dict: keep only mapped columns (graceful
    no-op for unknown keys, REST parity) and bigint-coerce the id/FK fields."""
    known = {k: v for k, v in data.items() if k in _SHOTS_ATTRS}
    for field in _SHOT_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    return known


class ScriptShotRepository:
    """Shot data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def list_by_scene(self, scene_id: str) -> List[Dict[str, Any]]:
        """All shots for a scene, ordered by sort_order (the storyboard column
        read order)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptShots)
                    .where(ScriptShots.scene_id == _bigint(scene_id))
                    .order_by(ScriptShots.sort_order.asc())
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list shots for scene {scene_id}: {e}")
            return []

    async def get_by_id(self, shot_id: str) -> Optional[Dict[str, Any]]:
        """A single shot by id, or None."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptShots)
                    .where(ScriptShots.id == _bigint(shot_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get shot {shot_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Writes — create / create_many / update / update_status / delete
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a shot. When ``sort_order`` is not supplied, auto-assign it to
        ``MAX(sort_order) + STEP`` within the same scene (sparse ordering, so
        later moves can bisect without renumbering)."""
        try:
            values = _shot_write_values(data)
            async with write_scope() as session:
                if data.get("sort_order") is None:
                    scene_id = values["scene_id"]
                    current_max = await session.scalar(
                        select(func.max(ScriptShots.sort_order)).where(
                            ScriptShots.scene_id == scene_id
                        )
                    )
                    values["sort_order"] = (current_max or 0) + STEP
                result = await session.execute(
                    insert(ScriptShots).values(**values).returning(ScriptShots)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created shot in scene {data.get('scene_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create shot: {e}")
            raise

    async def create_many(
        self, scene_id: str, shots: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Bulk-insert an ordered shot list for a scene in ONE transaction.

        Used by Auto Storyboard to land an AI-generated breakdown atomically.
        APPENDS to whatever the scene already holds: ``shot_number`` continues
        from the scene's current ``MAX(shot_number)`` and ``sort_order`` from
        ``MAX(sort_order) + STEP`` — so re-running Auto Storyboard adds a fresh
        batch rather than colliding numbers or clobbering the user's manual
        shots. ``status`` defaults to ``'empty'`` (a caller-supplied status is
        ignored — creation never sets a produced state). Returns the created
        rows in order."""
        sid = _bigint(scene_id)
        try:
            out: List[Dict[str, Any]] = []
            async with write_scope() as session:
                base_num = (
                    await session.scalar(
                        select(func.max(ScriptShots.shot_number)).where(
                            ScriptShots.scene_id == sid
                        )
                    )
                ) or 0
                base_sort = (
                    await session.scalar(
                        select(func.max(ScriptShots.sort_order)).where(
                            ScriptShots.scene_id == sid
                        )
                    )
                ) or 0
                for idx, shot in enumerate(shots, start=1):
                    values = _shot_write_values(shot)
                    values["scene_id"] = sid
                    values["shot_number"] = base_num + idx
                    values["sort_order"] = base_sort + idx * STEP
                    values.pop("status", None)  # empty on create (server default)
                    result = await session.execute(
                        insert(ScriptShots).values(**values).returning(ScriptShots)
                    )
                    row = result.scalars().first()
                    if row is None:
                        raise RuntimeError("Insert into script_shots returned no data")
                    out.append(_row(row))
            logger.info(f"Created {len(out)} shots in scene {scene_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to create_many shots for scene {scene_id}: {e}")
            raise

    async def update(
        self, shot_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update parameter tags / description only. NEVER touches ``status`` or
        the image/thumbnail/video URLs — those flow through ``update_status``."""
        try:
            values = {k: v for k, v in data.items() if k in _UPDATE_FIELDS}
            if not values:
                return await self.get_by_id(shot_id)
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptShots)
                    .where(ScriptShots.id == _bigint(shot_id))
                    .values(**values)
                    .returning(ScriptShots)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated shot {shot_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update shot {shot_id}: {e}")
            raise

    async def update_status(
        self,
        shot_id: str,
        status: str,
        *,
        image_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        video_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """The status write lane: set ``status`` and (optionally) the produced
        media URLs. Only URLs explicitly passed are written — a None argument
        leaves the existing column untouched (so a 'generating' flip does not
        clobber a prior image_url)."""
        try:
            values: Dict[str, Any] = {"status": status}
            if image_url is not None:
                values["image_url"] = image_url
            if thumbnail_url is not None:
                values["thumbnail_url"] = thumbnail_url
            if video_url is not None:
                values["video_url"] = video_url
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptShots)
                    .where(ScriptShots.id == _bigint(shot_id))
                    .values(**values)
                    .returning(ScriptShots)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated shot {shot_id} status → {status}")
            return out
        except Exception as e:
            logger.error(f"Failed to update shot status {shot_id}: {e}")
            raise

    async def update_video_url(
        self, shot_id: str, video_url: str
    ) -> Optional[Dict[str, Any]]:
        """Write ONLY ``video_url`` — never ``status``.

        The video lane is decoupled from the image ``status`` machine: a video
        run must not read-modify-write ``status`` (that races a concurrent image
        generation flipping it to 'done' and would clobber it). This single-
        column UPDATE touches ``video_url`` and nothing else."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptShots)
                    .where(ScriptShots.id == _bigint(shot_id))
                    .values(video_url=video_url)
                    .returning(ScriptShots)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated shot {shot_id} video_url")
            return out
        except Exception as e:
            logger.error(f"Failed to update shot video_url {shot_id}: {e}")
            raise

    async def delete(self, shot_id: str) -> bool:
        """Delete a shot."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ScriptShots).where(ScriptShots.id == _bigint(shot_id))
                )
            logger.info(f"Deleted shot {shot_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete shot {shot_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Reorder — sparse insertion within the scene, renumber on gap exhaustion.
    # ------------------------------------------------------------------ #

    async def move_shot(
        self,
        shot_id: str,
        *,
        before_shot_id: Optional[str] = None,
        after_shot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reorder a shot within its scene.

        The shot is bisected between the neighbours named by ``before_shot_id`` /
        ``after_shot_id`` (or appended to the tail when neither is given). When
        the neighbour gap is exhausted, the whole scene's shot group is
        renumbered to a fresh STEP ladder. Same sparse-ordering semantics as
        ``move_scene`` — shots never leave their scene."""
        sid = _bigint(shot_id)
        try:
            async with write_scope() as session:
                shot = (
                    (
                        await session.execute(
                            select(ScriptShots).where(ScriptShots.id == sid).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if shot is None:
                    raise ValueError(f"shot {shot_id} not found")

                scene_id = shot.scene_id
                sib_stmt = (
                    select(ScriptShots.id, ScriptShots.sort_order)
                    .where(ScriptShots.scene_id == scene_id)
                    .where(ScriptShots.id != sid)
                    .order_by(ScriptShots.sort_order.asc())
                )
                siblings = [
                    (r[0], r[1]) for r in (await session.execute(sib_stmt)).all()
                ]

                lower, upper = self._resolve_bounds(
                    siblings, before_shot_id, after_shot_id
                )
                new_order = self._sparse_between(lower, upper)
                if new_order is None:
                    await self._renumber_group(
                        session, siblings, sid, before_shot_id, after_shot_id
                    )
                else:
                    await session.execute(
                        update(ScriptShots)
                        .where(ScriptShots.id == sid)
                        .values(sort_order=new_order)
                    )

                moved = (
                    (
                        await session.execute(
                            select(ScriptShots).where(ScriptShots.id == sid).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                out = _row(moved) if moved else {}
            logger.info(f"Moved shot {shot_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to move shot {shot_id}: {e}")
            raise

    @staticmethod
    def _resolve_bounds(
        siblings: List[tuple],
        before_shot_id: Optional[str],
        after_shot_id: Optional[str],
    ) -> tuple:
        """Resolve (lower, upper) sort_order bounds the moved shot lands between.
        ``before`` places just above that shot; ``after`` just below. An
        unknown/absent anchor falls back to appending at the tail."""
        ids = [s[0] for s in siblings]
        orders = [s[1] for s in siblings]
        if before_shot_id is not None:
            bid = _bigint(before_shot_id)
            if bid in ids:
                i = ids.index(bid)
                return (orders[i - 1] if i > 0 else None), orders[i]
        if after_shot_id is not None:
            aid = _bigint(after_shot_id)
            if aid in ids:
                i = ids.index(aid)
                return orders[i], (orders[i + 1] if i + 1 < len(orders) else None)
        return (orders[-1] if orders else None), None

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
        session: Any,
        siblings: List[tuple],
        sid: int,
        before_shot_id: Optional[str],
        after_shot_id: Optional[str],
    ) -> None:
        """Rewrite every shot in the scene to a fresh STEP ladder, with the moved
        shot spliced in at the requested slot (one UPDATE per row)."""
        ids = [s[0] for s in siblings]
        insert_idx = len(ids)  # default: append at tail
        if before_shot_id is not None and _bigint(before_shot_id) in ids:
            insert_idx = ids.index(_bigint(before_shot_id))
        elif after_shot_id is not None and _bigint(after_shot_id) in ids:
            insert_idx = ids.index(_bigint(after_shot_id)) + 1
        ordered = ids[:insert_idx] + [sid] + ids[insert_idx:]
        for i, eid in enumerate(ordered):
            await session.execute(
                update(ScriptShots)
                .where(ScriptShots.id == eid)
                .values(sort_order=(i + 1) * STEP)
            )


def get_script_shot_repository() -> "ScriptShotRepository":
    """Return the ScriptShotRepository (ORM-only)."""
    return ScriptShotRepository()
