"""Data access for generated_media (Tier-1). Keyset list by (created_at, id) DESC."""

from __future__ import annotations

import base64
import json
from typing import Optional

from loguru import logger

from app.db import engine as db_engine
from app.services.library.media_storage import ObjectStore, resolve_media_source

_COLS = (
    "id, scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
    "origin_kind, origin_run_id, agent_id, canvas_id, node_id, prompt, model, "
    "provider, params, cost_cents, parent_resource_id, derivation_kind, "
    "promoted_resource_id, conversation_id, created_at"
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


# Columns for the project-renders join, prefixed so postgres doesn't choke
# on ambiguous names against script_shots/script_scenes/script_projects
# (all of which also have an `id`). Row dict keys come back as the bare
# column name (postgres ignores the qualifier for output naming), so
# `_normalize` works unchanged.
_PROJECT_COLS = ", ".join(f"gm.{c.strip()}" for c in _COLS.split(","))

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
        params: dict = {"project_id": int(project_id), "limit": limit + 1}
        kinds_sql = ", ".join(f"'{k}'" for k in _SHOT_ORIGIN_KINDS)
        where = [
            "sp.project_id = :project_id",
            "sp.status != 'deleted'",
            f"gm.origin_kind IN ({kinds_sql})",
        ]
        if episode_id is not None:
            where.append("sp.episode_id = :episode_id")
            params["episode_id"] = int(episode_id)
        decoded = _decode_cursor(cursor)
        if decoded:
            params["c_ts"], params["c_id"] = decoded
            where.append("(gm.created_at, gm.id) < (CAST(:c_ts AS timestamptz), :c_id)")
        rows = (
            await db_engine.fetch_all(
                f"SELECT {_PROJECT_COLS} FROM public.generated_media gm "
                "JOIN public.script_shots ss ON gm.node_id = CAST(ss.id AS TEXT) "
                "JOIN public.script_scenes sc ON sc.id = ss.scene_id "
                "JOIN public.script_projects sp ON sp.id = sc.script_id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY gm.created_at DESC, gm.id DESC LIMIT :limit",
                params,
            )
            or []
        )
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
    ) -> dict:
        limit = max(1, min(int(limit), 100))
        params: dict = {"scope_id": scope_id, "limit": limit + 1}
        where = ["scope_id = :scope_id"]
        if kind:
            where.append("media_kind = :kind")
            params["kind"] = kind
        # CC5 asset backlink: generations dispatched from an entity branch
        # carry entity_kind/entity_id in params (stamped by the frontend at
        # dispatch); the library asset strips filter on them (mig 360 index).
        if entity_kind:
            where.append("params->>'entity_kind' = :entity_kind")
            params["entity_kind"] = entity_kind
        if entity_id:
            where.append("params->>'entity_id' = :entity_id")
            params["entity_id"] = entity_id
        decoded = _decode_cursor(cursor)
        if decoded:
            params["c_ts"], params["c_id"] = decoded
            where.append("(created_at, id) < (CAST(:c_ts AS timestamptz), :c_id)")
        rows = (
            await db_engine.fetch_all(
                f"SELECT {_COLS} FROM public.generated_media "
                f"WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT :limit",
                params,
            )
            or []
        )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def get(self, gen_id: int, scope_id: int) -> Optional[dict]:
        return _normalize(
            await db_engine.fetch_one(
                f"SELECT {_COLS} FROM public.generated_media "
                "WHERE id = :id AND scope_id = :scope_id",
                {"id": gen_id, "scope_id": scope_id},
            )
        )

    async def get_by_id(self, gen_id: int) -> Optional[dict]:
        """Fetch a row by id without a scope filter (for public-serve endpoints)."""
        return _normalize(
            await db_engine.fetch_one(
                f"SELECT {_COLS} FROM public.generated_media WHERE id = :id",
                {"id": gen_id},
            )
        )

    async def delete(self, gen_id: int, scope_id: int) -> bool:
        # Capture the location BEFORE deleting so we can clean up an orphaned
        # object-store object afterwards (filesystem cleanup is pre-existing
        # behavior — left as-is; each fs row has a unique uuid path anyway).
        row = await db_engine.fetch_one(
            "SELECT file_path FROM public.generated_media "
            "WHERE id = :id AND scope_id = :scope_id",
            {"id": gen_id, "scope_id": scope_id},
        )
        n = await db_engine.execute(
            "DELETE FROM public.generated_media WHERE id = :id AND scope_id = :scope_id",
            {"id": gen_id, "scope_id": scope_id},
        )
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
        remaining = await db_engine.fetch_val(
            "SELECT COUNT(*) FROM public.generated_media WHERE file_path = :fp",
            {"fp": file_path},
        )
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
        return _normalize(
            await db_engine.execute_returning_one(
                f"UPDATE public.generated_media SET promoted_resource_id = :rid "
                f"WHERE id = :id RETURNING {_COLS}",
                {"id": gen_id, "rid": resource_id},
            )
        )
