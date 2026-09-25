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
    POST   /api/v1/workflows/{workflow_id}/restart  — fork a fresh run
    GET    /api/v1/workflows/{workflow_id}/steps    — step list snapshot

There is no run list here: listing the user's tasks is the Task Center's job
(``/api/v1/task-manager/tasks``, reading ``task_tracking``). A ``/runs`` list
that duplicated it had no caller and was removed (OpenAPI P9). GET
/api/v1/workflows itself belongs to workflow_templates_router; the two routers
share the prefix, so their paths must stay disjoint —
tests/test_route_uniqueness.py fails on any collision.

Every per-id endpoint first checks the caller owns the workflow
(``app/api/workflow_access.py``); DBOS itself has no notion of users.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from loguru import logger

from app.api.workflow_access import require_workflow_access, workflow_not_found
from app.core.deps import AuthDep
from app.schemas.workflow_responses import (
    DbosWorkflowCancelResult,
    DbosWorkflowRestartResult,
    DbosWorkflowResumeResult,
    DbosWorkflowSnapshot,
    DbosWorkflowSteps,
)
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


async def _status_read(workflow_id: str) -> Any:
    """Read one WorkflowStatus (or None if unknown), client-aware.

    Gateway-client prep (DORMANT): when the gateway DBOSClient handle is
    set, status reads go through the client; otherwise the in-process
    `DBOS.*` path is used (unchanged). `get_dbos_client()` is None
    everywhere today, so the existing `DBOS.get_workflow_status_async`
    branch is always taken — ZERO behavior change.

    The client has no `get_workflow_status_async`; we mirror the
    Optional[WorkflowStatus] contract via
    `retrieve_workflow_async(id).get_status()`, converting the
    `DBOSNonExistentWorkflowError` (raised for unknown ids) back into a
    `None` return so callers keep their 404 semantics."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        from dbos._error import DBOSNonExistentWorkflowError

        try:
            handle = await client.retrieve_workflow_async(workflow_id)
            return await handle.get_status()
        except DBOSNonExistentWorkflowError:
            return None
    from dbos import DBOS

    return await DBOS.get_workflow_status_async(workflow_id)


async def _retrieve_result(workflow_id: str) -> None:
    """Realise an ERROR workflow's result so its exception surfaces,
    client-aware. Dormant client branch uses the gateway handle; default
    uses the in-process `DBOS.retrieve_workflow`."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        handle = await client.retrieve_workflow_async(workflow_id)
        await handle.get_result()
    else:
        from dbos import DBOS

        handle = DBOS.retrieve_workflow(workflow_id)
        await handle.get_result_async()


async def _steps_read(workflow_id: str) -> list[Any]:
    """List workflow steps, client-aware. Dormant client branch uses the
    gateway handle's `list_workflow_steps_async`; default uses
    `DBOS.list_workflow_steps_async`."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        return await client.list_workflow_steps_async(workflow_id)
    from dbos import DBOS

    return await DBOS.list_workflow_steps_async(workflow_id)


async def _cancel(workflow_id: str) -> None:
    """Cancel a workflow, client-aware."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        await client.cancel_workflow_async(workflow_id)
    else:
        from dbos import DBOS

        await DBOS.cancel_workflow_async(workflow_id)


async def _resume(workflow_id: str) -> None:
    """Resume a workflow, client-aware."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        await client.resume_workflow_async(workflow_id)
    else:
        from dbos import DBOS

        await DBOS.resume_workflow_async(workflow_id)


async def _fork(workflow_id: str) -> Any:
    """Fork a workflow from step 1 (full replay), client-aware. Returns
    the new handle. `fork_workflow_async(workflow_id, start_step)` takes
    `start_step` positionally.

    Pass the pinned application_version so the forked (ENQUEUED) row carries
    the same version the worker is pinned to — DBOS `fork_workflow` inserts
    application_version verbatim with NO fallback to the live version, so
    omitting it leaves NULL and a version-pinned worker may never dequeue the
    fork (orphan → 'lost'). None in dev (combined, unpinned) is harmless.
    """
    from app.services.infra.dbos_orchestrator import (
        _resolve_pinned_app_version,
        get_dbos_client,
    )

    version = _resolve_pinned_app_version()
    client = get_dbos_client()
    if client is not None:
        return await client.fork_workflow_async(
            workflow_id, 1, application_version=version
        )
    from dbos import DBOS

    return await DBOS.fork_workflow_async(
        workflow_id, start_step=1, application_version=version
    )


async def _get_status(workflow_id: str) -> Optional[dict[str, Any]]:
    """One DBOS status read. Returns None if workflow_id is unknown.

    For ERROR workflows we additionally realise the result to surface
    the underlying exception — `WorkflowStatus.error` from
    list/get_status alone is None until the result is realised."""
    if not dbos_orchestrator.is_enabled():
        return None

    ws = await _status_read(workflow_id)
    if ws is None:
        return None
    snapshot = _serialize_status(ws, include_io=True)
    if snapshot.get("status") == "ERROR" and not snapshot.get("error"):
        try:
            await _retrieve_result(workflow_id)
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
        steps = await _steps_read(workflow_id)
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


@router.get("/{workflow_id}/status", response_model=DbosWorkflowSnapshot)
async def get_workflow_status(
    workflow_id: str,
    auth: AuthDep,
) -> Any:
    """One-shot status snapshot. Use the SSE endpoint for live updates.

    ``input`` / ``output`` are arbitrary workflow values, so the snapshot is
    encoded here (as FastAPI did for the bare dict) before the model sees it:
    Pydantic would render a nested datetime differently, and cannot serialize
    an arbitrary object at all."""
    await require_workflow_access(workflow_id, auth.user_id)
    snap = await _get_status(workflow_id)
    if snap is None:
        raise workflow_not_found()
    return jsonable_encoder(snap)


@router.get("/{workflow_id}/steps", response_model=DbosWorkflowSteps)
async def get_workflow_steps(
    workflow_id: str,
    auth: AuthDep,
) -> Any:
    """Step list snapshot. Frontend uses this to render per-step
    progress timelines (e.g. parse → save → auto_tag → dispatch)."""
    await require_workflow_access(workflow_id, auth.user_id)
    snap = await _get_status(workflow_id)
    if snap is None:
        raise workflow_not_found()
    return jsonable_encoder(
        {"workflow_id": workflow_id, "steps": await _get_steps(workflow_id)}
    )


@router.post("/{workflow_id}/cancel", response_model=DbosWorkflowCancelResult)
async def cancel_workflow(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Request cancellation. DBOS marks the workflow CANCELLED and
    in-flight steps complete or raise depending on the runtime."""
    await require_workflow_access(workflow_id, auth.user_id)
    if not dbos_orchestrator.is_enabled():
        raise HTTPException(503, detail="DBOS not enabled")

    try:
        await _cancel(workflow_id)
    except Exception as e:
        logger.warning(f"[workflows] cancel({workflow_id}): {e}")
        raise HTTPException(400, detail=str(e))
    return {"status": "cancel_requested", "workflow_id": workflow_id}


@router.post("/{workflow_id}/resume", response_model=DbosWorkflowResumeResult)
async def resume_workflow(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Resume a previously paused or cancelled workflow."""
    await require_workflow_access(workflow_id, auth.user_id)
    if not dbos_orchestrator.is_enabled():
        raise HTTPException(503, detail="DBOS not enabled")

    try:
        await _resume(workflow_id)
    except Exception as e:
        logger.warning(f"[workflows] resume({workflow_id}): {e}")
        raise HTTPException(400, detail=str(e))
    return {"status": "resumed", "workflow_id": workflow_id}


@router.post(
    "/{workflow_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DbosWorkflowRestartResult,
)
async def restart_workflow(
    workflow_id: str,
    auth: AuthDep,
) -> dict[str, Any]:
    """Re-execute a completed/failed workflow as a NEW workflow with
    the same inputs. Returns the new workflow_id.

    Differs from /resume — resume re-runs a paused workflow under its
    existing id (replaying durable steps); restart forks a fresh id.
    """
    await require_workflow_access(workflow_id, auth.user_id)
    if not dbos_orchestrator.is_enabled():
        raise HTTPException(503, detail="DBOS not enabled")

    try:
        # start_step=1: replay all steps from scratch (DBOS step ids
        # are 1-indexed). For partial restart, frontend would need to
        # let user pick the step.
        new_handle = await _fork(workflow_id)
    except Exception as e:
        logger.warning(f"[workflows] restart({workflow_id}): {e}")
        raise HTTPException(400, detail=str(e))
    return {
        "status": "restarted",
        "original_workflow_id": workflow_id,
        "new_workflow_id": new_handle.workflow_id,
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


@router.get(
    "/{workflow_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Server-Sent Events: `event: status` carries a "
                "DbosWorkflowSnapshot (plus `steps`, a DbosWorkflowStep list, "
                "when include_steps=true) on every change; then `event: done`, "
                "`event: timeout` or `event: not_found`. `: ping` comments "
                "keep proxies from closing an idle stream."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
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

        caller_id = await consume_ticket(ticket)
        if not caller_id:
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
            caller_id = (await _validate_bearer_token(bearer)).user_id
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            )

    # Ownership, then existence, before opening the stream so the client
    # gets a synchronous 404 instead of the SSE not_found event.
    await require_workflow_access(workflow_id, caller_id)
    snap = await _get_status(workflow_id)
    if snap is None:
        raise workflow_not_found()

    return StreamingResponse(
        _sse_event_stream(workflow_id, request, include_steps),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx: don't buffer SSE
        },
    )
