# AI Task Architecture Refactoring — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rename `videos` → `parsed_media` across the entire stack, migrate AI status to per-user `resources` table, split `ai_pipeline` into 3 sub-tasks in `unified_tasks`, and redesign TasksPanel UI.

**Architecture:** Full rename of the `videos` ecosystem (7 tables, 1 view) to `parsed_media` / `media_*`. AI status columns move from shared `parsed_media` table to per-user `resources` table. `unified_tasks` becomes the single task data source — the monolithic `ai_pipeline` task_type splits into `ai_extract`, `ai_transcription`, `ai_summary`, each getting its own row linked by `group_id`. Frontend TasksPanel gets big category tabs (All/Transfer/AI/Other) with sub-type pills and dynamic stats.

**Tech Stack:** PostgreSQL (Supabase), FastAPI, Celery, React 19 + TypeScript + Vite, TailwindCSS

**Design Doc:** `docs/plans/2026-02-18-ai-task-architecture-refactoring-design.md`

---

## Phase 1: Database Migrations

### Task 1: Migration 066 — Rename tables and columns

**Files:**
- Create: `supabase/migrations/066_rename_videos_to_parsed_media.sql`

**Step 1: Write the migration SQL**

```sql
-- 066_rename_videos_to_parsed_media.sql
-- Full rename: videos ecosystem → parsed_media ecosystem

BEGIN;

-- ═══════════════════════════════════════════════════
-- 1. Rename tables
-- ═══════════════════════════════════════════════════
ALTER TABLE videos RENAME TO parsed_media;
ALTER TABLE video_transcripts RENAME TO media_transcripts;
ALTER TABLE video_summaries RENAME TO media_summaries;
ALTER TABLE video_collections RENAME TO media_collections;
ALTER TABLE video_tags RENAME TO media_tags;
ALTER TABLE video_analysis RENAME TO media_analysis;
ALTER TABLE video_access_logs RENAME TO media_access_logs;

-- ═══════════════════════════════════════════════════
-- 2. Rename video_id columns → media_id
-- ═══════════════════════════════════════════════════
ALTER TABLE media_transcripts RENAME COLUMN video_id TO media_id;
ALTER TABLE media_summaries RENAME COLUMN video_id TO media_id;
ALTER TABLE media_collections RENAME COLUMN video_id TO media_id;
ALTER TABLE media_tags RENAME COLUMN video_id TO media_id;
ALTER TABLE media_analysis RENAME COLUMN video_id TO media_id;
ALTER TABLE media_access_logs RENAME COLUMN video_id TO media_id;
ALTER TABLE resources RENAME COLUMN video_id TO media_id;
ALTER TABLE project_files RENAME COLUMN video_id TO media_id;
ALTER TABLE unified_tasks RENAME COLUMN video_id TO media_id;

-- ═══════════════════════════════════════════════════
-- 3. Recreate the view (DROP + CREATE, views can't be renamed easily)
-- ═══════════════════════════════════════════════════
DROP VIEW IF EXISTS videos_with_tags;

CREATE OR REPLACE VIEW parsed_media_with_tags AS
SELECT
  pm.*,
  COALESCE(
    array_agg(t.name ORDER BY t.name) FILTER (WHERE t.name IS NOT NULL),
    '{}'
  ) AS tags,
  vs.summary_text
FROM parsed_media pm
LEFT JOIN media_tags mt ON pm.id = mt.media_id
LEFT JOIN tags t ON mt.tag_id = t.id
LEFT JOIN LATERAL (
  SELECT summary_text FROM media_summaries
  WHERE media_id = pm.id
  ORDER BY created_at DESC LIMIT 1
) vs ON true
GROUP BY pm.id, vs.summary_text;

-- ═══════════════════════════════════════════════════
-- 4. Update Realtime publication
-- ═══════════════════════════════════════════════════
-- Remove old table names from realtime publication (ignore errors if not found)
DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS videos;
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS video_transcripts;
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS video_summaries;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Add new table names
ALTER PUBLICATION supabase_realtime ADD TABLE parsed_media;
ALTER PUBLICATION supabase_realtime ADD TABLE media_transcripts;
ALTER PUBLICATION supabase_realtime ADD TABLE media_summaries;

-- ═══════════════════════════════════════════════════
-- 5. Rename indexes (PostgreSQL auto-renames constraint-based indexes
--    with table rename, but manually created indexes need explicit rename)
-- ═══════════════════════════════════════════════════
-- These may fail if auto-renamed; wrap in DO block
DO $$
BEGIN
  ALTER INDEX IF EXISTS idx_video_transcripts_video_id RENAME TO idx_media_transcripts_media_id;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
DO $$
BEGIN
  ALTER INDEX IF EXISTS idx_video_summaries_video_id RENAME TO idx_media_summaries_media_id;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
DO $$
BEGIN
  ALTER INDEX IF EXISTS idx_resources_video_id RENAME TO idx_resources_media_id;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- ═══════════════════════════════════════════════════
-- 6. Update RLS policies (drop old, create new with updated names)
-- ═══════════════════════════════════════════════════
-- parsed_media (was videos) - policies auto-follow table rename, just update names
-- media_transcripts, media_summaries - same

-- Update dashboard stats RPC if it references 'videos'
CREATE OR REPLACE FUNCTION get_dashboard_stats(p_user_id UUID)
RETURNS JSON AS $$
DECLARE
  result JSON;
BEGIN
  SELECT json_build_object(
    'total_media', (SELECT COUNT(*) FROM parsed_media WHERE user_id = p_user_id),
    'downloaded', (SELECT COUNT(*) FROM parsed_media WHERE user_id = p_user_id AND video_download_status = 'COMPLETED'),
    'pending', (SELECT COUNT(*) FROM parsed_media WHERE user_id = p_user_id AND video_download_status = 'PENDING'),
    'failed', (SELECT COUNT(*) FROM parsed_media WHERE user_id = p_user_id AND video_download_status = 'FAILED'),
    'total_size_bytes', (SELECT COALESCE(SUM(datasize_bytes), 0) FROM parsed_media WHERE user_id = p_user_id AND video_download_status = 'COMPLETED'),
    'this_week', (SELECT COUNT(*) FROM parsed_media WHERE user_id = p_user_id AND created_at >= date_trunc('week', now())),
    'today', (SELECT COUNT(*) FROM parsed_media WHERE user_id = p_user_id AND created_at >= date_trunc('day', now()))
  ) INTO result;
  RETURN result;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMIT;
```

**Step 2: Review and verify SQL syntax**

Read through the migration and verify:
- All 7 tables renamed
- All 9 columns renamed
- View recreated with new names
- Realtime publication updated
- RPC function updated

**Step 3: Commit**

```bash
git add supabase/migrations/066_rename_videos_to_parsed_media.sql
git commit -m "migration: rename videos ecosystem to parsed_media (066)"
```

---

### Task 2: Migration 067 — AI status on resources + group_id

**Files:**
- Create: `supabase/migrations/067_ai_status_on_resources.sql`

**Step 1: Write the migration SQL**

```sql
-- 067_ai_status_on_resources.sql
-- Move AI status tracking from parsed_media (shared) to resources (per-user)
-- Add group_id to unified_tasks for linking AI sub-tasks

BEGIN;

-- ═══════════════════════════════════════════════════
-- 1. Add AI status columns to resources
-- ═══════════════════════════════════════════════════
ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS transcript_status VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS summary_status VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS visual_analysis_status VARCHAR(20) NOT NULL DEFAULT 'none';

COMMENT ON COLUMN resources.transcript_status IS 'Per-user AI transcript status: none|pending|processing|completed|failed';
COMMENT ON COLUMN resources.summary_status IS 'Per-user AI summary status: none|pending|processing|completed|failed';
COMMENT ON COLUMN resources.visual_analysis_status IS 'Per-user AI visual analysis status: none|pending|processing|completed|failed';

-- Index for filtering by AI status
CREATE INDEX IF NOT EXISTS idx_resources_transcript_status
  ON resources(transcript_status) WHERE transcript_status != 'none';
CREATE INDEX IF NOT EXISTS idx_resources_summary_status
  ON resources(summary_status) WHERE summary_status != 'none';

-- ═══════════════════════════════════════════════════
-- 2. Add group_id to unified_tasks
-- ═══════════════════════════════════════════════════
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS group_id UUID;

CREATE INDEX IF NOT EXISTS idx_unified_tasks_group
  ON unified_tasks(group_id) WHERE group_id IS NOT NULL;

COMMENT ON COLUMN unified_tasks.group_id IS 'Groups related sub-tasks (e.g. 3 AI steps for same media)';

-- ═══════════════════════════════════════════════════
-- 3. Backfill: copy AI status from parsed_media to resources
-- ═══════════════════════════════════════════════════
UPDATE resources r
SET
  transcript_status = COALESCE(pm.transcript_status, 'none'),
  summary_status = COALESCE(pm.summary_status, 'none'),
  visual_analysis_status = COALESCE(pm.visual_analysis_status, 'none')
FROM parsed_media pm
WHERE r.media_id = pm.id
  AND r.transcript_status = 'none'
  AND (pm.transcript_status IS NOT NULL AND pm.transcript_status != 'none'
       OR pm.summary_status IS NOT NULL AND pm.summary_status != 'none'
       OR pm.visual_analysis_status IS NOT NULL AND pm.visual_analysis_status != 'none');

COMMIT;
```

**Step 2: Commit**

```bash
git add supabase/migrations/067_ai_status_on_resources.sql
git commit -m "migration: add AI status to resources + group_id to unified_tasks (067)"
```

---

### Task 3: Execute migrations on local Supabase

**Step 1: Execute migration 066**

Open Supabase Studio at http://127.0.0.1:54323, go to SQL Editor, paste and run `066_rename_videos_to_parsed_media.sql`.

Expected: All statements succeed. Verify:
- `SELECT * FROM parsed_media LIMIT 1;` — works
- `SELECT * FROM media_transcripts LIMIT 1;` — works
- `SELECT * FROM parsed_media_with_tags LIMIT 1;` — works
- `SELECT * FROM videos LIMIT 1;` — ERROR (table doesn't exist)

**Step 2: Execute migration 067**

Paste and run `067_ai_status_on_resources.sql`.

Expected: All statements succeed. Verify:
- `SELECT transcript_status, summary_status FROM resources LIMIT 1;` — shows 'none'
- `SELECT group_id FROM unified_tasks LIMIT 1;` — shows NULL

---

## Phase 2: Backend Rename (video → media)

### Task 4: Rename core repository file

**Files:**
- Rename: `backend/app/repositories/video_repository.py` → `backend/app/repositories/media_repository.py`
- Modify: `backend/app/repositories/__init__.py`

**Step 1: Rename file and update class name**

```bash
cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system
mv backend/app/repositories/video_repository.py backend/app/repositories/media_repository.py
```

In `media_repository.py`:
- Rename class `VideoRepository` → `MediaRepository`
- Change `TABLE_NAME = "videos"` → `TABLE_NAME = "parsed_media"`
- Change any `"videos_with_tags"` → `"parsed_media_with_tags"`
- Change any `video_id` column references → `media_id` where referring to FK columns in OTHER tables
- Keep `platform_id` and parsed_media's own columns as-is

**Step 2: Update `__init__.py`**

```python
# backend/app/repositories/__init__.py
from app.repositories.media_repository import MediaRepository

__all__ = [
    "MediaRepository",
]
```

**Step 3: Commit**

```bash
git add backend/app/repositories/media_repository.py backend/app/repositories/__init__.py
git rm backend/app/repositories/video_repository.py
git commit -m "refactor: rename VideoRepository → MediaRepository"
```

---

### Task 5: Rename schemas

**Files:**
- Rename: `backend/app/schemas/video.py` → `backend/app/schemas/media.py` (if exists)
- Modify: `backend/app/schemas/projects.py` — `video_id` → `media_id` in LinkVideoRequest
- Modify: `backend/app/schemas/search.py` — `video_id` → `media_id`
- Modify: `backend/app/schemas/tags.py` — `video_id` → `media_id`
- Modify: `backend/app/schemas/cleanup.py` — `video_id` → `media_id`
- Modify: `backend/app/schemas/ai.py` — update references
- Modify: `backend/app/schemas/collections.py` — update references

**Step 1: Update each schema file**

In each file, find-and-replace:
- `video_id` field names → `media_id`
- `VideoXxx` class names → `MediaXxx` where applicable
- Import paths referencing old names

**Step 2: Commit**

```bash
git add backend/app/schemas/
git commit -m "refactor: rename video_id → media_id in all schemas"
```

---

### Task 6: Rename video_service and videos_router

**Files:**
- Rename: `backend/app/services/video_service.py` → `backend/app/services/media_service.py`
- Rename: `backend/app/api/videos_router.py` → `backend/app/api/media_router.py`
- Modify: `backend/app/api/__init__.py` — update imports

**Step 1: Rename files**

```bash
mv backend/app/services/video_service.py backend/app/services/media_service.py
mv backend/app/api/videos_router.py backend/app/api/media_router.py
```

**Step 2: Update media_service.py**

- `from app.repositories.video_repository import VideoRepository` → `from app.repositories.media_repository import MediaRepository`
- All `VideoRepository()` → `MediaRepository()`
- All `"videos"` table refs → `"parsed_media"`
- All `video_id` column refs → `media_id` (when referring to FK in other tables)

**Step 3: Update media_router.py**

- Same import updates
- `from app.services.video_service import ...` → `from app.services.media_service import ...`
- Table name strings

**Step 4: Update `backend/app/api/__init__.py`**

```python
# Line 34-35: Change
from app.api.media_router import legacy_router as legacy_douyin_router
from app.api.media_router import router as media_router

# Line 41: Change
api_router.include_router(router=media_router, tags=["Media"])
```

**Step 5: Commit**

```bash
git add backend/app/services/media_service.py backend/app/api/media_router.py backend/app/api/__init__.py
git rm backend/app/services/video_service.py backend/app/api/videos_router.py
git commit -m "refactor: rename video_service → media_service, videos_router → media_router"
```

---

### Task 7: Update all remaining repositories

**Files:**
- Modify: `backend/app/repositories/ai_repository.py` — `"video_transcripts"` → `"media_transcripts"`, `"video_summaries"` → `"media_summaries"`, `video_id` → `media_id`
- Modify: `backend/app/repositories/analysis_repository.py` — `"video_analysis"` → `"media_analysis"`, `video_id` → `media_id`
- Modify: `backend/app/repositories/collections_repository.py` — `"video_collections"` → `"media_collections"`, `video_id` → `media_id`
- Modify: `backend/app/repositories/tags_repository.py` — `"video_tags"` → `"media_tags"`, `video_id` → `media_id`
- Modify: `backend/app/repositories/projects_repository.py` — `video_id` → `media_id`
- Modify: `backend/app/repositories/resources_repository.py` — `video_id` → `media_id`

**Step 1: In each file, apply these replacements:**

| Pattern | Replacement |
|---------|-------------|
| `"video_transcripts"` | `"media_transcripts"` |
| `"video_summaries"` | `"media_summaries"` |
| `"video_collections"` | `"media_collections"` |
| `"video_tags"` | `"media_tags"` |
| `"video_analysis"` | `"media_analysis"` |
| `"video_access_logs"` | `"media_access_logs"` |
| `"videos"` | `"parsed_media"` |
| `"videos_with_tags"` | `"parsed_media_with_tags"` |
| `.eq("video_id",` | `.eq("media_id",` |
| `["video_id"]` | `["media_id"]` |
| `"video_id"` (as column name) | `"media_id"` |
| `VideoRepository` | `MediaRepository` |
| `from app.repositories.video_repository` | `from app.repositories.media_repository` |

**Step 2: Commit**

```bash
git add backend/app/repositories/
git commit -m "refactor: update all repositories for parsed_media rename"
```

---

### Task 8: Update all remaining services

**Files:**
- Modify: `backend/app/services/collections_service.py` — table refs + column refs
- Modify: `backend/app/services/search_service.py` — field names
- Modify: `backend/app/services/cleanup_service.py` — field names
- Modify: `backend/app/services/resources_service.py` — `video_id` → `media_id`
- Modify: `backend/app/services/projects_service.py` — `link_video()` methods, `video_id` → `media_id`
- Modify: `backend/app/services/downloader.py` — VideoRepository → MediaRepository
- Modify: `backend/app/services/whisper_service.py` — `video_id` → `media_id` in DB writes
- Modify: `backend/app/services/llm_analysis_service.py` — `video_id` → `media_id` in DB writes
- Modify: `backend/app/services/task_tracker.py` — `video_id` → `media_id` param name
- Modify: `backend/app/services/lightweight_parser.py` — update refs

**Step 1: Apply the same pattern replacements as Task 7 across all service files.**

Key attention points:
- `task_tracker.py`: The `create()` method has a `video_id` parameter → rename to `media_id`
- `whisper_service.py`: `transcribe_and_save(video_id=...)` → `transcribe_and_save(media_id=...)`
- `llm_analysis_service.py`: `generate_summary_and_save(video_id=...)` → `generate_summary_and_save(media_id=...)`
- `resources_service.py`: `create_from_video()` method — rename to `create_from_media()`, `video_id` param → `media_id`

**Step 2: Commit**

```bash
git add backend/app/services/
git commit -m "refactor: update all services for parsed_media rename"
```

---

### Task 9: Update all remaining API routers

**Files:**
- Modify: `backend/app/api/ai_router.py` — update imports + `platform_id` → keep for now (will refactor to `resource_id` in Phase 3)
- Modify: `backend/app/api/analysis_router.py` — VideoRepository → MediaRepository
- Modify: `backend/app/api/cleanup_router.py` — field names
- Modify: `backend/app/api/collections_router.py` — update refs
- Modify: `backend/app/api/search_router.py` — field names
- Modify: `backend/app/api/tags_router.py` — field names
- Modify: `backend/app/api/projects_router.py` — `video_id` → `media_id`
- Modify: `backend/app/api/tasks_router.py` — update refs
- Modify: `backend/app/api/task_router.py` — update refs

**Step 1: Apply same replacements across all router files.**

**Step 2: Commit**

```bash
git add backend/app/api/
git commit -m "refactor: update all API routers for parsed_media rename"
```

---

### Task 10: Update Celery tasks

**Files:**
- Modify: `backend/app/tasks/ai_tasks.py` — `VideoRepository` → `MediaRepository`, `"transcript_status"` updates on parsed_media table (temporary — will change to resources in Phase 3)
- Modify: `backend/app/tasks/download_tasks.py` — same pattern
- Modify: `backend/app/tasks/analysis_tasks.py` — same pattern
- Modify: `backend/app/tasks/parse_tasks.py` — same pattern
- Modify: `backend/app/tasks/scheduled_tasks.py` — same pattern
- Modify: `backend/app/tasks/transcode_tasks.py` — same pattern (if references exist)

**Step 1: Apply replacements**

In `ai_tasks.py`, the `_update_status()` helper currently does:
```python
from app.repositories.video_repository import VideoRepository
repo = VideoRepository()
video = run_async(repo.get_by_platform_id(platform_id))
```
Change to:
```python
from app.repositories.media_repository import MediaRepository
repo = MediaRepository()
media = run_async(repo.get_by_platform_id(platform_id))
```

All other task files: same import + class name updates.

**Step 2: Commit**

```bash
git add backend/app/tasks/
git commit -m "refactor: update all Celery tasks for parsed_media rename"
```

---

### Task 11: Update any remaining backend files + verify

**Files:**
- Modify: `backend/app/scripts/migrate_storage_size.py` (if exists) — update refs
- Modify: `backend/app/services/__init__.py` (if exports VideoRepository)

**Step 1: Search for ANY remaining `video_repository`, `VideoRepository`, `"videos"` references in backend**

```bash
cd backend && grep -rn 'VideoRepository\|video_repository\|"videos"\|"video_' app/ --include="*.py" | grep -v __pycache__
```

Fix all remaining references.

**Step 2: Verify backend starts**

```bash
cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend
uv run uvicorn app.main:app --reload --port 8081
```

Expected: Server starts without import errors. Check `http://localhost:8081/docs` loads.

**Step 3: Commit any fixes**

```bash
git add backend/
git commit -m "refactor: fix remaining backend references to videos → parsed_media"
```

---

## Phase 3: Backend AI Refactoring

### Task 12: Refactor chain_ai_pipeline() to create 3 sub-tasks

**Files:**
- Modify: `backend/app/tasks/ai_tasks.py`
- Modify: `backend/app/services/task_tracker.py`

**Step 1: Update task_tracker.py**

Add `group_id` support to the `create()` method:

```python
async def create(
    self, user_id: str, task_type: str, title: str,
    media_id: str = None, resource_id: str = None,
    group_id: str = None,  # NEW
    subtitle: str = None, metadata: dict = None,
) -> str:
    data = {
        "user_id": user_id,
        "task_type": task_type,
        "title": title,
        "media_id": media_id,
        "resource_id": resource_id,
        "group_id": group_id,  # NEW
        "subtitle": subtitle,
        "metadata": metadata or {},
    }
    # ... rest of create logic
```

**Step 2: Refactor chain_ai_pipeline() in ai_tasks.py**

Replace the current single unified_task with 3 separate tasks:

```python
def chain_ai_pipeline(
    platform_id: str,
    user_id: str,
    resource_id: str = None,  # NEW — for updating resources.X_status
    transcript_bool: bool = True,
    summary_bool: bool = True,
):
    import uuid
    from celery import chain

    group_id = str(uuid.uuid4())
    task_ids = {}

    try:
        from app.services.task_tracker import get_task_tracker
        tracker = get_task_tracker()

        if transcript_bool:
            task_ids['extract'] = run_async(tracker.create(
                user_id=user_id,
                task_type="ai_extract",
                title=f"Audio Extract: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                group_id=group_id,
            ))
            task_ids['transcribe'] = run_async(tracker.create(
                user_id=user_id,
                task_type="ai_transcription",
                title=f"Transcribe: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                group_id=group_id,
            ))

        if summary_bool and transcript_bool:
            task_ids['summary'] = run_async(tracker.create(
                user_id=user_id,
                task_type="ai_summary",
                title=f"Summarize: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                group_id=group_id,
            ))

        # Start the first task
        first_key = 'extract' if 'extract' in task_ids else None
        if first_key:
            run_async(tracker.start(task_ids[first_key]))

    except Exception as e:
        logger.warning(f"[AI] Failed to create unified tasks: {e}")

    tasks = []
    if transcript_bool:
        tasks.append(extract_audio_task.si(
            platform_id, user_id, resource_id,
            task_ids.get('extract'), task_ids.get('transcribe'),
        ))
        tasks.append(transcribe_audio_task.si(
            platform_id, user_id, resource_id,
            None, task_ids.get('transcribe'),
        ))
    if summary_bool and transcript_bool:
        tasks.append(generate_summary_task.si(
            platform_id, user_id, resource_id,
            task_ids.get('summary'),
        ))

    if tasks:
        pipeline = chain(*tasks)
        pipeline.apply_async()
```

**Step 3: Update each sub-task signature**

Each task now receives `resource_id` and its own `unified_task_id`:

```python
@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def extract_audio_task(
    self, platform_id: str, user_id: str,
    resource_id: str = None,
    unified_task_id: str = None,
    next_task_id: str = None,  # for starting the next task in chain
):
    # ... existing logic ...
    # On success:
    _complete_unified(unified_task_id)
    _start_unified(next_task_id)  # start the transcription task
    _update_resource_status(resource_id, "transcript_status", "processing")
```

Add helper `_update_resource_status()`:

```python
def _update_resource_status(resource_id: str, field: str, status: str):
    """Update AI status on the resources table (per-user)."""
    if not resource_id:
        return
    try:
        from app.repositories.resources_repository import ResourcesRepository
        repo = ResourcesRepository()
        run_async(repo.update_resource(resource_id, {field: status}))
    except Exception as e:
        logger.debug(f"[AI] Resource status update failed: {e}")
```

**Step 4: Commit**

```bash
git add backend/app/tasks/ai_tasks.py backend/app/services/task_tracker.py
git commit -m "feat: split ai_pipeline into 3 sub-tasks with group_id"
```

---

### Task 13: Update AI router endpoints to accept resource_id

**Files:**
- Modify: `backend/app/api/ai_router.py`

**Step 1: Update endpoints**

Change endpoint signatures from `platform_id` to `resource_id`, resolve internally:

```python
@router.post("/ai/transcribe")
async def trigger_transcription(
    auth: AuthDep,
    resource_id: str = Query(...),
):
    """Trigger AI transcription for a resource."""
    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource or not resource.get("media_id"):
        raise HTTPException(404, "Resource not found or has no linked media")

    media_repo = MediaRepository()
    media = await media_repo.get_by_id(resource["media_id"])
    platform_id = media["platform_id"]

    # Update resource status
    await repo.update_resource(resource_id, {"transcript_status": "pending"})

    chain_ai_pipeline(
        platform_id=platform_id,
        user_id=auth.user_id,
        resource_id=resource_id,
        transcript_bool=True,
        summary_bool=False,
    )
    return {"success": True}
```

Keep old `platform_id`-based endpoints as deprecated aliases for backward compat during transition.

**Step 2: Commit**

```bash
git add backend/app/api/ai_router.py
git commit -m "feat: AI router accepts resource_id, updates resource AI status"
```

---

### Task 14: Update download_tasks auto-AI trigger

**Files:**
- Modify: `backend/app/tasks/download_tasks.py`

**Step 1: Update `_maybe_chain_ai_pipeline()`**

Pass `resource_id` when auto-triggering AI after download:

```python
def _maybe_chain_ai_pipeline(platform_id, user_id, resource_id=None):
    # ... existing user_settings check ...
    chain_ai_pipeline(
        platform_id=platform_id,
        user_id=user_id,
        resource_id=resource_id,
        transcript_bool=ai_settings.get("auto_transcribe", False),
        summary_bool=ai_settings.get("auto_summarize", False),
    )
```

Ensure the download task passes `resource_id` from the resource it creates.

**Step 2: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "feat: pass resource_id in auto-triggered AI pipeline"
```

---

### Task 15: Verify backend AI flow

**Step 1: Start backend**

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8081
```

Expected: No import errors.

**Step 2: Check API docs**

Visit `http://localhost:8081/docs`, verify:
- AI endpoints appear with updated parameter names
- Media endpoints work (renamed from videos)

---

## Phase 4: Frontend Rename + UI

### Task 16: Update types.ts

**Files:**
- Modify: `frontend/types.ts`

**Step 1: Rename Video → ParsedMedia**

```typescript
// Line 13: Rename interface
export interface ParsedMedia {
  // ... same fields ...
  // Remove AI status fields (moved to Resource):
  // transcript_status, summary_status, visual_analysis_status, transcript_bool, summary_bool
}

// Line 120: Update alias
export type DouyinBase = ParsedMedia;

// Line 165: Update response type
export interface MediaListResponse {
  success: boolean;
  count: number;
  media: ParsedMedia[];  // was videos: Video[]
}

// Line 231: Rename
export interface CollectionMedia {  // was CollectionVideo
  collection_id: number;
  media_id: number;  // was video_id
  added_by: string;
  added_at: string;
}

// Line 279: Add AI fields to Resource
export interface Resource {
  // ... existing fields ...
  media_id: string | null;  // was video_id
  // NEW: per-user AI status
  transcript_status: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  summary_status: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  visual_analysis_status: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
}

// Line 408: Update CleanupSuggestion
export interface CleanupSuggestion {
  media_id: number;  // was video_id
  // ... rest same ...
}

// Line 424: Update SearchResult
export interface SearchResult {
  media_id: number;  // was video_id
  // ... rest same ...
}

// Line 438: Update VideoAnalysis → MediaAnalysis
export interface MediaAnalysis {
  media_id: number;  // was video_id
  // ... rest same ...
}

// Line 612: Update ProjectFile
export interface ProjectFile {
  // ...
  media_id: string | null;  // was video_id
  // ...
}
```

**Step 2: Commit**

```bash
git add frontend/types.ts
git commit -m "refactor: rename Video → ParsedMedia, video_id → media_id in types"
```

---

### Task 17: Update frontend services

**Files:**
- Modify: `frontend/services/dataService.ts` — `TABLE_NAME='parsed_media'`, `VIEW_NAME='parsed_media_with_tags'`, `Video` → `ParsedMedia`
- Modify: `frontend/services/collectionService.ts` — `.from('parsed_media')`, `video_id` → `media_id`
- Modify: `frontend/services/aiService.ts` — endpoint URLs, `resource_id` params
- Modify: `frontend/services/searchService.ts` — field names
- Modify: `frontend/services/tagsService.ts` — field names
- Modify: `frontend/services/analysisService.ts` — field names
- Modify: `frontend/services/cleanupService.ts` — field names
- Modify: `frontend/services/resourceService.ts` — `video_id` → `media_id`
- Modify: `frontend/services/projectsService.ts` — `video_id` → `media_id`
- Modify: `frontend/services/parserService.ts` — update type refs

**Step 1: Apply find-and-replace across all service files:**

| Pattern | Replacement |
|---------|-------------|
| `import { Video` / `import type { Video` | `import { ParsedMedia` / `import type { ParsedMedia` |
| `: Video` (type annotation) | `: ParsedMedia` |
| `Video[]` | `ParsedMedia[]` |
| `Video \| null` | `ParsedMedia \| null` |
| `'videos'` (table name) | `'parsed_media'` |
| `'videos_with_tags'` | `'parsed_media_with_tags'` |
| `video_id` (property access/query) | `media_id` |
| `videoId` (variable name) | `mediaId` |
| `CollectionVideo` | `CollectionMedia` |
| `VideoAnalysis` | `MediaAnalysis` |

**Step 2: Update aiService.ts specifically**

Change endpoint calls from `platform_id` to `resource_id`:

```typescript
export async function triggerTranscription(resourceId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(
    `${apiUrl}/api/v1/ai/transcribe?resource_id=${resourceId}`,
    { method: 'POST', headers: await getAuthHeaders() }
  );
  if (!response.ok) throw new Error('Failed to trigger transcription');
}
```

Similarly for `triggerSummary()` and `triggerVisualAnalysis()`.

**Step 3: Commit**

```bash
git add frontend/services/
git commit -m "refactor: update all frontend services for parsed_media rename"
```

---

### Task 18: Update TaskManagerContext

**Files:**
- Modify: `frontend/contexts/TaskManagerContext.tsx`

**Step 1: Expand TaskType**

```typescript
export type TaskType = 'download' | 'upload' | 'transcode' | 'ai_extract' | 'ai_transcription' | 'ai_summary';

export interface UnifiedTask {
  // ... existing fields ...
  media_id?: string;    // renamed from video_id
  group_id?: string;    // NEW: links related AI sub-tasks
}

// Category mapping for UI
export const TASK_CATEGORIES = {
  all: null as TaskType[] | null,
  transfer: ['download', 'upload'] as TaskType[],
  ai: ['ai_extract', 'ai_transcription', 'ai_summary'] as TaskType[],
  other: ['transcode'] as TaskType[],
} as const;

export type TaskCategory = keyof typeof TASK_CATEGORIES;
```

**Step 2: Update utility functions**

```typescript
export function taskTypeLabel(type: TaskType): string {
  switch (type) {
    case 'download': return 'Download';
    case 'upload': return 'Upload';
    case 'transcode': return 'Transcode';
    case 'ai_extract': return 'Audio Extract';
    case 'ai_transcription': return 'Transcription';
    case 'ai_summary': return 'Summary';
    default: return type;
  }
}

export function taskTypeIcon(type: TaskType): string {
  switch (type) {
    case 'download': return '📥';
    case 'upload': return '📤';
    case 'transcode': return '🔄';
    case 'ai_extract': return '🎵';
    case 'ai_transcription': return '📝';
    case 'ai_summary': return '✨';
    default: return '⚙️';
  }
}
```

**Step 3: Commit**

```bash
git add frontend/contexts/TaskManagerContext.tsx
git commit -m "feat: expand TaskType with AI sub-types and category mapping"
```

---

### Task 19: Rewrite TasksPanel.tsx

**Files:**
- Modify: `frontend/components/TasksPanel.tsx`
- Delete: `frontend/components/AITasksPanel.tsx` (after integration)

**Step 1: Rewrite TasksPanel with category tabs + sub-pills + stats**

Complete rewrite. Key structure:

```tsx
import React, { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useTaskManager,
  TASK_CATEGORIES,
  TaskCategory,
  TaskType,
  taskTypeLabel,
  taskTypeIcon,
  formatSpeed,
  formatFileSize,
} from '../contexts/TaskManagerContext';

type StatusFilter = 'all' | 'active' | 'completed' | 'failed';

export const TasksPanel: React.FC = () => {
  const { t } = useTranslation();
  const { tasks, cancelTask, retryTask, deleteTask, clearCompleted } = useTaskManager();

  const [category, setCategory] = useState<TaskCategory>('all');
  const [subType, setSubType] = useState<TaskType | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  // Filter tasks by category + sub-type
  const filteredByCategory = useMemo(() => {
    const types = TASK_CATEGORIES[category];
    if (!types) return tasks; // 'all'
    return tasks.filter((t) => types.includes(t.task_type));
  }, [tasks, category]);

  const filteredTasks = useMemo(() => {
    let result = filteredByCategory;
    if (subType) result = result.filter((t) => t.task_type === subType);
    if (statusFilter === 'active') result = result.filter((t) => ['pending', 'processing'].includes(t.status));
    if (statusFilter === 'completed') result = result.filter((t) => t.status === 'completed');
    if (statusFilter === 'failed') result = result.filter((t) => t.status === 'failed');
    return result;
  }, [filteredByCategory, subType, statusFilter]);

  // Stats for current category
  const stats = useMemo(() => ({
    active: filteredByCategory.filter((t) => ['pending', 'processing'].includes(t.status)).length,
    completed: filteredByCategory.filter((t) => t.status === 'completed').length,
    failed: filteredByCategory.filter((t) => t.status === 'failed').length,
    total: filteredByCategory.length,
  }), [filteredByCategory]);

  // Sub-type pills for current category
  const subTypes = TASK_CATEGORIES[category];

  // Group AI tasks by group_id for collapsed rendering
  // ... (implementation detail)

  return (
    <div className="space-y-4">
      {/* Category Tabs */}
      <div className="flex gap-1 bg-zinc-900/50 rounded-lg p-1">
        {(['all', 'transfer', 'ai', 'other'] as TaskCategory[]).map((cat) => (
          <button
            key={cat}
            onClick={() => { setCategory(cat); setSubType(null); }}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              category === cat ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {cat === 'all' ? 'All' : cat === 'transfer' ? 'Transfer' : cat === 'ai' ? 'AI' : 'Other'}
          </button>
        ))}
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-4 gap-2">
        {(['active', 'completed', 'failed', 'total'] as StatusFilter[]).map((key) => (
          /* ... stat card ... */
        ))}
      </div>

      {/* Sub-type Pills */}
      {subTypes && (
        <div className="flex gap-2">
          <button onClick={() => setSubType(null)} className={...}>All</button>
          {subTypes.map((st) => (
            <button key={st} onClick={() => setSubType(st)} className={...}>
              {taskTypeLabel(st)}
            </button>
          ))}
        </div>
      )}

      {/* Task List */}
      <div className="space-y-2">
        {filteredTasks.map((task) => (
          <TaskItem key={task.id} task={task} ... />
        ))}
      </div>
    </div>
  );
};
```

**Step 2: Delete AITasksPanel.tsx**

After verifying TasksPanel handles all task types:
```bash
git rm frontend/components/AITasksPanel.tsx
```

Remove any imports of `AITasksPanel` from other files.

**Step 3: Commit**

```bash
git add frontend/components/TasksPanel.tsx
git rm frontend/components/AITasksPanel.tsx
git commit -m "feat: rewrite TasksPanel with category tabs, sub-pills, and stats"
```

---

### Task 20: Update VideoDetailPanel + remaining components

**Files:**
- Modify: `frontend/components/VideoDetailPanel.tsx` — read AI status from resource, keep transcript/summary from media_transcripts
- Modify: All components importing `Video` type → `ParsedMedia`
- Modify: `frontend/components/LinkVideoModal.tsx` → rename to `LinkMediaModal.tsx`
- Modify: `frontend/hooks/useLibrary.ts` — `Video[]` → `ParsedMedia[]`, table refs
- Modify: `frontend/hooks/useParser.ts` — type refs

**Step 1: Update VideoDetailPanel**

Key change: Read AI status from the `resource` object (passed via context/props) instead of `video`:

```typescript
// Before: video.transcript_status
// After: resource.transcript_status
```

Transcript/summary data still fetched via `media_id`:
```typescript
const transcript = await getTranscript(resource.media_id);
```

**Step 2: Batch-update all components**

Search for `Video` type imports and update:
```bash
grep -rn "import.*Video.*from.*types" frontend/components/ frontend/hooks/ frontend/pages/
```

Update each file:
- `import { Video }` → `import { ParsedMedia }`
- `Video` type usage → `ParsedMedia`
- `video_id` property access → `media_id`

Affected components (from exploration):
- `CompactMediaCard.tsx`, `MediaCard.tsx`, `LibraryTable.tsx`, `LibraryFeed.tsx`
- `CollectionFolderCard.tsx`, `RipVaultView.tsx`, `VideoReviewPage.tsx`
- `CleanupSuggestionsView.tsx`, `FileCard.tsx`, `FileInfoPanel.tsx`
- `ProjectFilesView.tsx`, `TeamLibraryView.tsx`, `LinkVideoModal.tsx`
- `TaskMonitor.tsx`, `VideoAnalysisPanel.tsx`

**Step 3: Rename LinkVideoModal**

```bash
mv frontend/components/LinkVideoModal.tsx frontend/components/LinkMediaModal.tsx
```

Update all imports of `LinkVideoModal` → `LinkMediaModal`.

**Step 4: Update hooks**

`useLibrary.ts`:
```typescript
import { ParsedMedia, Collection } from '../types';
const [library, setLibrary] = useState<ParsedMedia[]>([]);
// .from('parsed_media') instead of .from('videos')
// 'media_collections' instead of 'video_collections'
```

**Step 5: Commit**

```bash
git add frontend/components/ frontend/hooks/ frontend/pages/
git commit -m "refactor: update all frontend components for ParsedMedia rename"
```

---

### Task 21: Update i18n + constants

**Files:**
- Modify: `frontend/public/locales/en.json` — add AI task category labels
- Modify: `frontend/public/locales/zh.json` — same
- Modify: `frontend/constants.ts` — update any `videos` refs

**Step 1: Add TasksPanel i18n keys**

In `en.json` (under `tasks` namespace):
```json
"tasks": {
  "categoryAll": "All",
  "categoryTransfer": "Transfer",
  "categoryAI": "AI",
  "categoryOther": "Other",
  "statsActive": "Active",
  "statsCompleted": "Done",
  "statsFailed": "Failed",
  "statsTotal": "Total",
  "typeDownload": "Download",
  "typeUpload": "Upload",
  "typeTranscode": "Transcode",
  "typeAiExtract": "Audio Extract",
  "typeAiTranscription": "Transcription",
  "typeAiSummary": "Summary",
  "noTasks": "No tasks yet",
  "clearCompleted": "Clear Completed"
}
```

Add corresponding Chinese translations in `zh.json`.

**Step 2: Commit**

```bash
git add frontend/public/locales/
git commit -m "feat: add TasksPanel i18n translations"
```

---

### Task 22: Final verification

**Step 1: Search for any remaining `Video` type or `videos` table references**

```bash
grep -rn '"videos"' frontend/ --include="*.ts" --include="*.tsx" | grep -v node_modules
grep -rn 'video_id' frontend/ --include="*.ts" --include="*.tsx" | grep -v node_modules
grep -rn ': Video\b' frontend/ --include="*.ts" --include="*.tsx" | grep -v node_modules
```

Fix any remaining references.

**Step 2: Build frontend**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with zero TypeScript errors.

**Step 3: Start backend**

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8081
```

Expected: Starts clean.

**Step 4: Smoke test**

1. Open http://localhost:5176/
2. Login with test account
3. Verify Parser page loads (media list from `parsed_media`)
4. Verify Resources page loads
5. Open TasksPanel → see category tabs (All/Transfer/AI/Other)
6. Trigger a download → appears in Transfer tab
7. Trigger AI transcription → appears in AI tab as sub-task

**Step 5: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix: final cleanup for parsed_media rename"
```

---

## Summary

| Phase | Tasks | Commits | Key Deliverable |
|-------|-------|---------|-----------------|
| 1. DB Migrations | 1-3 | 2 | Tables + columns renamed, AI status on resources |
| 2. Backend Rename | 4-11 | 7 | All Python code uses `parsed_media` / `MediaRepository` |
| 3. Backend AI | 12-15 | 3 | 3 sub-tasks with `group_id`, resource-level status |
| 4. Frontend | 16-22 | 6 | `ParsedMedia` type, TasksPanel with tabs/pills/stats |
| **Total** | **22** | **~18** | Full rename + AI refactoring complete |
