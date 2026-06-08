# Async Script-AI Endpoints — Design Spec

Date: 2026-06-09
Status: Autonomous build (user pre-approved direction; review at PR)

## Problem

Three script-AI endpoints await an LLM (5–30s) inline before responding:
- `POST /scripts/expand-chapter` — LLM expands a chapter → `update_chapter(content)`.
- `POST /scripts/create-branches` — LLM → N branch chapters via `create_chapter` loop.
- `POST /scripts/convert-to-storyboard` — LLM splits to scenes → `storyboard_nodes` + `script_storyboard_links`.

They block the request the whole LLM duration. The existing `generate_outline` already uses the right async shape (`mgr.create` → `start_workflow_routed` → return `task_id`), with `script_outline_workflow` doing an LLM `@DBOS.step` + a persist `@DBOS.step`.

## Goal

Convert all three to the async pattern: endpoint creates a task, dispatches a DBOS workflow that does the LLM + DB writes, returns `task_id` immediately. The frontend dispatches, watches `task_tracking` for completion, then refetches and updates the canvas.

## Important findings (from research) that shape this

1. **No working frontend async-script template exists.** `generate_outline` is half-migrated: `CreateStoryDialog` still expects an inline `outline` field the backend doesn't return. We must BUILD the completion pattern and also FIX the outline dialog with it.
2. **No `useTask(taskId)` hook.** `TaskManagerContext` already subscribes to `task_tracking` Realtime and exposes `useTaskManager().tasks`. We add a small `useTaskCompletion(taskId, {onComplete, onError})` hook on top of it.
3. **Canvas is a Zustand store, no live sync.** Safest refresh on completion = **refetch the script project and rebuild the canvas from server state** (server is source of truth), not delta-merging AI output into the store. Avoids dup/lost-node bugs.
4. **`convert_to_storyboard` has NO frontend caller** (only tests). So: backend → async; frontend service signature updated to `{task_id}`, but no dialog to rewire (note it; a future storyboard UI consumes it).

## Backend

### Three workflows (`backend/app/workflows/script_ai_workflows.py` — one file, three workflows)
Mirror `script_outline_workflow` exactly (LLM `@DBOS.step` + persist `@DBOS.step`; task-tracking is created by the ENDPOINT via `mgr.create` and mirrored by the migration-180 trigger; workflow body does the work; failure → `raise`). Service instances are constructed INSIDE steps (not passed across step boundary — same constraint as upload_postprocess/script_outline).

- `script_expand_chapter_workflow(script_id, chapter_id, title, summary, context, user_id)`:
  step1 `ScriptAIService(user_id).expand_chapter(...)` → html; step2 `ScriptService().update_chapter(chapter_id, {"content": html})`.
- `script_create_branches_workflow(script_id, chapter_id, title, summary, branch_count, branch_type, context, user_id)`:
  step1 `create_branches(...)` → list; step2 read parent position via `chapter_repo.get_by_id`, loop `create_chapter(...)` with the SAME x/y offset math as the current handler (lines 108–131).
- `script_to_storyboard_workflow(script_id, chapter_id, storyboard_project_id, user_id)`:
  step1 read chapter + project style_guide; step2 `split_chapter_to_scenes(...)`; step3 if `storyboard_project_id`: `node_repo.bulk_upsert` + `create_storyboard_link` per scene (same as handler lines 167–207).

Register all three in `_dispatch_bundle.py`. Unique `@DBOS.step` names (prefix `script_ai_*`) — DBOS requires globally-unique step names (we hit this on upload_postprocess).

### Endpoints (`script_ai_router.py`)
Rewrite the 3 handlers to mirror `generate_outline`:
```
await _verify_script_access(script_id, user_id)
task_id = await mgr.create(user_id=..., task_type="<unique>", title="...")
await start_workflow_routed("<unique>", dbos_workflow_callable=<wf>, dbos_workflow_kwargs={...})
return {"success": True, "task_id": task_id}
```
task_types: `script_expand_chapter`, `script_create_branches`, `script_to_storyboard`.

## Frontend

### `useTaskCompletion` hook (`frontend/hooks/useTaskCompletion.ts`)
```
useTaskCompletion(taskId: string | null, { onComplete, onError })
```
Uses `useTaskManager().tasks`; an effect watches `tasks.find(t => t.id === taskId)`; on `status==='completed'` → `onComplete(task)` once; on `'failed'|'cancelled'` → `onError(task)` once. Guards against double-fire (ref). Returns `{ status, task }` for "generating…" UI.

### Service (`scriptService.ts`)
Change `expandChapter` / `createBranches` / `convertToStoryboard` return types to `Promise<{ task_id: string }>` (drop the sync result fields). `generateOutline` already returns `{task_id}`.

### Dialogs — dispatch → watch → reload → close
Reload strategy: a single `reloadScript()` that calls `fetchScriptProject(scriptId)` and rebuilds the canvas store from server (the function the editor uses on mount — reuse it; if it's inline in the page, extract a `loadScriptIntoCanvas(scriptId)` helper).

- **`ExpandChapterDialog`**: on submit → `expandChapter()` → store `task_id`, show "Expanding…" (disable, spinner). `useTaskCompletion`: on complete → `reloadScript()` (or refetch the single chapter + `updateNodeData`) → close + toast; on error → toast, re-enable.
- **`CreateBranchDialog`**: same shape → on complete → `reloadScript()` (new branch nodes come from server) → close; on error → toast.
- **`CreateStoryDialog`** (FIX the broken outline flow): stop expecting inline `outline`; on dispatch show "Generating outline…", `useTaskCompletion` → on complete `reloadScript()` → close; on error toast.
- **convert-to-storyboard**: no dialog today — only update the service signature; note for a future storyboard caller.

### Canvas reload
Find how `ScriptEditorPage` loads chapters into the Zustand canvas on mount; extract/expose a `reloadScript(scriptId)` the dialogs can call. Server holds positions, so a full reload is safe + simple.

## Error handling
- Workflow failure → `raise` (DBOS → trigger marks task failed → frontend `useTaskCompletion` onError → toast).
- Endpoint still 403/404s via `_verify_script_access` before dispatch.
- Dialogs keep the user's input + re-enable on failure (no data loss).

## Testing
- Backend: one test per workflow (mock the AI service step + the persist repo calls; assert persist called with the right data; assert failure raises). Endpoint tests: returns `{task_id}`, dispatches once, 403 on cross-team.
- Frontend: `useTaskCompletion` unit test (fires onComplete once when the matching task flips to completed; onError on failed). Dialog tests: dispatch sets generating state; on task completion calls reload + close; on failure shows error. Service tests updated to `{task_id}`.

## Files
- Create: `backend/app/workflows/script_ai_workflows.py` (+ test)
- Modify: `backend/app/api/script_ai_router.py` (3 handlers async), `backend/app/workflows/_dispatch_bundle.py`
- Create: `frontend/hooks/useTaskCompletion.ts` (+ test)
- Modify: `frontend/services/scriptService.ts` (+ test), `ExpandChapterDialog.tsx`, `CreateBranchDialog.tsx`, `CreateStoryDialog.tsx`, the script editor page (extract `reloadScript`)
- No migration.

## Out of scope
- Live Realtime sync of `script_chapters`/`storyboard_nodes` (we reload-on-complete instead).
- A storyboard UI for convert-to-storyboard (backend ready; frontend caller is future work).
