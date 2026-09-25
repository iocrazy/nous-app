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

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.core.admin_deps import AdminAuthDep
from app.schemas.admin_observability import AdminAgentMetrics

router = APIRouter()

PROMETHEUS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get(
    "/agent-metrics/prometheus",
    summary="Prometheus text-format counters (for scrape)",
    response_class=PlainTextResponse,
    responses={200: {"description": "Prometheus exposition format 0.0.4"}},
)
async def agent_metrics_prometheus(request: Request, _auth: AdminAuthDep):
    """Phase K (K4): Prometheus expfmt 0.0.4 output of AgentMetrics.

    Plain-text content type so a Prometheus scrape job can pull this
    endpoint directly (no JSON parsing). Header set per Prometheus spec
    so scrapers detect format correctly."""
    from fastapi.responses import Response

    from app.agent_framework.prometheus_exporter import render_prometheus

    metrics = getattr(request.app.state, "agent_metrics", None)
    if metrics is None:
        return Response(
            content="# agent_metrics not initialized\n",
            media_type=PROMETHEUS_MEDIA_TYPE,
        )
    body = render_prometheus(metrics)
    return Response(
        content=body,
        media_type=PROMETHEUS_MEDIA_TYPE,
    )


@router.get(
    "/agent-metrics",
    summary="Snapshot of agent harness counters",
    response_model=AdminAgentMetrics,
)
async def agent_metrics(request: Request, _auth: AdminAuthDep) -> Any:
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
