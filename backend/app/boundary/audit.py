"""Boundary audit log — Layer 5 observability.

Every block from validate_url, SafeAsyncClient redirect hook, or
SsrfProxy CONNECT/HTTP path lands a row in the ``boundary_audit`` table.
Operators query it to see attack attempts, false positives, and trends.

Best-effort semantics: failure to write the audit row MUST NOT propagate
out — the boundary block itself happens regardless. Audit writes are
fire-and-forget via ``asyncio.create_task`` to avoid adding latency to
the request-rejection path.

Server-side only. The raw URL is logged here but the API layer (global
BoundaryError handler) returns a generic safe message to the client.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

# Layer constants — keep aligned with migration 185 column comment.
LAYER_VALIDATE = "l1_validate"
LAYER_PINNED_DNS = "l2_pinned"
LAYER_SAFE_HTTP = "l3_safehttp"
LAYER_PROXY = "l3_proxy"

_RAW_URL_TRUNC = 2000


async def _write_row(payload: dict[str, Any]) -> None:
    """Insert one boundary_audit row. Lazily imports the Supabase client
    so the boundary package stays a leaf module at import time."""
    try:
        # Deferred import — boundary package is otherwise stdlib-only.
        from app.db import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        await sb.table("boundary_audit").insert(payload).execute()
    except Exception as exc:
        # Audit failures are diagnostic, not load-bearing. Loguru will
        # carry this so we know if the audit pipeline is itself broken.
        logger.debug(
            f"boundary_audit: write failed (non-fatal): "
            f"{type(exc).__name__}: {exc}"
        )


def log_block(
    *,
    layer: str,
    reason: str,
    raw_url: Optional[str] = None,
    resolved_ip: Optional[str] = None,
    user_id: Optional[int] = None,
    request_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Fire-and-forget audit log of a boundary block.

    Returns immediately. The actual DB insert runs as a background task
    on the running event loop. If no loop is running (e.g. called from a
    sync workflow context), the audit is silently dropped — the block
    itself still happened, just without observability for that path.

    Args:
        layer: One of LAYER_* constants.
        reason: Short canonical reason string (snake_case).
        raw_url: The rejected URL / target. Server-side only — never
            returned to the client.
        resolved_ip: If DNS was attempted, the offending IP.
        user_id: If available from request context.
        request_id: For correlating with application_logs.
        metadata: Free-form extra context (dict, JSON-serializable).
    """
    if raw_url and len(raw_url) > _RAW_URL_TRUNC:
        raw_url = raw_url[:_RAW_URL_TRUNC] + "...[truncated]"

    payload: dict[str, Any] = {
        "layer": layer,
        "reason": reason,
        "raw_url": raw_url,
        "resolved_ip": resolved_ip,
        "user_id": user_id,
        "request_id": request_id,
        "metadata_json": metadata or {},
    }

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop — sync context. Drop silently rather than
        # blocking the caller with a sync DB write.
        logger.debug(
            f"boundary_audit: no running loop, dropped "
            f"layer={layer} reason={reason}"
        )
        return

    loop.create_task(_write_row(payload))
