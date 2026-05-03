"""Admin endpoint exposing agent harness telemetry counters.

Wave I (I3). Reads app.state.agent_metrics + app.state.bounds_registry +
app.state.root_abort_registry + app.state.model_health snapshot — gives
ops a one-call view of the harness state.

GET /api/v1/admin/agent-metrics → {
  counters: {compaction_triggered: 12, ...},
  bounds: {...},
  root_aborts: {...},
  model_health: {...},
}

Read-only; no mutation endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.admin_deps import AdminAuthDep

router = APIRouter()


@router.get("/agent-metrics", summary="Snapshot of agent harness counters")
async def agent_metrics(request: Request, _auth: AdminAuthDep) -> dict:
    """Return the in-process counter store + related registry snapshots.

    Per-process — multi-replica fleets see only this process's view.
    For aggregate metrics across replicas use a real metrics backend
    (Prometheus exporter + scrape).
    """
    state = request.app.state

    out: dict = {}

    metrics = getattr(state, "agent_metrics", None)
    out["counters"] = metrics.snapshot() if metrics else {}

    bounds = getattr(state, "bounds_registry", None)
    out["bounds"] = bounds.snapshot() if bounds else {}

    root_aborts = getattr(state, "root_abort_registry", None)
    out["root_aborts"] = root_aborts.snapshot() if root_aborts else {}

    model_health = getattr(state, "model_health", None)
    out["model_health"] = model_health.snapshot() if model_health else {}

    hooks = getattr(state, "hook_registry", None)
    out["hooks_registered"] = hooks.names() if hooks else []

    engines = getattr(state, "context_engines", None)
    out["context_engines"] = engines.names() if engines else []

    return out
