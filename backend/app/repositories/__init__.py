# app/repositories/__init__.py

"""
数据访问层模块

使用 Supabase 作为数据存储后端。
"""

from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository

__all__ = [
    "SupabaseDouyinRepository",
]
