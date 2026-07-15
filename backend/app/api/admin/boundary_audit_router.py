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


def _serialize(row: Any) -> dict[str, Any]:
    """BoundaryAudit ORM row → PostgREST-shaped dict (datetime → ISO str)."""
    return {
        "id": row.id,
        "blocked_at": row.blocked_at.isoformat() if row.blocked_at else None,
        "layer": row.layer,
        "reason": row.reason,
        "raw_url": row.raw_url,
        "resolved_ip": row.resolved_ip,
        "user_id": row.user_id,
        "request_id": row.request_id,
        "metadata_json": row.metadata_json,
    }


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
        from sqlalchemy import func, select

        from app.db.session import read_scope
        from app.models import BoundaryAudit

        conds = []
        if layer:
            conds.append(BoundaryAudit.layer == layer)
        if reason:
            conds.append(BoundaryAudit.reason == reason)

        async with read_scope() as session:
            total = (
                await session.execute(
                    select(func.count()).select_from(BoundaryAudit).where(*conds)
                )
            ).scalar()
            rows = (
                (
                    await session.execute(
                        select(BoundaryAudit)
                        .where(*conds)
                        .order_by(BoundaryAudit.blocked_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "items": [_serialize(r) for r in rows],
            "total": total,
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
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import BoundaryAudit

        # Read recent rows and aggregate in Python — small enough that we
        # don't need a SQL aggregate function. If the table grows huge a
        # follow-up commit can add a materialized view.
        #
        # TODO(boundary-audit): the pre-ORM path passed the string
        # "NOW() - INTERVAL '7 days'" as a PostgREST filter literal, which
        # Postgres rejected as an invalid timestamptz — so this endpoint
        # always errored into the empty-summary except branch. The bind below
        # applies the intended 7-day window; awaiting supervisor sign-off on
        # this behaviour change (see B2 report).
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        async with read_scope() as session:
            rows = [
                {"layer": layer, "reason": reason}
                for layer, reason in (
                    await session.execute(
                        select(BoundaryAudit.layer, BoundaryAudit.reason)
                        .where(BoundaryAudit.blocked_at >= cutoff)
                        .order_by(BoundaryAudit.blocked_at.desc())
                        .limit(5000)
                    )
                ).all()
            ]
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
