# backend/app/repositories/user_settings_repository.py

"""
用户设置数据访问层

处理用户个人设置的 CRUD 操作。
使用异步 Supabase 客户端。
"""

from typing import Any, Dict, Optional

from loguru import logger

from app.core.cache import user_settings_cache
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

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table("user_settings")

    async def _load_user_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            table = await self._get_table()
            result = await table.select("*").eq("user_id", user_id).execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"获取用户设置失败: {e}")
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
        are upserted as-is; PostgREST only touches the columns in the payload,
        so a ``download_path`` write never disturbs ``settings_json``.

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

            # Plain columns (everything except settings_json) — PostgREST upsert
            # updates only the named columns on conflict, leaving settings_json
            # untouched.
            if rest:
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
        read-modify-write window. Falls back to the (non-atomic) PostgREST
        read-merge-write only when the SQLAlchemy engine isn't configured.
        """
        partial = partial or {}
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

    async def _atomic_merge_settings_json(
        self, user_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Single-statement jsonb merge: ``settings_json = existing || patch``.

        Race-free: the ``||`` runs inside the ``ON CONFLICT DO UPDATE`` against
        the locked, committed row. ``CAST(:patch AS jsonb)`` (not ``:patch::jsonb``)
        — SQLAlchemy's text() bind parser eats a colon from ``::``.
        """
        import json

        from app.db import engine as db_engine

        sql = (
            "INSERT INTO public.user_settings (user_id, settings_json) "
            "VALUES (:uid, CAST(:patch AS jsonb)) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb) "
            "|| CAST(:patch AS jsonb), "
            "updated_at = NOW() "
            "RETURNING *"
        )
        row = await db_engine.execute_returning_one(
            sql, {"uid": user_id, "patch": json.dumps(patch)}
        )
        return self._normalize_row(row)

    async def _rmw_merge_settings_json(
        self, user_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Legacy non-atomic merge via PostgREST (read fresh → merge → upsert).

        Used only when the SQLAlchemy engine isn't configured (migration window
        / local without Supavisor). Reads uncached to narrow the race window.
        """
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
        """asyncpg returns ``jsonb`` as a JSON string — decode settings_json back
        to a dict so callers get the same shape PostgREST gives them."""
        if row and isinstance(row.get("settings_json"), str):
            import json

            try:
                return {**row, "settings_json": json.loads(row["settings_json"])}
            except (ValueError, TypeError):
                pass
        return row

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
