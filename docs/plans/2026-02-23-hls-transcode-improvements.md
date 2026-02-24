# HLS Transcode Improvements

## Background

HLS multi-bitrate transcoding pipeline is fully implemented end-to-end:

- **Backend**: `transcode_tasks.py` → `TranscodeService` → ffmpeg → 480p/720p/1080p + passthrough → master.m3u8
- **Task tracking**: `unified_tasks` integration with Celery, retry (max 2, exponential backoff)
- **API**: `POST /resources/{id}/versions/{vid}/transcode` (retry), `GET .../hls/{path}` (serve)
- **Frontend**: `ResourceDetail.tsx` shows transcode status badges + retry button; `VideoPlayer.tsx` uses hls.js with quality selector

### Audit Findings (2026-02-22)

Database query of `resource_versions` revealed:

| Status | Count | Notes |
|--------|-------|-------|
| completed | 1 | Working correctly |
| pending | 1 | Stuck — Celery worker was not running |
| NULL | 3 | Downloaded before transcode chain existed |
| (no file_path) | 2 | Download failed, no source to transcode |

**Root issues**:
1. No size/duration gating — all videos trigger transcode, including 2MB clips
2. `_maybe_chain_transcode` has minimal logging — hard to trace why some videos skipped
3. Old videos (NULL status) have no way to batch-trigger transcoding
4. Retry endpoint calls `maybe_trigger_transcode` which uses dedup — can block legitimate retries
5. PlayerPage (`/player/:id`) uses `parsed_media` data, not `resource_versions` — no HLS integration

## Changes

### 1. Size/Duration Gating in `maybe_trigger_transcode`

**File**: `backend/app/tasks/transcode_tasks.py` — `maybe_trigger_transcode()`

Add gating before dispatching Celery task. Only transcode videos where:
- File size > 100 MB, **OR**
- Duration > 10 minutes

This requires probing the file with ffprobe before dispatching. Since `maybe_trigger_transcode` is called synchronously from Celery download task context, probe needs to be sync-compatible.

```python
def maybe_trigger_transcode(resource_id, version_id, mime_type, user_id=None, force=False):
    """Trigger HLS transcoding if file is a large video.

    Args:
        force: Skip size/duration gating (for manual retry and batch re-transcode).
    """
    if not mime_type or not mime_type.startswith("video/"):
        return

    if not force:
        # Check file size and duration
        from app.repositories.resources_repository import ResourcesRepository
        repo = ResourcesRepository()
        version = run_async(repo.get_version_by_id(version_id))
        if not version or not version.get("file_path"):
            logger.info(f"[Transcode] Skip: no file_path for version {version_id}")
            return

        file_path = Path(settings.DOWNLOAD_PATH) / version["file_path"]
        if not file_path.exists():
            logger.info(f"[Transcode] Skip: file not found {file_path}")
            return

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        duration_sec = _probe_duration_sync(str(file_path))

        if file_size_mb < 100 and (duration_sec or 0) < 600:
            logger.info(
                f"[Transcode] Skip: too small ({file_size_mb:.0f}MB, {duration_sec or '?'}s) "
                f"for version {version_id}"
            )
            return

    # ... existing dedup + dispatch logic ...
```

New helper `_probe_duration_sync()` — lightweight ffprobe call returning seconds:

```python
def _probe_duration_sync(filepath: str) -> Optional[float]:
    """Quick ffprobe to get duration in seconds."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            dur = info.get("format", {}).get("duration")
            return float(dur) if dur else None
    except Exception:
        pass
    return None
```

### 2. Enhanced Logging in `_maybe_chain_transcode`

**File**: `backend/app/tasks/download_tasks.py` — `_maybe_chain_transcode()`

Add structured logging at each decision point:

```python
def _maybe_chain_transcode(platform_id: str, user_id: str):
    try:
        repo = ResourcesRepository()
        resource = run_async(repo.get_resource_by_platform_id(platform_id))
        if not resource:
            logger.info(f"[Transcode/Chain] No resource found for platform_id={platform_id}")
            return

        mime = resource.get("mime_type", "")
        if not mime.startswith("video/"):
            logger.debug(f"[Transcode/Chain] Not a video ({mime}), skip: {platform_id}")
            return

        resource_id = str(resource["id"])
        versions = run_async(repo.get_versions(resource_id))
        if not versions:
            logger.warning(f"[Transcode/Chain] No versions for resource {resource_id}")
            return

        latest = versions[0]
        version_id = str(latest["id"])
        logger.info(
            f"[Transcode/Chain] Chaining transcode: resource={resource_id}, "
            f"version={version_id}, mime={mime}"
        )

        from app.tasks.transcode_tasks import maybe_trigger_transcode
        maybe_trigger_transcode(resource_id, version_id, mime, user_id=user_id)
    except Exception as e:
        logger.error(f"[Transcode/Chain] Failed for {platform_id}: {e}", exc_info=True)
```

### 3. Batch Re-Transcode API

**File**: `backend/app/api/resources_router.py`

New endpoint to scan and enqueue transcoding for all videos with NULL status:

```
POST /api/v1/resources/transcode/batch
```

Logic:
1. Query `resource_versions` where `mime_type LIKE 'video/%'` AND `transcode_status IS NULL` AND `file_path IS NOT NULL`
2. For each version, call `maybe_trigger_transcode(force=True)` to skip size gating
3. Return count of queued items

```python
@router.post("/transcode/batch")
async def batch_transcode(auth: AuthDep):
    """Queue HLS transcoding for all video versions with NULL transcode_status."""
    repo = ResourcesRepository()
    # Custom query for NULL-status video versions
    versions = await repo.get_untranscoded_video_versions(user_id=auth.user_id)

    queued = 0
    for v in versions:
        maybe_trigger_transcode(
            str(v["resource_id"]), str(v["id"]),
            v.get("mime_type", "video/mp4"),
            user_id=auth.user_id,
            force=True,
        )
        queued += 1

    return {"success": True, "queued": queued, "total_found": len(versions)}
```

**File**: `backend/app/repositories/resources_repository.py`

New method:

```python
async def get_untranscoded_video_versions(self, user_id: str = None) -> list:
    """Get video versions that have never been transcoded."""
    query = self.client.table("resource_versions") \
        .select("id, resource_id, mime_type, file_path") \
        .like("mime_type", "video/%") \
        .is_("transcode_status", "null") \
        .not_.is_("file_path", "null")
    result = query.execute()
    return result.data or []
```

**Frontend entry point**: Button in ResourceDetail or a management page. Low priority — can use API directly for now.

### 4. Retry Not Blocked by Dedup

**File**: `backend/app/api/resources_router.py` — `retry_transcode()`

Current code calls `maybe_trigger_transcode()` which checks dedup. For manual retry, should use `force=True`:

```python
@router.post("/{resource_id}/versions/{version_id}/transcode")
async def retry_transcode(resource_id, version_id, auth):
    # ... existing validation ...

    # Reset status before retrying
    await repo.update_version(version_id, {"transcode_status": "pending"})

    maybe_trigger_transcode(
        resource_id, version_id, mime,
        user_id=auth.user_id,
        force=True,  # Skip size gating AND dedup for manual retry
    )
    return {"success": True, "message": "Transcoding queued"}
```

Also update `maybe_trigger_transcode` to skip dedup when `force=True`:

```python
if not force:
    # dedup check
    result = run_async(orchestrator.acquire_or_subscribe(...))
    if result["action"] in ("subscribed", "completed"):
        return
```

### 5. PlayerPage HLS Integration (Deferred)

**Current gap**: PlayerPage (`/player/:id`) loads video from `parsed_media.download_path` — it has no knowledge of `resource_versions` or HLS.

**Approach**: When PlayerPage fetches media detail, also check if a linked resource exists with completed HLS. If so, prefer the HLS URL.

This requires:
1. A backend join or separate call: `GET /api/v1/videos/{id}` response already includes `resource_id` if linked
2. Frontend: If `resource_id` exists, fetch the resource's latest version and check `hls_path` + `transcode_status`
3. Pass HLS URL to `VideoPlayer` component

**Decision**: Defer to a follow-up — PlayerPage is for parsed_media quick preview, while ResourceDetail already has full HLS support. Users who need HLS playback should use the Resources view.

## Implementation Order

| Step | Scope | Effort |
|------|-------|--------|
| 1. Size/duration gating | `transcode_tasks.py` | Small |
| 2. Enhanced logging | `download_tasks.py` | Small |
| 3. Retry dedup fix | `resources_router.py` + `transcode_tasks.py` | Small |
| 4. Batch re-transcode API | `resources_router.py` + `resources_repository.py` | Medium |
| 5. PlayerPage HLS | Deferred | — |

Steps 1-3 can be done together. Step 4 after verifying 1-3 work.

## Verification

1. `cd backend && uv run python -c "from app.tasks.transcode_tasks import maybe_trigger_transcode; print('OK')"` — import check
2. Download a small video (~5MB) → verify transcode is skipped (check logs for "Skip: too small")
3. Download a large video (>100MB) → verify transcode triggers automatically
4. In ResourceDetail, click retry on a failed version → verify it re-queues without dedup block
5. Call `POST /resources/transcode/batch` → verify NULL-status versions get queued
6. Check `unified_tasks` table → verify all new transcodes create task entries
