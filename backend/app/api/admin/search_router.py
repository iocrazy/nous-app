"""Admin API routes for cross-log search and request tracing."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.search_repository import get_admin_search_repository

router = APIRouter()


# ============================================
# Response Schemas
# ============================================


class UnifiedLogEntry(BaseModel):
    id: str
    source: str  # request | app | frontend | audit
    timestamp: str
    level: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    module: Optional[str] = None
    message: Optional[str] = None
    action: Optional[str] = None
    target_type: Optional[str] = None
    request_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class Facets(BaseModel):
    levels: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    modules: Dict[str, int] = {}
    status_codes: Dict[str, int] = {}


class SearchResponse(BaseModel):
    items: List[UnifiedLogEntry]
    total: int
    facets: Facets


class TraceEntry(BaseModel):
    source: str  # request | app
    timestamp: str
    level: Optional[str] = None
    module: Optional[str] = None
    message: Optional[str] = None
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    offset_ms: int = 0


class TraceResponse(BaseModel):
    request_id: str
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    total_ms: Optional[int] = None
    entries: List[TraceEntry]


# ============================================
# Helpers
# ============================================


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp string into datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _apply_text_filter(value: Optional[str], query: str) -> bool:
    """Check if a value contains the query text (case-insensitive)."""
    if not value:
        return False
    return query.lower() in value.lower()


def _apply_filter(
    entry: dict, field: str, operator: str, value: str, negate: bool
) -> bool:
    """Apply a single structured filter to an entry."""
    actual = entry.get(field)
    if actual is None:
        return negate

    actual_str = str(actual).lower()
    val_lower = value.lower()

    if operator == "eq":
        match = actual_str == val_lower
    elif operator == "prefix":
        match = actual_str.startswith(val_lower)
    elif operator == "gt":
        try:
            match = float(actual) > float(value)
        except (ValueError, TypeError):
            match = False
    elif operator == "lt":
        try:
            match = float(actual) < float(value)
        except (ValueError, TypeError):
            match = False
    elif operator == "gte":
        try:
            match = float(actual) >= float(value)
        except (ValueError, TypeError):
            match = False
    elif operator == "lte":
        try:
            match = float(actual) <= float(value)
        except (ValueError, TypeError):
            match = False
    elif operator == "contains":
        match = val_lower in actual_str
    else:
        match = actual_str == val_lower

    return not match if negate else match


# ============================================
# Main Search Endpoint
# ============================================


@router.get("", response_model=SearchResponse)
async def search_logs(
    auth: AdminAuthDep,
    q: Optional[str] = Query(None, description="Free text search"),
    filters: Optional[str] = Query(
        None, description="JSON array of structured filters"
    ),
    sources: Optional[str] = Query(
        "request,app,frontend,audit",
        description="Comma-separated sources to search",
    ),
    period: Optional[str] = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Search across all log tables with unified results."""
    repo = get_admin_search_repository()

    # Compute time range
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        t_start, t_end = start_date, end_date
    else:
        hours_map = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
        hours = hours_map.get(period or "24h", 24)
        t_start = now - timedelta(hours=hours)
        t_end = now

    start_iso = t_start.isoformat()
    end_iso = t_end.isoformat()

    source_list = [
        s.strip() for s in (sources or "request,app,frontend,audit").split(",")
    ]

    # Parse structured filters
    parsed_filters: List[dict] = []
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            pass

    all_entries: List[UnifiedLogEntry] = []
    facets = Facets()

    # ---- Search request logs ----
    if "request" in source_list:
        rows = await repo.request_logs(start_iso, end_iso)
        for r in rows:
            entry = {
                "source": "request",
                "method": r.get("method"),
                "path": r.get("path"),
                "status_code": r.get("status_code"),
                "response_time_ms": r.get("response_time_ms"),
                "message": f"{r.get('method')} {r.get('path')} → {r.get('status_code')}",
                "request_id": r.get("request_id"),
                "timestamp": r.get("timestamp", ""),
            }

            # Apply free text filter
            if q and not any(
                _apply_text_filter(entry.get(f), q)
                for f in ("path", "message", "request_id")
            ):
                continue

            # Apply structured filters
            skip = False
            for pf in parsed_filters:
                if not _apply_filter(
                    entry,
                    pf["field"],
                    pf["operator"],
                    pf["value"],
                    pf.get("negate", False),
                ):
                    skip = True
                    break
            if skip:
                continue

            all_entries.append(UnifiedLogEntry(id=str(r.get("id", "")), **entry))

            # Facets
            sc = str(r.get("status_code", ""))
            facets.sources["request"] = facets.sources.get("request", 0) + 1
            if sc:
                facets.status_codes[sc] = facets.status_codes.get(sc, 0) + 1

    # ---- Search app logs ----
    if "app" in source_list:
        rows = await repo.app_logs(start_iso, end_iso)
        for r in rows:
            extra = r.get("extra") or {}
            entry = {
                "source": "app",
                "level": r.get("level"),
                "module": r.get("module"),
                "message": r.get("message"),
                "request_id": extra.get("request_id"),
                "timestamp": r.get("logged_at", ""),
            }

            if q and not any(
                _apply_text_filter(entry.get(f), q)
                for f in ("message", "module", "level")
            ):
                continue

            skip = False
            for pf in parsed_filters:
                if not _apply_filter(
                    entry,
                    pf["field"],
                    pf["operator"],
                    pf["value"],
                    pf.get("negate", False),
                ):
                    skip = True
                    break
            if skip:
                continue

            all_entries.append(UnifiedLogEntry(id=str(r.get("id", "")), **entry))

            level = r.get("level", "")
            facets.sources["app"] = facets.sources.get("app", 0) + 1
            if level:
                facets.levels[level] = facets.levels.get(level, 0) + 1
            mod = (r.get("module") or "").split(".")[-1]
            if mod:
                facets.modules[mod] = facets.modules.get(mod, 0) + 1

    # ---- Search frontend error logs ----
    if "frontend" in source_list:
        rows = await repo.frontend_logs(start_iso, end_iso)
        for r in rows:
            entry = {
                "source": "frontend",
                "level": "ERROR",
                "message": r.get("message"),
                "path": r.get("url"),
                "module": r.get("error_type"),
                "timestamp": r.get("created_at", ""),
            }

            if q and not any(
                _apply_text_filter(entry.get(f), q)
                for f in ("message", "path", "module")
            ):
                continue

            skip = False
            for pf in parsed_filters:
                if not _apply_filter(
                    entry,
                    pf["field"],
                    pf["operator"],
                    pf["value"],
                    pf.get("negate", False),
                ):
                    skip = True
                    break
            if skip:
                continue

            all_entries.append(UnifiedLogEntry(id=str(r.get("id", "")), **entry))

            facets.sources["frontend"] = facets.sources.get("frontend", 0) + 1
            facets.levels["ERROR"] = facets.levels.get("ERROR", 0) + 1

    # ---- Search audit logs ----
    if "audit" in source_list:
        rows = await repo.audit_logs(start_iso, end_iso)
        for r in rows:
            entry = {
                "source": "audit",
                "action": r.get("action"),
                "target_type": r.get("target_type"),
                "message": f"{r.get('action')} {r.get('target_type')} by {r.get('admin_email', 'unknown')}",
                "details": r.get("details"),
                "timestamp": r.get("created_at", ""),
            }

            if q and not _apply_text_filter(entry.get("message"), q):
                continue

            skip = False
            for pf in parsed_filters:
                if not _apply_filter(
                    entry,
                    pf["field"],
                    pf["operator"],
                    pf["value"],
                    pf.get("negate", False),
                ):
                    skip = True
                    break
            if skip:
                continue

            all_entries.append(UnifiedLogEntry(id=str(r.get("id", "")), **entry))

            facets.sources["audit"] = facets.sources.get("audit", 0) + 1

    # Sort all entries by timestamp descending
    all_entries.sort(key=lambda e: e.timestamp or "", reverse=True)

    total = len(all_entries)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated = all_entries[start_idx:end_idx]

    return SearchResponse(items=paginated, total=total, facets=facets)


# ============================================
# Request Trace Endpoint
# ============================================


@router.get("/trace/{request_id}", response_model=TraceResponse)
async def get_request_trace(
    auth: AdminAuthDep,
    request_id: str,
):
    """Get all logs correlated with a specific request_id."""
    repo = get_admin_search_repository()

    req_log = await repo.get_request_log(request_id)

    method = req_log.get("method") if req_log else None
    path = req_log.get("path") if req_log else None
    status_code = req_log.get("status_code") if req_log else None
    total_ms = req_log.get("response_time_ms") if req_log else None
    req_ts = req_log.get("timestamp") if req_log else None

    entries: List[TraceEntry] = []
    base_time = _parse_ts(req_ts)

    # Add request entry
    if req_log:
        entries.append(
            TraceEntry(
                source="request",
                timestamp=req_ts or "",
                status_code=status_code,
                response_time_ms=total_ms,
                message=f"{method} {path} → {status_code}",
                offset_ms=0,
            )
        )

    # Fetch correlated app logs (where extra->>'request_id' matches)
    app_rows = await repo.app_logs_by_request_id(request_id)

    for a in app_rows:
        ts = a.get("logged_at", "")
        dt = _parse_ts(ts)
        offset = 0
        if base_time and dt:
            offset = int((dt - base_time).total_seconds() * 1000)

        mod = a.get("module") or ""
        short_mod = mod.split(".")[-1] if "." in mod else mod

        entries.append(
            TraceEntry(
                source="app",
                timestamp=ts,
                level=a.get("level"),
                module=short_mod,
                message=a.get("message"),
                offset_ms=offset,
            )
        )

    # Sort entries by offset
    entries.sort(key=lambda e: e.offset_ms)

    return TraceResponse(
        request_id=request_id,
        method=method,
        path=path,
        status_code=status_code,
        total_ms=total_ms,
        entries=entries,
    )
