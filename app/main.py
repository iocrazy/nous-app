from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, Security
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger


from app.api import api_router
from app.db.init_db import init_db

from app.db.database import async_engine
from app.core.config import settings
from app.core.auth import check_user_permission




@asynccontextmanager
async def lifespan(app: FastAPI):

    await init_db() # 在启动时初始化数据库
    # app.state.redis = await init_redis() # 在启动时初始化redis

    yield logger.info(f"{settings.APP_NAME}启动成功")

    # 关闭 db 数据库引擎
    logger.info("正在关闭数据库引擎...")
    await async_engine.dispose()
    logger.info("数据库引擎已关闭")

    # # 关闭 redis 连接
    # if hasattr(app.state, "redis") and app.state.redis:
    #     logger.info("正在关闭Redis连接...")
    #     await app.state.redis.close()
    #     logger.info("Redis连接已关闭")


    logger.info(f"{settings.APP_NAME}关闭成功")

app = FastAPI(lifespan=lifespan)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "message": "Service is running"}



if __name__ == '__main__':
    # 使用配置文件中的主机设置，端口固定为8080（Docker标准）
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.APP_PORT,  # 使用常量，Docker容器内部固定端口
        reload=settings.RELOAD
    )
