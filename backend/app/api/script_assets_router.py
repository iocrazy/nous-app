"""Script Assets Router — CRUD endpoints for script asset entities."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user
from app.schemas.script import ScriptAssetCreate, ScriptAssetUpdate
from app.services.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


async def _verify_script_access(script_id: str, user_id: str) -> None:
    """Verify the authenticated user has access to this script (via team membership)."""
    svc = ScriptService()
    project = await svc.project_repo.get_by_id(script_id)
    if not project:
        raise HTTPException(status_code=404, detail="Script project not found")
    user_team = await get_team_id_for_user(user_id)
    if str(user_team) != str(project.get("team_id")):
        raise HTTPException(status_code=403, detail="Access denied")


@router.post("/{script_id}/assets")
async def create_asset(
    auth: AuthDep, script_id: str, body: ScriptAssetCreate
) -> Dict[str, Any]:
    """Create a new script asset (worldview, character, location, prop, plot_point)."""
    try:
        await _verify_script_access(script_id, auth.user_id)
        svc = ScriptService()
        data = {**body.model_dump(exclude_none=True), "script_id": script_id}
        asset = await svc.create_asset(data)
        return {"success": True, "data": asset}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[ScriptAssets] create_asset failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to create asset"
        )


@router.get("/{script_id}/assets")
async def list_assets(
    auth: AuthDep,
    script_id: str,
    asset_type: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """List all assets for a script project, optionally filtered by type."""
    try:
        await _verify_script_access(script_id, auth.user_id)
        svc = ScriptService()
        assets = await svc.list_assets(script_id, asset_type=asset_type)
        return {"success": True, "data": assets}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[ScriptAssets] list_assets failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to list assets"
        )


@router.put("/assets/{asset_id}")
async def update_asset(
    auth: AuthDep, asset_id: str, body: ScriptAssetUpdate
) -> Dict[str, Any]:
    """Update an existing script asset."""
    try:
        svc = ScriptService()
        asset = await svc.update_asset(
            asset_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": asset}
    except Exception as exc:
        logger.error("[ScriptAssets] update_asset %s failed: %s", asset_id, exc)
        raise HTTPException(
            status_code=500, detail="Failed to update asset"
        )


@router.delete("/assets/{asset_id}")
async def delete_asset(auth: AuthDep, asset_id: str) -> Dict[str, Any]:
    """Delete a script asset."""
    try:
        svc = ScriptService()
        await svc.delete_asset(asset_id)
        return {"success": True}
    except Exception as exc:
        logger.error("[ScriptAssets] delete_asset %s failed: %s", asset_id, exc)
        raise HTTPException(
            status_code=500, detail="Failed to delete asset"
        )
