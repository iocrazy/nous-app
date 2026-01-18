# Progressive Download Progress Design

> Created: 2026-01-18
> Status: Approved

## Overview

Improve user experience by returning video metadata immediately while showing download progress separately. Currently, users see only "Task status: 执行中" until the entire download completes, which feels unresponsive.

## Core Requirements

| Item | Decision |
|------|----------|
| Core Feature | Return metadata first, download video separately with progress |
| Progress Display | Percentage progress bar |
| Single Video Layout | Keep current layout; left side shows progress → video after completion |
| Progress Bar Style | Neon border + Wave liquid, switchable |
| Style Switcher | Global settings page |
| Progress Fetching | Polling (reuse existing mechanism) |
| Failure Handling | Auto-retry 2-3 times, show retry button if still fails |
| Batch Download | Top shows total progress + each card shows individual progress |
| Mini Card Progress | Simplified version, simple progress bar / percentage only |

---

## Architecture

```
User clicks Analyze
       ↓
┌─────────────────────────────────────────────────────────┐
│  Task 1: parse_metadata_task                            │
│  - Extract URL → Fetch aweme_detail → Parse basic info  │
│  - Duration: 2-5 seconds                                │
│  - Returns: title, author, likes, duration, thumbnail   │
└─────────────────────────────────────────────────────────┘
       ↓ Returns immediately, triggers Task 2
┌─────────────────────────────────────────────────────────┐
│  Task 2: download_media_task                            │
│  - Download video/images/audio/cover                    │
│  - Duration: 10 sec - several minutes (depends on size) │
│  - Progress: written to Redis, frontend polls           │
│  - On complete: update database, notify frontend        │
└─────────────────────────────────────────────────────────┘
```

**Data Flow:**
1. Frontend submits URL → Backend creates Task 1
2. Task 1 completes → Returns metadata + creates Task 2 (returns download_task_id)
3. Frontend receives metadata → Immediately renders right side info + left side shows progress bar
4. Frontend polls Task 2 progress → Updates progress bar
5. Task 2 completes → Frontend switches to video player

---

## Backend Implementation

### Task 1: parse_metadata_task

```python
@shared_task
def parse_metadata_task(url, user_id, options):
    # 1. Extract valid URL
    # 2. Fetch aweme_detail (call Douyin API)
    # 3. Parse basic metadata (no file downloads)
    # 4. Save to database (download_status = 'pending')
    # 5. Trigger Task 2
    download_task = download_media_task.delay(aweme_id, options)

    return {
        "status": "success",
        "aweme_id": aweme_id,
        "metadata": { ... },  # title, author, duration, statistics, etc.
        "thumbnail_url": "...",  # original thumbnail (not downloaded)
        "download_task_id": download_task.id  # frontend uses this to poll download progress
    }
```

### Task 2: download_media_task

```python
@shared_task(bind=True)
def download_media_task(self, aweme_id, options):
    task_id = self.request.id

    # Continuously update Redis during download
    for progress in download_with_progress(urls):
        redis.set(f"download_progress:{task_id}", {
            "percent": progress.percent,  # 0-100
            "downloaded": progress.bytes,
            "total": progress.total_bytes,
            "speed": progress.speed  # bytes/sec
        })

    # Update database on completion
    update_video_record(aweme_id, local_paths)
    return {"status": "success", "aweme_id": aweme_id}
```

### New API Endpoint

```
GET /tasks/{task_id}/progress
→ Returns { percent: 45, speed: "2.5 MB/s", status: "downloading" }
```

---

## Frontend Implementation

### Component Structure

```
components/
  DownloadProgress/
    index.tsx              # Unified export, selects which to render based on settings
    types.ts               # Shared type definitions
    NeonBorder.tsx         # Neon border effect
    WaveLiquid.tsx         # Wave liquid effect
    SimpleBar.tsx          # Simplified version (for mini cards)
```

### Unified Props Interface

```typescript
interface DownloadProgressProps {
  percent: number;          // 0-100
  status: 'downloading' | 'completed' | 'failed' | 'retrying';
  speed?: string;           // "2.5 MB/s"
  onRetry?: () => void;     // Retry callback on failure
  size?: 'normal' | 'mini'; // mini for batch small cards
}
```

### Progress Bar Styles

**Neon Border Effect (NeonBorder.tsx)**
- Video thumbnail as background (blurred + darkened)
- Border uses purple gradient stroke, progress goes around
- Center shows percentage + download speed
- On complete: border glows and flashes, then fades to video

**Wave Liquid Effect (WaveLiquid.tsx)**
- Video thumbnail as background
- Purple wave rises from bottom
- Wave has slight undulation animation
- Percentage floats above the wave

---

## Settings & State Management

### Settings

```typescript
interface UserSettings {
  // ... existing settings
  downloadProgressStyle: 'neon' | 'wave';  // default 'neon'
}
```

### Settings Page UI

```
┌─────────────────────────────────────────┐
│  Settings                               │
├─────────────────────────────────────────┤
│                                         │
│  Download Progress Style                │
│  ┌─────────────┐  ┌─────────────┐       │
│  │  ○ Neon     │  │  ○ Wave     │       │
│  │  [preview]  │  │  [preview]  │       │
│  └─────────────┘  └─────────────┘       │
│                                         │
└─────────────────────────────────────────┘
```

### State Management

```typescript
interface ParseState {
  status: 'idle' | 'parsing' | 'downloading' | 'completed' | 'failed';
  metadata: VideoMetadata | null;     // Filled after Task 1 completes
  downloadTaskId: string | null;      // Task 2's ID
  downloadProgress: {
    percent: number;
    speed: string;
    retryCount: number;
  } | null;
}
```

### Polling Logic Changes

1. After submit, poll Task 1 (parse metadata)
2. Task 1 completes → Save metadata + get downloadTaskId
3. Switch to polling Task 2 (download progress)
4. Task 2 completes → Final completion

---

## Batch Download & Error Handling

### Batch Download UI

```
┌─────────────────────────────────────────────────────────┐
│  Batch Download                    Total: 3/10 (30%)   │
│  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
├─────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐        │
│ │ [thumb] │ │ [thumb] │ │ [thumb] │ │ [thumb] │        │
│ │ ✓ Done  │ │ 45% ███ │ │ Queued  │ │ Queued  │        │
│ │ title...│ │ title...│ │ title...│ │ title...│        │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘        │
└─────────────────────────────────────────────────────────┘
```

### Mini Card States

- `queued` - Gray, shows "Queued"
- `parsing` - Blue spinning icon
- `downloading` - Simple progress bar + percentage
- `completed` - Green checkmark ✓
- `failed` - Red ✗ + click to retry

### Error Retry Mechanism

```
Download fails
    ↓
Auto-retry (max 3 times, increasing intervals: 5s → 10s → 20s)
    ↓
Still fails?
    ↓
Show error state + retry button
    ↓
User clicks retry → Create new download_task
```

### Retry UI Feedback

- During retry: "Retrying (2/3)..."
- Final failure: Red border + "Download failed" + 🔄 button

---

## Database & API Changes

### Database Changes

```sql
-- New fields in douyin_videos table
ALTER TABLE douyin_videos ADD COLUMN download_status VARCHAR(20)
  DEFAULT 'pending';  -- pending | downloading | completed | failed

ALTER TABLE douyin_videos ADD COLUMN download_progress INTEGER
  DEFAULT 0;  -- 0-100, optional, for persistence
```

### API Changes Summary

| Endpoint | Change |
|----------|--------|
| `POST /douyin/fetch` | Response adds `download_task_id` field |
| `GET /tasks/{id}/progress` | **NEW** - Get download progress |
| `POST /douyin/retry/{aweme_id}/download` | **NEW** - Retry download |

### Response Structure Change

```json
// POST /douyin/fetch response (after Task 1 completes)
{
  "status": "success",
  "aweme_id": "xxx",
  "download_task_id": "celery-task-uuid",
  "metadata": {
    "video_title": "...",
    "author": "...",
    "duration": 120,
    "statistics": { "likes": 1000, ... },
    "thumbnail_url": "https://..."
  }
}
```

---

## Implementation Checklist

### Backend
- [ ] Create `parse_metadata_task` (Task 1)
- [ ] Create `download_media_task` (Task 2) with progress tracking
- [ ] Add Redis progress storage logic
- [ ] Add `GET /tasks/{id}/progress` endpoint
- [ ] Add `POST /douyin/retry/{aweme_id}/download` endpoint
- [ ] Database migration for new fields

### Frontend
- [ ] Create `DownloadProgress` component structure
- [ ] Implement `NeonBorder.tsx` effect
- [ ] Implement `WaveLiquid.tsx` effect
- [ ] Implement `SimpleBar.tsx` for mini cards
- [ ] Add progress style setting to Settings page
- [ ] Update `useTaskPolling` for two-phase polling
- [ ] Update single video parse UI
- [ ] Update batch download UI
- [ ] Add retry button and logic

---

## Notes

- Progress bar style can be changed anytime in Settings
- Mini cards use simplified progress bar regardless of style setting
- Thumbnail URL from metadata is used as progress bar background before local download completes
