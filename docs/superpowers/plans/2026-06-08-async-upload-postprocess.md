# Async Upload Post-Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make `POST /resources/upload` return as soon as bytes are written + the resource row exists; run ffprobe/Pillow metadata, thumbnail, and the transcode-trigger in a background DBOS workflow.

**Architecture:** New `upload_postprocess_workflow` (DBOS) dispatched after the sync upload core. Frontend needs NO change — `ResourcesContext` already reloads on `resources` UPDATE when `thumbnail_path` changes (`ResourcesContext.tsx:480`), which re-fetches the now-populated metadata.

**Spec:** `docs/superpowers/specs/2026-06-08-async-upload-postprocess-design.md`
**Branch:** `feature/async-upload-postprocess` (spec committed). **No migration.**

---

### Task 1: `upload_postprocess_workflow` (DBOS) + registration + test

**Files:**
- Create: `backend/app/workflows/upload_postprocess.py`
- Modify: `backend/app/workflows/_dispatch_bundle.py` (register import)
- Test: `backend/tests/test_upload_postprocess_workflow.py`

**Context:** Mirror an existing post-processing single-asset workflow for the task-tracking discipline. READ `backend/app/workflows/extract_audio.py` first — copy its exact pattern for: `manager.create()` the task_tracking row, `manager.start()/update_progress()/complete()`, failure via `raise`, `@DBOS.step` wrapping for I/O, and how it receives the manager (`get_task_manager`). Follow CLAUDE.md §路线C: never PATCH phase columns; business fields (subtitle/metadata) via the manager's metadata patch; short-circuit failure uses `raise`, not a failed dict.

- [ ] **Step 1: Write the workflow**

`upload_postprocess_workflow(resource_id: str, file_path: str, file_type: str, mime_type: str, user_id: str)`:
- Creates its task_tracking row (task_type `"upload_postprocess"`, subtitle `Post-processing <filename>` — pull filename from the resource or pass it in; simplest: subtitle from `file_path`).
- Step A — metadata: instantiate `ResourcesService()`; if `file_type in ("video","audio")` → `await svc._extract_video_metadata(abs_path)`, elif `"image"` → `await svc._extract_image_metadata(abs_path)` (abs_path = `Path(settings.DOWNLOAD_PATH)/file_path`). On success `await svc.repo.update_resource(resource_id, metadata)` and update V1 version (`svc.repo.update_version` for the current version — get it via the resource's current_version/version lookup; if a helper exists use it, else update by resource_id+version_number=1). Probe failure → log, skip (non-fatal).
- Step B — thumbnail: `await ThumbnailService().generate_thumbnail(resource_id, file_path, mime_type)`; on result `await svc.repo.update_resource(resource_id, {"thumbnail_path": thumb})`. Failure → log, skip (non-fatal).
- Step C — transcode: `await svc._trigger_transcode_async(resource_id, version_id, mime_type, user_id)` for video (get version_id of V1). This already self-gates + dispatches its own workflow.
- `manager.complete()` at the end. Wrap the whole body so a hard error `raise`s (DBOS marks failed → trigger mirrors).

- [ ] **Step 2: Register** — add to `_dispatch_bundle.py` (alphabetical with the others):
```python
from app.workflows.upload_postprocess import upload_postprocess_workflow  # noqa: F401
```

- [ ] **Step 3: Test** `backend/tests/test_upload_postprocess_workflow.py` (mirror an existing workflow test's mocking — mock `ResourcesService._extract_video_metadata`/`_extract_image_metadata`, `ThumbnailService.generate_thumbnail`, `repo.update_resource`, the task manager, and `_trigger_transcode_async`):
  - audio resource → calls `_extract_video_metadata` and `update_resource(metadata)` + thumbnail update; manager.complete called.
  - probe raises → workflow still completes (non-fatal), `update_resource` for metadata not called / thumbnail still attempted.
  - thumbnail raises → still completes.
  Run: `cd backend && uv run pytest tests/test_upload_postprocess_workflow.py -v` → pass.

- [ ] **Step 4: Lint + commit**
```
cd backend && uv run black app/workflows/upload_postprocess.py app/workflows/_dispatch_bundle.py tests/test_upload_postprocess_workflow.py && uv run isort <same> && uv run ruff check <same>
cd .. && git add backend/app/workflows/upload_postprocess.py backend/app/workflows/_dispatch_bundle.py backend/tests/test_upload_postprocess_workflow.py
git commit -m "feat(upload): upload_postprocess DBOS workflow (metadata + thumbnail + transcode)"
```

---

### Task 2: Trim `upload_resource` to the sync core

**Files:** Modify `backend/app/services/library/resources_service.py`

**Context:** `upload_resource` (line 84-200+) currently extracts metadata inline (162-165), merges into `update_resource` (168) + `create_version` (181), and triggers transcode inline (197+). Defer all of that.

- [ ] **Step 1: Edit `upload_resource`**
  - Replace the metadata block (lines ~159-169): set `update_data = {"file_path": relative_path}` (NO metadata). Keep `resource = await self.repo.update_resource(resource_id, update_data)`.
  - `create_version`: drop `**metadata` from `version_data` (file_path/size/hash stay).
  - Remove the inline transcode trigger block (the `if file_type == "video": ... _trigger_transcode_async ...` near line 197) — it moves to the workflow (Task 1 Step C).
  - Return the resource dict (now has file_path, null metadata).
  - Leave `_extract_video_metadata`/`_extract_image_metadata`/`_trigger_transcode_async` methods in place (the workflow calls them).

- [ ] **Step 2: Update/run existing upload service tests** — find tests that assert `upload_resource` sets duration/resolution and adjust them to expect metadata deferred (or assert file_path set + metadata absent). Run `cd backend && uv run pytest -k "upload" -q`.

- [ ] **Step 3: Lint + commit**
```
cd backend && uv run black/isort/ruff app/services/library/resources_service.py <touched tests>
git add backend/app/services/library/resources_service.py <tests> && git commit -m "refactor(upload): defer metadata/thumbnail/transcode out of sync upload_resource"
```

---

### Task 3: Router — complete upload task after sync core, dispatch workflow, drop inline thumbnail

**Files:** Modify `backend/app/api/resources_upload_router.py`

**Context:** The `upload` route (`:126`) creates+starts the `upload` task, awaits `svc.upload_resource`, completes the task, then generates the thumbnail inline (`:184-196`). 

- [ ] **Step 1: Edit the route**
  - Keep create+start `upload` task and `result = await svc.upload_resource(...)`.
  - Keep `tracker.complete(unified_task_id, ...)` (bytes written = upload done).
  - DELETE the inline thumbnail block (`:184-196`).
  - After completing the upload task, dispatch the workflow:
```python
if result.get("file_path") and result.get("mime_type"):
    try:
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.upload_postprocess import upload_postprocess_workflow
        await start_workflow_routed(
            "upload_postprocess",
            dbos_workflow_callable=upload_postprocess_workflow,
            dbos_workflow_kwargs={
                "resource_id": str(result["id"]),
                "file_path": result["file_path"],
                "file_type": result.get("file_type", ""),
                "mime_type": result.get("mime_type", ""),
                "user_id": auth.user_id,
            },
        )
    except Exception as e:
        logger.warning(f"[Upload] postprocess dispatch failed (non-fatal): {e}")
```
  - Return `{"success": True, "data": result}` (unchanged shape; metadata/thumbnail now null at return time).

- [ ] **Step 2: Lint + commit**
```
cd backend && uv run black/isort/ruff app/api/resources_upload_router.py
git add backend/app/api/resources_upload_router.py && git commit -m "feat(upload): return after sync core + dispatch upload_postprocess workflow"
```

---

### Task 4: Verify + version bump

- [ ] **Step 1:** `cd backend && uv run pytest tests/test_upload_postprocess_workflow.py -k "upload" -q` + the workflow test → green. Quick import check: `uv run python -c "import app.workflows._dispatch_bundle"` (no import error, workflow registered).
- [ ] **Step 2:** Bump `frontend/package.json` version (patch).
- [ ] **Step 3:** `git add frontend/package.json && git commit -m "chore: bump version (async upload post-processing)"`

---

## Notes for the executor
- **No frontend change** — `ResourcesContext` Realtime already reloads on `resources` UPDATE when `thumbnail_path` changes (`:480`), which re-fetches the populated metadata. (If the detail page open during postprocess should live-update, that's an optional follow-up, not this plan.)
- **No migration** — reuses existing metadata + `thumbnail_path` columns.
- **Task discipline** (CLAUDE.md §路线C): workflow uses `manager` API for phase, `raise` on failure, business fields via metadata patch.
- **Deploy is GitHub-Actions-billing-gated** — backend deploy may need the public-repo workaround at merge time (same as #560/#562/#564 this session).
