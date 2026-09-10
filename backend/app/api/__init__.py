# app/api/__init__.py

"""
API Router Module

Uses Supabase as backend data storage.
"""

from fastapi import APIRouter

from app.api.admin import admin_router
from app.api.agent_memory_user_router import router as agent_memory_user_router
from app.api.ai_library_router import router as ai_library_router
from app.api.ai_memory_router import router as ai_memory_router
from app.api.ai_router import router as ai_pipeline_router
from app.api.ai_settings_router import router as ai_settings_router
from app.api.api_key_router import router as api_key_router

# Aliased to ``assets_api_router`` (not ``assets_router``) for the same reason
# as ``distribution_api_router`` below: reusing the submodule basename here
# would overwrite the ``app.api.assets_router`` module attribute with the
# APIRouter instance, breaking ``from app.api import assets_router as ar``.
from app.api.assets_router import router as assets_api_router
from app.api.beat_memos_router import router as beat_memos_router
from app.api.beat_templates_router import router as beat_templates_router
from app.api.canvas_derive_router import router as canvas_derive_router
from app.api.canvases_router import router as canvases_router
from app.api.cleanup_router import router as cleanup_router
from app.api.collections_router import router as collections_router
from app.api.conversation_router import router as conversation_router
from app.api.cover_templates_router import router as cover_templates_router
from app.api.episodes_router import router as episodes_router
from app.api.error_report_router import router as error_report_router
from app.api.flows_router import router as flows_router
from app.api.frontend_config_router import router as frontend_config_router
from app.api.generated_media_router import router as generated_media_router

# Aliased for the same reason as ``assets_api_router`` above: the bare
# basename would shadow the ``app.api.generated_router`` module attribute.
from app.api.generated_router import router as generated_api_router
from app.api.inbox_router import router as inbox_router
from app.api.inspiration_router import router as inspiration_router
from app.api.invites_router import router as invites_router
from app.api.libraries_router import router as libraries_router
from app.api.logs_router import router as logs_router
from app.api.media_auth import router as media_auth_router
from app.api.media_router import legacy_router as legacy_douyin_router
from app.api.media_router import media_content_router
from app.api.media_router import router as media_router
from app.api.modules_router import router as modules_router
from app.api.notifications_router import router as notifications_router
from app.api.payment_router import router as payment_router
from app.api.points_router import router as points_router
from app.api.project_assets_router import router as _project_assets_router
from app.api.projects_router import router as projects_router

# Aliased for the same reason as ``assets_api_router`` above: the bare
# basename would shadow the ``app.api.prompts_router`` module attribute.
from app.api.prompts_router import router as prompts_api_router
from app.api.realtime_router import router as realtime_router
from app.api.resources_router import router as resources_router
from app.api.reviews_router import router as reviews_router

# Legacy storyboard workbench routers (sb_projects / sb_canvas / sb_characters /
# sb_ai / sb_export) retired in the Phase B P4 cutover — the storyboarding
# surface now lives in the script editor (script_shots_router). Their
# ``/storyboard/*`` paths are 410-tombstoned by sb_gone_router below.
from app.api.sb_gone_router import router as sb_gone_router
from app.api.script_ai_router import router as script_ai_router
from app.api.script_assets_router import router as script_assets_router
from app.api.script_beats_router import router as script_beats_router
from app.api.script_canvas_router import router as script_canvas_router
from app.api.script_export_router import router as script_export_router
from app.api.script_import_router import router as script_import_router
from app.api.script_import_scenes_router import router as script_import_scenes_router
from app.api.script_projects_router import router as script_projects_router
from app.api.script_scenes_router import router as script_scenes_router
from app.api.script_shots_router import router as script_shots_router
from app.api.script_versions_router import router as script_versions_router
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
from app.api.topics_router import router as _topics_router
from app.api.usage_router import router as usage_router
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

api_router.include_router(router=_topics_router, tags=["Topics"])

api_router.include_router(router=search_router, tags=["Search"])

api_router.include_router(router=collections_router, tags=["Collections"])

api_router.include_router(router=cleanup_router, tags=["Cleanup"])

api_router.include_router(router=logs_router, tags=["Logs"])

api_router.include_router(router=system_router, tags=["系统监控"])


api_router.include_router(router=ai_settings_router, tags=["AI"])
api_router.include_router(router=ai_memory_router, tags=["AI Memory"])
api_router.include_router(router=agent_memory_user_router, tags=["Agent Memories"])

api_router.include_router(router=ai_pipeline_router, tags=["AI"])

api_router.include_router(router=points_router, tags=["Points"])

api_router.include_router(router=payment_router, tags=["Payment"])

api_router.include_router(router=projects_router, tags=["MediaTrack"])
api_router.include_router(router=canvases_router, tags=["Canvas"])
api_router.include_router(router=canvas_derive_router, tags=["Canvas"])
api_router.include_router(router=_project_assets_router, tags=["Project Assets"])

api_router.include_router(router=generated_media_router, tags=["Generated Media"])
api_router.include_router(router=assets_api_router, tags=["Assets"])
api_router.include_router(router=prompts_api_router, tags=["Prompts"])
api_router.include_router(router=generated_api_router, tags=["Generated Inbox"])
api_router.include_router(router=cover_templates_router, tags=["Cover Templates"])

api_router.include_router(router=resources_router, tags=["Resources"])

api_router.include_router(router=shares_router, tags=["Shares"])

api_router.include_router(router=libraries_router, tags=["Libraries"])

api_router.include_router(router=reviews_router, tags=["Reviews"])

api_router.include_router(router=task_manager_router, tags=["Task Manager"])

# A3: Task Flows — group child tasks (parse → download → transcribe →
# summary) under one user-visible flow with cascade cancel.
api_router.include_router(router=flows_router)

api_router.include_router(router=teams_router, tags=["Teams"])
api_router.include_router(router=usage_router, tags=["Usage"])

api_router.include_router(router=conversation_router, tags=["Conversations"])

# DBOS orchestrator health + introspection (PR-D2.2)
from app.api.dbos_router import router as dbos_router  # noqa: E402

api_router.include_router(router=dbos_router, tags=["DBOS"])

# DBOS per-workflow status + SSE stream (PR-D4)
from app.api.workflows_router import router as workflows_router  # noqa: E402

api_router.include_router(router=workflows_router, tags=["DBOS Workflows"])

# Workflow templates + node bank (Project Workflow M1 PR-A). Same /workflows
# prefix as the DBOS router above; paths are disjoint (see router docstring).
from app.api.workflow_templates_router import (  # noqa: E402
    router as workflow_templates_router,
)

api_router.include_router(router=workflow_templates_router, tags=["Workflow Templates"])

# Ideation topic pool (Project Workflow M1.5). Mounted at /ideation/topics —
# /topics is owned by the hotspots/signal-feed router above, so the ideation
# surface takes a disjoint prefix.
from app.api.ideation_router import router as ideation_router  # noqa: E402

api_router.include_router(router=ideation_router, tags=["Ideation"])

# Issues — top-level user-visible entity (PR-D6)
from app.api.issues_router import router as issues_router  # noqa: E402

api_router.include_router(router=issues_router, tags=["Issues"])
from app.api.issue_progress_router import router as issue_progress_router  # noqa: E402

api_router.include_router(router=issue_progress_router, tags=["Issues"])

# Content relay pipelines (W2b) — fixed-order agent handoff on sub-issues
from app.api.pipelines_router import router as pipelines_router  # noqa: E402

api_router.include_router(router=pipelines_router, tags=["Pipelines"])

api_router.include_router(router=invites_router, tags=["Invites"])

api_router.include_router(router=modules_router, tags=["Modules"])

api_router.include_router(router=notifications_router, tags=["Notifications"])
api_router.include_router(router=inbox_router, tags=["Inbox"])

api_router.include_router(router=realtime_router, tags=["Realtime"])

api_router.include_router(router=error_report_router, tags=["Error Reporting"])

api_router.include_router(router=admin_router, tags=["Admin"])

# Retired legacy storyboard workbench — 410 Gone for every /storyboard/* path
# (the five sb_* routers are no longer registered; the shot board lives in the
# script editor now). Tables are left in place (deferred cutover migration).
api_router.include_router(router=sb_gone_router, tags=["Storyboard (retired)"])

api_router.include_router(router=style_templates_router, tags=["Style Templates"])

api_router.include_router(router=skills_router, tags=["Skills"])

api_router.include_router(router=script_projects_router, tags=["Scripts"])
api_router.include_router(router=script_canvas_router, tags=["Scripts"])
api_router.include_router(router=script_assets_router, tags=["Script Assets"])
api_router.include_router(router=script_ai_router, tags=["Script AI"])
api_router.include_router(router=script_import_router, tags=["Script Import"])
api_router.include_router(router=script_import_scenes_router, tags=["Script Import"])
api_router.include_router(router=script_export_router, tags=["Script Export"])
api_router.include_router(router=script_scenes_router, tags=["Script Scenes"])
api_router.include_router(router=script_shots_router, tags=["Script Shots"])
api_router.include_router(router=script_beats_router, tags=["Script Beats"])
api_router.include_router(router=beat_templates_router, tags=["Beat Templates"])
api_router.include_router(router=beat_memos_router, tags=["Beat Memos"])
api_router.include_router(router=script_versions_router, tags=["Script Versions"])
api_router.include_router(router=episodes_router, tags=["Episodes"])

api_router.include_router(router=ai_library_router, tags=["AI Library"])
from app.api.agent_inbox_router import router as agent_inbox_router  # noqa: E402

api_router.include_router(router=agent_inbox_router, tags=["AI Library"])

api_router.include_router(router=workforce_router, tags=["Workforce"])

# Phase N (N5) / D10-8: per-subsystem deep health probe.
from app.api.health_router import router as deep_health_router  # noqa: E402

api_router.include_router(router=deep_health_router, tags=["Health"])

from app.api.codex_daemon_dist_router import (  # noqa: E402
    router as codex_daemon_dist_router,
)
from app.api.codex_daemon_router import router as codex_daemon_router  # noqa: E402
from app.api.codex_daemon_ws_router import (  # noqa: E402
    router as codex_daemon_ws_router,
)
from app.api.jimeng_cli_router import codex_router as codex_cli_router  # noqa: E402
from app.api.jimeng_cli_router import router as jimeng_cli_router  # noqa: E402

# A 路线 (2026-05-04, session 2/3): 任务集 / 用户定时 / 4-Lane 优先级 / WS ticket
# Imports from feat/a5 + feat/a7 + feat/a9 (PR 队列 #158/159/160/161).
# flows_router (feat/a3) is already included above — a second include here
# double-registered every /flows route until test_route_uniqueness caught it.
from app.api.lanes_router import router as lanes_router  # noqa: E402
from app.api.schedules_router import router as schedules_router  # noqa: E402
from app.api.ws_ticket_router import router as ws_ticket_router  # noqa: E402

api_router.include_router(router=schedules_router, tags=["Schedules"])
api_router.include_router(router=lanes_router, tags=["Lanes"])
api_router.include_router(router=codex_daemon_router, tags=["Codex Daemon"])
api_router.include_router(router=codex_daemon_dist_router, tags=["Codex Daemon"])
api_router.include_router(router=jimeng_cli_router, tags=["Jimeng CLI"])
api_router.include_router(router=codex_cli_router, tags=["Codex CLI"])
api_router.include_router(router=codex_daemon_ws_router, tags=["Codex Daemon"])
api_router.include_router(router=ws_ticket_router, tags=["WS Ticket"])

# A8: paperclip-style chat thread per issue (issue_messages table, mig 205).
from app.api.issue_messages_router import router as issue_messages_router  # noqa: E402

api_router.include_router(router=issue_messages_router, tags=["Issue Messages"])

api_router.include_router(router=inspiration_router, tags=["Inspiration"])

# PR-D1: distribution accounts (platform OAuth connect/callback/refresh).
# NOTE: aliased to ``distribution_api_router`` (not ``distribution_router``) —
# reusing the submodule's own basename as the local name here would overwrite
# the ``app.api.distribution_router`` attribute with the APIRouter instance,
# breaking any ``import app.api.distribution_router as x`` elsewhere (e.g.
# the test module) which resolves via attribute traversal on the package.
from app.api.distribution_router import router as distribution_api_router  # noqa: E402

api_router.include_router(router=distribution_api_router, tags=["Distribution"])

# PR-D2: Douyin publish webhook (share-completion callback). Ungated by
# require_distribution — the platform calls this, not a logged-in user.
# Aliased the same way as distribution_api_router above, for the same reason
# (the submodule's own basename must stay resolvable via attribute traversal
# for ``import app.api.distribution_webhook as wh`` in tests).
from app.api.distribution_webhook import (  # noqa: E402
    router as distribution_webhook_router,
)

api_router.include_router(router=distribution_webhook_router, tags=["Distribution"])
