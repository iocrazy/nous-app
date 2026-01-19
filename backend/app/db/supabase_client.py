# app/db/supabase_client.py

"""
Supabase 客户端模块

提供 Supabase 客户端的初始化和管理。
支持同步和异步客户端，用于数据库操作、认证和存储功能。
"""

from typing import Optional
from supabase import create_client, Client
from supabase._async.client import AsyncClient
from supabase._async.client import create_client as create_async_client
from loguru import logger

from app.core.config import settings


class SupabaseClient:
    """Supabase 客户端单例（同步）"""

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


class AsyncSupabaseClient:
    """Supabase 异步客户端单例"""

    _instance: Optional[AsyncClient] = None
    _admin_instance: Optional[AsyncClient] = None

    @classmethod
    async def get_client(cls) -> AsyncClient:
        """获取异步 Supabase 客户端实例"""
        if cls._instance is None:
            if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
                raise ValueError("SUPABASE_URL 和 SUPABASE_ANON_KEY 必须配置")

            cls._instance = await create_async_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_ANON_KEY
            )
            logger.info("Supabase 异步客户端初始化成功")

        return cls._instance

    @classmethod
    async def get_admin_client(cls) -> AsyncClient:
        """获取异步 Supabase 管理员客户端"""
        if cls._admin_instance is None:
            if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError("SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY 必须配置")

            cls._admin_instance = await create_async_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY
            )
            logger.info("Supabase 异步管理员客户端初始化成功")

        return cls._admin_instance

    @classmethod
    async def close(cls):
        """关闭异步客户端连接"""
        if cls._instance:
            await cls._instance.auth.sign_out()
            cls._instance = None
        if cls._admin_instance:
            cls._admin_instance = None
            logger.info("Supabase 异步客户端已关闭")


# 同步便捷函数
def get_supabase() -> Client:
    """获取 Supabase 客户端"""
    return SupabaseClient.get_client()


def get_supabase_admin() -> Client:
    """获取 Supabase 管理员客户端"""
    return SupabaseClient.get_admin_client()


# 异步便捷函数
async def get_async_supabase() -> AsyncClient:
    """获取异步 Supabase 客户端"""
    return await AsyncSupabaseClient.get_client()


async def get_async_supabase_admin() -> AsyncClient:
    """获取异步 Supabase 管理员客户端"""
    return await AsyncSupabaseClient.get_admin_client()
