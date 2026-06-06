# backend/app/api/admin/nous_router.py

"""
Admin API for managing Nous platform-provided AI models.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.nous_repository import get_nous_repository
from app.schemas.nous import (
    NousModelCreate,
    NousModelResponse,
    NousModelUpdate,
)

router = APIRouter()


def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _to_response(row: dict) -> NousModelResponse:
    """Convert DB row to admin response with masked API key."""
    return NousModelResponse(
        id=str(row["id"]),
        name=row["name"],
        display_name=row["display_name"],
        category=row["category"],
        actual_provider=row["actual_provider"],
        actual_model=row["actual_model"],
        api_key_masked=_mask_key(row.get("api_key", "")),
        app_id=row.get("app_id"),
        base_url=row.get("base_url"),
        pricing_type=row["pricing_type"],
        pricing_value=float(row["pricing_value"]),
        is_enabled=row["is_enabled"],
        sort_order=row["sort_order"],
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )


@router.get("", response_model=List[NousModelResponse])
async def list_nous_models(auth: AdminAuthDep):
    """List all Nous models (including disabled)."""
    repo = get_nous_repository()
    rows = await repo.list_all()
    return [_to_response(r) for r in rows]


@router.post("", response_model=NousModelResponse)
async def create_nous_model(body: NousModelCreate, auth: AdminAuthDep):
    """Create a new Nous model."""
    repo = get_nous_repository()
    row = await repo.create(body.model_dump())
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create model")
    logger.info(f"[Admin] Created Nous model: {body.name}")
    return _to_response(row)


@router.put("/{model_id}", response_model=NousModelResponse)
async def update_nous_model(model_id: str, body: NousModelUpdate, auth: AdminAuthDep):
    """Update a Nous model."""
    repo = get_nous_repository()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = await repo.update(model_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Updated Nous model: {model_id}")
    return _to_response(row)


@router.delete("/{model_id}")
async def delete_nous_model(model_id: str, auth: AdminAuthDep):
    """Delete a Nous model."""
    repo = get_nous_repository()
    ok = await repo.delete(model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Deleted Nous model: {model_id}")
    return {"message": "Deleted"}
