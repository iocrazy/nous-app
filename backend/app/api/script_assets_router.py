"""Script Assets Router — CRUD endpoints for script asset entities."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_script_access, verify_script_read_access
from app.schemas.script import ScriptAssetCreate, ScriptAssetUpdate
from app.services.storyboard.script.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


@router.post("/{script_id}/assets")
async def create_asset(
    auth: AuthDep, script_id: str, body: ScriptAssetCreate
) -> Dict[str, Any]:
    """Create a new script asset (worldview, character, location, prop, plot_point)."""
    try:
        # Delegates to the shared, tested script-team guard (this router used
        # to carry its own private copy — a strict get_team_id_for_user()
        # equality check that 403'd every teammate on a shared-team script;
        # replaced 2026-08-12 to match the rest of the script/scene/shot
        # family and get its personal-project project_members fallback).
        await verify_script_access(script_id, auth)
        svc = ScriptService()
        data = {**body.model_dump(exclude_none=True), "script_id": script_id}
        asset = await svc.create_asset(data)
        return {"success": True, "data": asset}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAssets] create_asset failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create asset")


@router.get("/{script_id}/assets")
async def list_assets(
    auth: AuthDep,
    script_id: str,
    asset_type: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """List all assets for a script project, optionally filtered by type."""
    try:
        await verify_script_read_access(script_id, auth)
        svc = ScriptService()
        assets = await svc.list_assets(script_id, asset_type=asset_type)
        return {"success": True, "data": assets}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAssets] list_assets failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list assets")


@router.put("/assets/{asset_id}")
async def update_asset(
    auth: AuthDep, asset_id: str, body: ScriptAssetUpdate
) -> Dict[str, Any]:
    """Update an existing script asset."""
    try:
        svc = ScriptService()
        asset = await svc.update_asset(asset_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": asset}
    except Exception as exc:
        logger.error(f"[ScriptAssets] update_asset {asset_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update asset")


@router.delete("/assets/{asset_id}")
async def delete_asset(auth: AuthDep, asset_id: str) -> Dict[str, Any]:
    """Delete a script asset."""
    try:
        svc = ScriptService()
        await svc.delete_asset(asset_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[ScriptAssets] delete_asset {asset_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete asset")
