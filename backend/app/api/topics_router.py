from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.deps import (  # noqa: F401 — get_auth re-exported for test override
    AuthDep,
    get_auth,
)
from app.repositories.hotspots_repository import HotspotsRepository
from app.repositories.signal_sources_repository import SignalSourcesRepository
from app.schemas.topics import (
    DatesResponse,
    HotspotListResponse,
    HotspotOut,
    SourceHealthOut,
    SourceHealthResponse,
)
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
    q: Optional[str] = Query(None, description="free-text search over hotspots"),
    limit: int = Query(100, ge=1, le=300),
):
    repo = HotspotsRepository()
    # When searching, span all dates — a topic is found regardless of which
    # day it landed on. The day filter only applies to plain browsing.
    effective_day = None if (q and q.strip()) else day
    rows = await repo.list_for_date(effective_day, category, limit=limit, q=q)
    items = [_to_out(r) for r in rows]
    return HotspotListResponse(count=len(items), hotspots=items)


@router.get("/dates", response_model=DatesResponse)
async def hotspot_dates(auth: AuthDep, limit_days: int = Query(60, ge=1, le=180)):
    repo = HotspotsRepository()
    return DatesResponse(dates=await repo.distinct_dates(limit_days))


def _to_health_out(row: dict) -> SourceHealthOut:
    return SourceHealthOut(
        id=str(row.get("id")),
        name=row.get("name") or "",
        kind=row.get("kind") or "",
        category=row.get("category"),
        enabled=bool(row.get("enabled", True)),
        health=row.get("health") or "ok",
        consecutive_failures=int(row.get("consecutive_failures") or 0),
        last_error=row.get("last_error"),
        last_fetched_at=row.get("last_fetched_at"),
        last_ok_at=row.get("last_ok_at"),
    )


@router.get("/sources/health", response_model=SourceHealthResponse)
async def source_health(auth: AuthDep):
    """Read-only health of all signal sources (worst-first). Surfaces which
    feeds have gone degraded/dead so a maintainer can react. No mutation."""
    repo = SignalSourcesRepository()
    rows = await repo.list_all()
    sources = [_to_health_out(r) for r in rows]
    return SourceHealthResponse(count=len(sources), sources=sources)


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
