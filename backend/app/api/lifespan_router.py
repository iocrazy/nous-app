"""Liveness + readiness endpoints.

Distinct from the existing `/health` family:

* `/api/v1/healthz` — **liveness**. 200 the instant lifespan reaches
  `yield`. No DB calls, no external probes. If you can hit this and get
  200, the FastAPI worker is alive and accepting connections; whatever
  your client problem is, it isn't "the backend died".

* `/api/v1/readyz` — **readiness**. 200 only when every FINITE background
  task spawned during lifespan has finished AND every `long_running` daemon
  (reap sweep, stall detector) is still alive. Used by load balancers /
  orchestrators that want to delay routing traffic until first-time setup
  (seed loader, schema probe, etc.) settles. Returns 503 + per-task status
  while bg work is pending ("starting") or a daemon has crashed
  ("degraded") so dashboards can see what's holding it up.

The split lets the dev supervisor distinguish "starting up — wait" from
"wedged — kill". Matches k8s probe semantics.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Request, Response, status

router = APIRouter(tags=["Health"])


def _dbos_readiness() -> str:
    """Classify DBOS orchestrator state for the readiness gate.

    Three states, because "not enabled" alone can't tell an intentionally
    DBOS-less deployment from a broken one:

    * ``not_configured`` — no DSN. A supported deployment shape (see
      docker/docker-compose.yml: empty value → "DBOS disabled" warning), so
      it must NOT degrade readiness.
    * ``enabled`` — DSN set and the singleton/client is live.
    * ``configured_but_disabled`` — DSN set but the engine never came up.
      Always a real fault: every workflow dispatch 500s. The 2026-07-22 outage.

    Uses ``is_launched()``, NOT ``is_enabled()``: the latter only reports that a
    handle object exists. On the worker role ``init_dbos`` assigns the singleton
    before any DB I/O, so a failed ``DBOS.launch()`` leaves ``is_enabled()``
    True — verified in a throwaway container, where the first version of this
    gate still answered 200 dbos=enabled with the DSN pointed at the dead port.

    Imported lazily to keep this probe module free of service-layer imports.
    """
    if not os.environ.get("DBOS_DATABASE_URL", "").strip():
        return "not_configured"

    from app.services.infra import dbos_orchestrator

    return "enabled" if dbos_orchestrator.is_launched() else "configured_but_disabled"


async def _connection_pressure() -> Dict[str, Any]:
    """Latest Postgres connection-slot reading, for the readiness payload.

    Reads the CACHE the 5-minute sampler writes — it does not query Postgres.
    ``/readyz`` is hit by container healthchecks on a tight cadence, and a
    probe that opens a database connection to report on database connection
    exhaustion is the failure mode it exists to detect.

    Absent/expired cache returns ``{"status": "unknown"}`` rather than
    omitting the key or defaulting to a healthy-looking zero: "the sampler
    is not reporting" is itself information, and a probe that answers "fine"
    when it has no reading is worse than one that answers nothing.

    Never raises — a monitoring field must not be able to fail the probe it
    rides along on.
    """
    try:
        from app.services.infra.pg_connection_monitor import get_cached_sample

        cached = await get_cached_sample()
    except Exception:  # noqa: BLE001
        return {"status": "unknown", "reason": "sampler cache unavailable"}

    if not cached:
        return {"status": "unknown", "reason": "no recent sample"}
    return {
        "status": cached.get("status", "unknown"),
        "used": cached.get("used"),
        "max": cached.get("max_connections"),
        "percent": cached.get("percent"),
        "sampled_at": cached.get("sampled_at"),
    }


@router.get("/healthz", summary="Liveness probe (always 200 if process is alive)")
async def healthz() -> Dict[str, str]:
    """Always returns 200 with `{"status": "alive"}`.

    No DB, no external calls. The point is that *if you got a response*,
    lifespan completed and the worker is accepting connections.
    """
    return {"status": "alive"}


@router.get("/readyz", summary="Readiness probe (200 once bg startup settles)")
async def readyz(request: Request, response: Response) -> Dict[str, Any]:
    """200 once finite bg tasks are done and daemons are alive; 503 otherwise.

    503 payload distinguishes "starting" (finite startup work still pending)
    from "degraded" (a long_running daemon crashed). Includes per-task
    `done`/`long_running`/`duration_seconds`/`error` so it's useful both as
    a probe target and a debug endpoint.

    Also carries a `connections` block (Postgres connection-slot pressure).
    That block is REPORTED, never GATED — see `_connection_pressure`.
    """
    registry = getattr(request.app.state, "bg_tasks", None)
    if registry is None:
        # Lifespan didn't run / aborted — fail loud.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "reason": "background task registry missing on app.state",
            "tasks": [],
        }

    return await _readyz_payload(registry, response)


async def _readyz_payload(registry: Any, response: Response) -> Dict[str, Any]:
    """The verdict half of /readyz, separable from the app-state lookup so a
    registry in a known state can be asserted on directly."""
    dbos_state = _dbos_readiness()
    dbos_broken = dbos_state == "configured_but_disabled"

    snapshot = registry.status_snapshot()
    ready = registry.all_done() and not dbos_broken
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    if ready:
        verdict = "ready"
    elif registry.dead_daemons() or registry.failed_fatal_gates() or dbos_broken:
        verdict = "degraded"
    else:
        verdict = "starting"

    return {
        "status": verdict,
        "dbos": dbos_state,
        # ⚠️ INFORMATIONAL ONLY — deliberately computed AFTER `verdict` and
        # `response.status_code` are already decided, so there is no code path
        # by which it can change either. Connection pressure must not gate
        # readiness: a 503 here triggers the deploy chain's automatic rollback
        # (deploy-gpu.yml), which restarts containers, which opens MORE pools —
        # the response to the alarm would feed the fire. Same family as the
        # `long_running`/`gates_readiness` lesson: something worth reporting is
        # not automatically something worth failing on.
        "connections": await _connection_pressure(),
        "tasks": snapshot,
    }
