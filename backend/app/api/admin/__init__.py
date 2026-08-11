"""Admin API routers."""

from fastapi import APIRouter

from .agents_router import router as agents_catalog_router
from .ai_usage_router import router as ai_usage_router
from .alert_rules_router import router as alert_rules_router
from .audit_logs_router import router as audit_logs_router
from .backfill_router import router as backfill_router
from .boundary_audit_router import router as boundary_audit_router
from .celery_router import router as celery_router
from .credits_router import router as credits_router

# NOTE: aliased to ``..._api_router`` (not ``distribution_inspect_router``) —
# importing under the module's own name would rebind the package attribute
# ``app.api.admin.distribution_inspect_router`` from the MODULE to this
# APIRouter, so ``import app.api.admin.distribution_inspect_router as x``
# elsewhere (tests monkeypatching its helpers) silently gets the router object
# instead. Same trap, and same fix, as ``distribution_api_router`` in
# ``app/api/__init__.py``.
from .distribution_inspect_router import router as distribution_inspect_api_router
from .jimeng_auth_router import router as jimeng_auth_router
from .mediahub_model_router import router as mediahub_model_router
from .monitoring_router import router as monitoring_router
from .request_logs_router import router as request_logs_router
from .search_router import router as search_router
from .settings_router import router as settings_router
from .stats_router import router as stats_router
from .storage_migration_router import router as storage_migration_router
from .storage_router import router as storage_router
from .table_preferences_router import router as table_preferences_router
from .tags_router import router as tags_router
from .tasks_router import router as tasks_router
from .teams_router import router as teams_router
from .topic_sources_router import router as topic_sources_router
from .transcode_router import router as transcode_router
from .users_router import router as users_router
from .videos_router import router as videos_router

admin_router = APIRouter(prefix="/admin", tags=["Admin"])


@admin_router.get("/health")
async def admin_health():
    """Unauthenticated health check for admin API diagnostics."""
    from loguru import logger

    checks: dict = {"status": "ok"}

    # Check Supabase admin client initializes (auth/storage still use it)
    try:
        from app.db import get_async_supabase_admin

        await get_async_supabase_admin()
        checks["supabase_admin"] = "ok"
    except Exception as e:
        checks["supabase_admin"] = f"error: {type(e).__name__}: {e}"
        checks["status"] = "degraded"

    # Check user_profiles table access
    try:
        from sqlalchemy import func, select

        from app.db.session import read_scope
        from app.models import UserProfiles

        async with read_scope() as session:
            count = (
                await session.execute(select(func.count(UserProfiles.id)))
            ).scalar()
        checks["user_profiles"] = f"ok (count={count})"
    except Exception as e:
        checks["user_profiles"] = f"error: {type(e).__name__}: {e}"
        checks["status"] = "degraded"

    # Check resource_versions table access
    try:
        from sqlalchemy import func, select

        from app.db.session import read_scope
        from app.models import ResourceVersions

        async with read_scope() as session:
            count = (
                await session.execute(select(func.count(ResourceVersions.id)))
            ).scalar()
        checks["resource_versions"] = f"ok (count={count})"
    except Exception as e:
        checks["resource_versions"] = f"error: {type(e).__name__}: {e}"
        checks["status"] = "degraded"

    # Check JWT validation capability
    try:
        from app.db import get_async_supabase

        await get_async_supabase()  # probe: verify the anon client initializes
        checks["supabase_anon"] = "ok"
    except Exception as e:
        checks["supabase_anon"] = f"error: {type(e).__name__}: {e}"
        checks["status"] = "degraded"

    logger.info(f"[Admin Health] {checks}")
    return checks


admin_router.include_router(users_router, prefix="/users", tags=["Admin - Users"])
admin_router.include_router(teams_router, prefix="/teams", tags=["Admin - Teams"])
admin_router.include_router(videos_router, prefix="/videos", tags=["Admin - Videos"])
admin_router.include_router(
    audit_logs_router, prefix="/audit-logs", tags=["Admin - Audit Logs"]
)
admin_router.include_router(stats_router, prefix="/stats", tags=["Admin - Stats"])
admin_router.include_router(
    settings_router, prefix="/settings", tags=["Admin - Settings"]
)
admin_router.include_router(request_logs_router, prefix="/logs", tags=["Admin - Logs"])
admin_router.include_router(
    monitoring_router, prefix="/monitoring", tags=["Admin - Monitoring"]
)
admin_router.include_router(search_router, prefix="/search", tags=["Admin - Search"])
admin_router.include_router(
    alert_rules_router, prefix="/alerts", tags=["Admin - Alerts"]
)
admin_router.include_router(
    transcode_router, prefix="/transcode", tags=["Admin - Transcode"]
)
admin_router.include_router(tasks_router, prefix="/tasks", tags=["Admin - Tasks"])
admin_router.include_router(
    topic_sources_router, prefix="/topics/sources", tags=["Admin - Topic Sources"]
)
admin_router.include_router(
    table_preferences_router,
    prefix="/table-preferences",
    tags=["Admin - Table Preferences"],
)
admin_router.include_router(credits_router, prefix="/credits", tags=["Admin - Credits"])
admin_router.include_router(tags_router, prefix="/tags", tags=["Admin - Tags"])
admin_router.include_router(celery_router, prefix="/celery", tags=["Admin - Celery"])
admin_router.include_router(
    mediahub_model_router, prefix="/mediahub-models", tags=["Admin - Mediahub Models"]
)
admin_router.include_router(
    jimeng_auth_router, prefix="/jimeng", tags=["Admin - Jimeng Auth"]
)
admin_router.include_router(
    ai_usage_router, prefix="/ai-usage", tags=["Admin - AI Usage"]
)
admin_router.include_router(
    agents_catalog_router, prefix="/agents", tags=["Admin - Agents Catalog"]
)
admin_router.include_router(
    boundary_audit_router,
    prefix="/boundary-audit",
    tags=["Admin - Boundary Audit"],
)
admin_router.include_router(
    storage_migration_router,
    prefix="/storage-migration",
    tags=["Admin - Storage Migration"],
)
admin_router.include_router(
    storage_router,
    prefix="/storage",
    tags=["Admin - Storage"],
)
admin_router.include_router(
    backfill_router,
    prefix="/backfill",
    tags=["Admin - Backfill"],
)
admin_router.include_router(
    distribution_inspect_api_router,
    prefix="/distribution/inspect",
    tags=["Admin - Distribution Inspect"],
)

# Wave I (I3): agent harness telemetry snapshot
from .agent_telemetry_router import router as agent_telemetry_router  # noqa: E402

admin_router.include_router(
    agent_telemetry_router,
    tags=["Admin - Agent Telemetry"],
)
