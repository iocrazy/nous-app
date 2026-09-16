"""``GET /ai-library/usage/efficiency`` 的响应（三期 3c §3.3）。

比率在服务端算一次：``tool_error_rate`` 与 ``cost_per_deliverable_cents`` 的分母为 0 时
含义不同——前者「没调过工具」的错误率是 0（确定没错），后者「没产出」的单价是 **null**
（不知道，不是免费）。放前端各算各的必然有一处写成 0。"""

from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class EfficiencyGroup(BaseModel):
    key: str
    label: str
    run_count: int = 0
    failed_runs: int = 0
    avg_run_ms: Optional[int] = None
    tool_calls: int = 0
    tool_errors: int = 0
    tool_error_rate: float = 0.0
    deliverables: int = 0
    cost_cents: float = 0.0
    cost_per_deliverable_cents: Optional[float] = None


class EfficiencyResponse(BaseModel):
    scope: str
    from_: datetime.datetime = Field(..., alias="from")
    to: datetime.datetime
    groups: List[EfficiencyGroup]
    turn_end_reasons: Dict[str, int]

    model_config = {"populate_by_name": True}


class RunCostRow(BaseModel):
    """One run's money line for ``GET /ai-library/runs/costs`` (3c §4.2).

    ``cost_cents`` and ``charged_points`` are both ``None``-able and mean
    different things by it: no recorded cost, versus nobody billed this run
    (BYOK, or billing switched off). Neither is 0.
    """

    cost_cents: Optional[float] = None
    charged_points: Optional[float] = None
    model: Optional[str] = None
    status: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class RunCostsResponse(BaseModel):
    """Keyed by run id as a STRING — these are snowflake BIGINTs, and JS loses
    precision past 2^53. A run the caller may not see is simply absent from
    ``items``; the batch is not refused over it."""

    items: Dict[str, RunCostRow]
