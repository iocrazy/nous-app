# app/db/database.py

"""
数据库连接和会话管理模块

提供异步数据库引擎和会话工厂，用于管理 SQLite 数据库连接。
包含异步 SQLAlchemy 引擎配置和会话创建功能。
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.core.config import settings
import aiosqlite

# 异步引擎，本地 sqllite
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # 禁用 SQL 日志
    pool_pre_ping=True, #连接检查机制
    future=True
    )

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    class_=AsyncSession
    )
