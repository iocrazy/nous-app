"""Admin table preferences CRUD — server-side persistence for Notion-style table config."""

from fastapi import APIRouter, HTTPException

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.schemas.admin import (
    AdminTablePreferenceResponse,
    AdminTablePreferenceUpdate,
)

router = APIRouter()

VALID_TABLE_KEYS = {
    "media",
    "tasks",
    "transcode",
    "users",
    "teams",
    "audit-logs",
    "request-logs",
    "frontend-errors",
    "app-logs",
}


def _validate_table_key(table_key: str) -> None:
    if table_key not in VALID_TABLE_KEYS:
        raise HTTPException(
            status_code=400, detail=f"Invalid table_key: {table_key}"
        )


@router.get("/{table_key}", response_model=AdminTablePreferenceResponse)
async def get_table_preferences(table_key: str, auth: AdminAuthDep):
    """Retrieve saved table preferences for the current admin user."""
    _validate_table_key(table_key)
    supabase = await get_async_supabase_admin()
    result = (
        await supabase.table("admin_table_preferences")
        .select("table_key, filters, sorts, visible_columns, column_order")
        .eq("user_id", str(auth.user_id))
        .eq("table_key", table_key)
        .limit(1)
        .execute()
    )
    if result.data:
        return AdminTablePreferenceResponse(**result.data[0])
    return AdminTablePreferenceResponse(table_key=table_key)


@router.put("/{table_key}", response_model=AdminTablePreferenceResponse)
async def upsert_table_preferences(
    table_key: str,
    body: AdminTablePreferenceUpdate,
    auth: AdminAuthDep,
):
    """Create or update table preferences for the current admin user."""
    _validate_table_key(table_key)
    supabase = await get_async_supabase_admin()
    payload = {
        "user_id": str(auth.user_id),
        "table_key": table_key,
        "filters": [f.model_dump() for f in body.filters],
        "sorts": [s.model_dump() for s in body.sorts],
        "visible_columns": body.visible_columns,
        "column_order": body.column_order,
    }
    result = (
        await supabase.table("admin_table_preferences")
        .upsert(payload, on_conflict="user_id,table_key")
        .execute()
    )
    if result.data:
        return AdminTablePreferenceResponse(**result.data[0])
    raise HTTPException(status_code=500, detail="Failed to save preferences")


@router.delete("/{table_key}")
async def delete_table_preferences(table_key: str, auth: AdminAuthDep):
    """Delete saved table preferences for a specific table."""
    _validate_table_key(table_key)
    supabase = await get_async_supabase_admin()
    await (
        supabase.table("admin_table_preferences")
        .delete()
        .eq("user_id", str(auth.user_id))
        .eq("table_key", table_key)
        .execute()
    )
    return {"ok": True}
