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
from app.repositories.signal_sources_repository import (
    IMPLEMENTED_KINDS,
    SignalSourcesRepository,
)
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


class AdminSourceCreate(BaseModel):
    kind: str  # newsnow | rss (only kinds with a working adapter)
    name: str
    config: dict = {}
    category: Optional[str] = None
    tier: int = 2


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


@router.post("", response_model=AdminSourceOut)
async def create_source(body: AdminSourceCreate, auth: AdminAuthDep):
    """Create a SYSTEM source (visible to all users). ``kind`` is restricted to
    kinds with a working adapter (newsnow / rss); ``config`` is the adapter's
    shape — newsnow: {"platform_id": "..."}, rss: {"url": "..."}."""
    kind = (body.kind or "").strip()
    name = (body.name or "").strip()
    if kind not in IMPLEMENTED_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {', '.join(IMPLEMENTED_KINDS)}",
        )
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if not isinstance(body.config, dict):
        raise HTTPException(status_code=422, detail="config must be an object")
    if body.tier not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="tier must be 1, 2, or 3")
    # Validate the adapter's required config key up front (clear error vs a
    # source that silently fails every fetch).
    required = "platform_id" if kind == "newsnow" else "url"
    if not str(body.config.get(required) or "").strip():
        raise HTTPException(
            status_code=422, detail=f"{kind} source requires config.{required}"
        )
    row = await SignalSourcesRepository().admin_create_source(
        kind=kind,
        name=name,
        config=body.config,
        category=(body.category or None),
        tier=body.tier,
    )
    await create_audit_log(
        admin_id=auth.user_id,
        action="create_signal_source",
        target_type="signal_source",
        target_id=str(row.get("id")),
        details={"kind": kind, "name": name},
    )
    return _to_out(row)


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
