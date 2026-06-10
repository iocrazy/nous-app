# app/api/__init__.py

"""
API Router Module

Uses Supabase as backend data storage.
"""

from fastapi import APIRouter

from app.api.admin import admin_router
from app.api.ai_library_router import router as ai_library_router
from app.api.ai_router import router as ai_pipeline_router
from app.api.ai_settings_router import router as ai_settings_router
from app.api.api_key_router import router as api_key_router
from app.api.canvases_router import router as canvases_router
from app.api.cleanup_router import router as cleanup_router
from app.api.collections_router import router as collections_router
from app.api.error_report_router import router as error_report_router
from app.api.flows_router import router as flows_router
from app.api.frontend_config_router import router as frontend_config_router
from app.api.invites_router import router as invites_router
from app.api.libraries_router import router as libraries_router
from app.api.logs_router import router as logs_router
from app.api.media_auth import router as media_auth_router
from app.api.media_router import legacy_router as legacy_douyin_router
from app.api.media_router import media_content_router
from app.api.media_router import router as media_router
from app.api.notifications_router import router as notifications_router
from app.api.payment_router import router as payment_router
from app.api.points_router import router as points_router
from app.api.projects_router import router as projects_router
from app.api.realtime_router import router as realtime_router
from app.api.resources_router import router as resources_router
from app.api.reviews_router import router as reviews_router
from app.api.sb_ai_router import router as sb_ai_router
from app.api.sb_canvas_router import router as sb_canvas_router
from app.api.sb_characters_router import router as sb_characters_router
from app.api.sb_export_router import router as sb_export_router
from app.api.sb_projects_router import router as sb_projects_router
from app.api.script_ai_router import router as script_ai_router
from app.api.script_assets_router import router as script_assets_router
from app.api.script_canvas_router import router as script_canvas_router
from app.api.script_export_router import router as script_export_router
from app.api.script_import_router import router as script_import_router
from app.api.script_projects_router import router as script_projects_router
from app.api.search_router import router as search_router
from app.api.shares_router import router as shares_router
from app.api.skills_router import router as skills_router
from app.api.style_templates_router import router as style_templates_router
from app.api.supabase_auth_router import router as auth_router
from app.api.system_router import router as system_router
from app.api.tags_router import router as tags_router
from app.api.task_manager_router import router as task_manager_router
from app.api.task_router import router as task_router
from app.api.teams_router import router as teams_router
from app.api.temp_token_router import router as temp_token_router
from app.api.temp_ttl_router import router as temp_ttl_router
from app.api.user_settings_router import router as settings_router
from app.api.workforce_router import router as workforce_router

api_router = APIRouter()

# NOTE: lifespan_router (/healthz + /readyz) is intentionally NOT included
# here. It's mounted directly in main.py at /api/v1/* for BOTH gateway
# and worker roles, while api_router as a whole is gateway-only. Avoids
# double-registering the route.

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
api_router.include_router(router=canvases_router, tags=["Canvas"])

api_router.include_router(router=resources_router, tags=["Resources"])

api_router.include_router(router=shares_router, tags=["Shares"])

api_router.include_router(router=libraries_router, tags=["Libraries"])

api_router.include_router(router=temp_ttl_router, tags=["Library"])

api_router.include_router(router=reviews_router, tags=["Reviews"])

api_router.include_router(router=task_manager_router, tags=["Task Manager"])

# A3: Task Flows — group child tasks (parse → download → transcribe →
# summary) under one user-visible flow with cascade cancel.
api_router.include_router(router=flows_router)

api_router.include_router(router=teams_router, tags=["Teams"])

# DBOS orchestrator health + introspection (PR-D2.2)
from app.api.dbos_router import router as dbos_router  # noqa: E402

api_router.include_router(router=dbos_router, tags=["DBOS"])

# DBOS per-workflow status + SSE stream (PR-D4)
from app.api.workflows_router import router as workflows_router  # noqa: E402

api_router.include_router(router=workflows_router, tags=["DBOS Workflows"])

# Issues — top-level user-visible entity (PR-D6)
from app.api.issues_router import router as issues_router  # noqa: E402

api_router.include_router(router=issues_router, tags=["Issues"])

api_router.include_router(router=invites_router, tags=["Invites"])

api_router.include_router(router=notifications_router, tags=["Notifications"])

api_router.include_router(router=realtime_router, tags=["Realtime"])

api_router.include_router(router=error_report_router, tags=["Error Reporting"])

api_router.include_router(router=admin_router, tags=["Admin"])

api_router.include_router(router=sb_projects_router, tags=["Storyboard Projects"])
api_router.include_router(router=sb_canvas_router, tags=["Storyboard Canvas"])
api_router.include_router(router=sb_characters_router, tags=["Storyboard Characters"])
api_router.include_router(router=sb_ai_router, tags=["Storyboard AI"])
api_router.include_router(router=sb_export_router, tags=["Storyboard Export"])

api_router.include_router(router=style_templates_router, tags=["Style Templates"])

api_router.include_router(router=skills_router, tags=["Skills"])

api_router.include_router(router=script_projects_router, tags=["Scripts"])
api_router.include_router(router=script_canvas_router, tags=["Scripts"])
api_router.include_router(router=script_assets_router, tags=["Script Assets"])
api_router.include_router(router=script_ai_router, tags=["Script AI"])
api_router.include_router(router=script_import_router, tags=["Script Import"])
api_router.include_router(router=script_export_router, tags=["Script Export"])

api_router.include_router(router=ai_library_router, tags=["AI Library"])

api_router.include_router(router=workforce_router, tags=["Workforce"])

# Phase N (N5) / D10-8: per-subsystem deep health probe.
from app.api.health_router import router as deep_health_router  # noqa: E402

api_router.include_router(router=deep_health_router, tags=["Health"])

# A 路线 (2026-05-04, session 2/3): 任务集 / 用户定时 / 4-Lane 优先级 / WS ticket
# Imports from feat/a3 + feat/a5 + feat/a7 + feat/a9 (PR 队列 #158/159/160/161)
from app.api.flows_router import router as flows_router  # noqa: E402
from app.api.lanes_router import router as lanes_router  # noqa: E402
from app.api.schedules_router import router as schedules_router  # noqa: E402
from app.api.ws_ticket_router import router as ws_ticket_router  # noqa: E402

api_router.include_router(router=flows_router, tags=["Flows"])
api_router.include_router(router=schedules_router, tags=["Schedules"])
api_router.include_router(router=lanes_router, tags=["Lanes"])
api_router.include_router(router=ws_ticket_router, tags=["WS Ticket"])

# A8: paperclip-style chat thread per issue (issue_messages table, mig 205).
from app.api.issue_messages_router import router as issue_messages_router  # noqa: E402

api_router.include_router(router=issue_messages_router, tags=["Issue Messages"])
