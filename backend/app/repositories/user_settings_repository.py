# backend/app/repositories/user_settings_repository.py

"""
用户设置数据访问层

处理用户个人设置的 CRUD 操作。
"""

from typing import Optional, Dict, Any
from loguru import logger

from app.db.supabase_client import get_supabase_admin


class UserSettingsRepository:
    """用户设置仓库类"""

    def __init__(self):
        self.client = get_supabase_admin()
        self.table = self.client.table("user_settings")

    async def get_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        获取用户设置

        Args:
            user_id: 用户 ID

        Returns:
            设置数据，如果不存在则返回 None
        """
        try:
            result = self.table.select("*").eq("user_id", user_id).execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"获取用户设置失败: {e}")
            return None

    async def upsert(self, user_id: str, settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        创建或更新用户设置

        Args:
            user_id: 用户 ID
            settings: 设置数据

        Returns:
            更新后的设置数据
        """
        try:
            data = {
                "user_id": user_id,
                **settings
            }

            result = self.table.upsert(
                data,
                on_conflict="user_id"
            ).execute()

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
            self.table.delete().eq("user_id", user_id).execute()
            logger.info(f"用户设置已删除: user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"删除用户设置失败: {e}")
            return False
