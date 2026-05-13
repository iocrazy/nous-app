"""DBOS workflow status + SSE endpoints.

Frontends (ChatPanel, TaskCenter, Storyboard panels) subscribe here
instead of the legacy `task_tracking` Realtime channel for any task
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
from app.services.infra import dbos_orchestrator

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


def _stringify_error(err: Any) -> Optional[str]:
    """DBOS stores `error` as a deserialised exception instance (or
    None). `str(exc)` gives a clean message; for bare `Exception()`
    fall back to the type name. Pickled bytes (rare path) get repr'd."""
    if err is None:
        return None
    if isinstance(err, BaseException):
        msg = str(err) or type(err).__name__
        return msg[:500]
    if isinstance(err, (bytes, bytearray)):
        return f"<pickled-error len={len(err)}>"
    return str(err)[:500]


def _serialize_status(ws: Any, *, include_io: bool = False) -> dict[str, Any]:
    """Project a DBOS WorkflowStatus into a JSON-safe dict for the
    frontend. `include_io=True` adds inputs/output (used by detail
    endpoints; list endpoint omits to keep payload small)."""
    if ws is None:
        return {}
    out: dict[str, Any] = {
        "workflow_id": getattr(ws, "workflow_uuid", None)
        or getattr(ws, "workflow_id", None),
        "status": getattr(ws, "status", None),
        "name": getattr(ws, "name", None),
        "queue_name": getattr(ws, "queue_name", None),
        "created_at": getattr(ws, "created_at", None),
        "updated_at": getattr(ws, "updated_at", None),
        "error": _stringify_error(getattr(ws, "error", None)),
        "executor_id": getattr(ws, "executor_id", None),
        "app_version": getattr(ws, "app_version", None),
        "authenticated_user": getattr(ws, "authenticated_user", None),
    }
    if include_io:
        out["input"] = _safe_json(getattr(ws, "input", None))
        out["output"] = _safe_json(getattr(ws, "output", None))
    return out


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
    """One DBOS status read. Returns None if workflow_id is unknown.

    For ERROR workflows we additionally call retrieve_workflow().get_result_async()
    to surface the underlying exception — `WorkflowStatus.error` from
    list/get_status alone is None until the result is realised."""
    if not dbos_orchestrator.is_enabled():
        return None
    from dbos import DBOS

    ws = await DBOS.get_workflow_status_async(workflow_id)
    if ws is None:
        return None
    snapshot = _serialize_status(ws, include_io=True)
    if snapshot.get("status") == "ERROR" and not snapshot.get("error"):
        try:
            handle = DBOS.retrieve_workflow(workflow_id)
            await handle.get_result_async()
        except Exception as exc:
            snapshot["error"] = _stringify_error(exc)
    return snapshot


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


@router.post("/{workflow_id}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_workflow(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Re-execute a completed/failed workflow as a NEW workflow with
    the same inputs. Returns the new workflow_id.

    Differs from /resume — resume re-runs a paused workflow under its
    existing id (replaying durable steps); restart forks a fresh id.
    """
    if not dbos_orchestrator.is_enabled():
        raise HTTPException(503, detail="DBOS not enabled")
    from dbos import DBOS

    try:
        # start_step=1: replay all steps from scratch (DBOS step ids
        # are 1-indexed). For partial restart, frontend would need to
        # let user pick the step.
        new_handle = await DBOS.fork_workflow_async(workflow_id, start_step=1)
    except Exception as e:
        logger.warning(f"[workflows] restart({workflow_id}): {e}")
        raise HTTPException(400, detail=str(e))
    return {
        "status": "restarted",
        "original_workflow_id": workflow_id,
        "new_workflow_id": new_handle.workflow_id,
    }


def _serialize_task_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a task_tracking row into the workflows list shape.

    Renames the FK `dbos_workflow_id` to `workflow_id` so the wire
    contract reads naturally — every consumer thinks in terms of
    workflows, not tracking-row internals."""
    return {
        "workflow_id": row.get("dbos_workflow_id"),
        "task_type": row.get("task_type"),
        "task_kind": row.get("task_kind"),
        "status": row.get("status"),
        "phase": row.get("phase"),
        "title": row.get("title"),
        "subtitle": row.get("subtitle"),
        "progress": row.get("progress"),
        "error_msg": row.get("error_msg"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "updated_at": row.get("updated_at"),
        "media_id": row.get("media_id"),
        "resource_id": row.get("resource_id"),
        "group_id": row.get("group_id"),
    }


# DBOS status names some legacy callers may still pass on the query
# string. task_tracking uses lowercase business statuses, so we
# transparently map the old values across.
_LEGACY_DBOS_STATUS_MAP = {
    "PENDING": "pending",
    "ENQUEUED": "pending",
    "SUCCESS": "completed",
    "ERROR": "failed",
    "RETRIES_EXCEEDED": "failed",
    "CANCELLED": "cancelled",
}


@router.get("")
async def list_workflows(
    auth: AuthDep,
    name: Optional[str] = Query(
        None,
        description="Filter by task_type (e.g. download / parse / ai_transcription)",
    ),
    workflow_status: Optional[str] = Query(
        None,
        description=(
            "Status filter (task_tracking values): pending / processing / "
            "completed / failed / cancelled / lost. Legacy DBOS names "
            "(PENDING / SUCCESS / ERROR / ...) are still accepted and mapped."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort_desc: bool = Query(True, description="Newest first"),
) -> dict[str, Any]:
    """List the authenticated user's workflows from task_tracking.

    Reads task_tracking (CLAUDE.md 路线 C rule 1 — task_tracking is the
    UI source of truth). Previously this endpoint read dbos.workflow_status
    directly, which could diverge from the trigger-mirrored task_tracking
    state during the brief sync window — risking the same dual-source
    inconsistency that motivated 路线 C ("Engine 108 queued but Settings
    only 38 rows", 2026-05-05).

    Per-workflow detail / control endpoints (/status, /events, /steps,
    /cancel, /resume, /restart) still call DBOS directly because they
    need execution-engine state (input/output, step list, cancel signals)
    that task_tracking deliberately does NOT carry.
    """
    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    q = (
        sb.table("task_tracking")
        .select(
            "dbos_workflow_id, task_type, task_kind, status, phase, "
            "title, subtitle, progress, error_msg, created_at, "
            "started_at, completed_at, updated_at, media_id, "
            "resource_id, group_id"
        )
        .eq("user_id", str(auth.user_id))
    )
    if name:
        q = q.eq("task_type", name)
    if workflow_status:
        normalized = _LEGACY_DBOS_STATUS_MAP.get(
            workflow_status, workflow_status.lower()
        )
        q = q.eq("status", normalized)
    q = q.order("created_at", desc=sort_desc).range(offset, offset + limit - 1)

    try:
        result = await q.execute()
    except Exception as e:
        logger.warning(f"[workflows] list({str(auth.user_id)[:8]}): {e}")
        raise HTTPException(500, detail=str(e))

    rows = result.data or []
    return {
        "workflows": [_serialize_task_row(r) for r in rows],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


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
    ticket: Optional[str] = Query(
        None,
        description="One-shot 30s ticket from POST /api/v1/ws/ticket. "
        "Preferred over ?token= since the JWT never enters the URL.",
    ),
    token: Optional[str] = Query(
        None,
        description="DEPRECATED. Supabase JWT in URL leaks via access "
        "logs / Referer / browser history. Use ?ticket= instead. Kept "
        "reachable for legacy tabs only; logs a WARNING on every hit.",
    ),
    authorization: Optional[str] = Header(None),
) -> StreamingResponse:
    """SSE stream of DBOS workflow status changes. Closes on terminal
    state, 30-min ceiling, or client disconnect.

    Auth chain (preferred → deprecated):
      1. ?ticket=<random>  — one-shot, 30s, no JWT in URL
      2. Authorization: Bearer <JWT>  — for non-browser clients
      3. ?token=<JWT>  — DEPRECATED, JWT in URL → access-log leak
    """
    # Ticket (preferred) — consume the one-shot Redis token first.
    if ticket:
        from app.api.ws_ticket_router import consume_ticket

        if await consume_ticket(ticket):
            # Authenticated via ticket; skip JWT validation chain.
            pass
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired ticket",
            )
    else:
        # Header first (preferred for non-browser clients), then ?token=.
        # ?token= remains reachable for legacy tabs but logs WARNING.
        from app.core.deps import _validate_bearer_token

        if not authorization and token:
            logger.warning(
                "[workflows/events] DEPRECATED ?token= auth — JWT was just "
                "leaked to access logs. Client should migrate to ?ticket= "
                "(POST /api/v1/ws/ticket)."
            )

        bearer = authorization or (f"Bearer {token}" if token else None)
        if not bearer:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No auth: provide Authorization header or ?ticket=",
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
