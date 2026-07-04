"""Admin API routes for AI usage detail — per-user, per-model agent_runs.

Surfaces the row-level telemetry RunRecorder already writes to ``agent_runs``
(who called which model, tokens, cost, status, latency) with filtering +
pagination + user-email enrichment. The aggregate snapshot lives at
``/api/v1/ai-library/admin/telemetry``; this is the drill-down.

Admin-gated via ``AdminAuthDep`` — non-admin gets 403.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.repositories.agent_runs_repository import get_agent_runs_repository
from app.utils.admin_helpers import batch_get_user_auth_info

router = APIRouter()


class AiUsageRunItem(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    agent_id: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    status: str
    trigger: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cents: float = 0.0
    duration_ms: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error_code: Optional[str] = None


class AiUsageListResponse(BaseModel):
    items: List[AiUsageRunItem]
    total: int
    page: int
    page_size: int


def _duration_ms(started: Optional[str], ended: Optional[str]) -> Optional[int]:
    """Milliseconds between started_at and ended_at. None if either is missing
    or unparseable. Values arrive as ISO strings (datetime) or datetime."""
    if not started or not ended:
        return None
    try:
        s = (
            started
            if isinstance(started, datetime)
            else datetime.fromisoformat(str(started))
        )
        e = ended if isinstance(ended, datetime) else datetime.fromisoformat(str(ended))
        delta = (e - s).total_seconds() * 1000.0
        return int(delta) if delta >= 0 else None
    except (ValueError, TypeError):
        return None


@router.get("/runs", response_model=AiUsageListResponse)
async def list_ai_usage_runs(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1, le=365),
    sort_by: Optional[str] = Query("started_at"),
    sort_order: Optional[str] = Query("desc"),
) -> AiUsageListResponse:
    """Paginated per-user × per-model agent_runs with filters.

    ``days`` (optional) windows the query to the last N days by ``started_at``.
    ``user_id`` / ``model`` / ``provider`` / ``status`` are exact-match filters.
    """
    parsed_user: Optional[UUID] = None
    if user_id:
        try:
            parsed_user = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="user_id must be a UUID")

    started_after: Optional[datetime] = None
    if days:
        started_after = datetime.now(timezone.utc) - timedelta(days=days)

    repo = get_agent_runs_repository()
    offset = (page - 1) * page_size
    result = await repo.list_runs_admin(
        user_id=parsed_user,
        model=model,
        provider=provider,
        status=status,
        started_after=started_after,
        sort_by=sort_by or "started_at",
        sort_desc=(sort_order != "asc"),
        offset=offset,
        limit=page_size,
    )
    rows = result["items"]
    total = result["total"]

    # Enrich user_id → email (batch, concurrent). tuple[0] is the email.
    uids = list({str(r["user_id"]) for r in rows if r.get("user_id")})
    email_map = await batch_get_user_auth_info(uids) if uids else {}

    items = [
        AiUsageRunItem(
            id=str(r.get("id")),
            user_id=str(r["user_id"]) if r.get("user_id") else None,
            user_email=(
                email_map.get(str(r["user_id"]), (None,))[0]
                if r.get("user_id")
                else None
            ),
            agent_id=str(r["agent_id"]) if r.get("agent_id") else None,
            model=r.get("model"),
            provider=r.get("provider"),
            status=r.get("status", ""),
            trigger=r.get("trigger"),
            prompt_tokens=int(r.get("prompt_tokens") or 0),
            completion_tokens=int(r.get("completion_tokens") or 0),
            total_tokens=int(r.get("total_tokens") or 0),
            cost_cents=float(r.get("cost_cents") or 0.0),
            duration_ms=_duration_ms(r.get("started_at"), r.get("ended_at")),
            started_at=str(r["started_at"]) if r.get("started_at") else None,
            ended_at=str(r["ended_at"]) if r.get("ended_at") else None,
            error_code=r.get("error_code"),
        )
        for r in rows
    ]

    return AiUsageListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/models", response_model=List[str])
async def list_ai_usage_models(auth: AdminAuthDep) -> List[str]:
    """Distinct model names present in agent_runs — populates the filter
    dropdown in the admin AI Usage page."""
    repo = get_agent_runs_repository()
    return await repo.distinct_models()
