"""Admin API routes for System Settings management."""

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.schemas.admin import SystemSettingResponse, SystemSettingUpdate
from app.utils.admin_helpers import create_audit_log


router = APIRouter()


@router.get("", response_model=list[SystemSettingResponse])
async def list_settings(auth: AdminAuthDep):
    """List all system settings."""
    supabase = await get_async_supabase_admin()

    result = await supabase.table("system_settings").select("*").order("key").execute()

    # Exclude transcode_* keys — managed by dedicated Transcode Config page
    return [
        SystemSettingResponse(
            key=s["key"],
            value=s["value"],
            description=s.get("description"),
            updated_at=s["updated_at"],
            updated_by=s.get("updated_by"),
        )
        for s in (result.data or [])
        if not s["key"].startswith("transcode_")
    ]


@router.patch("/{key}", response_model=SystemSettingResponse)
async def update_setting(
    key: str,
    update: SystemSettingUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Update a system setting by key."""
    supabase = await get_async_supabase_admin()

    # Check if setting exists
    existing = await supabase.table("system_settings").select("key").eq("key", key).single().execute()
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found",
        )

    # Update the setting
    result = await (
        supabase.table("system_settings")
        .update({"value": update.value, "updated_by": auth.user_id})
        .eq("key", key)
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update setting",
        )

    # Audit log
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

    s = result.data[0]
    return SystemSettingResponse(
        key=s["key"],
        value=s["value"],
        description=s.get("description"),
        updated_at=s["updated_at"],
        updated_by=s.get("updated_by"),
    )
