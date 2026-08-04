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

from app.db import engine as db_engine
from app.db.session import read_scope, write_scope
from app.models import ScriptOps, ScriptProjects, ScriptScenes
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.services.script.scene_numbering import (
    compute_locked_insert_number,
    derive_scene_number,
    effective_scene_number,
    parse_scene_number,
    scene_number_sort_key,
)
from app.services.script.scene_ops import apply_ops, extract_text

# Sparse-ordering step. New scenes land at MAX+STEP; moves bisect neighbours,
# renumbering the whole group to a fresh ladder when the gap is exhausted.
STEP = 1000

# Sentinel so ``move_scene(chapter_id=UNSET)`` distinguishes "keep the current
# chapter" from "reparent to None" (detach). ``None`` is a legal target.
UNSET: Any = object()

_SCENES_N2A: Dict[str, str] = _name_to_attr(ScriptScenes)
_SCENES_ATTRS = {p.key for p in ScriptScenes.__mapper__.column_attrs}
_OPS_N2A: Dict[str, str] = _name_to_attr(ScriptOps)

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


# The private ``_effective_number`` helper used to live here; A4 moved it to
# scene_numbering.effective_scene_number so the agent-tool read path
# (scoped_script_gateway) computes scene_no_in_episode by the SAME rule
# instead of keeping a second copy.


def _scene_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the write ``values()`` dict: keep only mapped columns (graceful
    no-op for unknown keys, REST parity) and bigint-coerce the id/FK fields."""
    known = {k: v for k, v in data.items() if k in _SCENES_ATTRS}
    for field in _SCENE_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    return known


# ------------------------------------------------------------------ #
# Project-level scene rows (PR-10a, spec G13) — raw SQL, not ORM. This is
# a thin cross-table projection (episode_id / location_text / content_json)
# feeding the pure derivation helper in ``project_entities.py``, not a scene
# CRUD read — follows the app.db.engine raw-SQL house idiom (see
# project_stages_repository.py) rather than this file's read_scope pattern.
# ------------------------------------------------------------------ #

_PROJECT_SCENE_ROWS_SQL = """
    SELECT sp.episode_id AS episode_id,
           sc.location_text AS location_text,
           sc.content_json AS content_json
    FROM public.script_scenes sc
    JOIN public.script_projects sp ON sp.id = sc.script_id
    WHERE sp.project_id = :project_id AND sp.status != 'deleted'
"""


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
        sort_order (the canonical read order for the editor / scene list).

        Every scene dict carries ``scene_no_in_episode`` (agent-layer spec
        §4.3's honest field name — never the ambiguous ``current_scene``):
        the locked ``scene_number`` when set, else the writing-phase number
        DERIVED from this canonical position (never persisted), else ``None``
        for a scene created after lock but not yet positioned (deriving a
        guess there could collide with a real locked number)."""
        try:
            sid = _bigint(script_id)
            async with read_scope() as session:
                locked_at = await session.scalar(
                    select(ScriptProjects.numbering_locked_at).where(
                        ScriptProjects.id == sid
                    )
                )
                is_locked = locked_at is not None
                result = await session.execute(
                    select(ScriptScenes)
                    .where(ScriptScenes.script_id == sid)
                    .order_by(
                        ScriptScenes.chapter_id.asc().nulls_last(),
                        ScriptScenes.sort_order.asc(),
                    )
                )
                rows = result.scalars().all()
            out: List[Dict[str, Any]] = []
            for index, r in enumerate(rows):
                d = _row(r)
                d["scene_no_in_episode"] = effective_scene_number(
                    d["scene_number"], index, is_locked
                )
                out.append(d)
            return out
        except Exception as e:
            logger.error(f"Failed to list scenes for script {script_id}: {e}")
            return []

    async def get_by_id(self, scene_id: str) -> Optional[Dict[str, Any]]:
        """A single scene by id, or None. Carries ``scene_no_in_episode`` —
        see ``list_by_script`` for the derivation rule."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptScenes)
                    .where(ScriptScenes.id == _bigint(scene_id))
                    .limit(1)
                )
                row = result.scalars().first()
                if row is None:
                    return None
                out = _row(row)
                if out["scene_number"] is not None:
                    out["scene_no_in_episode"] = out["scene_number"]
                    return out

                locked_at = await session.scalar(
                    select(ScriptProjects.numbering_locked_at).where(
                        ScriptProjects.id == row.script_id
                    )
                )
                is_locked = locked_at is not None
                if is_locked:
                    out["scene_no_in_episode"] = None
                    return out

                ids = (
                    (
                        await session.execute(
                            select(ScriptScenes.id)
                            .where(ScriptScenes.script_id == row.script_id)
                            .order_by(
                                ScriptScenes.chapter_id.asc().nulls_last(),
                                ScriptScenes.sort_order.asc(),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                out["scene_no_in_episode"] = derive_scene_number(
                    list(ids).index(row.id)
                )
                return out
        except Exception as e:
            logger.error(f"Failed to get scene {scene_id}: {e}")
            return None

    async def list_scene_rows_for_project(
        self, project_id: str
    ) -> List[Dict[str, Any]]:
        """Scene rows (episode_id / location_text / content_json) for every
        non-deleted script in a project — the derivation source for the
        project-level Characters/Locations ASSETS view (spec G13). A primary
        read: a query failure propagates (not swallowed to []), matching the
        house convention for aggregate reads that feed a 500 on failure."""
        rows = await db_engine.fetch_all(
            _PROJECT_SCENE_ROWS_SQL, {"project_id": _bigint(project_id)}
        )
        return rows or []

    async def list_ops_by_scene(self, scene_id: str) -> List[Dict[str, Any]]:
        """The immutable op ledger for a scene, ordered by ``op_seq`` ASC (replay
        order). Each row carries ``op_json`` = ``{"ops": [...], "inverse": [...]}``
        native (JSONB stays a dict). Read by the version service to replay a scene
        to a commit watermark and to build inverse batches for rollback."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptOps)
                    .where(ScriptOps.scene_id == _bigint(scene_id))
                    .order_by(ScriptOps.op_seq.asc())
                )
                return [
                    _parity(_orm_obj_to_dict(r, _OPS_N2A))
                    for r in result.scalars().all()
                ]
        except Exception as e:
            logger.error(f"Failed to list ops for scene {scene_id}: {e}")
            return []

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

    async def create_with_content(
        self,
        data: Dict[str, Any],
        elements: List[dict],
        actor: str,
    ) -> Dict[str, Any]:
        """Create a scene AND its genesis content in ONE transaction.

        Unlike ``create`` (which lands an empty scene the editor then fills via
        ``apply_element_ops``), this seeds ``content_json`` = ``elements``,
        derives ``content`` via ``extract_text``, stamps ``content_version`` = 1,
        and appends the genesis op ledger row (``op_seq`` = 1, ``op_json`` = a
        batch of ``insert`` ops with their ``delete`` inverses) to ``script_ops``
        in the SAME committing transaction. Used by AI convert-to-scenes so a
        fully-formed scene and a replayable ledger land atomically — a partial
        write can never leave a scene without its genesis op.

        ``elements`` are trusted, id-bearing element dicts (the caller generates
        the ``el_`` ids and validates types). ``sort_order`` auto-assigns to
        ``MAX+STEP`` within the (script_id, chapter_id) group, exactly as
        ``create`` does, so successive scenes from one chapter keep order."""
        try:
            values = _scene_write_values(data)
            values["content_json"] = elements
            values["content"] = extract_text(elements)
            values["content_version"] = 1
            inserts = [
                {
                    "op": "insert",
                    "element_id": el["id"],
                    "payload": {k: v for k, v in el.items() if k != "id"},
                }
                for el in elements
            ]
            # Inverse undoes last-applied-first (scene_ops convention): delete
            # the elements in reverse insertion order back to an empty scene.
            inverse = [
                {"op": "delete", "element_id": el["id"]} for el in reversed(elements)
            ]
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
                if row is None:
                    raise RuntimeError("Insert into script_scenes returned no data")
                await session.execute(
                    insert(ScriptOps).values(
                        scene_id=row.id,
                        op_seq=1,
                        op_json={"ops": inserts, "inverse": inverse},
                        actor=actor,
                    )
                )
                out = _row(row)
            logger.info(
                f"Created scene with content in script {data.get('script_id')} "
                f"chapter {data.get('chapter_id')} ({len(elements)} elements)"
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create scene with content: {e}")
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

    async def delete(self, scene_id: str) -> Dict[str, Any]:
        """Delete a scene — hard DELETE while the script's numbering is
        unlocked (writing phase; ops cascade via FK ON DELETE CASCADE, same
        as before mig 403). Once the script is LOCKED, this instead OMITS
        the scene in place (agent-layer spec §4.2's "delete after lock"):
        stamps ``omitted_at`` and keeps the row + its ``scene_number`` —
        the printed-script "3 OMITTED" convention — so the number stays
        reserved forever and no downstream shot-id reference rots.

        Returns ``{"deleted": bool, "omitted": bool, "scene": dict | None}``.
        A missing scene is a quiet no-op (``deleted=False, omitted=False``),
        matching the previous method's idempotent-DELETE behavior.
        """
        sid = _bigint(scene_id)
        try:
            async with write_scope() as session:
                scene = (
                    await session.execute(
                        select(ScriptScenes.script_id).where(ScriptScenes.id == sid)
                    )
                ).first()
                if scene is None:
                    return {"deleted": False, "omitted": False, "scene": None}

                locked_at = await session.scalar(
                    select(ScriptProjects.numbering_locked_at).where(
                        ScriptProjects.id == scene.script_id
                    )
                )
                is_locked = locked_at is not None

                if is_locked:
                    result = await session.execute(
                        update(ScriptScenes)
                        .where(ScriptScenes.id == sid)
                        .values(omitted_at=func.now())
                        .returning(ScriptScenes)
                    )
                    row = result.scalars().first()
                    out = _row(row) if row else None
                    logger.info(f"Omitted scene {scene_id} (numbering locked)")
                    return {"deleted": False, "omitted": True, "scene": out}

                await session.execute(
                    sa_delete(ScriptScenes).where(ScriptScenes.id == sid)
                )
            logger.info(f"Deleted scene {scene_id}")
            return {"deleted": True, "omitted": False, "scene": None}
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

    # ------------------------------------------------------------------ #
    # Scene numbering — lock freeze + post-lock insert (agent-layer §4).
    # ------------------------------------------------------------------ #

    async def lock_numbering(self, script_id: str) -> Dict[str, Any]:
        """Freeze scene numbering for a script (spec §4.2 "锁定拍摄稿").

        Derives every scene's number from its CURRENT canonical order
        (chapter_id NULLS LAST, then sort_order ASC — same order
        ``list_by_script`` reads) and writes it into ``scene_number``, then
        stamps ``script_projects.numbering_locked_at`` — all in one
        transaction, so a crash mid-lock can never leave some scenes numbered
        and the project flag unset (or vice versa).

        Idempotent: locking an ALREADY-locked script is a no-op (returns the
        current scenes unchanged) — re-deriving would defeat the entire
        point of a freeze, silently reshuffling numbers a second `lock` call
        was never meant to touch.
        """
        sid = _bigint(script_id)
        async with write_scope() as session:
            project = (
                await session.execute(
                    select(ScriptProjects.numbering_locked_at).where(
                        ScriptProjects.id == sid
                    )
                )
            ).first()
            if project is None:
                raise ValueError(f"script {script_id} not found")

            if project.numbering_locked_at is not None:
                existing = await self.list_by_script(script_id)
                return {"already_locked": True, "scenes": existing}

            result = await session.execute(
                select(ScriptScenes)
                .where(ScriptScenes.script_id == sid)
                .order_by(
                    ScriptScenes.chapter_id.asc().nulls_last(),
                    ScriptScenes.sort_order.asc(),
                )
            )
            rows = result.scalars().all()
            updated: List[Dict[str, Any]] = []
            for index, row in enumerate(rows):
                number = derive_scene_number(index)
                await session.execute(
                    update(ScriptScenes)
                    .where(ScriptScenes.id == row.id)
                    .values(scene_number=number)
                )
                out = _row(row)
                out["scene_number"] = number
                out["scene_no_in_episode"] = number
                updated.append(out)

            await session.execute(
                update(ScriptProjects)
                .where(ScriptProjects.id == sid)
                .values(numbering_locked_at=func.now())
            )
        logger.info(
            f"Locked scene numbering for script {script_id} ({len(updated)} scenes)"
        )
        return {"already_locked": False, "scenes": updated}

    @staticmethod
    async def _locked_neighbour_numbers(script_id: Any, scene_id: Any) -> tuple:
        """Fresh script-wide siblings (canonical order) plus ``scene_id``'s
        OWN current immediate-neighbour numbers and the full existing-number
        set. Shared by ``create_after_lock``'s initial placement AND its
        same-base-corner reposition (below) so both read the CURRENT
        physical position rather than a stale one computed before a second
        ``move_scene()`` call."""
        async with read_scope() as session:
            sib_stmt = (
                select(
                    ScriptScenes.id, ScriptScenes.sort_order, ScriptScenes.scene_number
                )
                .where(ScriptScenes.script_id == script_id)
                .order_by(
                    ScriptScenes.chapter_id.asc().nulls_last(),
                    ScriptScenes.sort_order.asc(),
                )
            )
            siblings = [
                (r[0], r[1], r[2]) for r in (await session.execute(sib_stmt)).all()
            ]
        existing_numbers = [s[2] for s in siblings if s[2] is not None]
        ids = [s[0] for s in siblings]
        idx = ids.index(_bigint(scene_id))
        prev_number = siblings[idx - 1][2] if idx > 0 else None
        next_number = siblings[idx + 1][2] if idx + 1 < len(siblings) else None
        return prev_number, next_number, existing_numbers, siblings

    async def create_after_lock(
        self,
        data: Dict[str, Any],
        *,
        before_scene_id: Optional[str] = None,
        after_scene_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create + number a NEW scene under an ALREADY-LOCKED script (spec
        §4.2 "锁定后插入").

        Composed from the existing, separately-tested primitives —
        ``create()`` (lands the row; sort_order = chapter-group tail) then
        ``move_scene()`` (bisects it to the requested slot when anchors are
        given, reusing its exact gap-exhaustion renumber) — rather than
        duplicating their sort_order logic here. Neither ``create()`` nor
        ``move_scene()`` is modified by this addition, so their existing
        capture-style tests (which assert an EXACT statement sequence) are
        unaffected. This method's own job is just: AFTER placement, re-read
        the script-wide siblings in canonical order, find the new scene's
        OWN final position among them (its immediate neighbours there, not
        the before/after ids as originally given — this also makes the
        anchor-less tail-append case work with the same lookup), then
        compute + write ``scene_number`` via ``compute_locked_insert_number``
        — NEVER touching any existing scene's own number.

        Omitting both anchors is a tail append (the common "just keep
        writing past the locked draft" case): the new scene continues the
        plain integer sequence, no letter needed.

        SAME-BASE CORNER (fixed, was previously a real bug — see git blame):
        when the requested slot falls BETWEEN two neighbours that share an
        integer base (e.g. between "3" and "3A", or between "3A" and "3B"),
        no letter suffix can sort there — ``compute_locked_insert_number``
        always assigns "the next unused suffix across the WHOLE base",
        which necessarily sorts AFTER every existing member of that base's
        run, including ``next_number``. Left at the physically-requested
        slot, the row would display out of order relative to its own label
        (e.g. ``list_by_script`` rendering "3, 3B, 3A, 4"). Detected via
        ``base(prev_number) == base(next_number)`` and fixed by a SECOND
        ``move_scene()`` call that repositions the row to sit immediately
        after the base's current last member instead — ``new_number`` was
        already computed as exactly the right label for THAT position, so
        only the physical placement needs to move to agree with it.

        Raises ``ValueError`` if the script is not locked yet — pre-lock
        creation goes through plain ``create()``, which never touches
        ``scene_number`` (writing-phase numbers stay derived, not stored).
        """
        script_id = _scene_write_values(data)["script_id"]
        async with read_scope() as session:
            locked = await session.scalar(
                select(ScriptProjects.numbering_locked_at).where(
                    ScriptProjects.id == script_id
                )
            )
        if locked is None:
            raise ValueError(
                f"script {script_id} numbering is not locked; use create()"
            )

        created = await self.create(data)
        scene_id = created["id"]

        if before_scene_id is not None or after_scene_id is not None:
            created = await self.move_scene(
                scene_id,
                chapter_id=created.get("chapter_id"),
                before_scene_id=before_scene_id,
                after_scene_id=after_scene_id,
            )

        prev_number, next_number, existing_numbers, siblings = (
            await self._locked_neighbour_numbers(script_id, scene_id)
        )

        # SAME-BASE CORNER: reposition BEFORE assigning, so the row that
        # ends up under scene_id's id is the one actually holding the final
        # slot (the number, computed below, already matches that slot).
        if prev_number is not None and next_number is not None:
            base_prev, _ = parse_scene_number(prev_number)
            base_next, _ = parse_scene_number(next_number)
            if base_prev == base_next:
                same_base = [
                    (sid, num)
                    for sid, _order, num in siblings
                    if sid != _bigint(scene_id)
                    and num is not None
                    and parse_scene_number(num)[0] == base_prev
                ]
                last_member_id, _ = max(
                    same_base, key=lambda pair: scene_number_sort_key(pair[1])
                )
                created = await self.move_scene(
                    scene_id,
                    chapter_id=created.get("chapter_id"),
                    after_scene_id=str(last_member_id),
                )
                prev_number, next_number, existing_numbers, _siblings = (
                    await self._locked_neighbour_numbers(script_id, scene_id)
                )

        new_number = compute_locked_insert_number(
            prev_number, next_number, existing_numbers
        )

        async with write_scope() as session:
            result = await session.execute(
                update(ScriptScenes)
                .where(ScriptScenes.id == _bigint(scene_id))
                .values(scene_number=new_number)
                .returning(ScriptScenes)
            )
            row = result.scalars().first()
        out = _row(row) if row else created
        out["scene_no_in_episode"] = new_number
        logger.info(
            f"Assigned scene_number {new_number} to scene {scene_id} (post-lock insert)"
        )
        return out


def get_script_scene_repository() -> "ScriptSceneRepository":
    """Return the ScriptSceneRepository (ORM-only)."""
    return ScriptSceneRepository()
