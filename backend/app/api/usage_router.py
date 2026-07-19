"""AI Usage REST — token/cost observability for the Usage panel (W3c).

Range queries read ONLY the ai_usage_hourly rollup; the per-issue drill reads
agent_runs by its issue_id index. Team membership is validated server-side and
a cross-team lookup 404s (mirroring issues_router visibility) so existence
never leaks across the team boundary.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import AuthDep
from app.repositories import usage_repository
from app.repositories.team_repository import get_team_repository
from app.schemas.usage import (
    IssueUsageResponse,
    UsageDailyRow,
    UsageGroupRow,
    UsageSummaryResponse,
    UsageTotals,
)

router = APIRouter(prefix="/usage", tags=["Usage"])

_MAX_RANGE_DAYS = 366


def _parse_dt(value: Optional[str], *, default: datetime.datetime) -> datetime.datetime:
    """Parse a 'YYYY-MM-DD' or full-ISO string into a UTC-aware datetime.
    Blank/invalid → default."""
    if not value:
        return default
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


@router.get("/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    auth: AuthDep,
    team_id: str = Query(..., description="Team snowflake id (scope)"),
    frm: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    group_by: str = Query("model"),
):
    """Team AI usage over a window, grouped by one attribution dimension.

    group_by ∈ {agent, model, module, project, attribution}. 404 for
    non-members (no cross-team leakage)."""
    # Team-boundary gate: get_team_by_id returns None for non-members.
    team = await get_team_repository().get_team_by_id(team_id, auth.user_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found or access denied",
        )

    if group_by not in usage_repository.VALID_GROUP_BY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "group_by must be one of "
                f"{sorted(usage_repository.VALID_GROUP_BY)}"
            ),
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    to_dt = _parse_dt(to, default=now)
    frm_dt = _parse_dt(frm, default=to_dt - datetime.timedelta(days=30))
    if frm_dt >= to_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' must be before 'to'",
        )
    if (to_dt - frm_dt).days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"range exceeds {_MAX_RANGE_DAYS} days",
        )

    data = await usage_repository.summarize(
        team_id=team_id, frm=frm_dt, to=to_dt, group_by=group_by
    )
    total = data["total"]
    return UsageSummaryResponse(
        team_id=str(team_id),
        from_=frm_dt.isoformat(),
        to=to_dt.isoformat(),
        group_by=group_by,
        total=UsageTotals(
            prompt_tokens=_int(total.get("prompt_tokens")),
            completion_tokens=_int(total.get("completion_tokens")),
            total_tokens=_int(total.get("total_tokens")),
            cached_input_tokens=_int(total.get("cached_input_tokens")),
            cost_cents=_num(total.get("cost_cents")),
            event_count=_int(total.get("event_count")),
        ),
        groups=[
            UsageGroupRow(
                key=(str(r["grp"]) if r.get("grp") is not None else None),
                prompt_tokens=_int(r.get("prompt_tokens")),
                completion_tokens=_int(r.get("completion_tokens")),
                total_tokens=_int(r.get("total_tokens")),
                cost_cents=_num(r.get("cost_cents")),
                event_count=_int(r.get("event_count")),
            )
            for r in data["groups"]
        ],
        daily=[
            UsageDailyRow(
                day=str(r["day"]),
                key=(str(r["grp"]) if r.get("grp") is not None else None),
                total_tokens=_int(r.get("total_tokens")),
                cost_cents=_num(r.get("cost_cents")),
            )
            for r in data["daily"]
        ],
    )


@router.get("/issues/{issue_id}", response_model=IssueUsageResponse)
async def issue_usage(issue_id: int, auth: AuthDep):
    """Per-issue AI spend. Visibility follows the same team boundary as the
    issue itself (own OR assignee OR member of the issue's team); a cross-team
    lookup 404s."""
    from app.repositories.issue_repository import issue_repository

    row = await issue_repository.get_by_id(issue_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found"
        )

    user_id = str(auth.user_id)
    visible = (
        row.get("created_by_user_id") == user_id
        or row.get("assignee_user_id") == user_id
    )
    if not visible:
        team_id = row.get("team_id")
        if team_id is not None and await issue_repository.is_team_member(
            user_id, int(team_id)
        ):
            visible = True
    if not visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found"
        )

    totals = await usage_repository.issue_totals(issue_id)
    return IssueUsageResponse(
        issue_id=str(issue_id),
        prompt_tokens=_int(totals.get("prompt_tokens")),
        completion_tokens=_int(totals.get("completion_tokens")),
        total_tokens=_int(totals.get("total_tokens")),
        cost_cents=_num(totals.get("cost_cents")),
        run_count=_int(totals.get("run_count")),
    )
