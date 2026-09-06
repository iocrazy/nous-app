# Inbox & Library Small Tickets (unique promote index · system-folder move guard · inbox audio player) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three parked tickets from the asset-library P6 batch: the interim advisory lock on generated-media promotion gets its real guard (a partial unique index), batch Move can no longer silently relocate a system folder, and audio generations become playable in the inbox lightbox.

**Architecture:** Two additive migrations (456 unique index, 457 folders trigger) mirrored in the ORM; one frontend hook fix with a pure helper; one backend route widening (`/stream` serves audio rows) plus a lightbox player branch. No new tables, no data migration.

**Tech Stack:** FastAPI + SQLAlchemy async ORM; React 19 + TypeScript + Vitest; Postgres migrations under `supabase/migrations/`.

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` §7 (Generated inbox) and the P6 ledger rulings (`docs/superpowers/plans/2026-09-04-asset-library-p6-cleanup.md`). Facts established 2026-09-06 on prod: `generated_media` has 87 rows, 29 promoted, **0 duplicate `promoted_resource_id`**; `folders` has no trigger guarding `is_system` (only `update_folders_updated_at`), and `moveFolder` in `frontend/services/resourceService.ts` writes `folders` through Supabase directly, so the backend's 409 `system_folder` never applies to it; `generated_media` has no audio rows yet (image 86, video 1).

## Global Constraints

- No new `text()` raw SQL in application code (`Index(..., postgresql_where=text(...))` inside `__table_args__` is the established exception — see `models/generated_media.py`).
- Migrations are plain SQL, idempotent (`IF NOT EXISTS` / `DROP … IF EXISTS`), no `CREATE INDEX CONCURRENTLY` (the runner feeds migrations to one psql session), no `SET ROLE`.
- UI copy through i18n keys, English Title Case values in `frontend/public/locales/en.json`, Chinese in `zh.json`. No emoji; lucide icons.
- Every user-triggered path returns a typed result and is echoed — `catch { /* ignore */ }` is the defect these tickets exist to remove.
- Mocked HTTP bodies use real wire shapes. Never `git add -A`. Commit per task on the current branch.
- Latest migration on master is `455_resources_prompt_origin.sql`; this plan takes 456 and 457. Re-check with `git ls-tree --name-only origin/master:supabase/migrations | tail -1` before committing; renumber if taken.

---

### Task 1: Partial unique index on `generated_media.promoted_resource_id` (mig 456)

**Files:**
- Create: `supabase/migrations/456_generated_media_promoted_unique.sql`
- Modify: `backend/app/models/generated_media.py` (`__table_args__`)
- Modify: `backend/app/repositories/generated_media_repository.py` (docstring of `insert_registered_resource`, ~lines 600–616)
- Test: `backend/tests/migrations/test_456_generated_media_promoted_unique.py`

**Interfaces:**
- Migration body (verbatim):
  ```sql
  -- 456_generated_media_promoted_unique.sql
  --
  -- One inbox row per promoted resource. insert_registered_resource's
  -- find-or-mint is user-triggered and concurrently reachable; the advisory
  -- lock it takes serialises callers so the loser FINDS the winner's row, and
  -- this index is the guarantee behind it: a second row for the same resource
  -- can no longer exist even if a future caller forgets the lock. Checked on
  -- prod 2026-09-06 before writing: 0 duplicates among 29 promoted rows.
  -- idx_genmedia_promoted (plain btree) is subsumed and dropped.
  CREATE UNIQUE INDEX IF NOT EXISTS uq_genmedia_promoted_resource
    ON public.generated_media (promoted_resource_id)
    WHERE promoted_resource_id IS NOT NULL;
  DROP INDEX IF EXISTS public.idx_genmedia_promoted;
  ```
- Model: add to `__table_args__` beside `idx_genmedia_source_asset`:
  ```python
  Index(
      "uq_genmedia_promoted_resource",
      "promoted_resource_id",
      unique=True,
      postgresql_where=text("promoted_resource_id IS NOT NULL"),
  ),
  ```
- Repository docstring: replace the paragraph beginning "A partial unique index on ``promoted_resource_id`` would be the real fix, but it cannot be added blind…" with: the index exists since mig 456; the advisory lock is kept because it turns the race into "loser finds the row" instead of "loser gets IntegrityError", which is the behaviour the dialog wants.
- Test (pattern: `backend/tests/migrations/test_445_asset_library_core.py`): read the migration file, assert it contains `CREATE UNIQUE INDEX IF NOT EXISTS uq_genmedia_promoted_resource` and `WHERE promoted_resource_id IS NOT NULL` and `DROP INDEX IF EXISTS public.idx_genmedia_promoted`; and assert the ORM `Index` named `uq_genmedia_promoted_resource` on `GeneratedMedia.__table__` is `unique=True` with that `postgresql_where` text — so the two cannot drift.

- [ ] Step 1: write the test, run `cd backend && uv run pytest tests/migrations/test_456_generated_media_promoted_unique.py -q` → FAIL.
- [ ] Step 2: add the migration, the model index, the docstring rewrite → PASS. Also run `uv run pytest tests/repositories -q -k generated` and `black --check` / `isort --check-only` / `flake8` on the touched Python files.
- [ ] Step 3: commit `feat(generated): partial unique index on promoted_resource_id (mig 456) — the advisory lock's guarantee`.

### Task 2: Batch Move — skip system folders, say so, and never swallow the failure (+ DB guard, mig 457)

**Files:**
- Create: `supabase/migrations/457_folders_refuse_system_mutation.sql`
- Create: `frontend/hooks/moveBatch.ts` (pure helpers)
- Modify: `frontend/hooks/useResourceOperations.ts` (`handleFolderPickerConfirm`, ~lines 144–171)
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`
- Test: `frontend/hooks/moveBatch.test.ts`, `backend/tests/migrations/test_457_folders_refuse_system_mutation.py`

**Interfaces:**
- Migration (verbatim shape; trigger function + trigger, idempotent):
  ```sql
  -- 457_folders_refuse_system_mutation.sql
  --
  -- mig 441 put the system-folder guard in the API layer (409 system_folder on
  -- rename / move / trash / delete). The frontend's folder move writes
  -- `folders` through PostgREST directly and never meets that guard, so batch
  -- Move could relocate Chat Uploads or the cover-template library with no
  -- error anywhere. The guard now lives where every writer passes.
  CREATE OR REPLACE FUNCTION public.folders_refuse_system_mutation()
  RETURNS trigger LANGUAGE plpgsql AS $$
  BEGIN
    IF OLD.is_system THEN
      IF NEW.parent_id IS DISTINCT FROM OLD.parent_id
         OR NEW.library_id IS DISTINCT FROM OLD.library_id
         OR NEW.name IS DISTINCT FROM OLD.name
         OR NEW.is_trashed IS DISTINCT FROM OLD.is_trashed THEN
        RAISE EXCEPTION 'system_folder' USING
          ERRCODE = 'check_violation',
          DETAIL = format('folder %s (%s) is a system folder and cannot be renamed, moved or trashed', OLD.id, OLD.system_key),
          HINT = 'system_folder';
      END IF;
    END IF;
    RETURN NEW;
  END $$;
  DROP TRIGGER IF EXISTS trg_folders_refuse_system_mutation ON public.folders;
  CREATE TRIGGER trg_folders_refuse_system_mutation
    BEFORE UPDATE ON public.folders
    FOR EACH ROW EXECUTE FUNCTION public.folders_refuse_system_mutation();
  ```
  Deleting a system folder row is left to the API layer's 409 (a BEFORE DELETE guard would also block the legitimate scope-teardown paths; out of scope).
- `frontend/hooks/moveBatch.ts`:
  ```ts
  import type { Folder } from '../types';
  /** Split a move batch into what the server would accept and what it would
   *  refuse with 409 system_folder (mig 441 API guard, mig 457 DB guard). */
  export function partitionMovableFolders(folders: readonly Folder[]): { movable: Folder[]; locked: Folder[] };
  /** Typed reason for a failed move, from whatever the transport threw:
   *  `ApiError` with code system_folder, or a PostgREST error whose message /
   *  hint carries 'system_folder' (mig 457 raises with that HINT). */
  export type MoveFailureReason = 'system_folder' | 'unknown';
  export function describeMoveFailure(err: unknown): MoveFailureReason;
  ```
- `handleFolderPickerConfirm` (move branch): `const { movable, locked } = partitionMovableFolders(operationTargetFolders)`; if `locked.length > 0` toast `t('resources.systemFolderLocked', …)` (error) BEFORE moving anything; move only `movable`; on catch → `describeMoveFailure(err)`: `'system_folder'` → the same `systemFolderLocked` toast; `'unknown'` → `t('resources.moveFailed', 'Move failed — nothing was changed for the remaining items')` (error) and `console.error('[useResourceOperations] move failed:', err)`. Reloads (`loadFolders`, `loadChildFolders`, `reloadResources`) move into a `finally` so a partial batch is reflected. Success toast counts `movable.length + operationTargetItems.length`. The copy branch keeps its behaviour but loses the silent catch the same way (toast `resources.copyFailed` = "Copy failed"). New keys: `resources.moveFailed` / zh "移动失败 — 其余项未改动", `resources.copyFailed` / zh "复制失败".
- Tests: `moveBatch.test.ts` — partition splits on `is_system`; `describeMoveFailure` recognises `new ApiError('x', 409, { code: 'system_folder' })`, a PostgREST-shaped `{ code: '23514', message: 'system_folder', hint: 'system_folder' }`, and returns `'unknown'` for `new Error('boom')`. Backend test asserts the migration text contains `BEFORE UPDATE ON public.folders`, `OLD.is_system`, `HINT = 'system_folder'`.

- [ ] Step 1: tests first (both) → FAIL. Step 2: implement. Step 3: `cd frontend && npx vitest run hooks && npx tsc --noEmit -p tsconfig.json` (no new errors in touched files) and `npx eslint` on touched files; backend test file green. Step 4: commit `fix(resources): batch Move refuses system folders out loud, and no failure is swallowed (mig 457 DB guard)`.

### Task 3: Inbox audio — `/stream` serves audio rows; the lightbox gets a player

**Files:**
- Modify: `backend/app/api/generated_media_router.py` (`get_generation_stream`, ~line 146)
- Modify: `frontend/components/resources/generated/GeneratedView.tsx` (`srcFor` ~line 1145; pass `titleFor`)
- Modify: `frontend/components/resources/assets/sheet/PinLightbox.tsx` (audio branch; new optional `titleFor` prop)
- Test: `backend/tests/api/test_generated_media_stream.py` (nearest existing test for this route — extend it), `frontend/components/resources/assets/sheet/PinLightbox.test.tsx` (or the nearest existing lightbox test)

**Interfaces:**
- Backend: `get_generation_stream` accepts `media_kind in ("video", "audio")`; docstring says both and why (same world-readable-by-id posture: a bare `<audio src>` cannot carry a Bearer header either). 404 detail for the rest becomes `"not streamable"`. `_serve_video_stream` already serves the row's own mime — rename is NOT required; if you do rename it to `_serve_media_stream`, update every caller.
- Frontend: `PinLightboxProps.titleFor?: (id: string) => string` (defaults to `slotLabel`). When `kind === 'audio'`: render `<AudioWaveformPlayer src={srcFor(current)} filename={titleFor?.(current) ?? slotLabel} layout="full" />` inside a `div data-testid="pin-lightbox-audio" data-resource-id={current}` with a sensible max width (`w-full max-w-2xl`); `file` keeps the placeholder. `GeneratedView`: `srcFor` uses `generatedMediaStreamUrl(id)` for `video` **and** `audio`, `generatedMediaFileUrl` otherwise; pass `titleFor={(id) => itemsById.get(id)?.title ?? ''}`. Rewrite the "No player here either (ruling B …)" comment in PinLightbox and the card's "an audio player belongs [in the lightbox] when one arrives" comment in `GeneratedCard.tsx` (~line 203) to point at the lightbox player (card tile stays a placeholder that opens the lightbox — a player inside the tile's button would nest interactive elements).
- Tests: backend — an audio row (`media_kind='audio'`, `mime='audio/mpeg'`) is served (200/206 path reached; follow the existing video test's stubbing of `_serve_video_stream`/repository), an image row still 404s. Frontend — `kindFor` returning `'audio'` renders `pin-lightbox-audio` and no `pin-lightbox-placeholder`; `'file'` still renders the placeholder. Mock `AudioWaveformPlayer` (`vi.mock`) to a stub that records its `src`/`filename` props — the real one decodes audio via `fetch` + `AudioContext`, neither of which jsdom has.

- [ ] Step 1: tests first → FAIL. Step 2: implement. Step 3: `cd backend && uv run pytest tests/api -q -k generated_media`; `cd frontend && npx vitest run components/resources`; tsc + eslint on touched files. Step 4: commit `feat(generated): audio generations play in the inbox lightbox — /stream serves audio rows`.
