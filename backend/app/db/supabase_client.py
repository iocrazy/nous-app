# app/db/supabase_client.py

"""
Supabase 客户端模块

提供 Supabase 客户端的初始化和管理。
支持数据库操作、认证和存储功能。
"""

from typing import Optional
from supabase import create_client, Client
from loguru import logger

from app.core.config import settings


class SupabaseClient:
    """Supabase 客户端单例"""

    _instance: Optional[Client] = None
    _admin_instance: Optional[Client] = None

    @classmethod
    def get_client(cls) -> Client:
        """获取 Supabase 客户端实例"""
        if cls._instance is None:
            if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
                raise ValueError("SUPABASE_URL 和 SUPABASE_ANON_KEY 必须配置")

            cls._instance = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_ANON_KEY
            )
            logger.info("Supabase 客户端初始化成功")

        return cls._instance

    @classmethod
    def get_admin_client(cls) -> Client:
        """获取 Supabase 管理员客户端（使用 service_role key）"""
        if cls._admin_instance is None:
            if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError("SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY 必须配置")

            cls._admin_instance = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY
            )
            logger.info("Supabase 管理员客户端初始化成功")

        return cls._admin_instance

    @classmethod
    def close(cls):
        """关闭客户端连接"""
        if cls._instance:
            cls._instance = None
            logger.info("Supabase 客户端已关闭")


# 便捷函数
def get_supabase() -> Client:
    """获取 Supabase 客户端"""
    return SupabaseClient.get_client()


def get_supabase_admin() -> Client:
    """获取 Supabase 管理员客户端"""
    return SupabaseClient.get_admin_client()
