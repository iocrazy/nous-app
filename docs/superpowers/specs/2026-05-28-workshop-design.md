# Workshop / 工坊 — Task-Scoped Working Assets Design Spec

**Depends on:** `2026-05-28-id-unification-design.md` (Spec 1 must ship first so `task_id` is uniformly `BIGINT`).

**Replaces:** the legacy `folders.name='temp'` convention (introduced in sub-plan 1, refined in sub-plans 2 + 3).

**Status:** Approved 2026-05-28 (brainstorming complete; reviewer = me, per user delegation).

---

## Goal

Provide a first-class data model for assets that exist in the context of a task (an issue / to-do item or a chat session) rather than in a user's permanent workspace:

- AI-produced outputs (chat replies' generated images, transcripts, storyboard frames, etc.)
- User-uploaded files in task context (chat paste/drag, issue comment attachments)
- Process-produced intermediates (DBOS workflow step outputs, batch operation results)

These assets must:

- Be **bound to their parent task** (lifecycle cascades — parent deleted ⇒ asset deleted).
- Be **distinguishable from permanent library resources** (separate sidebar entry, separate API surface, separate clean-up logic).
- Be **promotable** to permanent storage by the user (`Workshop → Library` action), preserving content + tags.
- Stay **invisible by default** in cross-task tooling (the @-reference picker in S4 should not surface someone else's chat uploads as suggestions).

The user-facing name is **"Workshop"** / **"工坊"** — explicitly chosen during brainstorm to convey *work in progress*, not *temporary throwaway*.

## Why Not Just Use `folders.name='temp'`?

The current convention fails on four dimensions audited during the 2026-05-28 brainstorm:

1. **String-match fragility:** any folder a user names `temp` gets swept by the sweeper.
2. **No parent linkage:** a temp file knows its scope but not which chat session / issue produced it. Cascade-on-task-delete is impossible.
3. **No N:1 sharing:** the same generated frame can't be referenced by two tasks (it's *in* one folder).
4. **UX confusion:** users see "temp" alongside their real folders in `My Uploads`, can't tell what it is, can't predict when it'll disappear.

## Schema

### `workshop_items` table

```sql
CREATE TABLE public.workshop_items (
    id            BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),

    -- Parent task (the closest "task" containing this asset)
    task_type     TEXT NOT NULL
                  CHECK (task_type IN ('issue', 'chat_session')),
    task_id       BIGINT NOT NULL,         -- references issues.id OR ai_sessions.id

    -- File reference (N:1 — many workshop_items can point to one resource)
    resource_id   BIGINT NOT NULL
                  REFERENCES public.resources(id) ON DELETE CASCADE,

    -- Derived scopes (denormalised for index efficiency; kept consistent via app code)
    team_id       BIGINT NOT NULL
                  REFERENCES public.teams(id) ON DELETE CASCADE,
    project_id    BIGINT,                  -- nullable: task may be standalone
                  -- FK omitted intentionally — issues may exist without project

    -- Provenance + lifecycle
    created_by    UUID NOT NULL REFERENCES auth.users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_trashed    BOOLEAN NOT NULL DEFAULT FALSE,
    trashed_at    TIMESTAMPTZ
);

CREATE INDEX idx_workshop_task
    ON public.workshop_items (task_type, task_id)
    WHERE is_trashed = false;

CREATE INDEX idx_workshop_project
    ON public.workshop_items (project_id)
    WHERE project_id IS NOT NULL AND is_trashed = false;

CREATE INDEX idx_workshop_team
    ON public.workshop_items (team_id)
    WHERE is_trashed = false;

CREATE INDEX idx_workshop_resource
    ON public.workshop_items (resource_id)
    WHERE is_trashed = false;
```

### Rationale per column

- `task_type` enum (just 2 values): tightly scoped — covering more requires explicit migration. Plan-stage adds value `'project'` if standalone project-scoped assets emerge as a need.
- `task_id BIGINT`: after Spec 1, both `issues.id` and `ai_sessions.id` are Snowflake → uniform type.
- `resource_id BIGINT NOT NULL`: every workshop item points to a real `resources` row holding the file metadata (filename / mime / file_path). Files never live without a resources row.
- `team_id NOT NULL`: every task lives in a team (after Spec 1, personal = special team) — never NULL. Stored for index-driven team-wide queries without join.
- `project_id NULLABLE`: standalone tasks (issue without project, chat session without project) are explicitly allowed per brainstorm (constraint user added 2026-05-28).
- `is_trashed + trashed_at`: soft-delete pattern matching the rest of the schema. Sweeper writes `is_trashed=true`; existing `cleanup_trashed_resources_workflow` handles physical purge.

### What is intentionally NOT in this table

- No `folder_id` — workshop items are flat per task. Folders are for permanent resources.
- No `scope_type` / `scope_id` — workshop scope is the parent task, not a `personal`/`team` enum.
- No `library_id` — libraries are a team-level container for permanent assets, not for task assets.
- No `kind` enum (image vs doc) — that lives on the underlying `resources` row.

## Lifecycle

### Creation paths

Three entry points, all producing one `workshop_items` row per new asset:

#### 1. User uploads in chat composer (replaces current `save_chat_temp_upload`)

```
POST /api/v1/ai-library/sessions/{session_id}/uploads
        body: multipart/form-data file=...

  ↓ backend/app/services/library/workshop_upload.py::save_chat_session_upload
  1. Resolve session → team_id, project_id (derived)
  2. Save file to disk under teams/{team_id}/uploads/{resource_id}/v1/...
  3. INSERT resources row (filename, mime, file_path, ...)
  4. INSERT workshop_items row (task_type='chat_session', task_id=session_id, ...)
```

This **replaces** the current `app/services/library/chat_upload.py::save_chat_temp_upload`. The legacy function is deleted (no caller after PR-B).

#### 2. AI agent produces a file (via tool call)

A new helper called from agent tools / DBOS workflows:

```python
async def attach_to_task(
    *,
    task_type: Literal['issue', 'chat_session'],
    task_id: int,
    resource_id: int,
    created_by: str,
) -> int:
    """Create a workshop_items row attaching an existing resource to a task.

    Derives team_id, project_id from the task. Returns the new workshop_items.id.
    """
```

Call sites (plan-stage will enumerate; brainstorm-time confirmed list):

- `app/workflows/storyboard.py` — when a storyboard frame is generated for a chat session
- `app/services/ai/tools/*` — any tool that writes a file
- `app/workflows/ai_transcription.py` — when a transcription file lands

#### 3. Issue comment attachment (user uploads in an issue reply)

`POST /api/v1/issues/{issue_id}/messages` body already supports `attachments[]`. When an attachment has `kind='upload'` (a fresh file), instead of writing to a per-user temp folder, write to workshop bound to the issue:

- Upload pipeline same as path #1 but `task_type='issue'`, `task_id=issue_id`.

### Promote (workshop → library)

```
POST /api/v1/workshop/{workshop_item_id}/promote
        body: {target_folder_id?: BIGINT, target_library_id?: BIGINT}
```

```python
async def promote_workshop_item(workshop_item_id: int, *, target_folder_id, target_library_id):
    item = await repo.get_workshop_item(workshop_item_id)
    if not item:
        raise NotFound

    # Default destination: personal team's implicit "promoted" root (no folder)
    library = target_library_id or default_library_for_team(item.team_id)

    await repo.create_resource_item({
        'resource_id':   item.resource_id,
        'scope_type':    'team',
        'scope_id':      str(item.team_id),
        'library_id':    library,
        'folder_id':     target_folder_id,
    })

    # Hard-delete workshop_items row (not soft-delete: this is the intentional
    # graduate-to-permanent transition, not a trash event)
    await repo.delete_workshop_item(workshop_item_id)
```

Resource row is unchanged. After promote, the same `resources` row is now visible via the standard library `resource_items` join path; the workshop reference is gone.

Frontend `tempTtlService.promoteResource(resourceId, options)` (sub-plan 2) is kept as an API alias for one release cycle (`POST /resources/{id}/promote-temp` → routes to new endpoint by resource_id lookup), then removed.

### Sweeper (TTL cleanup)

The existing `app/workflows/temp_resource_sweeper.py` is renamed to `workshop_sweeper.py`. Its logic changes:

- **Old query:** `SELECT folders WHERE name='temp' AND scope=...` then list resources via `resource_items.folder_id`.
- **New query:** `SELECT workshop_items WHERE created_at + ttl_days < now() AND is_trashed=false`.

TTL setting source is unchanged — `teams.settings_json -> 'workshop_ttl_days'` (the JSON key is renamed from `chat_temp_ttl_days` in mig-stage to align with the new naming).

Sweeper action: `UPDATE workshop_items SET is_trashed=true, trashed_at=now()`. Physical resource cleanup happens via the next-tier sweeper (see § Resource cleanup).

Additionally, sweeper checks for **orphaned workshop items** (parent task deleted but workshop_items row survived because we don't have a real FK to issues/ai_sessions due to polymorphism):

```sql
-- Cascade-cleanup of workshop items whose parent task no longer exists
UPDATE workshop_items SET is_trashed=true, trashed_at=now()
 WHERE is_trashed = false
   AND (
     (task_type = 'issue' AND task_id NOT IN (SELECT id FROM issues))
     OR
     (task_type = 'chat_session' AND task_id NOT IN (SELECT id FROM ai_sessions))
   );
```

This handles the case where issues/ai_sessions are deleted (cascading FK on team_id is fine; the polymorphic task_id is not enforced).

Runs daily at the same cron tick as today's temp_resource_sweeper.

### Resource cleanup (N:1 reference counting)

When the last workshop_items row referencing a resource is removed (promoted, trashed, or deleted), the resource row + file may also be removed IF no `resource_items` row references it either.

This is a separate sweeper step (not on the deletion hot path):

```sql
DELETE FROM resources
 WHERE id IN (
     SELECT r.id FROM resources r
      LEFT JOIN workshop_items wi ON wi.resource_id = r.id AND wi.is_trashed = false
      LEFT JOIN resource_items ri ON ri.resource_id = r.id AND ri.is_trashed = false
     WHERE wi.id IS NULL AND ri.id IS NULL
       AND r.created_at < now() - INTERVAL '1 day'  -- safety: don't delete brand-new resources
 );
```

(File-on-disk removal piggybacks on existing `cleanup_trashed_resources_workflow`.)

## API Surface

Pattern: a new `/api/v1/workshop` namespace. Mirrors `/api/v1/resources` minus folder/library notions.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/ai-library/sessions/{sid}/uploads` | User upload in chat (existing pattern moved over) |
| `POST` | `/api/v1/issues/{iid}/uploads` | User upload in issue reply (or via existing issue messages endpoint) |
| `GET` | `/api/v1/workshop?task_type=&task_id=` | List one task's workshop items |
| `GET` | `/api/v1/workshop?project_id=` | List cross-task workshop items in a project |
| `GET` | `/api/v1/workshop?team_id=&limit=&cursor=` | List a team's recent workshop items (sidebar's "Workshop" view) |
| `GET` | `/api/v1/workshop/{id}` | Single workshop item detail (joins resources for file metadata) |
| `POST` | `/api/v1/workshop/{id}/promote` | Promote to library (params: target_folder_id?, target_library_id?) |
| `DELETE` | `/api/v1/workshop/{id}` | Soft-delete (sets is_trashed=true) |

Authz: all endpoints check `team_members.user_id = auth.uid()` against `workshop_items.team_id`. After Spec 1, this is the uniform authz check.

## Frontend Integration

### Sidebar item

Replaces sub-plan 4's `Temp` sidebar entry (which used the folder-name convention). The new entry:

```
Resources
├─ Shared
├─ Recycle Bin
├─ My Uploads          ← permanent (resource_items + folders + libraries)
├─ Workshop / 工坊      ← NEW: workshop_items
├─ Smart Folders
```

i18n keys: `resources.workshop` / `resources.workshopEmpty` (en + zh). The temp / tempEmpty keys from sub-plan 4 are renamed.

Icon: same `Clock` from lucide (already chosen during sub-plan 4). Visual continuity since users already know the entry.

### Workshop content view

`/resources/workshop` route. Renders `<WorkshopGrid>`:

- Default: list all workshop items for the current scope (personal team), sorted by `created_at DESC`
- Group by parent task: small section headers (`Chat: <session title>` / `Issue: <issue title>`)
- Each item card shows:
  - Standard `<ResourceCard>` (file preview + name)
  - Subtle parent task chip (`📎 Chat: drafting storyboard`)
  - Action menu: `Promote → ...` / `Open in task` / `Delete`

Promote UX: opens a small modal `<PromoteWorkshopModal>` letting user pick target library (defaults to personal) + folder (optional, autocomplete from existing folders in target).

### @-reference picker (S4) compatibility

Sub-plan 4's `useResourceSearch` and `GET /resources/search` query `resource_items`, not `workshop_items`. By default workshop items remain invisible to the @-picker — exactly the brainstorm-confirmed UX ("your chat uploads don't appear in your colleague's @-picker as candidates").

Add an optional toggle on the picker: `[ ] Include workshop` — when enabled, the picker also UNIONs `workshop_items` filtered by `team_id IN user's teams`. Each row in workshop subset shows a small "Workshop" badge.

## History Snapshot Compatibility (sub-plan 4 / S4)

The S4 chat message `attachments[]` JSONB already snapshots `{kind:'resource_ref', resource_id, name, mime, scope}` for every @-reference. After workshop_items rename / delete / promote, the snapshot remains valid for **display** (the chip still renders the name). For the agent's `ResourceFetch(id)` tool call:

- If the resource_id still exists in `resources` and the user can still access it (via either `resource_items` or `workshop_items`) → normal fetch
- If the resource was workshop-only and the workshop_items row has been trashed → ResourceFetch returns `{error: "resource graduated to library or removed"}` (handled by S4's existing error path)

No new code; S4's existing failure path covers this.

## Data Migration from Legacy `folders.name='temp'`

```sql
-- For each existing temp folder row, find its scope and try to derive
-- parent task context. If we can't, default to promoting to the user's
-- personal team root (no folder).

WITH temp_folders AS (
  SELECT f.id, f.scope_type, f.scope_id, f.created_by
    FROM folders f
   WHERE f.name = 'temp' AND f.is_trashed = false
)
-- For each resource in a temp folder, look up the most recent ai_session
-- that the creator authored (best-effort heuristic) and attach. Resources
-- without any plausible session are promoted to personal-team root.
-- Pseudocode — exact SQL produced at plan stage with care for performance.
```

The migration is **best-effort**, not perfect. Users will lose precise "this file came from chat session X" linkage for files uploaded before this change. Brainstorm-confirmed acceptable trade-off because:

- Most temp files are < 24h old at migration time (TTL has already swept the rest)
- The alternative (manual user-by-user migration) is impractical
- New uploads after the migration have proper parent linkage

After migration completes, the legacy temp folders are physically deleted (folders + their resource_items rows; resources rows are kept and now visible via workshop_items).

## Deployment Sequence

(Assumes Spec 1 has fully shipped: ai_sessions.id is BIGINT, teams.kind exists, all personal-scope rows already remapped to personal team snowflakes.)

1. **PR-A (zero downtime):** `workshop_items` schema + repository + service code. No callers yet. New API endpoints registered but feature-flagged off.
2. **PR-B (zero downtime):** `chat_upload.py::save_chat_temp_upload` → `workshop_upload.py::save_chat_session_upload`. Replace existing chat upload call site. Feature flag flipped on for chat uploads.
3. **PR-C (zero downtime):** AI tools + DBOS workflows that produce files start calling `attach_to_task`. New workshop items begin accumulating.
4. **PR-D (operator window):** Sweeper switch (`temp_resource_sweeper.py` → `workshop_sweeper.py`) + data migration from legacy temp folders. Operator runs the one-shot migration script.
5. **PR-E (zero downtime):** Frontend changes — sidebar rename, new `/workshop` route, `<WorkshopGrid>`, Promote UI. The S4 sidebar's `Temp` becomes `Workshop`.
6. **PR-F (zero downtime):** Remove dead code — old `chat_upload.py::save_chat_temp_upload`, legacy temp folder constants, old `tempTtlService.promoteResource` API alias (if grace period has passed).

## Testing Plan

### Per-PR

- Unit (backend): `test_workshop_items_repo.py`, `test_workshop_upload_service.py`, `test_workshop_sweeper.py`, `test_promote.py`
- Unit (frontend): `WorkshopGrid.test.tsx`, `PromoteWorkshopModal.test.tsx`
- Integration: end-to-end chat upload → workshop_items row created → sidebar shows it → promote works → row deleted, resource_items row created.

### Acceptance tests (cross-PR)

- Brand-new user signs up → uploads in chat → file appears in Workshop sidebar → user promotes to library → file appears in My Uploads → user deletes from My Uploads → file goes to Recycle Bin
- Two team members in the same team → user A uploads in chat session A → user B does NOT see it in their @-picker (workshop items are private by default per task ownership)
- User has 5-day-old workshop item, TTL is 3 days → sweeper soft-deletes it → user checks Recycle Bin → can restore (current trash behavior)
- Delete an ai_session → orphan-cleanup sweeper soft-deletes the workshop items → eventually disappears

### Rollback per PR

- PR-A: drop table (no data loss; no users yet)
- PR-B/C: revert code; new uploads go back to old chat_temp path (data continuity is fine)
- PR-D: switch sweeper back to old query; legacy temp folder migration is one-way (rollback means accepting some workshop items are orphaned but invisible)
- PR-E/F: revert frontend; backend behavior unaffected

## Out of Scope

- Workshop folders / sub-organization within a task — workshop is flat per task by design.
- Workshop versions — only one file per workshop item; for versioning, promote to library first then use existing `resource_versions` mechanism.
- Workshop sharing across teams — workshop items are team-private; cross-team share happens through promote-to-library.
- Smart Folder rules matching workshop items — workshop items don't participate in smart folder queries.
- Mobile app — backend changes ride along, no mobile-app-specific work.
- IssueReplyBox needs to consume the new workshop upload API — the actual frontend change is small (~10 lines), but included in PR-E with the rest of the frontend integration rather than as a separate sub-PR.
- Workshop "favorites" / "pinning" — out of scope; user can promote.

## Spec Self-Review (2026-05-28)

1. **Placeholder scan:** Two pseudocode blocks ("Pseudocode — exact SQL produced at plan stage" in § Data Migration) are intentional — the migration query depends on heuristics that the plan-stage will produce against real prod data shape. Acceptable per writing-plans skill's allowance for plan-stage refinement of operator scripts.
2. **Internal consistency:**
   - `task_type` enum is `('issue', 'chat_session')` everywhere (schema + sweeper + API).
   - Storage path always `teams/{team_id}/...` (depends on Spec 1).
   - `workshop_ttl_days` JSON key consistent across § Sweeper, § Schema rationale.
   - `team_id NOT NULL` consistent with Spec 1's personal=team abstraction.
3. **Scope check:** Six-PR sequence with clear feature flags. Each PR is independently revertable. Total surface ~ medium — comparable to the S4 PR cluster.
4. **Ambiguity scan:**
   - "Promote default destination" pinned to personal team's library root (no folder unless user picks one).
   - "Cascade cleanup of orphaned workshop items" pinned to sweeper (not delete trigger) — explicit because hot-path performance matters.
   - "Workshop items invisible to @-picker by default" pinned with explicit toggle.
   - Resource ref-counting cleanup deferred to a separate sweeper step (24h cooldown) to avoid hot-path slowdowns.
5. **Issues found + fixed inline:**
   - First draft had `task_id` as `BIGINT` but didn't note the dependency on Spec 1 — added explicit "Depends on" line at top.
   - First draft was silent on how AI agents call into `attach_to_task` — added § Creation paths #2 with named helper file.
   - First draft did not specify what happens when the underlying resource has multiple workshop refs and one is promoted — clarified via § Resource cleanup ref-counting section.
   - First draft mentioned but did not pin the @-picker compatibility behavior — added explicit § History Snapshot Compatibility section with concrete error message for ResourceFetch failure case.
   - First draft did not include orphan-cleanup for workshop_items whose polymorphic task_id parent no longer exists — added explicit SQL in § Sweeper.

No further issues. Ready for plan-stage. The Spec 1 dependency is explicit at top — writing-plans for Spec 2 should run AFTER Spec 1's plan is final (so file paths and migration ordering are coherent).
