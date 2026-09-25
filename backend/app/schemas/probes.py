"""Response shapes of the health / version / DBOS probe endpoints.

Each model declares exactly the keys its handler already sends; wire parity
is pinned by ``tests/api/test_probes_wire.py``.

``/api/v1/readyz`` is special: it is the deploy smoke's only trusted probe
(``deploy-gpu.yml`` rolls back on its 503) and its body has two shapes, so its
models are **declared, not enforced** — the route keeps ``response_model=None``
and names :class:`ReadyzResponse` only in ``responses=``. A declaration can
never turn a readiness verdict into a 500. ``extra="forbid"`` on these models
exists for the conformance test, which validates the real payloads against
them so the declaration cannot drift from what the handler builds.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class RootHealthResponse(BaseModel):
    """``GET /health``: hard-coded liveness string (Dockerfile HEALTHCHECK).

    Not a readiness signal — see ``/api/v1/readyz``.
    """

    status: str
    message: str


class ApiVersionResponse(BaseModel):
    """``GET /api/version``: build identity from ``/app/build-info.json``.

    Without that file (every gpupc image today) the body is only
    ``{commit_sha: null, available: false}``; the route declares
    ``response_model_exclude_unset`` so the other keys stay absent.
    """

    commit_sha: str | None
    commit_count: int | None = None
    service: str | None = None
    version: str | None = None
    available: bool


class DbosHealthResponse(BaseModel):
    """``GET /api/v1/dbos/health``.

    ``enabled`` is ``is_enabled()``: a handle exists, NOT that the engine
    launched. The readiness verdict is ``/api/v1/readyz``'s ``dbos`` field.
    """

    enabled: bool


class DbosRoutingResponse(BaseModel):
    """``GET /api/v1/dbos/routing`` (platform admins).

    ``loaded_at`` is the event-loop clock (seconds, monotonic) of the last
    refresh; ``task_types`` is sorted ``[task_type, mode]`` pairs.
    """

    loaded_at: float
    task_types: list[tuple[str, str]]


class DeepHealthProbe(BaseModel):
    status: Literal["ok", "degraded", "down"]
    message: str
    ms: int


class DeepHealthInProcess(BaseModel):
    """In-process registry snapshots. A snapshot that raised is reported as
    ``{"error": "<Type>: <message>"}`` in place of its value, and one whose
    registry is not on ``app.state`` is ``null``."""

    process_role: str | None
    bounds: dict[str, Any] | None
    hooks_registered: int
    context_engines: list[str]
    model_health_snapshot: dict[str, Any] | None
    lane_queue_depth: dict[str, Any] | None
    agent_metrics_keys_in_use: int | dict[str, Any] | None
    prometheus_pusher_active: bool
    root_aborts_count: int | dict[str, Any] | None


class DeepHealthResponse(BaseModel):
    """``GET /api/v1/health/deep`` (platform admins)."""

    status: Literal["healthy", "degraded", "unhealthy"]
    probes: dict[str, DeepHealthProbe]
    in_process: DeepHealthInProcess
    version: str


class ReadyzTask(BaseModel):
    """One background task from ``BackgroundTaskRegistry.status_snapshot``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    done: bool
    long_running: bool
    gates_readiness: bool
    fatal: bool
    duration_seconds: float | None
    error: str | None


class ReadyzConnections(BaseModel):
    """Postgres connection-slot pressure, read from the sampler's cache.

    REPORTED, never gated. ``{"status": "unknown", "reason": …}`` when there
    is no recent sample; otherwise the five reading keys.
    """

    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = None
    used: int | None = None
    max: int | None = None
    percent: float | None = None
    sampled_at: float | None = None


class ReadyzResponse(BaseModel):
    """``GET /api/v1/readyz`` — 200 when ``status == "ready"``, else 503.

    ``dbos`` is three-state; ``configured_but_disabled`` is always a fault.
    ``not_ready`` (lifespan never ran) carries only ``status`` / ``reason`` /
    ``tasks``; every other verdict carries ``dbos`` / ``connections`` /
    ``tasks`` and no ``reason``.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "starting", "degraded", "not_ready"]
    dbos: Literal["enabled", "not_configured", "configured_but_disabled"] | None = None
    reason: str | None = None
    connections: ReadyzConnections | None = None
    tasks: list[ReadyzTask]
