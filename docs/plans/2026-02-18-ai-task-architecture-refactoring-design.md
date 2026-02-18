# AI Task Architecture Refactoring — Design Document

> Date: 2026-02-18
> Branch: `feature/Points-capacity-payment-system`
> Scope: Full refactoring — DB rename + AI data model + TasksPanel UI

---

## 1. Goals

1. **Rename `videos` → `parsed_media`**: Full rename across DB tables, columns (`video_id` → `media_id`), backend code, and frontend types
2. **unified_tasks as single task data source**: Split monolithic `ai_pipeline` task into 3 sub-tasks (`ai_extract`, `ai_transcription`, `ai_summary`), each with its own `unified_tasks` row
3. **Per-user AI status on `resources`**: Move AI status columns from `parsed_media` (shared) to `resources` (per-user via `creator_id`), keeping transcripts/summaries shared by `media_id`
4. **TasksPanel UI redesign**: Big category tabs (All / Transfer / AI / Other) + sub-filter pills + dynamic stats cards

---

## 2. Database Changes

### 2a. Table Renames

| Old Name | New Name |
|----------|----------|
| `videos` | `parsed_media` |
| `video_transcripts` | `media_transcripts` |
| `video_summaries` | `media_summaries` |
| `video_collections` | `media_collections` |
| `video_tags` | `media_tags` |
| `video_analysis` | `media_analysis` |
| `video_access_logs` | `media_access_logs` |
| `videos_with_tags` (VIEW) | `parsed_media_with_tags` |

### 2b. Column Renames (`video_id` → `media_id`)

| Table | Column Change |
|-------|--------------|
| `media_transcripts` | `video_id` → `media_id` |
| `media_summaries` | `video_id` → `media_id` |
| `media_collections` | `video_id` → `media_id` |
| `media_tags` | `video_id` → `media_id` |
| `media_analysis` | `video_id` → `media_id` |
| `media_access_logs` | `video_id` → `media_id` |
| `resources` | `video_id` → `media_id` |
| `project_files` | `video_id` → `media_id` |
| `unified_tasks` | `video_id` → `media_id` |

### 2c. New Columns on `resources`

```sql
ALTER TABLE resources
  ADD COLUMN transcript_status VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN summary_status VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN visual_analysis_status VARCHAR(20) NOT NULL DEFAULT 'none';
-- Values: 'none' | 'pending' | 'processing' | 'completed' | 'failed'
```

### 2d. New Column on `unified_tasks`

```sql
ALTER TABLE unified_tasks
  ADD COLUMN group_id UUID;
-- Groups related sub-tasks (e.g. 3 AI steps for the same media)
CREATE INDEX idx_unified_tasks_group ON unified_tasks(group_id) WHERE group_id IS NOT NULL;
```

### 2e. Remove AI Status from `parsed_media`

After migration + backfill, drop legacy columns:
- `transcript_status`
- `summary_status`
- `visual_analysis_status`
- `transcript_bool`
- `summary_bool`

### 2f. Migration Strategy

Split into **3 sequential migrations** for safety:

1. **Migration A: Table & column renames** — `ALTER TABLE RENAME`, rebuild indexes, update RLS policies, recreate view
2. **Migration B: AI status on resources + unified_tasks group_id** — Add new columns
3. **Migration C: Backfill + cleanup** — Copy AI status from `parsed_media` to `resources`, drop legacy columns

---

## 3. Backend Changes

### 3a. File Renames

| Old File | New File |
|----------|----------|
| `repositories/video_repository.py` | `repositories/media_repository.py` |
| `services/video_service.py` | `services/media_service.py` |
| `api/videos_router.py` | `api/media_router.py` |
| `schemas/` — video-related | Update class names |

### 3b. Class/Constant Renames

| Old | New |
|-----|-----|
| `VideoRepository` | `MediaRepository` |
| `TABLE_NAME = "videos"` | `TABLE_NAME = "parsed_media"` |
| `VIEW_NAME = "videos_with_tags"` | `VIEW_NAME = "parsed_media_with_tags"` |
| `video_service.py` classes | `media_service.py` classes |
| `videos_router` prefix | `media_router` prefix |

### 3c. AI Tasks Refactoring (`ai_tasks.py`)

**Before** (current):
```python
chain_ai_pipeline() → creates 1 unified_task (type="ai_pipeline")
  → extract_audio_task (updates videos.transcript_status)
  → transcribe_audio_task (updates videos.transcript_status)
  → generate_summary_task (updates videos.summary_status)
```

**After** (new):
```python
chain_ai_pipeline() → creates 3 unified_tasks + 1 group_id
  → extract_audio_task   (own unified_task, type="ai_extract",       updates resources.transcript_status="processing")
  → transcribe_audio_task (own unified_task, type="ai_transcription", updates resources.transcript_status="completed")
  → generate_summary_task (own unified_task, type="ai_summary",       updates resources.summary_status="completed")
```

Key changes:
- `_update_status()` → updates `resources` table by `resource_id` (not `parsed_media` by `platform_id`)
- `chain_ai_pipeline()` accepts `resource_id` parameter (in addition to `platform_id`)
- Each sub-task creates its own `unified_task` row with shared `group_id`
- Each sub-task marks its own row as completed/failed independently

### 3d. AI Router Changes

Endpoints shift from `platform_id` to `resource_id`:

| Old Endpoint | New Endpoint |
|-------------|-------------|
| `POST /ai/transcribe/{platform_id}` | `POST /ai/transcribe/{resource_id}` |
| `POST /ai/summarize/{platform_id}` | `POST /ai/summarize/{resource_id}` |
| `GET /ai/transcript/{platform_id}` | `GET /ai/transcript/{resource_id}` |
| `GET /ai/summary/{platform_id}` | `GET /ai/summary/{resource_id}` |

The router resolves `resource_id` → `media_id` → physical file path internally.

### 3e. Other Affected Backend Files

All files referencing `video_id` or `VideoRepository` need updates:
- `services/resources_service.py` — `video_id` → `media_id`
- `services/projects_service.py` — `link_video()` → `link_media()`
- `services/collections_service.py` — table refs
- `services/search_service.py` — field names
- `services/cleanup_service.py` — field names
- `repositories/resources_repository.py` — column ref
- `repositories/projects_repository.py` — column ref
- `repositories/ai_repository.py` — table refs + column refs
- `repositories/tags_repository.py` — table refs
- `repositories/collections_repository.py` — table refs
- `repositories/analysis_repository.py` — table refs
- `schemas/projects.py` — `video_id` → `media_id`
- `schemas/search.py` — field names
- `schemas/tags.py` — field names
- `schemas/cleanup.py` — field names
- `tasks/download_tasks.py` — tracker calls, `_maybe_chain_ai_pipeline()` passes `resource_id`
- `tasks/analysis_tasks.py` — references
- `tasks/parse_tasks.py` — references

---

## 4. Frontend Changes

### 4a. Type Renames (`types.ts`)

```typescript
// Before
export interface Video { id: number; ... }
export interface CollectionVideo { collection_id: number; video_id: number; }

// After
export interface ParsedMedia { id: number; ... }
export interface CollectionMedia { collection_id: number; media_id: number; }
```

Remove from `ParsedMedia`: `transcript_status`, `summary_status`, `visual_analysis_status`, `transcript_bool`, `summary_bool` (moved to `Resource` type).

Add to `Resource`:
```typescript
transcript_status: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
summary_status: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
visual_analysis_status: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
```

### 4b. Service File Renames

| Old | New |
|-----|-----|
| `services/dataService.ts` (TABLE_NAME='videos') | Update to `'parsed_media'` / `'parsed_media_with_tags'` |
| `services/collectionService.ts` (`.from('videos')`) | Update to `'.from('parsed_media')'` |
| `services/aiService.ts` | Update endpoints to use `resource_id` |

### 4c. TaskManagerContext Expansion

```typescript
// Before
type TaskType = 'download' | 'upload' | 'transcode' | 'ai_pipeline';

// After
type TaskType = 'download' | 'upload' | 'transcode' | 'ai_extract' | 'ai_transcription' | 'ai_summary';

// Add
interface UnifiedTask {
  // ... existing fields
  group_id?: string;  // links related AI sub-tasks
  media_id?: string;  // renamed from video_id
}
```

Category mapping for UI:
```typescript
const TASK_CATEGORIES = {
  all: null,
  transfer: ['download', 'upload'],
  ai: ['ai_extract', 'ai_transcription', 'ai_summary'],
  other: ['transcode'],
} as const;
```

### 4d. TasksPanel UI Redesign

```
┌─────────────────────────────────────────────────────┐
│  [All]  [Transfer]  [AI]  [Other]     ← Big tabs    │
├─────────────────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐               │
│  │Active│ │Done  │ │Failed│ │Total │  ← Stats cards │
│  │  3   │ │  12  │ │  1   │ │  16  │   (filtered)  │
│  └──────┘ └──────┘ └──────┘ └──────┘               │
├─────────────────────────────────────────────────────┤
│  (Download) (Upload)              ← Sub-type pills  │
│  Shown only under Transfer tab                      │
│  AI tab shows: (Extract) (Transcribe) (Summary)     │
├─────────────────────────────────────────────────────┤
│  ┌─ Task ─────────────────────────────────────────┐ │
│  │ 📥 video_title.mp4        45% ████░░  2MB/s    │ │
│  │    Download · 2 min ago                   [⟳]  │ │
│  └────────────────────────────────────────────────┘ │
│  ┌─ AI Group (collapsed) ─────────────────────────┐ │
│  │ 🤖 AI: video_xyz                               │ │
│  │    ✅ Extract  ⏳ Transcribing...  ◯ Summary   │ │
│  │    ▶ Expand to see sub-tasks                   │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

- **AI Group rendering**: Tasks with same `group_id` collapse into one card showing 3-step pipeline progress
- **Individual AI tasks**: Under sub-pills, show as individual task cards
- **AITasksPanel.tsx**: Removed entirely — unified TasksPanel handles all

### 4e. VideoDetailPanel Changes

- Read AI status from `resources.transcript_status` / `resources.summary_status` (via resource context)
- Read transcript/summary artifacts from `media_transcripts` / `media_summaries` (via `media_id`)
- Trigger AI via `resource_id` (not `platform_id`)

### 4f. Component File Renames

| Component | Change |
|-----------|--------|
| `AITasksPanel.tsx` | **Delete** (replaced by TasksPanel) |
| `VideoDetailPanel.tsx` | Update to use resource-level AI status |
| `VideoReviewPage.tsx` | Update type refs: Video → ParsedMedia |
| `MediaCard.tsx` | Update type refs |
| `CompactMediaCard.tsx` | Update type refs |
| `LibraryTable.tsx` | Update type refs |
| `LibraryFeed.tsx` | Update type refs |
| `LinkVideoModal.tsx` | Rename to `LinkMediaModal.tsx`, update refs |
| All other components using `Video` type | Update import |

### 4g. Hook Changes

- `useLibrary.ts`: `Video[]` → `ParsedMedia[]`, realtime subscription table refs
- `useParser.ts`: Update type refs
- `TaskManagerContext.tsx`: Expand types as described in 4c

---

## 5. Affected File Count Summary

| Category | Files | Estimated Changes |
|----------|-------|-------------------|
| SQL migrations (new) | 3 | ~200 lines |
| Backend repositories | 7 | ~150 refs |
| Backend services | 8 | ~120 refs |
| Backend schemas | 5 | ~40 refs |
| Backend API routes | 5 | ~80 refs |
| Backend Celery tasks | 4 | ~60 refs |
| Frontend types | 1 | ~30 refs |
| Frontend services | 7 | ~80 refs |
| Frontend components | 18+ | ~200 refs |
| Frontend hooks/contexts | 3 | ~40 refs |
| **Total** | **~60 files** | **~1000 refs** |

---

## 6. Implementation Order

Given the massive scope (~60 files, ~1000 references), split into 4 phases:

### Phase 1: Database Migrations
1. Migration A: Rename tables + columns
2. Migration B: Add AI columns to resources + group_id to unified_tasks
3. Migration C: Backfill AI status from parsed_media → resources
4. Execute all 3 on local Supabase

### Phase 2: Backend Rename
1. Rename files: video_repository → media_repository, etc.
2. Update all table/column references
3. Update imports across all services/routes/tasks
4. Verify backend starts without errors

### Phase 3: Backend AI Refactoring
1. Refactor `chain_ai_pipeline()` → 3 sub-tasks with group_id
2. Update `_update_status()` → write to resources table
3. Update AI router endpoints to use resource_id
4. Update `_maybe_chain_ai_pipeline()` in download_tasks
5. Verify Celery tasks run correctly

### Phase 4: Frontend
1. Rename types: Video → ParsedMedia, video_id → media_id
2. Update all services and table references
3. Expand TaskManagerContext types
4. Rewrite TasksPanel UI (tabs + pills + stats + group rendering)
5. Delete AITasksPanel
6. Update VideoDetailPanel to use resource AI status
7. Update all remaining components
8. Verify `npm run build` passes

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Migration breaks existing data | Test on local Supabase first; backup before production |
| 60+ file changes may introduce regressions | Phase-by-phase with build checks after each |
| RLS policies reference old table names | Explicitly recreate all policies in Migration A |
| Realtime subscriptions break | Update supabase_realtime publication in migration |
| Frontend type errors cascade | Start from types.ts, fix imports outward |
| AI pipeline mid-flight during deploy | Add backward compat: accept both old/new task_types briefly |

---

## 8. Verification Checklist

- [ ] Local Supabase migrations execute without errors
- [ ] `uv run uvicorn app.main:app --reload --port 8081` starts clean
- [ ] `npm run build` passes with zero TS errors
- [ ] Download task creates unified_task correctly
- [ ] AI pipeline creates 3 separate unified_task rows with shared group_id
- [ ] Resources table shows per-user AI status
- [ ] TasksPanel shows big category tabs with correct filtering
- [ ] AI group tasks collapse/expand in TasksPanel
- [ ] VideoDetailPanel reads AI status from resources
- [ ] Transcript/summary artifacts accessible via media_id
