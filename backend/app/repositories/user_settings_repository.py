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

        Args:
            user_id: 用户 ID
            settings: 设置数据

        Returns:
            更新后的设置数据
        """
        try:
            data = {"user_id": user_id, **settings}

            table = await self._get_table()
            result = await table.upsert(data, on_conflict="user_id").execute()

            # Invalidate the cached copy so subsequent reads see the write.
            user_settings_cache.invalidate(user_id)

            if result.data and len(result.data) > 0:
                logger.info(f"用户设置已保存: user_id={user_id}")
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"保存用户设置失败: {e}")
            return None

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
