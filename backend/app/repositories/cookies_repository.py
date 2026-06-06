# backend/app/repositories/cookies_repository.py

"""
用户 Cookie 数据访问层

处理 user_cookies 表的 CRUD 操作。
使用异步 Supabase 客户端。
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.cookies_repository_orm import CookiesRepositoryOrm


class CookiesRepository:
    """用户 Cookie 仓库类 (异步)"""

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table("user_cookies")

    async def get_all_by_user(self, user_id: str) -> List[Dict]:
        """
        获取用户所有平台的 Cookie

        Args:
            user_id: 用户 ID

        Returns:
            Cookie 列表，失败返回空列表
        """
        try:
            table = await self._get_table()
            result = await table.select("*").eq("user_id", user_id).execute()
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"获取用户 Cookie 列表失败: user_id={user_id}, error={e}")
            return []

    async def get_by_user_and_platform(
        self, user_id: str, platform: str
    ) -> Optional[Dict]:
        """
        获取用户指定平台的 Cookie

        Args:
            user_id: 用户 ID
            platform: 平台标识（如 'douyin', 'bilibili'）

        Returns:
            Cookie 数据，不存在则返回 None
        """
        try:
            table = await self._get_table()
            result = (
                await table.select("*")
                .eq("user_id", user_id)
                .eq("platform", platform)
                .execute()
            )
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(
                f"获取用户 Cookie 失败: user_id={user_id}, platform={platform}, error={e}"
            )
            return None

    async def upsert(self, user_id: str, platform: str, data: Dict) -> Optional[Dict]:
        """
        创建或更新用户 Cookie

        保存时自动设置 is_valid=True 并清空 error_message。

        Args:
            user_id: 用户 ID
            platform: 平台标识
            data: Cookie 数据（cookie_value 等字段）

        Returns:
            保存后的 Cookie 数据，失败返回 None
        """
        try:
            payload = {
                "user_id": user_id,
                "platform": platform,
                "is_valid": True,
                "error_message": None,
                # Refresh on every save — the upsert has no on-update default,
                # so without this updated_at stayed frozen at first-insert time
                # even when cookie content changed, making "is my cookie fresh?"
                # checks lie (observed: 1082→1172-byte update, ts stuck on 4/15).
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **data,
            }

            table = await self._get_table()
            result = await table.upsert(
                payload, on_conflict="user_id,platform"
            ).execute()

            if result.data and len(result.data) > 0:
                logger.info(
                    f"用户 Cookie 已保存: user_id={user_id}, platform={platform}"
                )
                return result.data[0]
            return None
        except Exception as e:
            logger.error(
                f"保存用户 Cookie 失败: user_id={user_id}, platform={platform}, error={e}"
            )
            return None

    async def delete(self, user_id: str, platform: str) -> bool:
        """
        删除用户指定平台的 Cookie

        Args:
            user_id: 用户 ID
            platform: 平台标识

        Returns:
            是否删除成功
        """
        try:
            table = await self._get_table()
            await (
                table.delete().eq("user_id", user_id).eq("platform", platform).execute()
            )
            logger.info(f"用户 Cookie 已删除: user_id={user_id}, platform={platform}")
            return True
        except Exception as e:
            logger.error(
                f"删除用户 Cookie 失败: user_id={user_id}, platform={platform}, error={e}"
            )
            return False

    async def mark_invalid(
        self, user_id: str, platform: str, error_message: str
    ) -> None:
        """
        将 Cookie 标记为无效并记录错误信息

        Args:
            user_id: 用户 ID
            platform: 平台标识
            error_message: 错误描述
        """
        try:
            table = await self._get_table()
            await (
                table.update({"is_valid": False, "error_message": error_message})
                .eq("user_id", user_id)
                .eq("platform", platform)
                .execute()
            )
            logger.warning(
                f"用户 Cookie 已标记为无效: user_id={user_id}, platform={platform}, "
                f"reason={error_message}"
            )
        except Exception as e:
            logger.error(
                f"标记 Cookie 无效失败: user_id={user_id}, platform={platform}, error={e}"
            )


def get_cookies_repository() -> Union["CookiesRepository", "CookiesRepositoryOrm"]:
    """Return the right CookiesRepository implementation per env.

    ORM when ``USE_ORM_COOKIES`` is set AND the SQLAlchemy engine is configured;
    otherwise the legacy supabase-py REST path. A flag-on but engine-missing
    deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_COOKIES:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.cookies_repository_orm import CookiesRepositoryOrm

            return CookiesRepositoryOrm()
        logger.warning(
            "USE_ORM_COOKIES=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return CookiesRepository()
