# TaskOrchestrator Design (B+C Hybrid)

## Goal

Replace ad-hoc Celery task status tracking with a centralized TaskOrchestrator that provides: state machine validation, global dedup with multi-user subscription, and structured error classification. Applies to 5 of 7 task types (download, transcode, ai_extract, ai_transcription, ai_summary).

## Architecture

B+C hybrid approach:
- **B (State Machine)**: `TaskPhase` enum with `VALID_TRANSITIONS` table prevents illegal state changes
- **C (Event-Driven Dedup)**: Redis SET NX dedup lock + Celery signals for automatic lifecycle tracking + multi-user subscription via `subscribers` JSONB

## Tech Stack

- Python 3.12, FastAPI, Celery 5, Redis, Supabase (PostgreSQL)
- Frontend: React 19, TypeScript, Supabase Realtime

---

## Section 1: TaskOrchestrator Core

### 1.1 State Machine

```python
class TaskPhase(str, Enum):
    QUEUED = "queued"
    DEDUP_CHECK = "dedup_check"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

VALID_TRANSITIONS = {
    TaskPhase.QUEUED:      {TaskPhase.DEDUP_CHECK, TaskPhase.CANCELLED},
    TaskPhase.DEDUP_CHECK: {TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.FAILED},
    TaskPhase.PROCESSING:  {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.COMPLETED:   set(),
    TaskPhase.FAILED:      {TaskPhase.QUEUED},  # retry
    TaskPhase.CANCELLED:   set(),
}
```

- `DEDUP_CHECK → COMPLETED`: dedup hit (file already downloaded), skip processing
- `FAILED → QUEUED`: retry path, re-enters the pipeline

### 1.2 Dedup Keys

| Task Type | Dedup Key | Example |
|-----------|-----------|---------|
| download | `platform_id` | `task:download:7456123456789` |
| transcode | `version_id` | `task:transcode:v_98765` |
| ai_extract | `platform_id` | `task:ai_extract:7456123456789` |
| ai_transcription | `platform_id` | `task:ai_transcription:7456123456789` |
| ai_summary | `platform_id` | `task:ai_summary:7456123456789` |

### 1.3 Redis Dedup Lock

```python
async def acquire_or_subscribe(self, task_type, dedup_key, user_id, resource_id):
    lock_key = f"task:{task_type}:{dedup_key}"
    acquired = redis.set(lock_key, task_id, nx=True, ex=3600)

    if acquired:
        # First requester: create task + dispatch Celery
        return {"action": "created", "task_id": task_id}
    else:
        # Subsequent requester: subscribe to existing task
        existing_task_id = redis.get(lock_key)
        self._add_subscriber(existing_task_id, user_id, resource_id)
        return {"action": "subscribed", "task_id": existing_task_id}
```

### 1.4 Celery Signals

```python
from celery.signals import task_prerun, task_success, task_failure

@task_prerun.connect
def on_task_start(sender, task_id, **kwargs):
    orchestrator.transition(task_id, TaskPhase.PROCESSING)

@task_success.connect
def on_task_success(sender, result, **kwargs):
    orchestrator.transition(sender.request.id, TaskPhase.COMPLETED)
    orchestrator.notify_subscribers(sender.request.id, result)

@task_failure.connect
def on_task_failure(sender, exception, **kwargs):
    error_code = orchestrator.classify_error(exception)
    orchestrator.transition(sender.request.id, TaskPhase.FAILED, error_code=error_code)
    orchestrator.notify_subscribers(sender.request.id, error=error_code)
```

---

## Section 2: Multi-User Subscription

### 2.1 Database Changes

`unified_tasks` table additions:

```sql
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS phase TEXT DEFAULT 'queued',
  ADD COLUMN IF NOT EXISTS dedup_key TEXT,
  ADD COLUMN IF NOT EXISTS subscribers JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS error_code TEXT;
```

### 2.2 Subscribers Structure

```json
{
  "subscribers": [
    {"user_id": "user-A-uuid", "resource_id": "res-A-id", "subscribed_at": "2026-02-21T..."},
    {"user_id": "user-B-uuid", "resource_id": "res-B-id", "subscribed_at": "2026-02-21T..."}
  ]
}
```

### 2.3 Flow: User A Creates, User B Subscribes

```
User A: POST /videos/fetch {url: "https://..."}
  → media_service.save_metadata_only() → parsed_media + resource_A
  → orchestrator.acquire_or_subscribe("download", platform_id, userA, resA)
  → Redis SET NX success → create unified_task, dispatch Celery
  → response: {task_id, status: "created"}

User B: POST /videos/fetch {url: same URL}
  → media_service.save_metadata_only() → reuse parsed_media + resource_B
  → orchestrator.acquire_or_subscribe("download", platform_id, userB, resB)
  → Redis SET NX fail → subscribe to existing task
  → response: {task_id, status: "subscribed"}

Celery completes:
  → orchestrator.complete(task_id, result)
  → notify_subscribers():
      → UPDATE resource_A SET video_download_status='completed', file_path=...
      → UPDATE resource_B SET video_download_status='completed', file_path=...
  → Both users see completion via Supabase Realtime
```

### 2.4 Edge Cases

- **Lock TTL renewal**: Celery task renews Redis lock every 10min during processing
- **Subscriber cancellation**: User can cancel their subscription, removed from subscribers array, does not affect other subscribers
- **Redis restart**: On cold start, scan `unified_tasks` where `phase='processing'` and re-create locks
- **Task already completed when subscribing**: Check task phase before subscribing, if completed → directly copy result to subscriber's resource

---

## Section 3: Frontend Observability

### 3.1 Phase Display Mapping

| phase | Display | Color | Animation |
|-------|---------|-------|-----------|
| `queued` | "Queued" | gray | none |
| `dedup_check` | "Checking..." | blue | pulse |
| `processing` | "Downloading 45%" | blue | progress bar |
| `completed` | "Done" | green | checkmark |
| `failed` | "Failed: timeout" | red | retry button |
| `cancelled` | "Cancelled" | gray | none |

### 3.2 Error Classification

```python
ERROR_CODES = {
    "NETWORK_TIMEOUT":    {"message": "Network timeout, will auto-retry",       "retryable": True},
    "RESOURCE_404":       {"message": "Source deleted or unavailable",          "retryable": False},
    "STORAGE_FULL":       {"message": "Storage full, please free space",       "retryable": False},
    "RATE_LIMITED":       {"message": "Rate limited, retrying in {delay}s",    "retryable": True, "auto_retry": True},
    "TRANSCODE_FAILED":   {"message": "Transcode failed: unsupported format",  "retryable": False},
    "AI_QUOTA_EXCEEDED":  {"message": "AI quota exceeded, try tomorrow",       "retryable": False},
    "UNKNOWN":            {"message": "Unexpected error",                      "retryable": True},
}
```

Frontend behavior:
- `retryable=True` → show "Retry" button
- `retryable=False` → show error explanation only
- `auto_retry=True` → show countdown, auto-retry

### 3.3 Realtime Integration

Existing `TaskManagerContext` Supabase Realtime subscription continues unchanged. New fields are parsed optionally:

```typescript
const phase = payload.new.phase;
const errorCode = payload.new.error_code;
const subscribers = payload.new.subscribers;
const isMyTask = subscribers?.some(s => s.user_id === currentUserId)
                 || payload.new.user_id === currentUserId;
```

### 3.4 Progress Strategy

- **Coarse-grained** (phase changes): Supabase Realtime push, instant
- **Fine-grained** (progress 0-100%): Redis + frontend polling (1s interval), only during `processing` phase
- `queued` and `completed` phases do not trigger polling

---

## Section 4: Migration Strategy

### 4.1 Database Migration

Additive only (no column drops/renames):

```sql
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS phase TEXT DEFAULT 'queued',
  ADD COLUMN IF NOT EXISTS dedup_key TEXT,
  ADD COLUMN IF NOT EXISTS subscribers JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS error_code TEXT;

CREATE INDEX IF NOT EXISTS idx_unified_tasks_dedup_key
  ON unified_tasks (dedup_key) WHERE dedup_key IS NOT NULL;
```

### 4.2 Incremental Rollout

| Batch | Task Types | Reason |
|-------|-----------|--------|
| Batch 1 | `download` | Core scenario, highest dedup ROI |
| Batch 2 | `ai_extract`, `ai_transcription`, `ai_summary` | AI trio shares group_id, change together |
| Batch 3 | `transcode` | Independent flow, last to integrate |
| Skip | `upload`, `parse` | upload has no dedup value, parse is synchronous |

Each batch: independent commit, independent verification.

### 4.3 Backward Compatibility

TaskOrchestrator is a **wrapper**, not a replacement:

```
Current: router → media_service → celery task → download → update resources
New:     router → orchestrator.acquire_or_subscribe() → celery task → download → orchestrator.complete() → notify_subscribers()
```

- Existing `TaskTracker` continues working; TaskOrchestrator wraps it
- `unified_tasks.status` field unchanged (pending/processing/completed/failed); `phase` is additional granularity
- Frontend `TaskManagerContext` existing logic unaffected; new fields are optional

### 4.4 Rollback Plan

1. **Code rollback**: Revert TaskOrchestrator commits, Celery tasks fall back to direct calls
2. **Data compatible**: New columns have defaults, old code reads/writes normally
3. **Redis cleanup**: Dedup lock keys have 1h TTL, auto-expire
