# backend/app/repositories/user_settings_repository.py

"""用户设置数据访问层

处理用户个人设置的 CRUD 操作。

Backed by EITHER the SQLAlchemy 2.0 ORM session layer (Task 5.4, when
``USE_ORM_USER_SETTINGS`` is on AND the engine is configured) or the legacy
supabase-py async client. A single class keeps all 7 consumers' call sites
unchanged (``UserSettingsRepository()`` everywhere); each data-access method
branches internally on ``_use_orm()``.

THE #1 INVARIANT — MERGE-NOT-REPLACE
====================================
``settings_json`` is a SHARED column: General settings, ``ai_settings``,
``parse_mode`` and more all live under one JSON blob. A bare column REPLACE
wiped the AI provider config when a General setting was saved — real data loss
on 2026-06-02 (bug ``user_settings_json_clobber`` / PR #485; the keys had to be
re-entered by hand). Every settings_json write goes through the single
canonical merge path (``patch_settings_json`` → ``_atomic_merge_settings_json``)
which runs ``settings_json = COALESCE(existing,'{}'::jsonb) || CAST(:patch AS
jsonb)`` under ON CONFLICT — an atomic, race-free top-level merge that PRESERVES
every untouched key. The ORM swap only changes WHERE that statement runs (a
committing ``write_scope()`` session instead of ``db_engine.execute_returning_one``);
the ``||`` merge is preserved byte-for-byte.

VALUE-TYPE PARITY (REST → ORM)
==============================
The live baseline is PostgREST (JSON): it renders ``user_id`` / ``id`` (uuid)
as STRINGS and ``created_at`` / ``updated_at`` (timestamptz) as ISO STRINGS. The
type-sensitive consumer is ``user_settings_router`` — it feeds these into a
Pydantic ``UserSettingsResponse`` whose ``user_id`` / ``id`` / timestamps are
typed ``str``, and Pydantic v2 REJECTS a bare ``uuid.UUID`` / ``datetime`` for a
``str`` field (it does not coerce). So the ORM read dict coerces uuid→str and
datetime→ISO-str for exactly those columns (``_orm_row_to_rest_dict``).
``settings_json`` (jsonb) already deserializes to a Python ``dict`` via the ORM
— matching what PostgREST returned — so no ``json.loads`` is needed on the ORM
path. The other consumers only read ``settings_json`` (native dict is fine).
"""

from typing import Any, Dict, Optional

from loguru import logger

from app.core.cache import user_settings_cache
from app.core.config import settings as app_settings
from app.db.supabase_client import get_async_supabase_admin


def merge_settings_json(
    existing: Optional[Dict[str, Any]], incoming: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Shallow-merge ``incoming`` over ``existing``, preserving untouched keys.

    ``settings_json`` is a SHARED column: General settings, ``ai_settings``,
    ``parse_mode`` and more all live under the same JSON blob. A bare column
    replace wiped the AI provider config when a General setting was saved —
    real data loss on 2026-06-02 (the keys had to be re-entered by hand; no
    backup/PITR existed to recover them).

    This is the single canonical merge used by ``UserSettingsRepository.upsert``
    so the guarantee is structural: every writer goes through one place that
    merges top-level keys instead of replacing the column. Incoming keys win;
    keys absent from ``incoming`` (e.g. ``ai_settings`` during a General save)
    are preserved.
    """
    return {**(existing or {}), **(incoming or {})}


class UserSettingsRepository:
    """用户设置仓库类 (异步)"""

    def __init__(self):
        pass

    # ── Backend selection ───────────────────────────────────────────

    @staticmethod
    def _use_orm() -> bool:
        """True when reads + the settings_json merge should run on the
        SQLAlchemy ORM session layer: the flag is on AND the engine is
        configured. A half-configured deploy (flag on, DSN empty) falls back
        to the supabase-py path so it doesn't crash."""
        if not app_settings.USE_ORM_USER_SETTINGS:
            return False
        from app.db import engine as db_engine

        if db_engine.is_configured():
            return True
        logger.warning(
            "USE_ORM_USER_SETTINGS=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
        return False

    @staticmethod
    def _orm_row_to_rest_dict(obj: Any) -> Dict[str, Any]:
        """SELECT *-shaped dict for a ``UserSettings`` ORM row, coerced to the
        REST value types the type-sensitive consumer needs.

        uuid (``id`` / ``user_id``) → str and timestamptz (``created_at`` /
        ``updated_at``) → ISO str, matching PostgREST. ``settings_json`` (jsonb)
        is already a Python dict via the ORM — left as-is. ``download_path`` is
        already str. See the VALUE-TYPE PARITY note in the module docstring."""
        import datetime
        import uuid

        from app.models import UserSettings
        from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

        raw = _orm_obj_to_dict(obj, _name_to_attr(UserSettings))
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, uuid.UUID):
                out[key] = str(value)
            elif isinstance(value, datetime.datetime):
                out[key] = value.isoformat()
            else:
                out[key] = value
        return out

    # ── supabase-py helpers (legacy / fallback path) ────────────────

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table("user_settings")

    async def _load_user_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load the user_settings row, ORM (read_scope) or supabase-py (REST)."""
        if self._use_orm():
            return await self._orm_load_user_settings(user_id)
        try:
            table = await self._get_table()
            result = await table.select("*").eq("user_id", user_id).execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"获取用户设置失败: {e}")
            return None

    async def _orm_load_user_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        """SELECT * via the ORM read_scope() session. Returns the same dict
        shape (and value types) the supabase-py REST path returned."""
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import UserSettings

        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(UserSettings).where(UserSettings.user_id == user_id).limit(1)
                )
                row = result.scalars().first()
                return self._orm_row_to_rest_dict(row) if row else None
        except Exception as e:
            logger.error(f"获取用户设置失败 (orm): {e}")
            return None

    async def get_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return cached user settings (30s TTL) or load from DB on miss.

        Guards against ``user_id=None`` leaking in from Celery contexts
        where the original requester is unknown (e.g. retry of an orphan
        download row, system-initiated parses). Without the guard,
        PostgREST stringifies None to "None" and PG rejects with 22P02
        invalid uuid syntax — quietly turned into ERROR log spam without
        breaking the call. Returning None here is the same observable
        outcome (no settings found) without the noise.
        """
        if not user_id or str(user_id).lower() in ("none", "null"):
            return None
        return await user_settings_cache.get_or_load(
            user_id,
            lambda: self._load_user_settings(user_id),
        )

    async def upsert(
        self, user_id: str, settings: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        创建或更新用户设置

        ``settings_json`` is routed through ``patch_settings_json`` — the single
        merge path — so no caller can wholesale-replace the shared blob and
        clobber keys it did not intend to touch. Plain columns (download_path…)
        are upserted as-is, touching ONLY the named columns on conflict, so a
        ``download_path`` write never disturbs ``settings_json``.

        Args:
            user_id: 用户 ID
            settings: 设置数据

        Returns:
            更新后的设置数据
        """
        try:
            rest = dict(settings)
            settings_json = rest.pop("settings_json", None)

            result_row: Optional[Dict[str, Any]] = None

            # Plain columns (everything except settings_json) — upsert updates
            # only the named columns on conflict, leaving settings_json
            # untouched.
            if rest:
                if self._use_orm():
                    result_row = await self._orm_upsert_plain(user_id, rest)
                else:
                    data = {"user_id": user_id, **rest}
                    table = await self._get_table()
                    result = await table.upsert(data, on_conflict="user_id").execute()
                    if result.data and len(result.data) > 0:
                        result_row = result.data[0]

            # settings_json — atomic top-level merge (race-free).
            if settings_json is not None:
                merged = await self.patch_settings_json(user_id, settings_json)
                result_row = merged or result_row

            user_settings_cache.invalidate(user_id)
            if result_row is not None:
                logger.info(f"用户设置已保存: user_id={user_id}")
            return result_row
        except Exception as e:
            logger.error(f"保存用户设置失败: {e}")
            return None

    async def _orm_upsert_plain(
        self, user_id: str, plain: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """INSERT … ON CONFLICT (user_id) DO UPDATE SET <only the named plain
        columns> via the committing write_scope() session.

        Critically, the ON CONFLICT SET list contains ONLY the named plain
        columns (+ updated_at) — NOT settings_json — so an existing
        settings_json blob is left untouched (plain-vs-json isolation)."""
        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.session import write_scope
        from app.models import UserSettings

        values = {"user_id": user_id, **plain}
        update_set = {**plain, "updated_at": func.now()}
        stmt = (
            pg_insert(UserSettings)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[UserSettings.user_id],
                set_=update_set,
            )
            .returning(UserSettings)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
            return self._orm_row_to_rest_dict(obj) if obj else None

    async def patch_settings_json(
        self, user_id: str, partial: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Merge ``partial`` top-level keys into ``settings_json``, atomically.

        THE canonical way to write ``user_settings.settings_json``. Pass only
        the keys you own (``{"parse_mode": ...}``, ``{"ai_settings": {...}}``);
        every other key is preserved.

        Uses a single ``INSERT … ON CONFLICT DO UPDATE SET settings_json =
        existing || patch`` so the merge happens inside one statement under a
        row lock — concurrent saves serialize and both patches survive, with no
        read-modify-write window. The statement runs inside the committing ORM
        ``write_scope()`` when the ORM path is active, else on
        ``db_engine.execute_returning_one`` (the Core path). Falls back to the
        (non-atomic) PostgREST read-merge-write only when neither is available.
        """
        partial = partial or {}

        if self._use_orm():
            try:
                row = await self._orm_atomic_merge_settings_json(user_id, partial)
                user_settings_cache.invalidate(user_id)
                if row is not None:
                    logger.info(f"用户设置已保存 (orm atomic merge): user_id={user_id}")
                    return row
            except Exception as e:
                logger.error(
                    f"orm settings_json merge failed, falling back to RMW: "
                    f"user_id={user_id}, error={e}"
                )
            row = await self._rmw_merge_settings_json(user_id, partial)
            user_settings_cache.invalidate(user_id)
            return row

        from app.db import engine as db_engine

        if db_engine.is_configured():
            try:
                row = await self._atomic_merge_settings_json(user_id, partial)
                user_settings_cache.invalidate(user_id)
                if row is not None:
                    logger.info(f"用户设置已保存 (atomic merge): user_id={user_id}")
                    return row
            except Exception as e:
                # Atomic path failed (transient DB error, role issue). Fall back
                # to the legacy read-merge-write rather than dropping the save.
                logger.error(
                    f"atomic settings_json merge failed, falling back to RMW: "
                    f"user_id={user_id}, error={e}"
                )

        row = await self._rmw_merge_settings_json(user_id, partial)
        user_settings_cache.invalidate(user_id)
        return row

    # The merge SQL — identical across the ORM (write_scope) and Core
    # (db_engine) execution paths. ``CAST(:patch AS jsonb)`` (not
    # ``:patch::jsonb``) — SQLAlchemy's text() bind parser eats a colon from
    # ``::``. ``COALESCE(existing,'{}') || patch`` is the merge-not-replace
    # guarantee (the #485 P0); it runs against the locked, committed row inside
    # ON CONFLICT DO UPDATE so it is race-free.
    _MERGE_SQL = (
        "INSERT INTO public.user_settings (user_id, settings_json) "
        "VALUES (:uid, CAST(:patch AS jsonb)) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb) "
        "|| CAST(:patch AS jsonb), "
        "updated_at = NOW() "
        "RETURNING *"
    )

    async def _orm_atomic_merge_settings_json(
        self, user_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Single-statement jsonb merge inside the COMMITTING write_scope()
        session — the ORM successor to ``_atomic_merge_settings_json``.

        Runs the SAME ``settings_json = existing || patch`` ON CONFLICT
        statement; only the execution path changes (write_scope session vs
        ``db_engine.execute_returning_one``). The merge is byte-for-byte the
        same — preserving the merge-not-replace guarantee. write_scope()
        COMMITs (or joins an ambient unit_of_work that commits), so the write
        actually persists."""
        import json

        from sqlalchemy import text

        from app.db.session import write_scope

        async with write_scope() as session:
            result = await session.execute(
                text(self._MERGE_SQL),
                {"uid": user_id, "patch": json.dumps(patch)},
            )
            row = result.mappings().first()
        return self._normalize_row(dict(row)) if row else None

    async def _atomic_merge_settings_json(
        self, user_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Single-statement jsonb merge: ``settings_json = existing || patch``,
        via the SQLAlchemy Core ``db_engine`` (committing) path.

        Race-free: the ``||`` runs inside the ``ON CONFLICT DO UPDATE`` against
        the locked, committed row. ``CAST(:patch AS jsonb)`` (not ``:patch::jsonb``)
        — SQLAlchemy's text() bind parser eats a colon from ``::``."""
        import json

        from app.db import engine as db_engine

        row = await db_engine.execute_returning_one(
            self._MERGE_SQL, {"uid": user_id, "patch": json.dumps(patch)}
        )
        return self._normalize_row(row)

    async def _rmw_merge_settings_json(
        self, user_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Legacy non-atomic merge via PostgREST (read fresh → merge → upsert).

        Used only when neither the ORM nor the Core engine path is available
        (migration window / local without Supavisor), or as the last-resort
        fallback if the atomic statement raised. Reads uncached to narrow the
        race window."""
        current = await self._load_user_settings(user_id)
        existing_json = (current or {}).get("settings_json") or {}
        merged = merge_settings_json(existing_json, patch)

        table = await self._get_table()
        result = await table.upsert(
            {"user_id": user_id, "settings_json": merged}, on_conflict="user_id"
        ).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    @staticmethod
    def _normalize_row(
        row: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Normalize a RETURNING * row to the REST-shaped dict callers expect.

        asyncpg returns ``jsonb`` as a JSON string — decode settings_json back
        to a dict. uuid (``id`` / ``user_id``) → str and timestamptz
        (``created_at`` / ``updated_at``) → ISO str so the value types match the
        PostgREST baseline the type-sensitive consumer (user_settings_router →
        Pydantic ``str`` fields) needs. Used by BOTH the ORM (write_scope) and
        Core (db_engine) merge paths since both return RETURNING * rows."""
        if not row:
            return row
        import datetime
        import json
        import uuid

        out: Dict[str, Any] = {}
        for key, value in row.items():
            if key == "settings_json" and isinstance(value, str):
                try:
                    out[key] = json.loads(value)
                except (ValueError, TypeError):
                    out[key] = value
            elif isinstance(value, uuid.UUID):
                out[key] = str(value)
            elif isinstance(value, datetime.datetime):
                out[key] = value.isoformat()
            else:
                out[key] = value
        return out

    async def delete(self, user_id: str) -> bool:
        """
        删除用户设置

        Args:
            user_id: 用户 ID

        Returns:
            是否删除成功
        """
        try:
            table = await self._get_table()
            await table.delete().eq("user_id", user_id).execute()
            user_settings_cache.invalidate(user_id)
            logger.info(f"用户设置已删除: user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"删除用户设置失败: {e}")
            return False
