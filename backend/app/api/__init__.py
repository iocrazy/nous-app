# app/api/__init__.py

"""
API 路由模块

使用 Supabase 作为后端数据存储。
"""

from fastapi import APIRouter

from app.api.supabase_auth_router import router as auth_router
from app.api.supabase_douyin_router import router as douyin_router
from app.api.api_key_router import router as api_key_router
from app.api.user_settings_router import router as settings_router
from app.api.frontend_config_router import router as frontend_config_router

api_router = APIRouter()

api_router.include_router(
    router=auth_router,
    tags=["认证"]
)

api_router.include_router(
    router=douyin_router,
    tags=["抖音视频"]
)

api_router.include_router(
    router=api_key_router,
    tags=["API 密钥管理"]
)

api_router.include_router(
    router=settings_router,
    tags=["用户设置"]
)

api_router.include_router(
    router=frontend_config_router,
    tags=["前端配置"]
)
