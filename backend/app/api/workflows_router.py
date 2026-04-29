"""DBOS workflow status + SSE endpoints.

Frontends (ChatPanel, TaskCenter, Storyboard panels) subscribe here
instead of the legacy `unified_tasks` Realtime channel for any task
that's been routed to a DBOS workflow. Both pipes can run side-by-side
during the shadow window.

Endpoints:
    GET    /api/v1/workflows/{workflow_id}/status   — one-shot status
    GET    /api/v1/workflows/{workflow_id}/events   — SSE stream
    POST   /api/v1/workflows/{workflow_id}/cancel   — request cancel
    POST   /api/v1/workflows/{workflow_id}/resume   — resume after pause
    GET    /api/v1/workflows/{workflow_id}/steps    — step list snapshot
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.core.deps import AuthDep
from app.services import dbos_orchestrator

router = APIRouter(prefix="/workflows", tags=["DBOS Workflows"])

# How often the SSE stream polls DBOS for status changes. 1.5s balances
# UI snappiness against sys_db read load. Don't drop below 0.5s without
# benchmarking — every connected client adds 1 read per tick.
_POLL_INTERVAL_SEC = 1.5

# Heartbeat to keep proxies (nginx, Cloudflare) from closing idle SSE
# connections. SSE comment lines ("`: ping`") don't show up in the
# EventSource API on the client.
_HEARTBEAT_INTERVAL_SEC = 15.0

# Hard ceiling on stream duration. A workflow that runs >30 min should
# be checked via the one-shot status endpoint instead, not via a
# long-lived SSE connection.
_MAX_STREAM_SECONDS = 30 * 60

# DBOS WorkflowStatusString terminal values. Stream closes when status
# enters this set.
_TERMINAL_STATES = frozenset({"SUCCESS", "ERROR", "CANCELLED"})


def _serialize_status(ws: Any) -> dict[str, Any]:
    """Project a DBOS WorkflowStatus into a JSON-safe dict for the
    frontend. We deliberately drop the `input` field — it can contain
    non-JSON-serialisable kwargs (e.g. open file handles in the legacy
    download path) and the frontend doesn't need it."""
    if ws is None:
        return {}
    return {
        "workflow_id": getattr(ws, "workflow_uuid", None)
        or getattr(ws, "workflow_id", None),
        "status": getattr(ws, "status", None),
        "name": getattr(ws, "name", None),
        "queue_name": getattr(ws, "queue_name", None),
        "created_at": getattr(ws, "created_at", None),
        "updated_at": getattr(ws, "updated_at", None),
        "output": _safe_json(getattr(ws, "output", None)),
        "error": (
            str(getattr(ws, "error", None)) if getattr(ws, "error", None) else None
        ),
        "executor_id": getattr(ws, "executor_id", None),
        "app_version": getattr(ws, "app_version", None),
    }


def _safe_json(value: Any) -> Any:
    """Best-effort JSON-safe coercion. Falls back to `repr()` for
    values pydantic / json can't handle."""
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return repr(value)[:500]


async def _get_status(workflow_id: str) -> Optional[dict[str, Any]]:
    """One DBOS status read. Returns None if workflow_id is unknown."""
    if not dbos_orchestrator.is_enabled():
        return None
    from dbos import DBOS

    ws = await DBOS.get_workflow_status_async(workflow_id)
    if ws is None:
        return None
    return _serialize_status(ws)


def _step_field(s: Any, key: str, default: Any = None) -> Any:
    """DBOS list_workflow_steps_async returns dict items in v2.19.0,
    but earlier/later versions may return dataclass instances. Support
    both shapes."""
    if isinstance(s, dict):
        return s.get(key, default)
    return getattr(s, key, default)


async def _get_steps(workflow_id: str) -> list[dict[str, Any]]:
    """Step list snapshot. Best-effort — returns [] on any error."""
    if not dbos_orchestrator.is_enabled():
        return []
    try:
        from dbos import DBOS

        steps = await DBOS.list_workflow_steps_async(workflow_id)
        return [
            {
                "function_id": _step_field(s, "function_id"),
                "function_name": _step_field(s, "function_name"),
                "output": _safe_json(_step_field(s, "output")),
                "error": (
                    str(_step_field(s, "error")) if _step_field(s, "error") else None
                ),
                "child_workflow_id": _step_field(s, "child_workflow_id"),
                "started_at_epoch_ms": _step_field(s, "started_at_epoch_ms"),
                "completed_at_epoch_ms": _step_field(s, "completed_at_epoch_ms"),
            }
            for s in steps
        ]
    except Exception as e:
        logger.debug(f"[workflows] list_workflow_steps_async({workflow_id}): {e}")
        return []


@router.get("/{workflow_id}/status")
async def get_workflow_status(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """One-shot status snapshot. Use the SSE endpoint for live updates."""
    snap = await _get_status(workflow_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow_id={workflow_id} unknown to DBOS",
        )
    return snap


@router.get("/{workflow_id}/steps")
async def get_workflow_steps(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Step list snapshot. Frontend uses this to render per-step
    progress timelines (e.g. parse → save → auto_tag → dispatch)."""
    snap = await _get_status(workflow_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow_id={workflow_id} unknown to DBOS",
        )
    return {"workflow_id": workflow_id, "steps": await _get_steps(workflow_id)}


@router.post("/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Request cancellation. DBOS marks the workflow CANCELLED and
    in-flight steps complete or raise depending on the runtime."""
    if not dbos_orchestrator.is_enabled():
        raise HTTPException(503, detail="DBOS not enabled")
    from dbos import DBOS

    try:
        await DBOS.cancel_workflow_async(workflow_id)
    except Exception as e:
        logger.warning(f"[workflows] cancel({workflow_id}): {e}")
        raise HTTPException(400, detail=str(e))
    return {"status": "cancel_requested", "workflow_id": workflow_id}


@router.post("/{workflow_id}/resume")
async def resume_workflow(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Resume a previously paused or cancelled workflow."""
    if not dbos_orchestrator.is_enabled():
        raise HTTPException(503, detail="DBOS not enabled")
    from dbos import DBOS

    try:
        await DBOS.resume_workflow_async(workflow_id)
    except Exception as e:
        logger.warning(f"[workflows] resume({workflow_id}): {e}")
        raise HTTPException(400, detail=str(e))
    return {"status": "resumed", "workflow_id": workflow_id}


async def _sse_event_stream(
    workflow_id: str,
    request: Request,
    include_steps: bool,
) -> AsyncIterator[bytes]:
    """SSE generator. Yields bytes (SSE wire format) until terminal
    state, max stream duration, or client disconnect.

    Wire format:
        event: status\\n
        data: {...json...}\\n\\n

    Heartbeat (proxy keepalive, ignored by EventSource):
        : ping\\n\\n
    """
    last_serialized: Optional[str] = None
    last_step_count = -1
    last_heartbeat = time.monotonic()
    started = time.monotonic()

    while True:
        # Client disconnected? Stop polling DBOS.
        if await request.is_disconnected():
            return

        # Hard ceiling.
        if time.monotonic() - started > _MAX_STREAM_SECONDS:
            yield b"event: timeout\ndata: {}\n\n"
            return

        snap = await _get_status(workflow_id)
        if snap is None:
            # First-tick 404 → tell the client and stop. A previously
            # known workflow disappearing mid-stream is treated the
            # same way (DBOS gc?).
            yield b'event: not_found\ndata: {"reason":"workflow_id_unknown"}\n\n'
            return

        payload: dict[str, Any] = dict(snap)
        if include_steps:
            steps = await _get_steps(workflow_id)
            payload["steps"] = steps
            step_count = len(steps)
        else:
            step_count = last_step_count  # don't trigger emission on step count

        serialized = json.dumps(payload, default=str)

        if serialized != last_serialized or step_count != last_step_count:
            yield f"event: status\ndata: {serialized}\n\n".encode("utf-8")
            last_serialized = serialized
            last_step_count = step_count
            last_heartbeat = time.monotonic()

        # Terminal? Last emit already went out above; close cleanly.
        if snap.get("status") in _TERMINAL_STATES:
            yield b"event: done\ndata: {}\n\n"
            return

        # Heartbeat to keep proxies happy when nothing's changed.
        if time.monotonic() - last_heartbeat > _HEARTBEAT_INTERVAL_SEC:
            yield b": ping\n\n"
            last_heartbeat = time.monotonic()

        await asyncio.sleep(_POLL_INTERVAL_SEC)


@router.get("/{workflow_id}/events")
async def stream_workflow_events(
    workflow_id: str,
    request: Request,
    include_steps: bool = Query(
        False,
        description="Also stream the step list on every status change. "
        "Heavier but lets the UI render per-step timelines.",
    ),
    token: Optional[str] = Query(
        None,
        description="Supabase JWT for browsers (EventSource can't set "
        "Authorization headers). Server validates the same way as "
        "Bearer header. Falls back to header auth when omitted.",
    ),
    authorization: Optional[str] = Header(None),
) -> StreamingResponse:
    """SSE stream of DBOS workflow status changes. Closes on terminal
    state, 30-min ceiling, or client disconnect.

    Auth: prefers Authorization header; falls back to ?token= query
    parameter for browser EventSource compatibility.

    Frontend usage:
        const es = new EventSource(
          `/api/v1/workflows/${id}/events?token=${jwt}`
        );
        es.addEventListener("status", (e) => render(JSON.parse(e.data)));
        es.addEventListener("done",   () => es.close());
        es.addEventListener("not_found", () => showError("workflow gone"));
    """
    # Auth: header first (preferred), then query token fallback. We
    # don't reuse `Depends(get_optional_auth)` here because we want
    # to fall through to the query-param path WITHOUT raising 401
    # when the header is absent — a behaviour that's awkward to
    # express through Depends on a single endpoint.
    from app.core.deps import _validate_bearer_token

    bearer = authorization or (f"Bearer {token}" if token else None)
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No auth: provide Authorization header or ?token=",
        )
    try:
        await _validate_bearer_token(bearer)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )

    # Validate workflow exists before opening the stream so the client
    # gets a synchronous 404 instead of the SSE not_found event.
    snap = await _get_status(workflow_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow_id={workflow_id} unknown to DBOS",
        )

    return StreamingResponse(
        _sse_event_stream(workflow_id, request, include_steps),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx: don't buffer SSE
        },
    )
