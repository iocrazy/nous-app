"""Admin API routes for boundary_audit (B9-G).

Read-only viewer of boundary policy blocks. Operators query this to see
SSRF attack attempts, false positives, and trends across the 5 boundary
layers.

Server-side raw_url is shown ONLY to admins via this endpoint. The
public API never echoes raw rejected URLs (per RFC).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from loguru import logger

from app.core.admin_deps import AdminAuthDep

router = APIRouter()


@router.get("")
async def list_boundary_audit(
    admin: AdminAuthDep,
    layer: Optional[str] = Query(
        None,
        description="Filter by layer: l1_validate, l2_pinned, l3_safehttp, l3_proxy",
    ),
    reason: Optional[str] = Query(None, description="Filter by reason (exact match)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List recent boundary blocks (most recent first).

    Pagination via limit/offset. Filters by layer + reason are AND-combined.
    """
    try:
        from app.db import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        query = sb.table("boundary_audit").select("*", count="exact")
        if layer:
            query = query.eq("layer", layer)
        if reason:
            query = query.eq("reason", reason)
        query = query.order("blocked_at", desc=True).range(offset, offset + limit - 1)
        result = await query.execute()
        return {
            "items": result.data or [],
            "total": getattr(result, "count", None),
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error(f"[admin/boundary-audit] list failed: {e}")
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "error": str(e),
        }


@router.get("/summary")
async def boundary_audit_summary(admin: AdminAuthDep) -> dict[str, Any]:
    """Aggregated counts by layer + reason for the last 7 days.

    Used by the admin dashboard to spot surges in attack attempts."""
    try:
        from app.db import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        # Read recent rows and aggregate in Python — small enough that we
        # don't need a SQL aggregate function. If the table grows huge a
        # follow-up commit can add a materialized view.
        result = await (
            sb.table("boundary_audit")
            .select("layer, reason, blocked_at")
            .gte(
                "blocked_at",
                "NOW() - INTERVAL '7 days'",
            )
            .order("blocked_at", desc=True)
            .limit(5000)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"[admin/boundary-audit] summary failed: {e}")
        return {"by_layer": {}, "by_reason": {}, "total_7d": 0, "error": str(e)}

    by_layer: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for r in rows:
        by_layer[r["layer"]] = by_layer.get(r["layer"], 0) + 1
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1

    return {
        "by_layer": by_layer,
        "by_reason": by_reason,
        "total_7d": len(rows),
    }
