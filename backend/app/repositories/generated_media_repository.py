"""Data access for generated_media (Tier-1). Keyset list by (created_at, id) DESC."""

from __future__ import annotations

import base64
import json
from typing import Optional

from app.db import engine as db_engine

_COLS = (
    "id, scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
    "origin_kind, origin_run_id, agent_id, canvas_id, node_id, prompt, model, "
    "provider, params, cost_cents, parent_resource_id, derivation_kind, "
    "promoted_resource_id, channel_id, created_at"
)

# Snowflake BIGINT columns: must be str() before reaching the frontend to avoid
# JS precision loss (same guard used throughout resources_repository.py).
_BIGINT_COLS = (
    "id",
    "scope_id",
    "canvas_id",
    "parent_resource_id",
    "promoted_resource_id",
    "channel_id",
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


class GeneratedMediaRepository:
    async def list_for_scope(
        self,
        scope_id: int,
        *,
        kind: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 30,
    ) -> dict:
        limit = max(1, min(int(limit), 100))
        params: dict = {"scope_id": scope_id, "limit": limit + 1}
        where = ["scope_id = :scope_id"]
        if kind:
            where.append("media_kind = :kind")
            params["kind"] = kind
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
        n = await db_engine.execute(
            "DELETE FROM public.generated_media WHERE id = :id AND scope_id = :scope_id",
            {"id": gen_id, "scope_id": scope_id},
        )
        return bool(n)

    async def mark_promoted(self, gen_id: int, resource_id: int) -> Optional[dict]:
        """Set promoted_resource_id (Tier-1 → Tier-2 link). Returns the row."""
        return _normalize(
            await db_engine.execute_returning_one(
                f"UPDATE public.generated_media SET promoted_resource_id = :rid "
                f"WHERE id = :id RETURNING {_COLS}",
                {"id": gen_id, "rid": resource_id},
            )
        )
