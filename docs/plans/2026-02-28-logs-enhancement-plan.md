# Logs Enhancement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Monitoring Dashboard with statistics/charts and an Advanced Global Search system to the admin panel.

**Architecture:** Phase 1 adds a new `/monitoring` page with a dedicated backend stats API that aggregates data from `api_request_logs` and `application_logs` tables, rendered with Arco Charts. Phase 2 adds a `/search` page with cross-log search, request tracing, KQL-like query parsing, faceted filtering, and enhances all existing log pages with DateRangePicker and CSV export.

**Tech Stack:** React 19 + TypeScript, @arco-design/charts (G2-based), TanStack React Query, FastAPI, Supabase (PostgreSQL)

**Design Doc:** `docs/plans/2026-02-28-logs-enhancement-design.md`

---

## Phase 1: Monitoring Dashboard

### Task 1: Install @arco-design/charts

**Files:**
- Modify: `admin/package.json`

**Step 1: Install the charts library**

Run:
```bash
cd admin && npm install @arco-design/charts
```

**Step 2: Verify installation**

Run:
```bash
cd admin && npx tsc --noEmit
```
Expected: No errors

**Step 3: Commit**

```bash
git add admin/package.json admin/package-lock.json
git commit -m "chore(admin): add @arco-design/charts dependency"
```

---

### Task 2: Backend — Monitoring Stats API

**Files:**
- Create: `backend/app/api/admin/monitoring_router.py`
- Modify: `backend/app/api/admin/__init__.py` (add router registration)

**Step 1: Create the monitoring router**

Create `backend/app/api/admin/monitoring_router.py`:

```python
"""Admin API routes for system monitoring statistics."""

from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin


router = APIRouter()


# ============================================
# Response Schemas
# ============================================

class OverviewStats(BaseModel):
    total_requests: int
    error_rate: float
    avg_response_ms: float
    app_error_count: int
    frontend_error_count: int


class TrendPoint(BaseModel):
    time: str
    requests: int
    errors: int


class SlowApiEntry(BaseModel):
    path: str
    avg_ms: float
    p95_ms: float
    count: int


class ErrorEndpointEntry(BaseModel):
    path: str
    error_count: int
    last_status: Optional[int] = None


class ErrorModuleEntry(BaseModel):
    module: str
    count: int


class RecentErrorEntry(BaseModel):
    level: str
    module: Optional[str] = None
    message: str
    logged_at: str


class MonitoringStatsResponse(BaseModel):
    overview: OverviewStats
    request_trend: List[TrendPoint]
    top_slow_apis: List[SlowApiEntry]
    top_error_endpoints: List[ErrorEndpointEntry]
    log_level_distribution: dict
    top_error_modules: List[ErrorModuleEntry]
    recent_errors: List[RecentErrorEntry]


# ============================================
# Helper: compute time range and granularity
# ============================================

PERIOD_MAP = {
    "1h": (1, 5),        # 1 hour, 5-min buckets
    "6h": (6, 5),        # 6 hours, 5-min buckets
    "24h": (24, 60),     # 24 hours, 1-hour buckets
    "7d": (168, 360),    # 7 days, 6-hour buckets
    "30d": (720, 1440),  # 30 days, 1-day buckets
}


def get_time_range(
    period: Optional[str],
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> tuple[datetime, datetime, int]:
    """Returns (start, end, bucket_minutes)."""
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        delta_hours = (end_date - start_date).total_seconds() / 3600
        if delta_hours <= 6:
            bucket = 5
        elif delta_hours <= 24:
            bucket = 60
        elif delta_hours <= 168:
            bucket = 360
        else:
            bucket = 1440
        return start_date, end_date, bucket

    hours, bucket = PERIOD_MAP.get(period or "24h", (24, 60))
    return now - timedelta(hours=hours), now, bucket


# ============================================
# Main Endpoint
# ============================================

@router.get("/stats", response_model=MonitoringStatsResponse)
async def get_monitoring_stats(
    auth: AdminAuthDep,
    period: Optional[str] = Query("24h", regex="^(1h|6h|24h|7d|30d)$"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """
    Get aggregated monitoring statistics for the admin dashboard.

    - **period**: Preset time range (1h, 6h, 24h, 7d, 30d)
    - **start_date** / **end_date**: Custom date range (overrides period)
    """
    supabase = await get_async_supabase_admin()
    start, end, bucket_minutes = get_time_range(period, start_date, end_date)
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    # ---- Fetch request logs ----
    req_result = await (
        supabase.table("api_request_logs")
        .select("path,method,status_code,response_time_ms,timestamp")
        .gte("timestamp", start_iso)
        .lte("timestamp", end_iso)
        .order("timestamp", desc=False)
        .limit(10000)
        .execute()
    )
    req_logs = req_result.data or []

    # ---- Fetch app logs (level counts + recent errors) ----
    app_result = await (
        supabase.table("application_logs")
        .select("level,module,message,logged_at")
        .gte("logged_at", start_iso)
        .lte("logged_at", end_iso)
        .order("logged_at", desc=True)
        .limit(5000)
        .execute()
    )
    app_logs = app_result.data or []

    # ---- Fetch frontend error count ----
    fe_result = await (
        supabase.table("frontend_error_logs")
        .select("id", count="exact")
        .gte("created_at", start_iso)
        .lte("created_at", end_iso)
        .execute()
    )
    fe_error_count = fe_result.count or 0

    # ---- Compute overview ----
    total_requests = len(req_logs)
    error_requests = sum(1 for r in req_logs if (r.get("status_code") or 0) >= 400)
    error_rate = round((error_requests / total_requests * 100) if total_requests > 0 else 0, 2)
    avg_ms = round(
        sum(r.get("response_time_ms") or 0 for r in req_logs) / total_requests
        if total_requests > 0 else 0,
        1,
    )
    app_error_count = sum(
        1 for a in app_logs if a.get("level") in ("ERROR", "CRITICAL")
    )

    overview = OverviewStats(
        total_requests=total_requests,
        error_rate=error_rate,
        avg_response_ms=avg_ms,
        app_error_count=app_error_count,
        frontend_error_count=fe_error_count,
    )

    # ---- Compute request trend (bucketed) ----
    from collections import defaultdict

    buckets: dict[str, dict] = defaultdict(lambda: {"requests": 0, "errors": 0})
    for r in req_logs:
        ts = r.get("timestamp", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        # Truncate to bucket
        minutes = (dt.hour * 60 + dt.minute) // bucket_minutes * bucket_minutes
        if bucket_minutes >= 1440:
            key = dt.strftime("%Y-%m-%dT00:00")
        elif bucket_minutes >= 60:
            bucket_hour = minutes // 60
            key = dt.strftime(f"%Y-%m-%dT{bucket_hour:02d}:00")
        else:
            bucket_min = minutes % 60
            bucket_hour = minutes // 60
            key = dt.strftime(f"%Y-%m-%dT{bucket_hour:02d}:{bucket_min:02d}")

        buckets[key]["requests"] += 1
        if (r.get("status_code") or 0) >= 400:
            buckets[key]["errors"] += 1

    request_trend = [
        TrendPoint(time=k, requests=v["requests"], errors=v["errors"])
        for k, v in sorted(buckets.items())
    ]

    # ---- Compute top slow APIs ----
    from statistics import median

    path_stats: dict[str, list[int]] = defaultdict(list)
    for r in req_logs:
        ms = r.get("response_time_ms")
        path = r.get("path", "")
        if ms is not None and path:
            path_stats[path].append(ms)

    top_slow = []
    for path, times in path_stats.items():
        times_sorted = sorted(times)
        count = len(times_sorted)
        avg = round(sum(times_sorted) / count, 1)
        p95_idx = min(int(count * 0.95), count - 1)
        p95 = times_sorted[p95_idx]
        top_slow.append(SlowApiEntry(path=path, avg_ms=avg, p95_ms=p95, count=count))

    top_slow.sort(key=lambda x: x.avg_ms, reverse=True)
    top_slow_apis = top_slow[:10]

    # ---- Compute top error endpoints ----
    error_paths: dict[str, dict] = defaultdict(lambda: {"count": 0, "last_status": None})
    for r in req_logs:
        sc = r.get("status_code") or 0
        if sc >= 400:
            path = r.get("path", "")
            error_paths[path]["count"] += 1
            error_paths[path]["last_status"] = sc

    top_error_endpoints = sorted(
        [
            ErrorEndpointEntry(path=p, error_count=v["count"], last_status=v["last_status"])
            for p, v in error_paths.items()
        ],
        key=lambda x: x.error_count,
        reverse=True,
    )[:10]

    # ---- Compute log level distribution ----
    level_dist: dict[str, int] = defaultdict(int)
    for a in app_logs:
        level_dist[a.get("level", "UNKNOWN")] += 1

    # ---- Compute top error modules ----
    module_errors: dict[str, int] = defaultdict(int)
    for a in app_logs:
        if a.get("level") in ("ERROR", "CRITICAL", "WARNING"):
            mod = a.get("module") or "unknown"
            # Use short module name (last segment)
            short = mod.split(".")[-1] if "." in mod else mod
            module_errors[short] += 1

    top_error_modules = sorted(
        [ErrorModuleEntry(module=m, count=c) for m, c in module_errors.items()],
        key=lambda x: x.count,
        reverse=True,
    )[:10]

    # ---- Recent errors (last 5 ERROR/CRITICAL) ----
    recent_errors = [
        RecentErrorEntry(
            level=a["level"],
            module=(a.get("module") or "").split(".")[-1] or None,
            message=a.get("message", ""),
            logged_at=a.get("logged_at", ""),
        )
        for a in app_logs
        if a.get("level") in ("ERROR", "CRITICAL")
    ][:5]

    return MonitoringStatsResponse(
        overview=overview,
        request_trend=request_trend,
        top_slow_apis=top_slow_apis,
        top_error_endpoints=top_error_endpoints,
        log_level_distribution=dict(level_dist),
        top_error_modules=top_error_modules,
        recent_errors=recent_errors,
    )
```

**Step 2: Register the router in admin/__init__.py**

In `backend/app/api/admin/__init__.py`, add after the existing router imports:

```python
from app.api.admin.monitoring_router import router as monitoring_router
```

And add the include:

```python
admin_router.include_router(monitoring_router, prefix="/monitoring", tags=["Admin - Monitoring"])
```

**Step 3: Verify backend starts**

Run:
```bash
cd backend && uv run python -c "from app.api.admin.monitoring_router import router; print('OK')"
```
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/api/admin/monitoring_router.py backend/app/api/admin/__init__.py
git commit -m "feat(backend): add monitoring stats API endpoint"
```

---

### Task 3: Frontend — Monitoring API Hook

**Files:**
- Create: `admin/src/api/endpoints/monitoring.ts`

**Step 1: Create the API hook**

Create `admin/src/api/endpoints/monitoring.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface OverviewStats {
  total_requests: number
  error_rate: number
  avg_response_ms: number
  app_error_count: number
  frontend_error_count: number
}

export interface TrendPoint {
  time: string
  requests: number
  errors: number
}

export interface SlowApiEntry {
  path: string
  avg_ms: number
  p95_ms: number
  count: number
}

export interface ErrorEndpointEntry {
  path: string
  error_count: number
  last_status: number | null
}

export interface ErrorModuleEntry {
  module: string
  count: number
}

export interface RecentErrorEntry {
  level: string
  module: string | null
  message: string
  logged_at: string
}

export interface MonitoringStats {
  overview: OverviewStats
  request_trend: TrendPoint[]
  top_slow_apis: SlowApiEntry[]
  top_error_endpoints: ErrorEndpointEntry[]
  log_level_distribution: Record<string, number>
  top_error_modules: ErrorModuleEntry[]
  recent_errors: RecentErrorEntry[]
}

interface MonitoringParams {
  period?: string
  start_date?: string
  end_date?: string
}

export function useMonitoringStats(params: MonitoringParams) {
  return useQuery({
    queryKey: ['admin', 'monitoring', 'stats', params],
    queryFn: async () => {
      const query: Record<string, string> = {}
      if (params.period) query.period = params.period
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date

      const { data } = await apiClient.get<MonitoringStats>(
        '/api/v1/admin/monitoring/stats',
        { params: query },
      )
      return data
    },
    refetchInterval: 60_000,
  })
}
```

**Step 2: Verify**

Run:
```bash
cd admin && npx tsc --noEmit
```
Expected: No errors

**Step 3: Commit**

```bash
git add admin/src/api/endpoints/monitoring.ts
git commit -m "feat(admin): add monitoring stats API hook"
```

---

### Task 4: Frontend — Monitoring Page (Overview Cards + Trend Chart)

**Files:**
- Create: `admin/src/pages/monitoring/index.tsx`

**Step 1: Create the monitoring page**

Create `admin/src/pages/monitoring/index.tsx` with the full page component. This includes:

- Time range selector (preset buttons + DatePicker.RangePicker)
- 4 overview Statistic cards in a Row/Col grid
- DualAxes chart: bar (request count) + line (error count) by time bucket
- Top Slow APIs table + Top Error Endpoints table side by side
- Pie chart for log level distribution
- Top Error Modules table + Recent Errors list side by side

Use `@arco-design/charts` for `DualAxes` and `Pie`.
Use Arco Design `Card`, `Grid.Row`, `Grid.Col`, `Statistic`, `Table`, `Tag`, `Button`, `DatePicker`, `Space` components.

Key interactions:
- Clicking a row in Top Slow APIs navigates to `/request-logs?path=<path>`
- Clicking a recent error navigates to `/request-logs?tab=app-logs`
- Time range buttons update `period` state, custom DatePicker sets `start_date`/`end_date`
- Active period button highlighted with `type="primary"`

**Step 2: Verify**

Run:
```bash
cd admin && npx tsc --noEmit
```
Expected: No errors

**Step 3: Commit**

```bash
git add admin/src/pages/monitoring/index.tsx
git commit -m "feat(admin): add monitoring dashboard page with charts and stats"
```

---

### Task 5: Register Monitoring Route and Menu Item

**Files:**
- Modify: `admin/src/App.tsx` — add route
- Modify: `admin/src/layouts/AdminLayout.tsx` — add menu item

**Step 1: Add route in App.tsx**

Import the component:
```typescript
import { MonitoringDashboard } from './pages/monitoring'
```

Add route inside the AdminLayout routes (between Audit Logs and Request Logs):
```tsx
<Route path="/monitoring" element={<MonitoringDashboard />} />
```

**Step 2: Add menu item in AdminLayout.tsx**

Import icon:
```typescript
import { IconDashboard } from '@arco-design/web-react/icon'
```

Add menu item between "Audit Logs" and "Request Logs":
```typescript
{ key: '/monitoring', label: 'Monitoring', icon: <IconDashboard /> },
```

**Step 3: Verify**

Run:
```bash
cd admin && npx tsc --noEmit && npm run build
```
Expected: Build succeeds

**Step 4: Commit**

```bash
git add admin/src/App.tsx admin/src/layouts/AdminLayout.tsx
git commit -m "feat(admin): register monitoring route and sidebar menu item"
```

---

### Task 6: Verify Phase 1 End-to-End

**Step 1: Start backend**

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8080
```

**Step 2: Start admin frontend**

```bash
cd admin && npm run dev
```

**Step 3: Open browser and verify**

1. Navigate to admin login, sign in
2. Click "Monitoring" in sidebar
3. Verify: overview cards show data, trend chart renders, top tables populate
4. Switch time range (1h → 24h → 7d) — chart should update
5. Click a row in Top Slow APIs — should navigate to Request Logs

**Step 4: Take screenshot for verification**

Use Playwright MCP to take a screenshot of the monitoring page.

**Step 5: Commit any fixes, then final Phase 1 commit**

```bash
git add -A && git commit -m "feat(admin): complete monitoring dashboard (Phase 1)"
```

---

## Phase 2: Advanced Search & Filter

### Task 7: Shared TimeRangeSelector Component

**Files:**
- Create: `admin/src/components/TimeRangeSelector.tsx`

**Step 1: Create the shared component**

Create `admin/src/components/TimeRangeSelector.tsx`:

A reusable component that renders:
- Preset period buttons: `1h`, `6h`, `24h`, `7d`, `30d`
- A `DatePicker.RangePicker` for custom date range
- Props: `period`, `onPeriodChange`, `dateRange`, `onDateRangeChange`
- Active preset button styled with `type="primary"`
- Selecting custom date range clears the preset (sets period to empty)
- Selecting a preset clears the custom date range

**Step 2: Verify**

```bash
cd admin && npx tsc --noEmit
```

**Step 3: Commit**

```bash
git add admin/src/components/TimeRangeSelector.tsx
git commit -m "feat(admin): add shared TimeRangeSelector component"
```

---

### Task 8: CSV Export Utility

**Files:**
- Create: `admin/src/utils/csv-export.ts`

**Step 1: Create the utility**

Create `admin/src/utils/csv-export.ts`:

```typescript
/**
 * Export an array of objects to a CSV file and trigger download.
 */
export function exportToCsv(
  filename: string,
  rows: Record<string, unknown>[],
  columns?: { key: string; label: string }[],
) {
  if (rows.length === 0) return

  const cols = columns || Object.keys(rows[0]).map((k) => ({ key: k, label: k }))
  const header = cols.map((c) => c.label).join(',')

  const body = rows
    .map((row) =>
      cols
        .map((c) => {
          const val = row[c.key]
          if (val == null) return ''
          const str = typeof val === 'object' ? JSON.stringify(val) : String(val)
          // Escape quotes and wrap in quotes if contains comma/quote/newline
          if (str.includes(',') || str.includes('"') || str.includes('\n')) {
            return `"${str.replace(/"/g, '""')}"`
          }
          return str
        })
        .join(','),
    )
    .join('\n')

  const csv = `${header}\n${body}`
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
```

**Step 2: Verify**

```bash
cd admin && npx tsc --noEmit
```

**Step 3: Commit**

```bash
git add admin/src/utils/csv-export.ts
git commit -m "feat(admin): add CSV export utility"
```

---

### Task 9: KQL-like Query Parser

**Files:**
- Create: `admin/src/utils/query-parser.ts`

**Step 1: Create the parser**

Create `admin/src/utils/query-parser.ts`:

Parses queries like `level:ERROR AND module:supabase* AND response_time:>500` into structured filter objects.

```typescript
export interface ParsedFilter {
  field: string
  operator: 'eq' | 'prefix' | 'gt' | 'lt' | 'gte' | 'lte' | 'contains'
  value: string
  negate: boolean
}

export interface ParsedQuery {
  freeText: string
  filters: ParsedFilter[]
}

/**
 * Parse a KQL-like query string into structured filters.
 *
 * Supported syntax:
 * - `keyword` — free text search
 * - `field:value` — exact match
 * - `field:val*` — prefix match
 * - `field:>N` — greater than
 * - `field:<N` — less than
 * - `AND` / `OR` — logical operators (AND is default)
 * - `NOT field:value` — negation
 */
export function parseQuery(input: string): ParsedQuery {
  const filters: ParsedFilter[] = []
  const freeTextParts: string[] = []

  // Split by AND/OR (keep it simple: treat everything as AND for now)
  const tokens = input
    .replace(/\bOR\b/g, 'AND')
    .split(/\bAND\b/)
    .map((t) => t.trim())
    .filter(Boolean)

  for (let token of tokens) {
    let negate = false
    if (token.startsWith('NOT ')) {
      negate = true
      token = token.slice(4).trim()
    }

    const colonIdx = token.indexOf(':')
    if (colonIdx === -1) {
      freeTextParts.push(token)
      continue
    }

    const field = token.slice(0, colonIdx).trim()
    let value = token.slice(colonIdx + 1).trim()

    let operator: ParsedFilter['operator'] = 'eq'
    if (value.startsWith('>')) {
      operator = 'gt'
      value = value.slice(1)
    } else if (value.startsWith('<')) {
      operator = 'lt'
      value = value.slice(1)
    } else if (value.startsWith('>=')) {
      operator = 'gte'
      value = value.slice(2)
    } else if (value.startsWith('<=')) {
      operator = 'lte'
      value = value.slice(2)
    } else if (value.endsWith('*')) {
      operator = 'prefix'
      value = value.slice(0, -1)
    }

    filters.push({ field, operator, value, negate })
  }

  return {
    freeText: freeTextParts.join(' '),
    filters,
  }
}

/**
 * Convert parsed query to API params for the search endpoint.
 */
export function queryToParams(parsed: ParsedQuery): Record<string, string> {
  const params: Record<string, string> = {}
  if (parsed.freeText) {
    params.q = parsed.freeText
  }
  // Serialize filters as JSON for the backend
  if (parsed.filters.length > 0) {
    params.filters = JSON.stringify(parsed.filters)
  }
  return params
}
```

**Step 2: Verify**

```bash
cd admin && npx tsc --noEmit
```

**Step 3: Commit**

```bash
git add admin/src/utils/query-parser.ts
git commit -m "feat(admin): add KQL-like query parser utility"
```

---

### Task 10: Backend — Inject request_id into loguru context

**Files:**
- Modify: `backend/app/middleware/request_logging.py`

**Step 1: Add loguru context binding in the middleware**

In `request_logging.py`, after generating the `request_id`, add a loguru context bind so all application logs within that request carry the `request_id`:

```python
from loguru import logger

# After: request_id = str(uuid.uuid4())
# Add:
with logger.contextualize(request_id=request_id):
    response = await call_next(request)
```

This wraps the `call_next(request)` in a loguru context, so any `logger.info(...)` call during request processing will have `request_id` in `record["extra"]`.

**Step 2: Update DatabaseLogSink to include request_id in extra**

The `db_log_sink.py` `_serialize` method already stores `record["extra"]` as the `extra` JSONB field. Since `logger.contextualize` adds `request_id` to `extra`, it will automatically be saved. No changes needed to the sink.

**Step 3: Verify**

```bash
cd backend && uv run python -c "from app.middleware.request_logging import RequestLoggingMiddleware; print('OK')"
```

**Step 4: Commit**

```bash
git add backend/app/middleware/request_logging.py
git commit -m "feat(backend): inject request_id into loguru context for log correlation"
```

---

### Task 11: Backend — Cross-Log Search API

**Files:**
- Create: `backend/app/api/admin/search_router.py`
- Modify: `backend/app/api/admin/__init__.py`

**Step 1: Create the search router**

Create `backend/app/api/admin/search_router.py`:

Endpoints:
- `GET /admin/search` — Cross-log search with unified results
  - Params: `q` (free text), `filters` (JSON array of ParsedFilter), `sources` (comma-separated: request,app,frontend,audit), `period`, `start_date`, `end_date`, `page`, `pageSize`
  - Queries each selected source table with appropriate filters
  - Merges results into a unified timeline sorted by timestamp
  - Returns: `{ items: UnifiedLogEntry[], total: int, facets: Facets }`

- `GET /admin/search/facets` — Get facet counts for current query
  - Returns counts by level, source, module, status_code, etc.

- `GET /admin/search/trace/{request_id}` — Get all logs for a specific request_id
  - Queries `api_request_logs` by `request_id` + `application_logs` where `extra->>'request_id'` matches
  - Returns chronological list of all related entries

**Step 2: Register the router**

In `backend/app/api/admin/__init__.py`:

```python
from app.api.admin.search_router import router as search_router
admin_router.include_router(search_router, prefix="/search", tags=["Admin - Search"])
```

**Step 3: Verify**

```bash
cd backend && uv run python -c "from app.api.admin.search_router import router; print('OK')"
```

**Step 4: Commit**

```bash
git add backend/app/api/admin/search_router.py backend/app/api/admin/__init__.py
git commit -m "feat(backend): add cross-log search and request tracing API"
```

---

### Task 12: Frontend — Global Search Page

**Files:**
- Create: `admin/src/pages/search/index.tsx`
- Create: `admin/src/api/endpoints/search.ts`

**Step 1: Create the search API hooks**

Create `admin/src/api/endpoints/search.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface UnifiedLogEntry {
  id: string
  source: 'request' | 'app' | 'frontend' | 'audit'
  timestamp: string
  level?: string
  method?: string
  path?: string
  status_code?: number
  response_time_ms?: number
  module?: string
  message?: string
  action?: string
  target_type?: string
  request_id?: string
  details?: Record<string, unknown>
}

export interface Facets {
  levels: Record<string, number>
  sources: Record<string, number>
  modules: Record<string, number>
  status_codes: Record<string, number>
}

export interface SearchResponse {
  items: UnifiedLogEntry[]
  total: number
  facets: Facets
}

export interface TraceEntry {
  source: 'request' | 'app'
  timestamp: string
  level?: string
  module?: string
  message?: string
  status_code?: number
  response_time_ms?: number
  offset_ms: number
}

export interface TraceResponse {
  request_id: string
  method?: string
  path?: string
  status_code?: number
  total_ms?: number
  entries: TraceEntry[]
}
```

Plus `useSearch(params)`, `useSearchFacets(params)`, `useRequestTrace(requestId)` hooks.

**Step 2: Create the search page**

Create `admin/src/pages/search/index.tsx`:

Layout:
- Top: Search input with KQL syntax hints, TimeRangeSelector
- Left sidebar: Faceted checkboxes (Level, Source, Module) with counts
- Center: Unified timeline list, color-coded by source
- Click request entry → show trace panel (Modal or Drawer)
- Bottom: Pagination
- URL state: all filters encoded in query params
- Saved searches: localStorage dropdown in search bar

**Step 3: Verify**

```bash
cd admin && npx tsc --noEmit
```

**Step 4: Commit**

```bash
git add admin/src/pages/search/ admin/src/api/endpoints/search.ts
git commit -m "feat(admin): add global cross-log search page with facets and tracing"
```

---

### Task 13: Add DateRangePicker and Export to Existing Pages

**Files:**
- Modify: `admin/src/pages/request-logs/index.tsx`
- Modify: `admin/src/pages/audit-logs/index.tsx`

**Step 1: Add TimeRangeSelector to Request Logs page**

In the filter Card of each tab (Request Logs, Frontend Errors, Application Logs), replace or augment existing filters with the shared `TimeRangeSelector` component. Pass `start_date` / `end_date` to the API hooks.

**Step 2: Add Export button**

Add an "Export CSV" button to the top-right of each tab's filter Card. On click, call `exportToCsv()` with the current page's data.

**Step 3: Add TimeRangeSelector to Audit Logs page**

Same pattern: add `TimeRangeSelector` to the filter Card, pass dates to `useAuditLogs`.

**Step 4: Verify**

```bash
cd admin && npx tsc --noEmit && npm run build
```

**Step 5: Commit**

```bash
git add admin/src/pages/request-logs/index.tsx admin/src/pages/audit-logs/index.tsx
git commit -m "feat(admin): add date range picker and CSV export to all log pages"
```

---

### Task 14: Register Search Route and Menu Item

**Files:**
- Modify: `admin/src/App.tsx`
- Modify: `admin/src/layouts/AdminLayout.tsx`

**Step 1: Add route and menu item**

Import:
```typescript
import { GlobalSearch } from './pages/search'
```

Route:
```tsx
<Route path="/search" element={<GlobalSearch />} />
```

Menu item (place near top, before Audit Logs):
```typescript
{ key: '/search', label: 'Search', icon: <IconSearch /> },
```

**Step 2: Verify**

```bash
cd admin && npx tsc --noEmit && npm run build
```

**Step 3: Commit**

```bash
git add admin/src/App.tsx admin/src/layouts/AdminLayout.tsx
git commit -m "feat(admin): register search route and sidebar menu item"
```

---

### Task 15: URL State & Saved Searches

**Step 1: Add URL state sync to Search page**

Use `useSearchParams` from react-router-dom to sync search query, period, filters to URL.

**Step 2: Add saved searches**

Add a dropdown in the search input that:
- Shows saved searches from `localStorage.getItem('saved-searches')`
- Has a "Save current search" option
- Has delete buttons per saved item

**Step 3: Verify & Commit**

```bash
cd admin && npx tsc --noEmit && npm run build
git add -A && git commit -m "feat(admin): add URL state persistence and saved searches"
```

---

### Task 16: Verify Phase 2 End-to-End

**Step 1: Restart backend and frontend**

**Step 2: Verify Search page**

1. Navigate to Search page
2. Type `level:ERROR` → verify results from app logs
3. Type `status:5*` → verify request log results
4. Check facet counts update
5. Click a request entry → verify trace panel shows correlated logs
6. Switch time range → results update
7. Export CSV → file downloads
8. Save a search → verify it appears in dropdown
9. Copy URL → paste in new tab → same results

**Step 3: Verify enhanced existing pages**

1. Go to Request Logs → verify DateRangePicker works
2. Click Export → CSV downloads
3. Go to Audit Logs → same checks

**Step 4: Take screenshots**

**Step 5: Final commit**

```bash
git add -A && git commit -m "feat(admin): complete advanced search and filter (Phase 2)"
```

---

## Verification Checklist

- [ ] `cd admin && npx tsc --noEmit` — no TypeScript errors
- [ ] `cd admin && npm run build` — production build succeeds
- [ ] `cd backend && uv run python -c "from app.api.admin import admin_router"` — backend imports OK
- [ ] Monitoring page renders with charts and data
- [ ] Search page returns cross-log results
- [ ] Request tracing shows correlated logs
- [ ] DateRangePicker works on all log pages
- [ ] CSV export produces valid files
- [ ] URL sharing preserves search state
