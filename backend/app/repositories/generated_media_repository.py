"""Data access for generated_media (Tier-1). Keyset list by (created_at, id) DESC.

ORM-model style (read_scope/write_scope + select on ``GeneratedMedia``),
converged from the raw db_engine call style. Return dict shapes are
byte-identical (native values; ``_normalize`` stringifies the snowflake /
uuid columns exactly as before).
"""

from __future__ import annotations

import base64
import datetime
import json
from typing import Optional

from loguru import logger
from sqlalchemy import Text as SAText
from sqlalchemy import cast
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, tuple_
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import (
    GeneratedMedia,
    ScriptProjects,
    ScriptScenes,
    ScriptShots,
)
from app.services.library.media_storage import ObjectStore, resolve_media_source

# The projection every read returns (matches the legacy _COLS order; the
# object-store-only content_sha256 stays internal, exactly as before).
_GM_COLS = (
    GeneratedMedia.id,
    GeneratedMedia.scope_id,
    GeneratedMedia.creator_id,
    GeneratedMedia.media_kind,
    GeneratedMedia.mime,
    GeneratedMedia.file_path,
    GeneratedMedia.file_size_bytes,
    GeneratedMedia.origin_kind,
    GeneratedMedia.origin_run_id,
    GeneratedMedia.agent_id,
    GeneratedMedia.canvas_id,
    GeneratedMedia.node_id,
    GeneratedMedia.prompt,
    GeneratedMedia.model,
    GeneratedMedia.provider,
    GeneratedMedia.params,
    GeneratedMedia.cost_cents,
    GeneratedMedia.parent_resource_id,
    GeneratedMedia.derivation_kind,
    GeneratedMedia.promoted_resource_id,
    GeneratedMedia.conversation_id,
    GeneratedMedia.created_at,
)

# Snowflake BIGINT columns: must be str() before reaching the frontend to avoid
# JS precision loss (same guard used throughout resources_repository.py).
_BIGINT_COLS = (
    "id",
    "scope_id",
    "canvas_id",
    "parent_resource_id",
    "promoted_resource_id",
    "conversation_id",
)
_UUID_COLS = ("creator_id", "agent_id")


def _normalize(row: Optional[dict]) -> Optional[dict]:
    """Stringify bigint and UUID fields so JSON serialisation is lossless."""
    if not row:
        return row
    out = dict(row)
    for c in _BIGINT_COLS:
        if out.get(c) is not None:
            out[c] = str(out[c])
    for c in _UUID_COLS:
        if out.get(c) is not None:
            out[c] = str(out[c])
    return out


def _encode_cursor(created_at: str, gen_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at, gen_id]).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[str, int]]:
    if not cursor:
        return None
    try:
        ts, gid = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return str(ts), int(gid)
    except Exception:  # noqa: BLE001
        return None


def _cursor_ts(ts: str) -> datetime.datetime:
    """Cursor timestamp string → datetime for the asyncpg-strict bind.

    The cursor carries ``str(created_at)`` — ``fromisoformat`` accepts both
    the space-separated ``str(datetime)`` form and the T-separated ISO form.
    """
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


# origin_kind values that represent a shot's rendered output (image or
# video) — the only generated_media rows addressable from a project via
# node_id = str(shot_id).
_SHOT_ORIGIN_KINDS = ("shot_generate", "shot_video")


class GeneratedMediaRepository:
    async def list_for_project(
        self,
        project_id: str | int,
        *,
        episode_id: Optional[str | int] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """Renders for a project (optionally narrowed to one episode).

        generated_media has no direct project/episode column — the join
        chain is generated_media.node_id (= str(shot_id) for shot-origin
        rows) -> script_shots.scene_id -> script_scenes.script_id ->
        script_projects.project_id/episode_id. Same cursor-pagination shape
        as ``list_for_scope`` (keyset on (created_at, id) DESC, exclusive
        upper bound via a peek-ahead row).
        """
        limit = max(1, min(int(limit), 100))
        stmt = (
            select(*_GM_COLS)
            .select_from(GeneratedMedia)
            .join(ScriptShots, GeneratedMedia.node_id == cast(ScriptShots.id, SAText))
            .join(ScriptScenes, ScriptScenes.id == ScriptShots.scene_id)
            .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
            .where(
                ScriptProjects.project_id == int(project_id),
                ScriptProjects.status != "deleted",
                GeneratedMedia.origin_kind.in_(_SHOT_ORIGIN_KINDS),
            )
        )
        if episode_id is not None:
            stmt = stmt.where(ScriptProjects.episode_id == int(episode_id))
        decoded = _decode_cursor(cursor)
        if decoded:
            c_ts, c_id = decoded
            stmt = stmt.where(
                tuple_(GeneratedMedia.created_at, GeneratedMedia.id)
                < tuple_(_cursor_ts(c_ts), c_id)
            )
        stmt = stmt.order_by(
            GeneratedMedia.created_at.desc(), GeneratedMedia.id.desc()
        ).limit(limit + 1)
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def list_for_scope(
        self,
        scope_id: int,
        *,
        kind: Optional[str] = None,
        entity_kind: Optional[str] = None,
        entity_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 30,
        cover_source: Optional[str] = None,
    ) -> dict:
        limit = max(1, min(int(limit), 100))
        stmt = select(*_GM_COLS).where(GeneratedMedia.scope_id == scope_id)
        # Cover Studio rounds: stamped by /distribution/covers/generate as
        # params.cover.source_video_id — the studio's history per video.
        if cover_source:
            stmt = stmt.where(
                GeneratedMedia.params["cover"]["source_video_id"].astext == cover_source
            )
        if kind:
            stmt = stmt.where(GeneratedMedia.media_kind == kind)
        # CC5 asset backlink: generations dispatched from an entity branch
        # carry entity_kind/entity_id in params (stamped by the frontend at
        # dispatch); the library asset strips filter on them (mig 360 index).
        if entity_kind:
            stmt = stmt.where(
                GeneratedMedia.params["entity_kind"].astext == entity_kind
            )
        if entity_id:
            stmt = stmt.where(GeneratedMedia.params["entity_id"].astext == entity_id)
        decoded = _decode_cursor(cursor)
        if decoded:
            c_ts, c_id = decoded
            stmt = stmt.where(
                tuple_(GeneratedMedia.created_at, GeneratedMedia.id)
                < tuple_(_cursor_ts(c_ts), c_id)
            )
        stmt = stmt.order_by(
            GeneratedMedia.created_at.desc(), GeneratedMedia.id.desc()
        ).limit(limit + 1)
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def get(self, gen_id: int, scope_id: int) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_GM_COLS).where(
                            GeneratedMedia.id == gen_id,
                            GeneratedMedia.scope_id == scope_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def get_by_id(self, gen_id: int) -> Optional[dict]:
        """Fetch a row by id without a scope filter (for public-serve endpoints)."""
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_GM_COLS).where(GeneratedMedia.id == gen_id)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def delete(self, gen_id: int, scope_id: int) -> bool:
        # Capture the location BEFORE deleting so we can clean up an orphaned
        # object-store object afterwards (filesystem cleanup is pre-existing
        # behavior — left as-is; each fs row has a unique uuid path anyway).
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(GeneratedMedia.file_path).where(
                            GeneratedMedia.id == gen_id,
                            GeneratedMedia.scope_id == scope_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        async with write_scope() as session:
            result = await session.execute(
                sa_delete(GeneratedMedia).where(
                    GeneratedMedia.id == gen_id,
                    GeneratedMedia.scope_id == scope_id,
                )
            )
            n = result.rowcount
        if n and row:
            await self._maybe_remove_object(row.get("file_path") or "")
        return bool(n)

    async def _maybe_remove_object(self, file_path: str) -> None:
        """Remove the backing object IFF no other row still references it.

        Content-addressed keys are SHARED by dedup — two generated_media rows
        with identical bytes point at the same object. Only remove when the
        refcount hits zero (no sibling row has the same file_path), else we'd
        delete a live object out from under another row. A removal failure is
        swallowed (a leaked object is acceptable; failing the delete is not).
        """
        if not file_path:
            return
        loc = resolve_media_source(file_path)
        if not loc.is_object_store:
            return
        async with read_scope() as session:
            remaining = (
                await session.execute(
                    select(func.count()).where(GeneratedMedia.file_path == file_path)
                )
            ).scalar()
        if remaining and int(remaining) > 0:
            return  # still referenced — keep the object
        try:
            await ObjectStore(loc.bucket).remove(loc.key)
        except Exception as exc:
            logger.warning(
                f"[generated_media] object cleanup failed (leak, non-fatal): "
                f"key={loc.key} error={exc!r}"
            )

    async def mark_promoted(self, gen_id: int, resource_id: int) -> Optional[dict]:
        """Set promoted_resource_id (Tier-1 → Tier-2 link). Returns the row."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(GeneratedMedia)
                        .where(GeneratedMedia.id == gen_id)
                        .values(promoted_resource_id=resource_id)
                        .returning(*_GM_COLS)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None
