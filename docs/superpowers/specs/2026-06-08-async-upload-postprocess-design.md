# Async Upload Post-Processing — Design Spec

Date: 2026-06-08
Status: Awaiting user review

## Problem

`POST /api/v1/resources/upload` is **fully synchronous**: the HTTP response only
returns after stream-to-disk **and** the post-processing tail (ffprobe/Pillow
metadata extraction + thumbnail generation) all finish inline
(`resources_upload_router.py:126`, `resources_service.upload_resource`,
thumbnail at router `:184`). For large media the processing tail piles onto the
request → long requests, proxy/gateway timeout risk, no server-side progress.

Receiving the multipart bytes (stream-to-disk) is inherently part of the request
and cannot be deferred. What CAN move off the request is the **post-write
processing**: ffprobe/Pillow metadata + thumbnail.

## Goal

Return the upload response as soon as the bytes are written and the resource row
exists (resource immediately listable/playable), and run metadata + thumbnail in
a background DBOS workflow that fills them in afterwards.

## Decision (confirmed with user)

Async mechanism = **DBOS workflow** (consistent with download/parse/transcode;
durable across restarts; real `task_tracking` progress; retryable), via the
existing `start_workflow_routed` pattern. Not FastAPI BackgroundTasks.

## Sync / async split

**Synchronous (in the request, returns immediately after):**
1. `stream_upload_to_disk` → temp file (unavoidable — byte transfer).
2. sniff mime + classify.
3. `create_resource` (row + id).
4. move temp → final path; `update_resource({file_path})` — **file_path set, metadata NOT yet**.
5. `create_version` (V1, file_path, no metadata yet).
6. `create_resource_item` (scope link).
7. complete the `upload` task_tracking row (bytes done).
8. **dispatch** `upload_postprocess_workflow` (DBOS) and return.

**Asynchronous (`upload_postprocess_workflow`, its own task_tracking row):**
1. ffprobe (video/audio) / Pillow (image) metadata → `update_resource(metadata)` + `update_version(metadata)`.
2. thumbnail generation → `update_resource({thumbnail_path})`.
3. **HLS transcode trigger** for large video — move `_trigger_transcode_async` here from `upload_resource`. It currently does its OWN inline ffprobe for size/duration gating (`resources_service.py:911`), which is part of the sync tail we're removing; running it in the workflow (where duration was just probed in step 1, so it can be reused/re-probed off-request) takes that ffprobe off the request too. (Transcode itself already dispatches its own DBOS workflow — unchanged.)

Rationale for splitting at file_path: the resource must be immediately listable
AND its bytes reachable; metadata (duration/resolution/bitrate) + thumbnail are
display enrichments that can arrive a beat later.

## Architecture

### New workflow `backend/app/workflows/upload_postprocess.py`
`@DBOS.workflow upload_postprocess_workflow(resource_id, file_path, file_type, mime_type, user_id)`:
- Follows the task-system discipline (CLAUDE.md §路线C): `manager.create()` its
  task_tracking row, `manager.start()/complete()/fail()` via the manager API
  (never PATCH phase columns); failure path `raise` (not return failed dict);
  business fields (subtitle "Post-processing <filename>", metadata) via PATCH.
- Steps wrapped as `@DBOS.step` where they do I/O (ffprobe subprocess, thumbnail).
- Idempotent: safe to re-run (re-probe + re-thumbnail overwrite the same columns).
- Reuses the existing extraction helpers: `ResourcesService._extract_video_metadata` /
  `_extract_image_metadata` and `ThumbnailService.generate_thumbnail` (move the
  thumbnail call out of the router into the workflow).

### `resources_service.upload_resource` — trimmed to sync core
- Keep steps 1-6 above. Set `update_resource({file_path})` (drop the inline
  metadata merge). `create_version` without metadata.
- Return the resource dict (file_path set, metadata null).
- Do NOT call `_extract_video_metadata`/`_extract_image_metadata` inline.
- Do NOT call `_trigger_transcode_async` inline (moves to the workflow) — removes
  its gating ffprobe from the request.

### `resources_upload_router.upload` — dispatch + return
- Keep the `upload` task: create + start before, **complete after the sync core**
  (bytes written = upload done).
- Remove the inline `ThumbnailService.generate_thumbnail` block (`:184`) — moves to the workflow.
- After the sync core, `await start_workflow_routed("upload_postprocess", dbos_workflow_callable=upload_postprocess_workflow, dbos_workflow_kwargs={resource_id, file_path, file_type, mime_type, user_id})`.
- Return `{success, data: result}` immediately (result has file_path, null metadata/thumbnail).

### Register the workflow
Add `upload_postprocess_workflow` to the DBOS workflow registry/import wherever
`transcode_workflow` etc. are registered (so DBOS knows it).

## Frontend

The resource appears immediately with no thumbnail/duration. Two requirements:
1. **Graceful render** of a resource with null `thumbnail_path` / null metadata
   (the list/detail already fall back to a type icon / placeholder — verify).
2. **Auto-refresh when postprocess completes** so the thumbnail/metadata appear
   without a manual reload. Determine the existing mechanism: if the frontend
   subscribes to `resources` Realtime, it updates automatically; if not, refetch
   the resource (or list) when the postprocess `task_tracking` row completes
   (the Task Center already subscribes to `task_tracking`). Pick whichever
   matches the current code — do NOT add a new polling loop if Realtime exists.

## Error handling
- Probe/thumbnail failure in the workflow → non-fatal per asset: log, leave that
  column null, still complete the workflow (the file is already usable). Mirror
  today's "thumbnail failed (non-fatal)" behavior.
- The sync upload still fails fast on >500MB / write errors (unchanged), failing
  the `upload` task.
- Workflow short-circuit/failure uses `raise` (so DBOS + task_tracking reflect it).

## Testing
- Backend: `upload_postprocess_workflow` unit test — given a resource + file,
  it writes metadata + thumbnail_path (mock ffprobe/thumbnail); failure path
  leaves columns null + does not crash. Service test: `upload_resource` returns
  with file_path set and NO metadata (metadata deferred). Router test: response
  returns before postprocess; workflow dispatched once.
- Frontend: a resource card/detail with null thumbnail/metadata renders (placeholder);
  updates when the row changes (whichever refresh mechanism is chosen).

## Files
- Create: `backend/app/workflows/upload_postprocess.py`
- Create: `backend/tests/test_upload_postprocess_workflow.py`
- Modify: `backend/app/services/library/resources_service.py` (`upload_resource` → sync core; metadata deferred)
- Modify: `backend/app/api/resources_upload_router.py` (dispatch workflow, drop inline thumbnail, complete upload task after sync core)
- Modify: DBOS workflow registry (register `upload_postprocess_workflow`)
- Modify (frontend, if needed): the resource list/detail refresh-on-complete wiring
- No migration (reuses existing metadata + `thumbnail_path` columns)
