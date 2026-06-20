from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.deps import (  # noqa: F401 — get_auth re-exported for test override
    AuthDep,
    get_auth,
)
from app.repositories.hotspots_repository import HotspotsRepository
from app.schemas.topics import DatesResponse, HotspotListResponse, HotspotOut
from app.services.storyboard.script.script_ai_service import ScriptAIService

router = APIRouter(prefix="/topics")


def _to_out(row: dict) -> HotspotOut:
    return HotspotOut(
        id=str(row.get("id")),
        title=row.get("title") or "",
        url=row.get("url"),
        origin_url=row.get("origin_url"),
        source_label=row.get("source_label"),
        summary=row.get("summary"),
        ai_summary=row.get("ai_summary"),
        reason=row.get("reason"),
        score=row.get("score"),
        tags=row.get("tags") or [],
        category=row.get("category"),
        media_url=row.get("media_url"),
        cover_url=row.get("cover_url"),
        captured_at=row.get("captured_at"),
    )


@router.get("", response_model=HotspotListResponse)
async def list_hotspots(
    auth: AuthDep,
    day: Optional[str] = Query(None, description="YYYY-MM-DD"),
    category: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=300),
):
    repo = HotspotsRepository()
    rows = await repo.list_for_date(day, category, limit=limit)
    items = [_to_out(r) for r in rows]
    return HotspotListResponse(count=len(items), hotspots=items)


@router.get("/dates", response_model=DatesResponse)
async def hotspot_dates(auth: AuthDep, limit_days: int = Query(60, ge=1, le=180)):
    repo = HotspotsRepository()
    return DatesResponse(dates=await repo.distinct_dates(limit_days))


async def _generate_script_for(title: str, summary: str, user_id: str) -> list:
    """Reuse the existing script_ai agent. Real signature (verified):
    ScriptAIService(user_id=...).generate_outline(premise) -> List[Dict[str,str]]."""
    svc = ScriptAIService(user_id=user_id)
    premise = f"Topic: {title}\n\nContext: {summary or ''}"
    return await svc.generate_outline(premise)


@router.post("/{hotspot_id}/generate-script")
async def generate_script(hotspot_id: str, auth: AuthDep):
    repo = HotspotsRepository()
    row = await repo.get_by_id(hotspot_id)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    script = await _generate_script_for(
        row.get("title") or "",
        row.get("ai_summary") or row.get("summary") or "",
        auth.user_id,
    )
    return {"success": True, "script": script}
