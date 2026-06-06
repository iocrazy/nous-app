"""Admin API routes for System Settings management."""

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.schemas.admin import SystemSettingResponse, SystemSettingUpdate
from app.utils.admin_helpers import create_audit_log

router = APIRouter()


def _to_response(row: dict) -> SystemSettingResponse:
    return SystemSettingResponse(
        key=row["key"],
        value=row["value"],
        description=row.get("description"),
        updated_at=row["updated_at"],
        updated_by=row.get("updated_by"),
    )


@router.get("", response_model=list[SystemSettingResponse])
async def list_settings(auth: AdminAuthDep):
    """List all system settings (excluding transcode_*, managed elsewhere)."""
    repo = get_system_settings_repository()
    rows = await repo.list_non_transcode()
    return [_to_response(r) for r in rows]


@router.patch("/{key}", response_model=SystemSettingResponse)
async def update_setting(
    key: str,
    update: SystemSettingUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Update a system setting by key."""
    repo = get_system_settings_repository()

    if not await repo.exists(key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found",
        )

    updated = await repo.update(key, update.value, auth.user_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update setting",
        )

    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_setting",
        target_type="system_setting",
        target_id=key,
        details={"value": update.value},
        ip_address=client_ip,
    )

    logger.info(f"Setting '{key}' updated by admin {auth.user_id}")
    return _to_response(updated)
