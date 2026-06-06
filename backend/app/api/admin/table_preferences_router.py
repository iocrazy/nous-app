"""Admin table preferences CRUD — server-side persistence for Notion-style table config."""

from fastapi import APIRouter, HTTPException

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.table_preferences_repository import (
    get_admin_table_preferences_repository,
)
from app.schemas.admin import (
    AdminTablePreferenceResponse,
    AdminTablePreferenceUpdate,
)

router = APIRouter()

VALID_TABLE_KEYS = {
    "admin_media",
    "tasks",
    "transcode",
    "users",
    "teams",
    "audit-logs",
    "request-logs",
    "frontend-errors",
    "app-logs",
    "credits-transactions",
    "credits-orders",
}


def _validate_table_key(table_key: str) -> None:
    if table_key not in VALID_TABLE_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid table_key: {table_key}")


@router.get("/{table_key}", response_model=AdminTablePreferenceResponse)
async def get_table_preferences(table_key: str, auth: AdminAuthDep):
    """Retrieve saved table preferences for the current admin user."""
    _validate_table_key(table_key)
    repo = get_admin_table_preferences_repository()
    row = await repo.get(str(auth.user_id), table_key)
    if row:
        return AdminTablePreferenceResponse(**row)
    return AdminTablePreferenceResponse(table_key=table_key)


@router.put("/{table_key}", response_model=AdminTablePreferenceResponse)
async def upsert_table_preferences(
    table_key: str,
    body: AdminTablePreferenceUpdate,
    auth: AdminAuthDep,
):
    """Create or update table preferences for the current admin user."""
    _validate_table_key(table_key)
    repo = get_admin_table_preferences_repository()
    row = await repo.upsert(
        user_id=str(auth.user_id),
        table_key=table_key,
        filters=[f.model_dump() for f in body.filters],
        sorts=[s.model_dump() for s in body.sorts],
        visible_columns=body.visible_columns,
        column_order=body.column_order,
    )
    if row:
        return AdminTablePreferenceResponse(**row)
    raise HTTPException(status_code=500, detail="Failed to save preferences")


@router.delete("/{table_key}")
async def delete_table_preferences(table_key: str, auth: AdminAuthDep):
    """Delete saved table preferences for a specific table."""
    _validate_table_key(table_key)
    repo = get_admin_table_preferences_repository()
    await repo.delete(str(auth.user_id), table_key)
    return {"ok": True}
