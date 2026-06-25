"""Admin API routes for Topic-Inspiration signal sources.

Admin-global control of signal sources: enable/disable (disabling stops
collection AND drops the source's hotspots from every user's feed) and set the
credibility tier (the scoring prior). Distinct from the user-facing per-client
"hide" (which only affects one user and keeps the source collecting).
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.repositories.signal_sources_repository import SignalSourcesRepository
from app.utils.admin_helpers import create_audit_log

router = APIRouter()


class AdminSourceOut(BaseModel):
    id: str
    name: str
    kind: str
    category: Optional[str] = None
    enabled: bool = True
    tier: int = 2
    health: str = "ok"
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_ok_at: Optional[str] = None
    is_system: bool = True  # user_id IS NULL → platform source


class AdminSourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    tier: Optional[int] = None


class AdminSourceListResponse(BaseModel):
    count: int
    sources: List[AdminSourceOut]


def _to_out(row: dict) -> AdminSourceOut:
    return AdminSourceOut(
        id=str(row.get("id")),
        name=row.get("name") or "",
        kind=row.get("kind") or "",
        category=row.get("category"),
        enabled=bool(row.get("enabled", True)),
        tier=int(row.get("tier") or 2),
        health=row.get("health") or "ok",
        consecutive_failures=int(row.get("consecutive_failures") or 0),
        last_error=row.get("last_error"),
        last_ok_at=row.get("last_ok_at"),
        is_system=row.get("user_id") is None,
    )


@router.get("", response_model=AdminSourceListResponse)
async def list_sources(auth: AdminAuthDep):
    """All signal sources (enabled + disabled), worst-health-first."""
    rows = await SignalSourcesRepository().list_all()
    return AdminSourceListResponse(count=len(rows), sources=[_to_out(r) for r in rows])


@router.patch("/{source_id}", response_model=AdminSourceOut)
async def update_source(source_id: str, body: AdminSourceUpdate, auth: AdminAuthDep):
    """Toggle a source's global ``enabled`` and/or set its ``tier`` (1/2/3).
    Disabling stops collection and removes its hotspots from every user's feed."""
    if body.tier is not None and body.tier not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="tier must be 1, 2, or 3")
    repo = SignalSourcesRepository()
    row = await repo.admin_update(source_id, enabled=body.enabled, tier=body.tier)
    if not row:
        raise HTTPException(status_code=404, detail="source not found")
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_signal_source",
        target_type="signal_source",
        target_id=source_id,
        details={"enabled": body.enabled, "tier": body.tier},
    )
    return _to_out(row)
