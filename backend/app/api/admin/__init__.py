"""Admin API routers."""

from fastapi import APIRouter

from .users_router import router as users_router
from .teams_router import router as teams_router

admin_router = APIRouter(prefix="/admin", tags=["Admin"])

admin_router.include_router(users_router, prefix="/users", tags=["Admin - Users"])
admin_router.include_router(teams_router, prefix="/teams", tags=["Admin - Teams"])
