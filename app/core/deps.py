from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_session

# 异步数据库会话依赖
AsyncSessionDep = Annotated[AsyncSession, Depends(get_async_session)]