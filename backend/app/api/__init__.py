# app/api/__init__.py

"""
API Router Module

Uses Supabase as backend data storage.
"""

from fastapi import APIRouter

from app.api.ai_router import router as ai_pipeline_router
from app.api.ai_settings_router import router as ai_settings_router
from app.api.analysis_router import router as analysis_router
from app.api.api_key_router import router as api_key_router
from app.api.cleanup_router import router as cleanup_router
from app.api.collections_router import router as collections_router
from app.api.frontend_config_router import router as frontend_config_router
from app.api.libraries_router import router as libraries_router
from app.api.logs_router import router as logs_router
from app.api.payment_router import router as payment_router
from app.api.points_router import router as points_router
from app.api.projects_router import router as projects_router
from app.api.resources_router import router as resources_router
from app.api.reviews_router import router as reviews_router
from app.api.search_router import router as search_router
from app.api.shares_router import router as shares_router
from app.api.supabase_auth_router import router as auth_router
from app.api.temp_token_router import router as temp_token_router
from app.api.system_router import router as system_router
from app.api.tags_router import router as tags_router
from app.api.task_manager_router import router as task_manager_router
from app.api.task_router import router as task_router
from app.api.user_settings_router import router as settings_router
from app.api.media_router import legacy_router as legacy_douyin_router
from app.api.media_router import router as media_router
from app.api.media_router import media_content_router
from app.api.teams_router import router as teams_router
from app.api.invites_router import router as invites_router
from app.api.video_collections_router import router as video_collections_router
from app.api.notifications_router import router as notifications_router
from app.api.realtime_router import router as realtime_router
from app.api.error_report_router import router as error_report_router
from app.api.admin import admin_router
from app.api.media_auth import router as media_auth_router
from app.api.sb_projects_router import router as sb_projects_router
from app.api.sb_canvas_router import router as sb_canvas_router
from app.api.sb_characters_router import router as sb_characters_router
from app.api.sb_ai_router import router as sb_ai_router
from app.api.sb_export_router import router as sb_export_router
from app.api.script_projects_router import router as script_projects_router
from app.api.script_canvas_router import router as script_canvas_router

api_router = APIRouter()

api_router.include_router(router=auth_router, tags=["Authentication"])

api_router.include_router(router=media_auth_router, tags=["Media Auth"])

api_router.include_router(router=temp_token_router, tags=["Temp Token"])

api_router.include_router(router=media_router, tags=["Media"])

api_router.include_router(router=media_content_router, tags=["Media Content"])

api_router.include_router(router=legacy_douyin_router, tags=["Legacy"])

api_router.include_router(router=api_key_router, tags=["API 密钥管理"])

api_router.include_router(router=settings_router, tags=["用户设置"])

api_router.include_router(router=frontend_config_router, tags=["前端配置"])

api_router.include_router(router=task_router, tags=["任务管理"])

api_router.include_router(router=tags_router, tags=["Tags"])

api_router.include_router(router=analysis_router, tags=["Analysis"])

api_router.include_router(router=search_router, tags=["Search"])

api_router.include_router(router=collections_router, tags=["Collections"])

api_router.include_router(router=cleanup_router, tags=["Cleanup"])

api_router.include_router(router=logs_router, tags=["Logs"])

api_router.include_router(router=system_router, tags=["系统监控"])


api_router.include_router(router=ai_settings_router, tags=["AI"])

api_router.include_router(router=ai_pipeline_router, tags=["AI"])

api_router.include_router(router=points_router, tags=["Points"])

api_router.include_router(router=payment_router, tags=["Payment"])

api_router.include_router(router=projects_router, tags=["MediaTrack"])

api_router.include_router(router=resources_router, tags=["Resources"])

api_router.include_router(router=shares_router, tags=["Shares"])

api_router.include_router(router=libraries_router, tags=["Libraries"])

api_router.include_router(router=reviews_router, tags=["Reviews"])

api_router.include_router(router=task_manager_router, tags=["Task Manager"])

api_router.include_router(router=teams_router, tags=["Teams"])

api_router.include_router(router=invites_router, tags=["Invites"])

api_router.include_router(router=video_collections_router, tags=["Video Collections"])

api_router.include_router(router=notifications_router, tags=["Notifications"])

api_router.include_router(router=realtime_router, tags=["Realtime"])

api_router.include_router(router=error_report_router, tags=["Error Reporting"])

api_router.include_router(router=admin_router, tags=["Admin"])

api_router.include_router(router=sb_projects_router, tags=["Storyboard Projects"])
api_router.include_router(router=sb_canvas_router, tags=["Storyboard Canvas"])
api_router.include_router(router=sb_characters_router, tags=["Storyboard Characters"])
api_router.include_router(router=sb_ai_router, tags=["Storyboard AI"])
api_router.include_router(router=sb_export_router, tags=["Storyboard Export"])

api_router.include_router(router=script_projects_router, tags=["Scripts"])
api_router.include_router(router=script_canvas_router, tags=["Scripts"])
