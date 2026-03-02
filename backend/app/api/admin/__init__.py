"""Admin API routers."""

from fastapi import APIRouter

from .users_router import router as users_router
from .teams_router import router as teams_router
from .audit_logs_router import router as audit_logs_router
from .stats_router import router as stats_router
from .videos_router import router as videos_router
from .settings_router import router as settings_router
from .request_logs_router import router as request_logs_router
from .monitoring_router import router as monitoring_router
from .search_router import router as search_router
from .alert_rules_router import router as alert_rules_router

admin_router = APIRouter(prefix="/admin", tags=["Admin"])

admin_router.include_router(users_router, prefix="/users", tags=["Admin - Users"])
admin_router.include_router(teams_router, prefix="/teams", tags=["Admin - Teams"])
admin_router.include_router(videos_router, prefix="/videos", tags=["Admin - Videos"])
admin_router.include_router(audit_logs_router, prefix="/audit-logs", tags=["Admin - Audit Logs"])
admin_router.include_router(stats_router, prefix="/stats", tags=["Admin - Stats"])
admin_router.include_router(settings_router, prefix="/settings", tags=["Admin - Settings"])
admin_router.include_router(request_logs_router, prefix="/request-logs", tags=["Admin - Request Logs"])
admin_router.include_router(monitoring_router, prefix="/monitoring", tags=["Admin - Monitoring"])
admin_router.include_router(search_router, prefix="/search", tags=["Admin - Search"])
admin_router.include_router(alert_rules_router, prefix="/alerts", tags=["Admin - Alerts"])
