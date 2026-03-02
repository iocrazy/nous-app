# Logs Enhancement Design

Date: 2026-02-28

## Overview

Enhance the admin panel's logging system with a Monitoring Dashboard and Advanced Search capabilities. The system currently has four log types (Request Logs, Frontend Errors, Application Logs, Audit Logs) with basic list/filter views. This design adds visualization, cross-log search, request tracing, and advanced filtering.

## Phase 1: Monitoring Dashboard

### Location

New sidebar menu item "Monitoring" → independent page `/monitoring`.

### Layout

```
┌─ Time Range ──────────────────────────────────────────────┐
│  [Last 1h] [6h] [24h] [7d] [30d]  📅 Custom DateRange    │
├───────────┬───────────┬───────────┬───────────────────────┤
│ Total     │ Error     │ Avg       │ Active App            │
│ Requests  │ Rate      │ Response  │ Errors                │
├───────────┴───────────┴───────────┴───────────────────────┤
│  Request Volume & Error Rate (DualAxes chart)             │
│  X: time  Y1: request count (bar)  Y2: error rate% (line)│
├──────────────────────────┬────────────────────────────────┤
│  Top 10 Slow APIs        │  Top 10 Error Endpoints        │
│  (table: path, avg_ms,   │  (table: path, error_count,    │
│   p95_ms, count)         │   last_status)                 │
├──────────────────────────┴────────────────────────────────┤
│  App Log Level Distribution (Pie chart)                   │
├──────────────────────────┬────────────────────────────────┤
│  Top Error Modules        │  Recent Errors (last 5)       │
│  (table: module, count)   │  (ERROR/CRITICAL entries)     │
└──────────────────────────┴────────────────────────────────┘
```

### Backend API

`GET /api/v1/admin/monitoring/stats`

**Query params**: `period` (1h|6h|24h|7d|30d) or `start_date` + `end_date`

**Response**:
```json
{
  "overview": {
    "total_requests": 1234,
    "error_rate": 2.3,
    "avg_response_ms": 45,
    "app_error_count": 5,
    "frontend_error_count": 3
  },
  "request_trend": [
    { "time": "2026-02-28T10:00", "requests": 120, "errors": 3 }
  ],
  "top_slow_apis": [
    { "path": "/api/v1/videos/fetch", "avg_ms": 320, "p95_ms": 580, "count": 45 }
  ],
  "top_error_endpoints": [
    { "path": "/api/v1/resources", "error_count": 12, "last_status": 500 }
  ],
  "log_level_distribution": {
    "INFO": 200, "WARNING": 30, "ERROR": 12, "SUCCESS": 8, "CRITICAL": 1
  },
  "top_error_modules": [
    { "module": "request_logging", "count": 5 }
  ],
  "recent_errors": [
    { "level": "ERROR", "module": "...", "message": "...", "logged_at": "..." }
  ]
}
```

**Time granularity auto-adaptation**:
- 1h/6h → 5-minute buckets
- 24h → 1-hour buckets
- 7d → 6-hour buckets
- 30d → 1-day buckets

### Frontend

- **Chart library**: `@arco-design/charts` (G2-based, matches Arco Design theme)
- **Components**: DualAxes (trend), Pie (log levels), Statistic cards, Table (top lists)
- **Auto-refresh**: React Query `refetchInterval: 60_000` (60s)
- **Interactions**:
  - Click top slow/error API row → navigate to Request Logs with path filter
  - Click recent error → navigate to Application Logs
  - Time range persisted in URL query param (`?period=24h`)

### Files

| File | Action |
|------|--------|
| `admin/src/pages/monitoring/index.tsx` | **New** — Monitoring page |
| `admin/src/api/endpoints/monitoring.ts` | **New** — `useMonitoringStats` hook |
| `backend/app/api/admin/monitoring_router.py` | **New** — Stats API |
| `backend/app/api/admin/__init__.py` | **Edit** — Register router |
| `admin/src/App.tsx` | **Edit** — Add route |
| `admin/src/layouts/AdminLayout.tsx` | **Edit** — Add sidebar menu item |

---

## Phase 2: Advanced Search & Filter

### 2.1 Global Cross-Log Search Page

New sidebar menu item "Search" → page `/search`.

**Features**:
- Single search box queries across all 4 log tables simultaneously
- Results displayed in unified timeline, color-coded by source
- Faceted sidebar showing counts per filter value
- Detail panel on click

**Layout**:
```
┌─ Search ──────────────────────────────────────────────────┐
│  🔍 [structured query input with syntax hints]            │
├───────────────────────────────────────────────────────────┤
│  Results (47)  [Request: 20] [App: 15] [FE: 8] [Audit: 4]│
├────────────────────┬──────────────────────────────────────┤
│  Facets            │  Timeline (mixed, sorted by time)    │
│  Level             │  12:43 🟢 INFO [app] supabase_cli... │
│  ☑ ERROR (12)      │  12:43 🔵 200  [req] GET /api/v1/.. │
│  ☑ WARNING (30)    │  12:42 🔴 500  [req] POST /api/v1.. │
│  Source            │    └─ 🔗 Related: 2 app logs         │
│  ☑ Request (20)    │  12:40 ⚙️ UPDATE [audit] Setting... │
│  Module            │                                      │
│    supabase (8)    │                                      │
└────────────────────┴──────────────────────────────────────┘
```

### 2.2 Request ID Correlation (Request Tracing)

Click any request log entry → trace panel shows all related logs by `request_id`:

```
┌─ Request Trace: req-abc-123 ──────────────────────┐
│  GET /api/v1/videos/fetch  →  200  (320ms)        │
│  Timeline:                                         │
│  00ms   📥 Request received                        │
│  12ms   📝 [INFO] supabase_client: query start     │
│  150ms  📝 [INFO] downloader: download start       │
│  290ms  ⚠️ [WARN] downloader: retry #1            │
│  320ms  📤 Response sent (200)                     │
└────────────────────────────────────────────────────┘
```

**Backend requirement**: Inject `request_id` from middleware into loguru context so application logs can be correlated with request logs.

### 2.3 Structured Query Language (KQL-like)

| Syntax | Meaning | Example |
|--------|---------|---------|
| `keyword` | Full-text search | `supabase` |
| `field:value` | Exact match | `level:ERROR` |
| `field:val*` | Prefix match | `module:supabase*` |
| `field:>N` | Numeric comparison | `response_time:>500` |
| `AND` / `OR` | Logical combination | `level:ERROR AND module:main` |
| `NOT` | Exclusion | `NOT path:/health` |

Parsed on the frontend → converted to API query parameters.

### 2.4 Faceted Filtering

Each filter shows count of matching entries. Requires backend facets API:
`GET /api/v1/admin/search/facets?q=...` → returns counts per level, source, module, etc.

### 2.5 Saved Searches

Stored in `localStorage`. Dropdown menu in search bar for quick access:
- "Production slow requests" → `response_time:>500 AND status:5*`
- "App errors today" → `level:ERROR AND source:app-logs`

### 2.6 URL State Sharing

All filter/search state encoded in URL query params. Copy URL to share exact view with colleagues.

### 2.7 DateRangePicker

Shared time range component used across all log pages:
- Preset buttons: Last 1h / 6h / 24h / 7d / 30d
- Custom date range picker (Arco Design `DatePicker.RangePicker`)

### 2.8 CSV Export

"Export" button on each log table, generates CSV from current filtered data on the frontend.

### Files

| File | Action |
|------|--------|
| `admin/src/pages/search/index.tsx` | **New** — Global search page |
| `admin/src/api/endpoints/search.ts` | **New** — Search API hooks |
| `admin/src/components/TimeRangeSelector.tsx` | **New** — Shared time range component |
| `admin/src/utils/query-parser.ts` | **New** — KQL-like query parser |
| `admin/src/utils/csv-export.ts` | **New** — CSV export utility |
| `backend/app/api/admin/search_router.py` | **New** — Cross-log search + facets API |
| `backend/app/middleware/request_logging.py` | **Edit** — Inject request_id to loguru context |
| `admin/src/pages/request-logs/index.tsx` | **Edit** — Add DateRangePicker, export |
| `admin/src/pages/audit-logs/index.tsx` | **Edit** — Add DateRangePicker, export |
| `admin/src/App.tsx` | **Edit** — Add /search route |
| `admin/src/layouts/AdminLayout.tsx` | **Edit** — Add Search menu item |

---

## Future Phases

### Phase 3: Real-time Log Streaming

- "Live Tail" mode on Application Logs tab
- Supabase Realtime subscription
- New log highlight animation
- Auto-scroll with pause on hover

### Phase 4: Alerts & Notifications

- New `alert_rules` table for threshold configuration
- Error rate > X% triggers Discord notification
- Alert history page
- Mute/snooze support

---

## Tech Stack

- **Charts**: `@arco-design/charts` (G2-based)
- **UI**: Arco Design components (existing)
- **State**: React Query / TanStack Query (existing)
- **Backend**: FastAPI + Supabase (existing)
- **Database**: PostgreSQL with existing log tables
