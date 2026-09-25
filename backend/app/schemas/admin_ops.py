"""Response models for the admin panel's operations routes (OpenAPI P8, D2).

Covers ``/admin/transcode`` (retry / batch), ``/admin/tasks`` (cancel /
retry), ``/admin/jimeng`` (status / login / logout), ``/admin/celery``
(workers / queues) and the two ``/ai-library/admin`` routes the admin panel
calls (reload-seeds / telemetry).

Each model declares exactly the keys the handler already sent; the wire
tests in ``tests/api/admin/test_admin_ops_wire.py`` compare every route with
``jsonable_encoder`` of the dict it used to return. Keys that only some
branches send are optional here and the route sets
``response_model_exclude_unset`` so an absent key stays absent.

Every class carries the ``Admin`` prefix: OpenAPI component names are global.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ── /admin/transcode ─────────────────────────────────────────────────────


class AdminTranscodeRetryResponse(BaseModel):
    message: str
    # The path parameter echoed back as given (a decimal Snowflake string).
    version_id: str


class AdminTranscodeBatchResponse(BaseModel):
    message: str
    total_found: int
    queued: int
    # True when the working set hit its cap: call again to drain the rest.
    has_more: bool


# ── /admin/tasks ─────────────────────────────────────────────────────────


class AdminTaskActionResponse(BaseModel):
    """Cancel and retry both answer ``{message, task_id}``."""

    message: str
    # ``task_tracking.dbos_workflow_id`` as given in the path.
    task_id: str


# ── /admin/jimeng ────────────────────────────────────────────────────────


class AdminJimengStatus(BaseModel):
    """``credit`` only when the CLI printed one; ``error`` only when not
    logged in and the provider classified why (an error code, never CLI
    output)."""

    logged_in: bool
    credit: int | None = None
    error: str | None = None


class AdminJimengLoginResponse(BaseModel):
    """``status='already'`` carries nothing else. ``status='pending'`` carries
    the device-flow material the operator needs to authorize: the link, the
    code to type, and when the window closes. The ``device_code`` (what the
    polling client redeems for a token) stays on the server."""

    status: Literal["pending", "already"]
    verification_uri: str | None = None
    user_code: str | None = None
    expires_at: str | None = None


class AdminJimengLogoutResponse(BaseModel):
    logged_in: bool


# ── /admin/celery (DBOS-backed since Celery was removed) ─────────────────


class AdminWorkerInfo(BaseModel):
    name: str
    status: str
    active: int
    # DBOS keeps no lifetime count here: always null.
    processed: int | None
    concurrency: int | None
    uptime: int | None


class AdminWorkersResponse(BaseModel):
    online: int
    total: int
    workers: list[AdminWorkerInfo]


class AdminQueueDepth(BaseModel):
    name: str
    messages: int


class AdminQueuesResponse(BaseModel):
    queues: list[AdminQueueDepth]


# ── /ai-library/admin/reload-seeds ───────────────────────────────────────


class AdminSeedCounters(BaseModel):
    agents: int
    skills: int
    skill_files: int


class AdminSeedLoadError(BaseModel):
    scope: str
    slug: str
    # ``{type, message, code?, details?, hint?, status?}`` from the exception.
    error: dict[str, Any]


class AdminSeedReloadResponse(BaseModel):
    agents: int
    skills: int
    agent_skill_bindings: int
    skipped: AdminSeedCounters
    upserted: AdminSeedCounters
    errors: list[AdminSeedLoadError]


# ── /ai-library/admin/telemetry ──────────────────────────────────────────


class AdminTelemetryOverview(BaseModel):
    total_runs: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_cents: float
    unique_agents: int
    unique_users: int


class AdminTelemetryTopAgent(BaseModel):
    # A UUID string, or ``"?"`` for a row with no agent.
    agent_id: str
    run_count: int
    cost_cents: float
    total_tokens: int


class AdminTelemetryTopUser(BaseModel):
    # A UUID string, or ``"?"`` for a row with no user.
    user_id: str
    run_count: int
    cost_cents: float
    total_tokens: int


class AdminTelemetryDailyPoint(BaseModel):
    # ``YYYY-MM-DD``, or ``"unknown"`` for a row with no start time.
    date: str
    runs: int
    cost_cents: float
    prompt_tokens: int
    completion_tokens: int


class AdminTelemetryFailureMode(BaseModel):
    error_code: str
    count: int


class AdminAgentTelemetry(BaseModel):
    window_days: int
    # Already ISO strings (``datetime.isoformat()``, ``+00:00``) in the handler.
    window_start: str
    window_end: str
    overview: AdminTelemetryOverview
    status_breakdown: dict[str, int]
    top_agents: list[AdminTelemetryTopAgent]
    top_users: list[AdminTelemetryTopUser]
    daily_trend: list[AdminTelemetryDailyPoint]
    failure_modes: list[AdminTelemetryFailureMode]
