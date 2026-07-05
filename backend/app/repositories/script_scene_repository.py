# app/repositories/script_scene_repository.py

"""Script Scene Repository — SQLAlchemy 2.0 ORM data access for the scene layer
(``script_scenes``) and its immutable operation ledger (``script_ops``).

ORM-only (read_scope / write_scope), matching the house idiom in
``projects_repository.py``. Reads swallow + return None/[] on failure; writes
log + re-raise. Every bigint id/FK is ``_bigint``-coerced at the boundary (the
5.3 trap — a snowflake compared/bound as str silently misses), and read dicts go
through strategy-C value-type parity (uuid → str, datetime → ISO str; bigint ids
STAY native int; JSONB stays a dict/list).

The load-bearing method is ``apply_element_ops``: a single transaction that
optimistically versions a scene's ``content_json`` via the pure ``apply_ops``
protocol (``app/services/script/scene_ops.py``), double-guards the UPDATE on the
read ``content_version``, and appends the op + its inverse to ``script_ops`` in
the SAME transaction. ``OpError`` from the protocol is NOT caught here — the
router maps it to 422; ``VersionConflict`` (below) maps to 409.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import ScriptOps, ScriptScenes
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.services.script.scene_ops import apply_ops, extract_text

# Sparse-ordering step. New scenes land at MAX+STEP; moves bisect neighbours,
# renumbering the whole group to a fresh ladder when the gap is exhausted.
STEP = 1000

# Sentinel so ``move_scene(chapter_id=UNSET)`` distinguishes "keep the current
# chapter" from "reparent to None" (detach). ``None`` is a legal target.
UNSET: Any = object()

_SCENES_N2A: Dict[str, str] = _name_to_attr(ScriptScenes)
_SCENES_ATTRS = {p.key for p in ScriptScenes.__mapper__.column_attrs}

# bigint columns coerced on write. Ids/FKs stay native int on read (5.3 trap).
_SCENE_BIGINT_FIELDS = ("script_id", "chapter_id", "location_id")

# update_meta whitelist — header fields + canvas coords only. NEVER content*,
# content_version, sort_order, script_id, chapter_id, or id (those move through
# create / apply_element_ops / move_scene, each with their own invariant).
_META_FIELDS = frozenset(
    {
        "heading_int_ext",
        "location_text",
        "location_id",
        "time_of_day",
        "position_x",
        "position_y",
        "width",
        "height",
    }
)


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime/date → ISO
    str. Bigint ids/FKs and JSONB (dict/list) stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ScriptScenes row."""
    return _parity(_orm_obj_to_dict(obj, _SCENES_N2A))


def _scene_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the write ``values()`` dict: keep only mapped columns (graceful
    no-op for unknown keys, REST parity) and bigint-coerce the id/FK fields."""
    known = {k: v for k, v in data.items() if k in _SCENES_ATTRS}
    for field in _SCENE_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    return known


class VersionConflict(Exception):
    """Raised when an ``apply_element_ops`` optimistic version guard fails.

    Carries ``current_version`` (the version the caller must re-sync to) and
    ``elements`` (the current ``content_json`` list) so the router can return a
    409 body the editor uses to rebase. The router maps this to HTTP 409."""

    def __init__(self, current_version: int, elements: List[dict]):
        self.current_version = current_version
        self.elements = elements
        super().__init__(f"content_version conflict: current={current_version}")


class ScriptSceneRepository:
    """Scene + op-ledger data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def list_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        """All scenes for a script, ordered by chapter_id NULLS LAST, then
        sort_order (the canonical read order for the editor / scene list)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptScenes)
                    .where(ScriptScenes.script_id == _bigint(script_id))
                    .order_by(
                        ScriptScenes.chapter_id.asc().nulls_last(),
                        ScriptScenes.sort_order.asc(),
                    )
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list scenes for script {script_id}: {e}")
            return []

    async def get_by_id(self, scene_id: str) -> Optional[Dict[str, Any]]:
        """A single scene by id, or None."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptScenes)
                    .where(ScriptScenes.id == _bigint(scene_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get scene {scene_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Writes — create / update_meta / delete
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a scene. When ``sort_order`` is not supplied, auto-assign it
        to ``MAX(sort_order) + STEP`` within the same (script_id, chapter_id)
        group (sparse ordering, so later moves can bisect without renumbering)."""
        try:
            values = _scene_write_values(data)
            async with write_scope() as session:
                if data.get("sort_order") is None:
                    script_id = values["script_id"]
                    chapter_id = values.get("chapter_id")
                    max_stmt = select(func.max(ScriptScenes.sort_order)).where(
                        ScriptScenes.script_id == script_id
                    )
                    if chapter_id is None:
                        max_stmt = max_stmt.where(ScriptScenes.chapter_id.is_(None))
                    else:
                        max_stmt = max_stmt.where(ScriptScenes.chapter_id == chapter_id)
                    current_max = await session.scalar(max_stmt)
                    values["sort_order"] = (current_max or 0) + STEP
                result = await session.execute(
                    insert(ScriptScenes).values(**values).returning(ScriptScenes)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(
                f"Created scene in script {data.get('script_id')} "
                f"chapter {data.get('chapter_id')}"
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create scene: {e}")
            raise

    async def update_meta(
        self, scene_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update header fields / canvas coords only. NEVER touches
        content_version, content, or content_json — those flow exclusively
        through ``apply_element_ops`` so the op ledger stays authoritative."""
        try:
            values = {k: v for k, v in data.items() if k in _META_FIELDS}
            if "location_id" in values and values["location_id"] is not None:
                values["location_id"] = _bigint(values["location_id"])
            if not values:
                return await self.get_by_id(scene_id)
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptScenes)
                    .where(ScriptScenes.id == _bigint(scene_id))
                    .values(**values)
                    .returning(ScriptScenes)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated scene meta {scene_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update scene meta {scene_id}: {e}")
            raise

    async def delete(self, scene_id: str) -> bool:
        """Delete a scene (its ops cascade via FK ON DELETE CASCADE)."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ScriptScenes).where(ScriptScenes.id == _bigint(scene_id))
                )
            logger.info(f"Deleted scene {scene_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete scene {scene_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Versioned element ops (the core) + op ledger — ONE transaction.
    # ------------------------------------------------------------------ #

    async def apply_element_ops(
        self,
        scene_id: str,
        ops: List[dict],
        expected_version: int,
        actor: str,
    ) -> Dict[str, Any]:
        """Apply a batch of element ops to a scene under optimistic concurrency.

        In one transaction: read (content_json, content_version); reject a stale
        ``expected_version`` with ``VersionConflict``; run the pure ``apply_ops``
        (``OpError`` propagates → 422); UPDATE the scene double-guarded on
        ``content_version`` (a lost race → rowcount 0 → re-read → conflict); and
        append the op + inverse to ``script_ops`` with ``op_seq`` = the new
        version. Returns ``{"content_version": new, "elements": new_elements}``.
        """
        sid = _bigint(scene_id)
        # No blanket try/except: VersionConflict + OpError are control flow the
        # router maps to 409/422, and a genuine DB error must propagate too
        # (write_scope rolls back on any raise — the silent-rollback P0 lesson).
        async with write_scope() as session:
            current = (
                await session.execute(
                    select(
                        ScriptScenes.content_json, ScriptScenes.content_version
                    ).where(ScriptScenes.id == sid)
                )
            ).first()
            if current is None:
                raise ValueError(f"scene {scene_id} not found")

            current_version = current.content_version
            elements = current.content_json or []
            if expected_version != current_version:
                raise VersionConflict(current_version, elements)

            # OpError propagates (NOT caught) — router maps to 422.
            new_elements, inverse_ops = apply_ops(elements, ops)
            new_version = current_version + 1

            result = await session.execute(
                update(ScriptScenes)
                .where(ScriptScenes.id == sid)
                .where(ScriptScenes.content_version == expected_version)
                .values(
                    content_json=new_elements,
                    content=extract_text(new_elements),
                    content_version=ScriptScenes.content_version + 1,
                )
            )
            if result.rowcount == 0:
                # A concurrent writer won between our SELECT and UPDATE.
                fresh = (
                    await session.execute(
                        select(
                            ScriptScenes.content_json,
                            ScriptScenes.content_version,
                        ).where(ScriptScenes.id == sid)
                    )
                ).first()
                raise VersionConflict(
                    fresh.content_version if fresh else current_version,
                    (fresh.content_json if fresh else elements) or [],
                )

            await session.execute(
                insert(ScriptOps).values(
                    scene_id=sid,
                    op_seq=new_version,
                    op_json={"ops": ops, "inverse": inverse_ops},
                    actor=actor,
                )
            )
        return {"content_version": new_version, "elements": new_elements}

    # ------------------------------------------------------------------ #
    # Reorder — sparse insertion, renumber on gap exhaustion.
    # ------------------------------------------------------------------ #

    async def move_scene(
        self,
        scene_id: str,
        *,
        chapter_id: Any = UNSET,
        before_scene_id: Optional[str] = None,
        after_scene_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reorder (and optionally reparent) a scene.

        ``chapter_id=UNSET`` keeps the current chapter; any other value (incl.
        None) reparents. The scene is bisected between the neighbours named by
        ``before_scene_id`` / ``after_scene_id`` (or appended to the tail when
        neither is given). When the neighbour gap is exhausted, the whole target
        chapter group is renumbered to a fresh STEP ladder."""
        sid = _bigint(scene_id)
        try:
            async with write_scope() as session:
                scene = (
                    (
                        await session.execute(
                            select(ScriptScenes).where(ScriptScenes.id == sid).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if scene is None:
                    raise ValueError(f"scene {scene_id} not found")

                script_id = scene.script_id
                target_chapter = (
                    scene.chapter_id if chapter_id is UNSET else _bigint(chapter_id)
                )

                sib_stmt = (
                    select(ScriptScenes.id, ScriptScenes.sort_order)
                    .where(ScriptScenes.script_id == script_id)
                    .where(ScriptScenes.id != sid)
                )
                if target_chapter is None:
                    sib_stmt = sib_stmt.where(ScriptScenes.chapter_id.is_(None))
                else:
                    sib_stmt = sib_stmt.where(ScriptScenes.chapter_id == target_chapter)
                sib_stmt = sib_stmt.order_by(ScriptScenes.sort_order.asc())
                siblings = [
                    (r[0], r[1]) for r in (await session.execute(sib_stmt)).all()
                ]

                lower, upper = self._resolve_bounds(
                    siblings, before_scene_id, after_scene_id
                )
                new_order = self._sparse_between(lower, upper)
                if new_order is None:
                    await self._renumber_group(
                        session,
                        siblings,
                        sid,
                        target_chapter,
                        before_scene_id,
                        after_scene_id,
                    )
                else:
                    await session.execute(
                        update(ScriptScenes)
                        .where(ScriptScenes.id == sid)
                        .values(chapter_id=target_chapter, sort_order=new_order)
                    )

                moved = (
                    (
                        await session.execute(
                            select(ScriptScenes).where(ScriptScenes.id == sid).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                out = _row(moved) if moved else {}
            logger.info(f"Moved scene {scene_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to move scene {scene_id}: {e}")
            raise

    @staticmethod
    def _resolve_bounds(
        siblings: List[tuple],
        before_scene_id: Optional[str],
        after_scene_id: Optional[str],
    ) -> tuple:
        """Resolve (lower, upper) sort_order bounds the moved scene lands
        between. ``before`` places just above that scene; ``after`` just below.
        An unknown/absent anchor falls back to appending at the tail."""
        ids = [s[0] for s in siblings]
        orders = [s[1] for s in siblings]
        if before_scene_id is not None:
            bid = _bigint(before_scene_id)
            if bid in ids:
                i = ids.index(bid)
                return (orders[i - 1] if i > 0 else None), orders[i]
        if after_scene_id is not None:
            aid = _bigint(after_scene_id)
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
        target_chapter: Optional[int],
        before_scene_id: Optional[str],
        after_scene_id: Optional[str],
    ) -> None:
        """Rewrite every scene in the target group to a fresh STEP ladder, with
        the moved scene spliced in at the requested slot (one UPDATE per row)."""
        ids = [s[0] for s in siblings]
        insert_idx = len(ids)  # default: append at tail
        if before_scene_id is not None and _bigint(before_scene_id) in ids:
            insert_idx = ids.index(_bigint(before_scene_id))
        elif after_scene_id is not None and _bigint(after_scene_id) in ids:
            insert_idx = ids.index(_bigint(after_scene_id)) + 1
        ordered = ids[:insert_idx] + [sid] + ids[insert_idx:]
        for i, eid in enumerate(ordered):
            values: Dict[str, Any] = {"sort_order": (i + 1) * STEP}
            if eid == sid:
                values["chapter_id"] = target_chapter
            await session.execute(
                update(ScriptScenes).where(ScriptScenes.id == eid).values(**values)
            )


def get_script_scene_repository() -> "ScriptSceneRepository":
    """Return the ScriptSceneRepository (ORM-only)."""
    return ScriptSceneRepository()
