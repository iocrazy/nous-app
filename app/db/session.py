# app/db/session.py

"""
数据库会话管理模块

提供异步数据库会话的上下文管理器，用于管理数据库连接的生命周期。
包含事务处理和异常回滚机制。
"""
from contextlib import asynccontextmanager

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    异步数据库会话依赖
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()



# 提供显式事务控制的上下文管理器,不会自动提交事务
@asynccontextmanager
async def get_async_transaction_session() -> AsyncGenerator[AsyncSession, None]:
    """
    提供显式事务控制的会话上下文管理器
    不会自动提交事务，需要调用方手动提交或回滚
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # 不自动提交，由调用方决定是否提交
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

