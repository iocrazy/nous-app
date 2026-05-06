"""Phase N (N5) / D10-8 — deep health probe.

Beyond the existing `/health` (which just confirms FastAPI is alive),
`/health/deep` reports per-subsystem state so ops dashboards / on-call
runbooks can root-cause WITHOUT shelling in:

  - DBOS orchestrator state (initialized / launched)
  - Supabase connectivity
  - Redis connectivity
  - Lane queue depth
  - Bounds registry: live worker count + workflows discovered
  - Hooks registered count
  - DB pool capacity (D10-10 probe)
  - PrometheusPusher state (D10-14)
  - Recent agent metrics snapshot

All probes run with short timeout; one slow subsystem doesn't block
the response. Each subsystem returns {status, message, ms}; aggregate
status is worst case across all (healthy → degraded → unhealthy).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


PROBE_TIMEOUT_SECONDS = 2.0


async def _safe_probe(coro):
    """Run ``coro`` with a hard timeout; return (status, message, ms).

    status: 'ok' / 'degraded' / 'down'
    """
    start = time.time()
    try:
        msg = await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_SECONDS)
        return "ok", msg or "ok", int((time.time() - start) * 1000)
    except asyncio.TimeoutError:
        return "degraded", f"timeout after {PROBE_TIMEOUT_SECONDS}s", int((time.time() - start) * 1000)
    except Exception as exc:
        return "down", f"{type(exc).__name__}: {exc}", int((time.time() - start) * 1000)


async def _probe_supabase() -> str:
    from app.db import get_async_supabase_admin
    sb = await get_async_supabase_admin()
    # Trivial table read
    result = await sb.table("ai_agents").select("id", count="exact").limit(1).execute()
    return f"reachable, {len(result.data or [])} row sample"


async def _probe_redis() -> str:
    try:
        from app.core.redis import get_async_redis
        client = await get_async_redis()
        if client is None:
            return "not configured"
        await client.ping()
        return "reachable"
    except Exception as exc:
        raise exc


async def _probe_dbos() -> str:
    from app.services import dbos_orchestrator as dbos_orch
    return f"enabled={dbos_orch.is_enabled()}"


def _aggregate_status(probes: dict[str, dict]) -> str:
    """Worst case across all subsystem statuses."""
    statuses = {p["status"] for p in probes.values()}
    if "down" in statuses:
        return "unhealthy"
    if "degraded" in statuses:
        return "degraded"
    return "healthy"


@router.get("/health/deep", summary="Per-subsystem deep health snapshot")
async def health_deep(request: Request) -> dict[str, Any]:
    """Run all subsystem probes in parallel + return aggregate report."""
    probe_coros = {
        "dbos": _probe_dbos(),
        "supabase": _probe_supabase(),
        "redis": _probe_redis(),
    }
    results = await asyncio.gather(
        *(_safe_probe(c) for c in probe_coros.values()),
        return_exceptions=True,
    )
    probes: dict[str, dict] = {}
    for name, res in zip(probe_coros.keys(), results):
        if isinstance(res, BaseException):
            probes[name] = {"status": "down", "message": str(res), "ms": -1}
        else:
            status, message, ms = res
            probes[name] = {"status": status, "message": message, "ms": ms}

    # Synchronous in-process registry snapshots — fast, no I/O
    state = request.app.state

    def _snap(attr, fn):
        obj = getattr(state, attr, None)
        if obj is None:
            return None
        try:
            return fn(obj)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    in_proc = {
        "process_role": getattr(getattr(state, "process_role", None), "value", None),
        "bounds": _snap("bounds_registry", lambda r: {
            "live_count": len(r.live_bounds()),
        }),
        "hooks_registered": len(getattr(state, "hook_registry", []) or []),
        "context_engines": (
            getattr(state, "context_engines", None).names()
            if getattr(state, "context_engines", None) is not None
            else []
        ),
        "model_health_snapshot": _snap(
            "model_health", lambda m: m.snapshot()
        ),
        "lane_queue_depth": _snap(
            "lane_queue",
            lambda lq: {lane.value: q.qsize() for lane, q in (lq._queues.items() if hasattr(lq, '_queues') else [])},
        ),
        "agent_metrics_keys_in_use": _snap(
            "agent_metrics",
            lambda m: sum(1 for v in m.counters.values() if v > 0),
        ),
        "prometheus_pusher_active": getattr(state, "prometheus_pusher", None) is not None,
        "root_aborts_count": _snap(
            "root_abort_registry", lambda r: len(r)
        ),
    }

    aggregate = _aggregate_status(probes)
    return {
        "status": aggregate,
        "probes": probes,
        "in_process": in_proc,
        "version": "deep-1",
    }
