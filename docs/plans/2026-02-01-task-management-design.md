# Task Management System Design

**Date:** 2026-02-01
**Status:** Approved
**Priority:** P0-P3

## Overview

A complete task management system that handles download reliability AND visibility. Solves the problem of incomplete downloads being marked as "success" and provides a unified task monitoring dashboard.

## Problems to Solve

1. **False "download success"** - Files exist but are incomplete/corrupted
2. **Retry doesn't work** - Skips existing files without checking integrity
3. **No visibility** - Can't tell which downloads actually failed
4. **Monitoring gaps** - Celery status exists but isn't integrated well

## Design Decisions

| Decision | Choice |
|----------|--------|
| File integrity | File size comparison (HEAD request) |
| Panel location | Settings → new "Tasks" tab |
| Detail level | Detailed: task list + queue stats + worker status + storage |
| Failed handling | Mixed: auto retry 3x, then manual |
| Storage | Redis only (use existing `download_status` in DB) |

---

## Part 1: File Integrity Verification

### Pre-download Check
```
1. File doesn't exist → normal download
2. File exists → HEAD request for expected size → compare with local
   - Size matches → skip, mark success
   - Size mismatch → delete local file, re-download
```

### Post-download Verification
```
1. After download, compare actual size with expected size
2. Size mismatch → mark as failed, trigger auto retry
3. After 3 retries still failed → mark as FAILED, wait for manual handling
```

### Files to Modify
- `backend/app/services/downloader.py` - Add size verification logic
- `backend/app/tasks/download_tasks.py` - Add retry count and failure handling

---

## Part 2: Task Status Data Structure

### Redis Task Status Storage

**Key format:** `task:{aweme_id}`

**Value structure:**
```json
{
  "aweme_id": "7601489034279456034",
  "video_title": "3名潜水员结伴前往中国最深的水...",
  "status": "downloading",
  "percent": 45,
  "downloaded": 52428800,
  "total": 116508672,
  "speed": "1.2 MB/s",
  "retry_count": 0,
  "max_retries": 3,
  "error": null,
  "started_at": "2026-02-01T22:30:00Z",
  "updated_at": "2026-02-01T22:31:00Z"
}
```

### Status Flow
```
pending → downloading → completed
              ↓
            failed (retry_count < 3) → downloading (auto retry)
              ↓
            failed (retry_count >= 3) → wait for manual retry
```

### Queue Stats Key
**Key:** `task:stats`
```json
{
  "pending": 2,
  "downloading": 1,
  "completed": 15,
  "failed": 3
}
```

---

## Part 3: UI Layout

### Parser Page (Link Parser)

Three status cards adjusted to:

| Card | Function |
|------|----------|
| **Queue** | Show pending/running count, click to jump to Tasks page |
| **Parse Mode** | Switch parse mode: LightHTTP (fast) / DrissionPage (stable) |
| **Storage** | NAS free space |

### Settings → Tasks Tab

```
┌─────────────────────────────────────────────────────┐
│  Tasks                                              │
├─────────────────────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │ Pending │ │ Running │ │ Done    │ │ Failed  │   │
│  │    2    │ │    1    │ │   15    │ │    3    │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │
│                                                     │
│  Celery Worker: ● Online    Storage: 7.5 TB Free   │
├─────────────────────────────────────────────────────┤
│  Filter: [All ▼]  [Search...]         [Retry All]  │
├─────────────────────────────────────────────────────┤
│  │ Status │ Video Title          │ Progress │ Act │ │
│  ├────────┼──────────────────────┼──────────┼─────┤ │
│  │ ● Run  │ 3名潜水员结伴前往... │ 45%      │     │ │
│  │ ○ Pend │ 城市地标撞出陈年...  │ -        │     │ │
│  │ ✗ Fail │ 史上最佳韦小宝...    │ 0/3      │ [↻] │ │
│  └────────┴──────────────────────┴──────────┴─────┘ │
└─────────────────────────────────────────────────────┘
```

---

## Part 4: Backend API

### New Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/tasks` | GET | Get task list (supports status filter) |
| `/api/v1/tasks/stats` | GET | Get queue statistics |
| `/api/v1/tasks/{aweme_id}/retry` | POST | Manual retry single task |
| `/api/v1/tasks/retry-all` | POST | Batch retry all failed tasks |
| `/api/v1/settings/parse-mode` | GET/PUT | Get/set parse mode |

### Response Examples

**GET /api/v1/tasks/stats**
```json
{
  "pending": 2,
  "downloading": 1,
  "completed": 15,
  "failed": 3,
  "worker_online": true,
  "storage_free": "7.5 TB",
  "storage_used_percent": 86.5
}
```

**GET /api/v1/tasks?status=failed**
```json
{
  "items": [
    {
      "aweme_id": "760148903427945603",
      "video_title": "史上最佳韦小宝...",
      "status": "failed",
      "retry_count": 3,
      "error": "File size mismatch: expected 116MB, got 7MB",
      "updated_at": "2026-02-01T22:31:00Z"
    }
  ],
  "total": 3
}
```

---

## Part 5: Implementation Checklist

### Backend Changes

| File | Change |
|------|--------|
| `app/services/downloader.py` | Complete file size verification |
| `app/tasks/download_tasks.py` | Add retry count, failure handling, status updates |
| `app/services/task_manager.py` | **NEW** - Task status management (Redis ops) |
| `app/api/tasks_router.py` | **NEW** - Task API routes |
| `app/api/__init__.py` | Register new router |

### Frontend Changes

| File | Change |
|------|--------|
| `components/TasksPanel.tsx` | **NEW** - Task management panel component |
| `components/ParseModeCard.tsx` | **NEW** - Parse mode switch card |
| `services/taskService.ts` | **NEW** - Task API calls |
| `components/SettingsView.tsx` | Add Tasks tab |
| `App.tsx` | Replace Network card with ParseModeCard, add Tasks entry |

### Implementation Priority

1. **P0** - File integrity verification + auto retry (fix core issue)
2. **P1** - TaskManager + API (backend foundation)
3. **P2** - TasksPanel UI (frontend display)
4. **P3** - ParseModeCard (UX improvement)
