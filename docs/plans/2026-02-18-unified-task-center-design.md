# Unified Task Center — Full-Stack Design

> Date: 2026-02-18
> Status: Approved
> Scope: Transfer (upload/download) + Transcode + AI Pipeline + Parser

## Problem

The system has 5 scattered "task-like" modules with independent architectures:
- **Upload**: React state (UploadContext) — lost on page refresh
- **Download**: `download_tasks` DB table + HTTP polling (2s interval)
- **Transcode**: `resource_versions.transcode_status` field + no real-time feedback
- **AI Pipeline**: `douyin_videos` scattered status fields + 10s polling
- **Parser**: inline API call, no task tracking

No unified model, no real-time push, no persistent upload progress.

## Solution

1. **Unified `unified_tasks` table** — single source of truth for all task types
2. **Supabase Realtime** — automatic push on table changes, replacing all polling
3. **TaskTracker service** — backend write layer with throttling
4. **TaskManagerContext** — frontend global state with Realtime subscription

## Architecture

```
┌─────────────┐    create row      ┌──────────────────┐    Realtime    ┌──────────┐
│ FastAPI API  │ ─────────────────→ │  unified_tasks   │ ──────────────→│ Frontend │
│ (trigger)    │                    │  (Supabase)      │   auto-push    │ realtime │
└──────┬──────┘                    └────────▲─────────┘               └──────────┘
       │ dispatch                           │
       ▼                                    │ UPDATE status/progress
┌─────────────┐                             │
│ Celery Task │ ────────────────────────────┘
│ (Worker)    │   TaskTracker writes back
└─────────────┘
```

## Section 1: Data Model

### unified_tasks table

```sql
CREATE TABLE unified_tasks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id),
  task_type    VARCHAR(20) NOT NULL,  -- 'download' | 'upload' | 'transcode' | 'ai_pipeline'
  status       VARCHAR(20) NOT NULL DEFAULT 'pending',
                -- pending → processing → completed | failed | cancelled

  -- Target association
  resource_id  TEXT,           -- associated resource ID (upload/transcode)
  video_id     TEXT,           -- associated video platform_id (download/ai)

  -- Progress tracking
  progress     SMALLINT DEFAULT 0,  -- 0-100
  speed        BIGINT,              -- bytes/sec (transfer types) or null
  total_bytes  BIGINT,              -- total file size

  -- Display info
  title        TEXT NOT NULL,       -- display name (filename/video title)
  subtitle     TEXT,                -- subtitle (e.g. "480p → 720p → 1080p")
  error_msg    TEXT,                -- error message on failure

  -- Type-specific data
  metadata     JSONB DEFAULT '{}',
    -- download: {url, cover_url, author, aweme_type, options}
    -- upload:   {scope_type, scope_id, folder_id, mime_type, file_hash}
    -- transcode:{version_id, tiers: ["480p","720p"], current_tier}
    -- ai:       {pipeline: ["extract","transcribe","summarize"], current_step}

  -- Timestamps
  created_at   TIMESTAMPTZ DEFAULT now(),
  started_at   TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_unified_tasks_user_status ON unified_tasks(user_id, status);
CREATE INDEX idx_unified_tasks_type ON unified_tasks(task_type);

-- Enable Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE unified_tasks;
```

### Status flow

```
pending → processing → completed
                    ↘ failed → (retry) → pending
         → cancelled
```

### Relationship with existing tables

- `download_tasks` → migrate data then drop
- Upload progress (React state) → replaced by unified_tasks rows
- `resource_versions.transcode_status` → kept for resource-level status, unified_tasks adds tracking
- `douyin_videos` AI status fields → kept for video-level status, unified_tasks adds tracking

## Section 2: Backend Architecture

### TaskTracker service

```python
# backend/app/services/task_tracker.py

class TaskTracker:
    """Unified task lifecycle manager — writes to unified_tasks table."""

    THROTTLE_INTERVAL = 1.0  # max 1 DB write per second per task

    async def create(self, user_id, task_type, title, **kwargs) -> str:
        """Create task record, return task_id. Call before dispatching Celery."""

    async def start(self, task_id):
        """Mark started: status=processing, started_at=now()"""

    async def update_progress(self, task_id, progress, speed=None, subtitle=None):
        """Update progress 0-100. Throttled to 1 write/sec for transfer tasks."""

    async def complete(self, task_id, metadata_patch=None):
        """Mark completed: status=completed, progress=100, completed_at=now()"""

    async def fail(self, task_id, error_msg):
        """Mark failed: status=failed, error_msg=..."""

    async def cancel(self, task_id):
        """Mark cancelled + revoke Celery task"""
```

### Integration points

**Download** (`download_tasks.py`):
- `tracker.create(user_id, 'download', title)` before dispatch
- `tracker.update_progress()` during download
- `tracker.complete()` on success

**Upload** (`resources_router.py`):
- `tracker.create(user_id, 'upload', filename)` at upload start
- `tracker.update_progress()` during file write
- `tracker.complete()` when done

**Transcode** (`transcode_tasks.py`):
- `tracker.create(user_id, 'transcode', filename)` when queued
- `tracker.update_progress(33, subtitle='Encoding 480p...')` per tier
- `tracker.complete()` when all tiers done

**AI Pipeline** (`ai_tasks.py`):
- `tracker.create(user_id, 'ai_pipeline', title)` when pipeline starts
- `tracker.update_progress(33/66/100)` per pipeline step
- `tracker.complete()` when pipeline finishes

### Task Manager API

```
GET    /api/v1/task-manager/tasks         — paginated list (Settings page)
GET    /api/v1/task-manager/tasks/active  — active tasks (TopBar panel init)
GET    /api/v1/task-manager/tasks/stats   — counts by type and status
POST   /api/v1/task-manager/tasks/{id}/cancel  — cancel task
POST   /api/v1/task-manager/tasks/{id}/retry   — retry failed task
DELETE /api/v1/task-manager/tasks/{id}         — delete record
```

## Section 3: Frontend Architecture

### TaskManagerContext

```typescript
// frontend/contexts/TaskManagerContext.tsx

interface UnifiedTask {
  id: string;
  task_type: 'download' | 'upload' | 'transcode' | 'ai_pipeline';
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  title: string;
  subtitle?: string;
  progress: number;
  speed?: number;
  total_bytes?: number;
  error_msg?: string;
  resource_id?: string;
  video_id?: string;
  metadata: Record<string, any>;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

interface TaskManagerState {
  tasks: UnifiedTask[];
  activeCounts: { download: number; upload: number; transcode: number; ai_pipeline: number };
  totalActive: number;
}
```

### Supabase Realtime subscription

```typescript
const channel = supabase
  .channel('user-tasks')
  .on('postgres_changes', {
    event: '*',
    schema: 'public',
    table: 'unified_tasks',
    filter: `user_id=eq.${userId}`,
  }, (payload) => {
    dispatch({ type: payload.eventType, task: payload.new });
  })
  .subscribe();
```

### Two UI entry points

**TopBar Quick Panel** — shows active tasks only, mixed timeline, icon per type:
- `↑` Upload  `↓` Download  `⟳` Transcode  `✦` AI
- Footer: "3 completed · 0 failed [Clear] [View All →]"

**Settings → Tasks Page** — full management with 5 tabs:
- `[ All ] [ Downloads ] [ Uploads ] [ Transcode ] [ AI Pipeline ]`
- Status filter, batch retry, clear completed, error details

### Upload flow change

Before: frontend UploadContext (React state) → lost on refresh
After: backend creates unified_tasks row → Realtime pushes progress → survives refresh

### Code to delete

- `UploadContext.tsx` — replaced by TaskManagerContext
- `TasksPanel.tsx` polling logic — replaced by Realtime
- `AITasksPanel.tsx` polling logic — replaced by Realtime

## Section 4: Migration Strategy

### Phase 1: Infrastructure (DB + TaskTracker + Realtime)

New files:
- `supabase/migrations/064_unified_tasks.sql`
- `backend/app/services/task_tracker.py`
- `backend/app/api/task_manager_router.py`
- `frontend/contexts/TaskManagerContext.tsx`

Modify: `backend/app/main.py` (register router)

Verify: manual INSERT → Realtime received → Context updated

### Phase 2: Integrate Download + Upload

Modify:
- `download_tasks.py` — call TaskTracker
- `resources_router.py` — call TaskTracker for uploads
- TopBar panel — render from TaskManagerContext

Delete: `UploadContext.tsx`

Verify: upload file → TopBar shows real-time progress → completed ✓

### Phase 3: Integrate Transcode + AI

Modify:
- `transcode_tasks.py` — call TaskTracker
- `ai_tasks.py` — call TaskTracker
- Settings Tasks page — query unified_tasks with 4 tabs

Delete: old polling code in `TasksPanel.tsx`, `AITasksPanel.tsx`

Verify: upload video → upload task → transcode task → AI task chain visible

### Phase 4: Cleanup

- Migrate `download_tasks` historical data → `unified_tasks`
- DROP TABLE `download_tasks`
- Delete old `/download-tasks/*` API endpoints
- Add Celery Beat job: cleanup completed tasks older than 30 days

## Section 5: Edge Cases

### Reconnection
- Supabase SDK handles reconnect with exponential backoff
- On reconnect: fetch `GET /tasks/active` to fill any gaps

### Task cancellation
- `POST /tasks/{id}/cancel` → update DB + `celery.control.revoke(task_id, terminate=True)`
- Upload cancel: also cleanup partial files on server

### Retry
- `POST /tasks/{id}/retry` → reset status=pending, progress=0 → re-dispatch Celery

### UI limits
- TopBar panel: max 20 active tasks, "+N more" for overflow
- Settings page: paginated, no limit

### Throttling
- TaskTracker: max 1 DB write/sec per task (progress updates)
- Frontend: `requestAnimationFrame` batching for Realtime events

### History cleanup
- Celery Beat daily job: delete completed/failed tasks older than 30 days
- User can manually "Clear completed"
