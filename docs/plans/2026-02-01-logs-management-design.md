# Logs Management System Design

## Overview

A comprehensive logs management panel in Settings for users to view, filter, search, and export their activity logs.

## Requirements

### Functional Requirements
- Display user activity logs in Settings > Logs tab
- Filter by log level (INFO, WARN, ERROR, PENDING)
- Filter by time range (Today, Last 7 days, Last 30 days, Custom)
- Search logs by keyword
- Pagination with configurable page size (50, 100, 200)
- Refresh logs
- Copy logs to clipboard
- Export logs (JSON/CSV)

### Log Types
- Video operations: parse, download, delete, retry
- Collection operations: create, add/remove items
- Tag operations: create, assign tags
- Account operations: login, logout, settings changes

## Database Schema

Using existing `user_logs` table (no changes needed):

```sql
user_logs (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id),
    action VARCHAR(50),      -- fetch, download, delete, retry, login, logout...
    message TEXT,
    status VARCHAR(20),      -- success, error, warning, info, pending
    aweme_id VARCHAR(50),    -- optional video reference
    details JSONB,           -- additional metadata
    created_at TIMESTAMPTZ
)
```

### Status to Level Mapping

| DB Status | Display Level | Color |
|-----------|---------------|-------|
| success   | INFO          | Green |
| info      | INFO          | Blue  |
| warning   | WARN          | Yellow |
| error     | ERROR         | Red   |
| pending   | PENDING       | Gray  |

## API Design

### GET /api/v1/logs

Query user activity logs with filtering and pagination.

**Request:**
```
GET /api/v1/logs
  ?level=info,warning,error   # Filter by levels (comma-separated)
  &start_date=2024-01-01      # Start date (ISO format)
  &end_date=2024-01-31        # End date (ISO format)
  &search=keyword             # Search in message field
  &page=1                     # Page number (default: 1)
  &page_size=50               # Items per page (50, 100, 200)
```

**Response:**
```json
{
  "success": true,
  "logs": [
    {
      "id": "uuid",
      "action": "fetch",
      "message": "解析视频成功: 热辣情侣写真挑战赛...",
      "status": "success",
      "aweme_id": "7584773856751907620",
      "details": {},
      "created_at": "2024-01-15T12:38:17Z"
    }
  ],
  "total": 234,
  "page": 1,
  "page_size": 50,
  "total_pages": 5
}
```

### GET /api/v1/logs/export

Export logs as JSON or CSV file.

**Request:**
```
GET /api/v1/logs/export
  ?format=json|csv
  &level=...
  &start_date=...
  &end_date=...
  &search=...
```

## Frontend Components

### Component Structure

```
SettingsView.tsx
└── LogsPanel.tsx (new)
    ├── LogsToolbar.tsx
    │   ├── LevelFilter (dropdown)
    │   ├── DateRangeFilter (dropdown)
    │   └── SearchInput
    ├── LogsList.tsx
    │   └── LogItem.tsx (for each log entry)
    ├── LogsPagination.tsx
    └── LogsActions.tsx
        ├── RefreshButton
        ├── CopyButton
        └── ExportDropdown
```

### UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  📋 Logs                                                        │
│  View your activity logs                                        │
├─────────────────────────────────────────────────────────────────┤
│  [All Levels ▼] [Today ▼] [🔍 Search logs...]                   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ 12:38:17  INFO   解析视频成功: 热辣情侣写真挑战赛...        ││
│  │ 12:37:42  INFO   下载完成: 7584773856751907620              ││
│  │ 12:37:15  WARN   重试下载: 连接超时                         ││
│  │ 12:36:58  ERROR  解析失败: 无效的分享链接                    ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  Showing 50 of 234     [50 ▼]    [← 1 2 3 ... →]                │
│                                                                  │
│  [🔄 Refresh]  [📋 Copy]  [📥 Export ▼]                         │
└─────────────────────────────────────────────────────────────────┘
```

### Filter Options

**Level Filter:**
- All Levels (default)
- INFO
- WARN
- ERROR
- PENDING

**Date Range Filter:**
- Today (default)
- Last 7 days
- Last 30 days
- Custom range (date picker)

**Page Size:**
- 50 (default)
- 100
- 200

## Implementation Tasks

### Backend
1. Create `logs_router.py` with GET /api/v1/logs endpoint
2. Create `logs_repository.py` for database queries
3. Add export endpoint for JSON/CSV download

### Frontend
1. Add "Logs" tab to SettingsView.tsx
2. Create LogsPanel.tsx component
3. Implement filtering, pagination, search
4. Implement copy and export functionality

## File Changes

### New Files
- `backend/app/api/logs_router.py`
- `backend/app/repositories/logs_repository.py`
- `frontend/components/LogsPanel.tsx`

### Modified Files
- `backend/app/main.py` - register logs router
- `frontend/components/SettingsView.tsx` - add Logs tab
