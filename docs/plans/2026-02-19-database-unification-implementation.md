# Database Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the dual media_*/resource_* table system by unifying all satellite tables to FK on `resources`, slim `parsed_media` to platform-snapshot-only, and standardize UUID tables to Snowflake BIGINT.

**Architecture:** Five SQL migrations executed in order, then backend code updates (table name constants + queries), then frontend Supabase query updates. Each migration is self-contained and safe to run independently.

**Tech Stack:** PostgreSQL (Supabase local), FastAPI (Python), React/TypeScript, Supabase JS client

---

## Pre-Requisites

- Local Supabase running: `supabase start` (shared across worktrees at 127.0.0.1:54321)
- Studio: http://127.0.0.1:54323
- Backend port: 8081
- Frontend port: 5176
- Current latest migration: `074_resource_metadata.sql`

---

### Task 1: Migration — Slim parsed_media (drop redundant columns)

**Files:**
- Create: `supabase/migrations/075_slim_parsed_media.sql`

**Step 1: Write the migration**

```sql
-- 075_slim_parsed_media.sql
-- Remove columns that are redundant or moved to resources table.
-- parsed_media should only hold immutable platform snapshot data.

BEGIN;

-- Columns identical/redundant to other columns
ALTER TABLE parsed_media DROP COLUMN IF EXISTS source_url;       -- identical to original_url
ALTER TABLE parsed_media DROP COLUMN IF EXISTS external_id;      -- overlaps with platform_id

-- Boolean flags redundant with status columns (status now on resources via 067)
ALTER TABLE parsed_media DROP COLUMN IF EXISTS transcript_bool;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS summary_bool;

-- User-editable fields that belong on resources (already there via 074)
ALTER TABLE parsed_media DROP COLUMN IF EXISTS notes;

-- AI status columns moved to resources in migration 067
ALTER TABLE parsed_media DROP COLUMN IF EXISTS transcript_status;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS summary_status;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS visual_analysis_status;

COMMIT;
```

**Step 2: Execute the migration on local Supabase**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f supabase/migrations/075_slim_parsed_media.sql`
Expected: `ALTER TABLE` messages, no errors.

**Step 3: Verify parsed_media still works**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT count(*) FROM parsed_media;"`
Expected: Returns row count without error.

**Step 4: Commit**

```bash
git add supabase/migrations/075_slim_parsed_media.sql
git commit -m "migration: slim parsed_media — drop redundant columns"
```

---

### Task 2: Migration — Rename media_* satellite tables to resource_*

**Files:**
- Create: `supabase/migrations/076_unify_media_to_resource.sql`

**Step 1: Write the migration**

This migration renames tables and updates FK columns from `media_id` to `resource_id`, pointing to `resources(id)` instead of `parsed_media(id)`. Since `media_summaries`, `media_transcripts`, `media_analysis`, `media_access_logs` all have 0 rows, we can safely restructure them.

```sql
-- 076_unify_media_to_resource.sql
-- Rename media_* satellite tables to resource_*, update FKs to point to resources.
-- These tables currently have 0 rows so restructuring is safe.

BEGIN;

-- ============================================================================
-- 1. media_summaries -> resource_summaries
-- ============================================================================
ALTER TABLE media_summaries RENAME TO resource_summaries;

-- Drop old FK and add new one pointing to resources
ALTER TABLE resource_summaries DROP CONSTRAINT IF EXISTS media_summaries_media_id_fkey;
ALTER TABLE resource_summaries RENAME COLUMN media_id TO resource_id;
ALTER TABLE resource_summaries
  ADD CONSTRAINT resource_summaries_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- Update unique constraint if exists
ALTER TABLE resource_summaries DROP CONSTRAINT IF EXISTS media_summaries_media_id_key;
ALTER TABLE resource_summaries ADD CONSTRAINT resource_summaries_resource_id_key UNIQUE (resource_id);

-- ============================================================================
-- 2. media_transcripts -> resource_transcripts
-- ============================================================================
ALTER TABLE media_transcripts RENAME TO resource_transcripts;

ALTER TABLE resource_transcripts DROP CONSTRAINT IF EXISTS media_transcripts_media_id_fkey;
ALTER TABLE resource_transcripts RENAME COLUMN media_id TO resource_id;
ALTER TABLE resource_transcripts
  ADD CONSTRAINT resource_transcripts_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

ALTER TABLE resource_transcripts DROP CONSTRAINT IF EXISTS media_transcripts_media_id_key;
ALTER TABLE resource_transcripts ADD CONSTRAINT resource_transcripts_resource_id_key UNIQUE (resource_id);

-- ============================================================================
-- 3. media_analysis -> resource_analysis
-- ============================================================================
ALTER TABLE media_analysis RENAME TO resource_analysis;

ALTER TABLE resource_analysis DROP CONSTRAINT IF EXISTS media_analysis_pkey;
ALTER TABLE resource_analysis RENAME COLUMN media_id TO resource_id;
-- media_analysis had composite PK (media_id, analysis_level), recreate it
ALTER TABLE resource_analysis
  ADD CONSTRAINT resource_analysis_pkey PRIMARY KEY (resource_id, analysis_level);

ALTER TABLE resource_analysis DROP CONSTRAINT IF EXISTS media_analysis_media_id_fkey;
ALTER TABLE resource_analysis
  ADD CONSTRAINT resource_analysis_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- ============================================================================
-- 4. media_access_logs -> resource_access_logs
-- ============================================================================
ALTER TABLE media_access_logs RENAME TO resource_access_logs;

ALTER TABLE resource_access_logs DROP CONSTRAINT IF EXISTS media_access_logs_media_id_fkey;
ALTER TABLE resource_access_logs RENAME COLUMN media_id TO resource_id;
ALTER TABLE resource_access_logs
  ADD CONSTRAINT resource_access_logs_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- Rename index if exists
DROP INDEX IF EXISTS idx_media_access_logs_media_id;
CREATE INDEX IF NOT EXISTS idx_resource_access_logs_resource_id
  ON resource_access_logs(resource_id);

-- ============================================================================
-- 5. DROP media_collections (replaced by resource_items + folders, 0 rows)
-- ============================================================================
DROP TABLE IF EXISTS media_collections;

-- ============================================================================
-- 6. DROP old views
-- ============================================================================
DROP VIEW IF EXISTS parsed_media_with_tags;
DROP VIEW IF EXISTS media_statistics;

COMMIT;
```

**Step 2: Execute the migration**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f supabase/migrations/076_unify_media_to_resource.sql`
Expected: `ALTER TABLE`, `DROP TABLE`, `DROP VIEW` messages, no errors.

**Step 3: Verify renamed tables exist**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'resource_%' ORDER BY tablename;"`
Expected: Shows `resource_access_logs`, `resource_analysis`, `resource_items`, `resource_summaries`, `resource_tags`, `resource_transcripts`, `resource_versions`.

**Step 4: Verify old tables are gone**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'media_%' ORDER BY tablename;"`
Expected: Empty result (no media_* tables left).

**Step 5: Commit**

```bash
git add supabase/migrations/076_unify_media_to_resource.sql
git commit -m "migration: rename media_* tables to resource_*, drop legacy views"
```

---

### Task 3: Migration — Enhance resource_tags and merge media_tags

**Files:**
- Create: `supabase/migrations/077_enhance_resource_tags.sql`

**Step 1: Write the migration**

`resource_tags` already exists with `(resource_id, tag_id)` composite PK. We need to add `source`, `confidence`, `created_at` columns. Then migrate the 3 rows from `media_tags` and drop it.

```sql
-- 077_enhance_resource_tags.sql
-- Add source/confidence/created_at to resource_tags, migrate media_tags data, drop media_tags.

BEGIN;

-- ============================================================================
-- 1. Enhance resource_tags with new columns
-- ============================================================================
ALTER TABLE resource_tags ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'user';
ALTER TABLE resource_tags ADD COLUMN IF NOT EXISTS confidence FLOAT;
ALTER TABLE resource_tags ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

-- ============================================================================
-- 2. Migrate media_tags data to resource_tags
--    media_tags has (media_id, tag_id, source, confidence, created_at)
--    We need to map media_id (parsed_media.id) -> resource_id (resources.id)
--    via resources.video_id = parsed_media.id
-- ============================================================================
INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
SELECT r.id, mt.tag_id, mt.source, mt.confidence, mt.created_at
FROM media_tags mt
JOIN resources r ON r.video_id = mt.media_id
ON CONFLICT (resource_id, tag_id) DO NOTHING;

-- ============================================================================
-- 3. Drop media_tags table
-- ============================================================================
DROP TABLE IF EXISTS media_tags;

COMMIT;
```

**Step 2: Execute the migration**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f supabase/migrations/077_enhance_resource_tags.sql`
Expected: `ALTER TABLE`, `INSERT`, `DROP TABLE` messages, no errors.

**Step 3: Verify resource_tags has new columns**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='resource_tags' ORDER BY ordinal_position;"`
Expected: Shows `resource_id`, `tag_id`, `tagged_by`, `source`, `confidence`, `created_at`.

**Step 4: Verify media_tags is gone**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT count(*) FROM media_tags;" 2>&1`
Expected: Error `relation "media_tags" does not exist`.

**Step 5: Commit**

```bash
git add supabase/migrations/077_enhance_resource_tags.sql
git commit -m "migration: enhance resource_tags with source/confidence, merge media_tags"
```

---

### Task 4: Migration — Standardize IDs to Snowflake BIGINT

**Files:**
- Create: `supabase/migrations/078_standardize_ids.sql`

**Step 1: Write the migration**

Migrate 8 core UUID tables to Snowflake BIGINT. For tables with 0 rows or minimal data, we can safely alter the column type. The `generate_snowflake_id()` function already exists from migration 050.

```sql
-- 078_standardize_ids.sql
-- Migrate core UUID tables to Snowflake BIGINT for consistency and performance.
-- generate_snowflake_id() function exists from migration 050.

BEGIN;

-- ============================================================================
-- Helper: Migrate a table's id column from UUID to BIGINT with Snowflake default
-- For tables with existing data, we need to handle FK references.
-- For empty/low-data tables, direct ALTER is safe.
-- ============================================================================

-- 1. resource_items (core table, has data — need careful migration)
--    First drop FKs referencing resource_items, alter, then recreate

-- Drop dependent FKs (none currently reference resource_items.id as FK)
ALTER TABLE resource_items
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- 2. resource_versions (has data potentially)
ALTER TABLE resource_versions
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- 3. tags (referenced by resource_tags.tag_id — need to update FK type too)
--    resource_tags.tag_id is UUID, must change to BIGINT to match
ALTER TABLE resource_tags DROP CONSTRAINT IF EXISTS resource_tags_tag_id_fkey;

ALTER TABLE tags
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE resource_tags
  ALTER COLUMN tag_id SET DATA TYPE BIGINT USING tag_id::text::bigint;

-- Recreate FK (tags may have been referenced by smart_collections too)
-- We'll add back the FK after both columns match types
ALTER TABLE resource_tags
  ADD CONSTRAINT resource_tags_tag_id_fkey
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE;

-- 4. unified_tasks
--    Check for FKs from task_assets
ALTER TABLE task_assets DROP CONSTRAINT IF EXISTS task_assets_task_id_fkey;

ALTER TABLE unified_tasks
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- 5. task_assets
ALTER TABLE task_assets
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- task_assets.task_id also needs to be BIGINT to match unified_tasks.id
ALTER TABLE task_assets
  ALTER COLUMN task_id SET DATA TYPE BIGINT USING task_id::text::bigint;

ALTER TABLE task_assets
  ADD CONSTRAINT task_assets_task_id_fkey
  FOREIGN KEY (task_id) REFERENCES unified_tasks(id) ON DELETE CASCADE;

-- 6. notifications (no inbound FKs)
ALTER TABLE notifications
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- 7. user_logs (no inbound FKs)
ALTER TABLE user_logs
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- 8. search_logs (no inbound FKs)
ALTER TABLE search_logs
  ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id()),
  ALTER COLUMN id SET DEFAULT generate_snowflake_id();

COMMIT;
```

**Step 2: Execute the migration**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f supabase/migrations/078_standardize_ids.sql`
Expected: `ALTER TABLE` messages, no errors.

**Step 3: Verify IDs are BIGINT**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE column_name='id' AND table_name IN ('resource_items','resource_versions','tags','unified_tasks','task_assets','notifications','user_logs','search_logs') ORDER BY table_name;"`
Expected: All show `data_type = bigint`.

**Step 4: Commit**

```bash
git add supabase/migrations/078_standardize_ids.sql
git commit -m "migration: standardize 8 core tables to Snowflake BIGINT IDs"
```

---

### Task 5: Migration — Create resource_statistics view

**Files:**
- Create: `supabase/migrations/079_resource_statistics_view.sql`

**Step 1: Write the migration**

```sql
-- 079_resource_statistics_view.sql
-- Create resource_statistics view to replace dropped media_statistics.

CREATE OR REPLACE VIEW resource_statistics AS
SELECT
  r.creator_id AS user_id,
  count(*) AS total_resources,
  count(*) FILTER (WHERE r.file_type = 'video') AS video_count,
  count(*) FILTER (WHERE r.file_type = 'image') AS image_count,
  count(*) FILTER (WHERE r.file_type = 'audio') AS audio_count,
  count(*) FILTER (WHERE r.file_type = 'document') AS document_count,
  coalesce(sum(r.file_size_bytes), 0) AS total_size_bytes,
  count(*) FILTER (WHERE r.is_trashed = true) AS trashed_count
FROM resources r
GROUP BY r.creator_id;
```

**Step 2: Execute the migration**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f supabase/migrations/079_resource_statistics_view.sql`
Expected: `CREATE VIEW`.

**Step 3: Verify view works**

Run: `psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "SELECT * FROM resource_statistics LIMIT 5;"`
Expected: Returns rows (or empty if no resources).

**Step 4: Commit**

```bash
git add supabase/migrations/079_resource_statistics_view.sql
git commit -m "migration: create resource_statistics view"
```

---

### Task 6: Backend — Update ai_repository.py table references

**Files:**
- Modify: `backend/app/repositories/ai_repository.py`

**Step 1: Update table names**

Change all `media_transcripts` -> `resource_transcripts` and `media_summaries` -> `resource_summaries`.

In `ai_repository.py`:
- Line 6 (docstring): `media_transcripts, media_summaries` -> `resource_transcripts, resource_summaries`
- Line 46: `client.table("media_transcripts")` -> `client.table("resource_transcripts")`
- Line 63: `client.table("media_transcripts")` -> `client.table("resource_transcripts")`
- Line 90: `client.table("media_summaries")` -> `client.table("resource_summaries")`
- Line 107: `client.table("media_summaries")` -> `client.table("resource_summaries")`

Also update `update_media_ai_status` (line 122-150) to update `resources` table instead of `parsed_media`:
- Line 141: `client.table("parsed_media")` -> `client.table("resources")`

And update `get_videos_needing_transcription` and `get_videos_needing_summary` (line 152-188):
- These query `parsed_media` for AI status columns that no longer exist there.
- They should now query `resources` table instead.
- Line 157: `client.table("parsed_media")` -> `client.table("resources")`
- Line 159: Select columns need updating to match resources columns
- Line 162: `transcript_bool` no longer exists, remove that filter
- Line 176: `client.table("parsed_media")` -> `client.table("resources")`
- Line 181: `summary_bool` no longer exists, remove that filter

**Step 2: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.repositories.ai_repository import AIRepository; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/repositories/ai_repository.py
git commit -m "refactor: update ai_repository to use resource_* tables"
```

---

### Task 7: Backend — Update tags_repository.py table references

**Files:**
- Modify: `backend/app/repositories/tags_repository.py`

**Step 1: Update table name constant and all references**

- Line 14: `MEDIA_TAGS_TABLE = "media_tags"` -> `RESOURCE_TAGS_TABLE = "resource_tags"`
- Line 30: `_get_media_tags_table` -> `_get_resource_tags_table`
- Line 33: `self.MEDIA_TAGS_TABLE` -> `self.RESOURCE_TAGS_TABLE`
- All callers of `_get_media_tags_table` -> `_get_resource_tags_table` (lines 38, 188, 195, 206, 219, 238)
- All `media_tags_table` variable names -> `resource_tags_table`
- Line 184: `"media_id"` key in dict -> `"resource_id"`
- Line 193: method param `media_id` can stay as-is in Python but the dict key must be `"resource_id"`
- Line 197/209/221: `.eq("media_id", ...)` -> `.eq("resource_id", ...)`
- Line 221: `media_tags_table.select("media_id, parsed_media(*)")` -> `resource_tags_table.select("resource_id, resources(*)")`
- Line 227: `r["parsed_media"]` -> `r["resources"]`
- Line 234: `"media_id"` key -> `"resource_id"`
- Line 271/280: `client.table("parsed_media")` and `client.table("media_tags")` in `_get_tag_counts_fallback` -> update to query `resources` and `resource_tags`

Also rename Python method parameters from `media_id` to `resource_id` for clarity:
- `add_tag_to_media` -> `add_tag_to_resource`
- `remove_tag_from_media` -> `remove_tag_from_resource`
- `get_media_tags` -> `get_resource_tags`
- `get_media_by_tag` -> `get_resources_by_tag`
- `bulk_add_tags_to_media` -> `bulk_add_tags_to_resource`

**Step 2: Verify import works**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.repositories.tags_repository import TagsRepository; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/repositories/tags_repository.py
git commit -m "refactor: update tags_repository to use resource_tags table"
```

---

### Task 8: Backend — Update analysis_repository.py table reference

**Files:**
- Modify: `backend/app/repositories/analysis_repository.py`

**Step 1: Update table name**

- Line 14: `TABLE_NAME = "media_analysis"` -> `TABLE_NAME = "resource_analysis"`

Also update any `media_id` column references to `resource_id`:
- Search for `.eq("media_id"` and change to `.eq("resource_id"`
- Method parameters named `media_id` should be renamed to `resource_id`

**Step 2: Verify import works**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.repositories.analysis_repository import AnalysisRepository; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/repositories/analysis_repository.py
git commit -m "refactor: update analysis_repository to use resource_analysis table"
```

---

### Task 9: Backend — Update search_service.py table references

**Files:**
- Modify: `backend/app/services/search_service.py`

**Step 1: Update all media_* table references**

- Line 167/170/189/217 (comments): `media_analysis` -> `resource_analysis`
- Line 191: `client.table("media_analysis")` -> `client.table("resource_analysis")`
  - Also change `.eq("media_id")` to `.eq("resource_id")` if present
- Line 224: `client.table("media_tags")` -> `client.table("resource_tags")`
  - Also change `.in_("media_id")` to `.in_("resource_id")` if present
- Line 275: `client.table("media_tags")` -> `client.table("resource_tags")`
  - Same column name update

**Step 2: Verify import works**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.services.search_service import SearchService; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/services/search_service.py
git commit -m "refactor: update search_service to use resource_* tables"
```

---

### Task 10: Backend — Update collections_service.py and remaining files

**Files:**
- Modify: `backend/app/services/collections_service.py`
- Modify: `backend/app/api/analysis_router.py`
- Modify: `backend/app/api/tags_router.py`
- Modify: `backend/app/tasks/analysis_tasks.py`
- Modify: `backend/app/repositories/media_repository.py`

**Step 1: Update collections_service.py**

- Lines 196/208/221: `client.table("media_tags")` -> `client.table("resource_tags")`
- All `.eq("media_id")` -> `.eq("resource_id")` in those queries

**Step 2: Update analysis_router.py**

- Line 82: `supabase.table("media_analysis")` -> `supabase.table("resource_analysis")`
- Also update `media_id` column references to `resource_id`
- Lines 126/305: Method names `get_media_analysis`/`delete_media_analysis` can keep their route names but update internal table references

**Step 3: Update tags_router.py**

- Line 201/207: The route calls `repo.get_media_tags()` which was renamed to `get_resource_tags()` in Task 7
- Update the call: `repo.get_media_tags(media_id)` -> `repo.get_resource_tags(media_id)`

**Step 4: Update analysis_tasks.py**

- Lines 88/227: `tags_repo.get_media_tags(media_id)` -> `tags_repo.get_resource_tags(media_id)`

**Step 5: Update media_repository.py**

- Line 94: `client.table("parsed_media_with_tags")` -> `client.table("parsed_media")` (view was dropped, use base table; tags now via resource_tags)
- Line 369: Same change
- Remove the `VIEW_NAME` reference if it exists

**Step 6: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run uvicorn app.main:app --port 8081 &; sleep 3; curl -s http://localhost:8081/docs | head -5; kill %1`
Expected: FastAPI docs HTML, no import errors.

**Step 7: Commit**

```bash
git add backend/app/services/collections_service.py backend/app/api/analysis_router.py backend/app/api/tags_router.py backend/app/tasks/analysis_tasks.py backend/app/repositories/media_repository.py
git commit -m "refactor: update remaining backend files to use resource_* tables"
```

---

### Task 11: Frontend — Update dataService.ts (drop parsed_media_with_tags view)

**Files:**
- Modify: `frontend/services/dataService.ts`

**Step 1: Update view reference**

The `parsed_media_with_tags` view was dropped. All queries using it should now use `parsed_media` directly.

- Line 5: `const VIEW_NAME = 'parsed_media_with_tags';` -> `const VIEW_NAME = 'parsed_media';`

Note: The view previously joined tags into an array. Now tags are on `resource_tags`, not `media_tags`. The frontend already has a separate tag system via `ResourcesView`, so the old view-based tag embedding is no longer needed for the main flows. The `tags` field in query results will simply be absent, which is fine since the Resources view fetches tags separately via the API.

**Step 2: Verify frontend builds**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds.

**Step 3: Commit**

```bash
git add frontend/services/dataService.ts
git commit -m "refactor: update dataService to use parsed_media directly (view dropped)"
```

---

### Task 12: Frontend — Update useLibrary.ts realtime subscriptions

**Files:**
- Modify: `frontend/hooks/useLibrary.ts`

**Step 1: Update media_tags realtime subscription**

- Line 375: `channel('media_tags_realtime')` -> `channel('resource_tags_realtime')`
- Line 378: `table: 'media_tags'` -> `table: 'resource_tags'`
- Line 381: `media_id` -> `resource_id` in payload destructuring
- Line 387: `.from('parsed_media_with_tags')` -> `.from('parsed_media')` (or remove the whole re-fetch if tags are handled separately)

**Step 2: Update media_collections realtime subscription (if exists)**

- Lines 335/338: `'media_collections_realtime'` and `table: 'media_collections'` — this table was dropped. Remove or comment out this entire subscription block since collections are now handled via `resource_items`.

**Step 3: Update media_collections queries**

- Lines 187/227/262: `.from('media_collections')` queries — these should be changed to query `resource_items` instead, or removed if the corresponding feature is unused.

**Step 4: Verify frontend builds**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds.

**Step 5: Commit**

```bash
git add frontend/hooks/useLibrary.ts
git commit -m "refactor: update useLibrary realtime subscriptions for unified tables"
```

---

### Task 13: Frontend — Update collectionService.ts

**Files:**
- Modify: `frontend/services/collectionService.ts`

**Step 1: Replace media_collections references**

The `media_collections` table was dropped. The `collectionService.ts` uses it extensively. Two options:
- (a) Replace with `resource_items` queries
- (b) Keep collections system via `collections` table but remove `media_collections` join

Since `collections` table still exists and uses `resource_items` for the link, update:

- Line 20: `'*, media_collections(count)'` -> `'*'` (remove count sub-query, or use a different count approach)
- Line 39: `.from('media_collections')` -> This needs reworking. Since there's no more `media_collections`, the video-in-collection feature needs to go through `resource_items` instead.

Given the complexity, the simplest approach: since this is legacy code and the new Resources view doesn't use collections (it uses folders), we should mark this service as deprecated and ensure it doesn't crash. Replace `media_collections` references with safe no-ops or redirect to resource_items.

**Step 2: Verify frontend builds**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds.

**Step 3: Commit**

```bash
git add frontend/services/collectionService.ts
git commit -m "refactor: update collectionService for dropped media_collections table"
```

---

### Task 14: Full Integration Verification

**Step 1: Run full frontend build**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds with no errors.

**Step 2: Start backend and verify no import errors**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && timeout 10 uv run uvicorn app.main:app --port 8081 2>&1 || true`
Expected: `Uvicorn running on http://0.0.0.0:8081`, no import errors.

**Step 3: Verify database integrity**

Run:
```bash
psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -c "
SELECT 'resource_summaries' AS tbl, count(*) FROM resource_summaries
UNION ALL SELECT 'resource_transcripts', count(*) FROM resource_transcripts
UNION ALL SELECT 'resource_analysis', count(*) FROM resource_analysis
UNION ALL SELECT 'resource_access_logs', count(*) FROM resource_access_logs
UNION ALL SELECT 'resource_tags', count(*) FROM resource_tags
UNION ALL SELECT 'resource_items', count(*) FROM resource_items
UNION ALL SELECT 'resources', count(*) FROM resources
UNION ALL SELECT 'parsed_media', count(*) FROM parsed_media;
"
```
Expected: All tables return counts without error.

**Step 4: Verify no old table references remain in code**

Run: `grep -rn "media_tags\|media_summaries\|media_transcripts\|media_analysis\|media_access_logs\|media_collections\|parsed_media_with_tags" backend/app/ frontend/ --include="*.py" --include="*.ts" --include="*.tsx" | grep -v node_modules | grep -v __pycache__`
Expected: No results (or only harmless comments/type definitions).
