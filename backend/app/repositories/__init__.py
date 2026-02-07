# app/repositories/__init__.py

"""
数据访问层模块

使用 Supabase 作为数据存储后端。
"""

from app.repositories.video_repository import VideoRepository

__all__ = [
    "VideoRepository",
]
