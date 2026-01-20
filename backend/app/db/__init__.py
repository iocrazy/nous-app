# app/db/__init__.py

"""
数据库模块

使用 Supabase 作为数据库后端。
支持同步和异步客户端。
"""

from app.db.supabase_client import (
    get_supabase,
    get_supabase_admin,
    get_async_supabase,
    get_async_supabase_admin,
)

__all__ = [
    "get_supabase",
    "get_supabase_admin",
    "get_async_supabase",
    "get_async_supabase_admin",
]
