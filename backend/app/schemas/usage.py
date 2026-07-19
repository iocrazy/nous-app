"""Schemas for the AI Usage panel (W3c)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class UsageTotals(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cost_cents: float = 0.0
    event_count: int = 0


class UsageGroupRow(BaseModel):
    """One attribution-dimension bucket's totals over the window."""

    key: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cents: float = 0.0
    event_count: int = 0


class UsageDailyRow(BaseModel):
    """One (day, group-key) point for the stacked time series."""

    day: str
    key: Optional[str] = None
    total_tokens: int = 0
    cost_cents: float = 0.0


class UsageSummaryResponse(BaseModel):
    team_id: str
    from_: str = Field(..., alias="from")
    to: str
    group_by: str
    total: UsageTotals
    groups: List[UsageGroupRow]
    daily: List[UsageDailyRow]

    model_config = {"populate_by_name": True}


class IssueUsageResponse(BaseModel):
    issue_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cents: float = 0.0
    run_count: int = 0
