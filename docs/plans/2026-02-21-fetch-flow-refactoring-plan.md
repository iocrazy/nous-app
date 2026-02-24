# Fetch Flow Refactoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the monolithic `/videos/fetch` endpoint into two (first-time parse vs subsequent per-type fetch), add full-type dedup, auto URL re-parse on missing/expired URLs, and structured logging.

**Architecture:** New `POST /videos/{platform_id}/fetch` endpoint handles subsequent downloads without re-parsing. Orchestrator dedup keys become per-type (`download:video:{id}`, `download:music:{id}`, etc). Celery task gains a URL-check-and-re-parse step before actual download. Frontend `PlayerPage` calls the new endpoint instead of `parseShareLink`.

**Tech Stack:** FastAPI, Celery, Redis (dedup), Supabase (PostgreSQL), React 19 + TypeScript

---

## Task 1: Add `MediaTypeFetchRequest` Schema

**Files:**
- Modify: `backend/app/schemas/media.py:252-282`

**Step 1: Add new schema class after `MediaFetchRequest`**

Add this class at line ~282 (after `MediaFetchRequest`):

```python
class MediaTypeFetchRequest(BaseModel):
    """Request body for subsequent per-type fetch (POST /videos/{platform_id}/fetch)."""

    types: list[str] = Field(
        ...,
        description="Media types to fetch: 'video', 'music', 'cover', 'image'",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_types(self):
        valid = {"video", "music", "cover", "image"}
        invalid = set(self.types) - valid
        if invalid:
            raise ValueError(f"Invalid types: {invalid}. Must be one of {valid}")
        return self
```

**Step 2: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.schemas.media import MediaTypeFetchRequest; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/schemas/media.py
git commit -m "feat: add MediaTypeFetchRequest schema for per-type fetch endpoint"
```

---

## Task 2: Extract Shared Dedup + Dispatch Helper

The new `/{platform_id}/fetch` endpoint and the existing `/fetch` endpoint both need: per-type dedup → Celery dispatch. Extract a shared helper to avoid duplication.

**Files:**
- Modify: `backend/app/api/media_router.py`

**Step 1: Add helper function `_dedup_and_dispatch`**

Add this function before the route endpoints (around line 78, before `# Route endpoints`):

```python
async def _dedup_and_dispatch(
    *,
    platform_id: str,
    user_id: str,
    resource_id: str | None,
    media_type: int,
    video_title: str,
    download_video: bool,
    download_music: bool,
    download_cover: bool,
    url: str | None = None,          # Only for yt-dlp platforms
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    """Per-type Orchestrator dedup check + Celery dispatch.

    Returns:
        {
            "task_id": str | None,
            "types_submitted": [...],
            "types_skipped": [...],
            "types_subscribed": [...],
        }
    """
    from app.services.task_orchestrator import get_orchestrator
    from app.tasks.download_tasks import download_unified_task
    from app.services.system_monitor_service import check_worker_ready

    is_image_type = int(media_type) in (2, 68)

    # Build requested types list
    requested = {}
    if download_video:
        requested["image" if is_image_type else "video"] = True
    if download_music:
        requested["music"] = True
    if download_cover:
        requested["cover"] = True

    if not requested:
        return {"task_id": None, "types_submitted": [], "types_skipped": list(requested.keys()), "types_subscribed": []}

    # Per-type dedup
    types_to_download = []
    types_subscribed = []
    types_skipped = []
    dedup_keys = {}

    orchestrator = get_orchestrator()
    for dtype in requested:
        try:
            result = await orchestrator.acquire_or_subscribe(
                task_type=f"download:{dtype}",
                dedup_identifier=platform_id,
                user_id=user_id,
                resource_id=resource_id or "",
            )
            action = result["action"]
            logger.info(f"[Download/Dedup] {dtype}={action} for {platform_id}")
            if action == "created":
                types_to_download.append(dtype)
                if result.get("dedup_key"):
                    dedup_keys[dtype] = result["dedup_key"]
            elif action == "subscribed":
                types_subscribed.append(dtype)
            else:  # completed
                types_skipped.append(dtype)
        except Exception as e:
            logger.warning(f"[Download/Dedup] {dtype} dedup failed, proceeding: {e}")
            types_to_download.append(dtype)

    task_id = None
    if types_to_download:
        # Map back to download_* bools
        dl_video = ("video" in types_to_download) or ("image" in types_to_download)
        dl_music = "music" in types_to_download
        dl_cover = "cover" in types_to_download
        # Use first dedup key for task tracking
        first_dedup_key = next(iter(dedup_keys.values()), None)

        try:
            ready, err_msg = check_worker_ready()
            if not ready:
                raise RuntimeError(err_msg)

            logger.info(
                f"[Download/Init] Celery dispatch: platform_id={platform_id}, "
                f"types={types_to_download}, user={user_id}"
            )
            celery_task = download_unified_task.delay(
                platform_id=platform_id,
                user_id=user_id,
                url=url,
                download_video=dl_video,
                download_music=dl_music,
                download_cover=dl_cover,
                media_type=media_type,
                video_title=video_title[:50] if video_title else "undefined",
                resource_id=resource_id,
                _dedup_key=first_dedup_key,
            )
            task_id = celery_task.id
        except Exception as celery_err:
            logger.warning(f"[Download/Init] Celery unavailable: {celery_err}")
            if background_tasks:
                from app.services.downloader import DownloaderService
                if dl_video:
                    if is_image_type:
                        background_tasks.add_task(DownloaderService.download_images_by_platform_id, platform_id, user_id=user_id)
                    else:
                        background_tasks.add_task(DownloaderService.download_video_by_platform_id, platform_id, user_id=user_id)
                if dl_music:
                    background_tasks.add_task(DownloaderService.download_music_by_platform_id, platform_id=platform_id, user_id=user_id)
                if dl_cover:
                    background_tasks.add_task(DownloaderService.download_cover_by_platform_id, platform_id, user_id=user_id)
                task_id = "background"

    return {
        "task_id": task_id,
        "types_submitted": types_to_download,
        "types_skipped": types_skipped,
        "types_subscribed": types_subscribed,
    }
```

**Step 2: Verify import compiles**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.media_router import _dedup_and_dispatch; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/api/media_router.py
git commit -m "refactor: extract _dedup_and_dispatch helper for shared dedup+Celery logic"
```

---

## Task 3: Add `POST /videos/{platform_id}/fetch` Endpoint

**Files:**
- Modify: `backend/app/api/media_router.py`

**Step 1: Add the new endpoint**

Add this after the existing `fetch_video` function (after the `except` block, around line 480):

```python
@router.post("/{platform_id}/fetch", tags=TAGS_FETCH)
async def fetch_media_by_type(
    platform_id: str,
    request: MediaTypeFetchRequest,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
):
    """
    Fetch specific media types for an already-parsed video.

    Unlike POST /fetch (first-time parse), this endpoint does NOT re-parse
    the original URL. It uses existing metadata to trigger downloads for
    the requested types.

    - **platform_id**: The media's platform identifier
    - **types**: List of types to fetch: "video", "music", "cover", "image"

    Authentication: Bearer Token or API Key
    """
    try:
        logger.info(
            f"[Download/Init] User {auth.user_id} requesting {request.types} "
            f"for {platform_id}"
        )

        # ① Load existing parsed_media
        repo = MediaRepository()
        media = await repo.get_by_platform_id(platform_id)
        if not media:
            raise HTTPException(status_code=404, detail="Media not found. Use POST /videos/fetch first.")

        media_id = media.get("id")
        media_type = media.get("media_type", 0)
        video_title = media.get("title", platform_id)

        # ② Points check
        points_service = PointsService()
        from app.db.supabase_client import get_async_supabase_admin as _get_admin
        _admin = await _get_admin()
        _tm = (
            await _admin.table("team_members")
            .select("team_id")
            .eq("user_id", auth.user_id)
            .limit(1)
            .execute()
        )
        _team_id = _tm.data[0]["team_id"] if _tm.data else None
        _points_cost = 0
        if _team_id:
            await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
            points_result = await points_service.check_and_consume(
                team_id=_team_id,
                user_id=auth.user_id,
                action_type="video_parse",
            )
            if not points_result["success"]:
                raise HTTPException(status_code=402, detail=points_result["reason"])
            _points_cost = points_result.get("points_cost", 0)

        # ③ Ensure user resource record exists
        from app.repositories.resources_repository import ResourcesRepository
        from app.services.media_service import MediaService

        resources_repo = ResourcesRepository()
        user_resource = await resources_repo.get_resource_by_media_id_and_creator(
            media_id, auth.user_id
        )
        resource_id = user_resource.get("id") if user_resource else None

        if not resource_id:
            # Create a minimal resource record
            resource_id = await MediaService._ensure_user_resource(
                resources_repo=resources_repo,
                media_id=media_id,
                user_id=auth.user_id,
                parsed_data=media,
                need_download_video="video" in request.types or "image" in request.types,
                need_download_music="music" in request.types,
                need_download_cover="cover" in request.types,
                is_image_type=str(media_type) in ("images", "image", "2", "68"),
                dedup_hit=False,
                existing_media=media,
            )
        else:
            # Update status for newly requested types (skipped → pending)
            status_updates = {}
            for t in request.types:
                status_field = f"{t}_download_status"
                if user_resource.get(status_field) == "skipped":
                    status_updates[status_field] = "pending"
            if status_updates:
                await resources_repo.update_download_status(resource_id, status_updates)

        # ④ Dedup + dispatch
        is_image = str(media_type) in ("images", "image", "2", "68")
        dispatch_result = await _dedup_and_dispatch(
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=resource_id,
            media_type=int(media_type) if str(media_type).isdigit() else 0,
            video_title=video_title,
            download_video="video" in request.types or "image" in request.types,
            download_music="music" in request.types,
            download_cover="cover" in request.types,
            background_tasks=background_tasks,
        )

        # ⑤ Log action
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"Fetch {request.types} for {video_title[:30]}...",
            status="success",
            aweme_id=platform_id,
        )

        return {
            "success": True,
            "message": "Fetch submitted",
            "platform_id": platform_id,
            "download_task_id": dispatch_result["task_id"],
            "types_submitted": dispatch_result["types_submitted"],
            "types_skipped": dispatch_result["types_skipped"],
            "types_subscribed": dispatch_result["types_subscribed"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Download/Init] Failed: {platform_id}, error: {e}")
        raise HTTPException(status_code=500, detail=f"Fetch failed: {str(e)}")
```

**Step 2: Add import for `MediaTypeFetchRequest` at top of file**

Add to imports (around line 30):

```python
from app.schemas.media import MediaFetchRequest, MediaTypeFetchRequest
```

(Replace existing `MediaFetchRequest` import if it's imported separately.)

**Step 3: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.media_router import router; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/api/media_router.py
git commit -m "feat: add POST /videos/{platform_id}/fetch endpoint for per-type fetch"
```

---

## Task 4: Refactor Existing `/fetch` to Use Shared Helper

Replace the inline dedup + Celery dispatch logic in `fetch_video()` with a call to `_dedup_and_dispatch`.

**Files:**
- Modify: `backend/app/api/media_router.py:287-391` (the dedup + dispatch section)

**Step 1: Replace the dedup + dispatch block**

In `fetch_video()`, find the section starting at `# Download media files` (around line 287) through the end of the Celery dispatch block (around line 391). Replace it with:

```python
        # Download media files
        resource_id = save_result.get("resource_id")
        dedup_hit = save_result.get("dedup_hit", False)
        need_download_video = request.video_bool and not dedup_hit

        dispatch_result = {"task_id": None, "types_submitted": [], "types_skipped": [], "types_subscribed": []}
        if need_download_video or request.music_bool or request.cover_bool:
            dispatch_result = await _dedup_and_dispatch(
                platform_id=platform_id,
                user_id=auth.user_id,
                resource_id=resource_id,
                media_type=int(media_type) if str(media_type).isdigit() else 0,
                video_title=video_title,
                download_video=need_download_video,
                download_music=request.music_bool,
                download_cover=request.cover_bool,
                background_tasks=background_tasks,
            )

        download_task_id = dispatch_result["task_id"]
```

Keep the rest of the response unchanged (the log_user_action and return block).

**Step 2: Verify backend starts and existing parse flow works**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.media_router import router; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/api/media_router.py
git commit -m "refactor: existing /fetch uses _dedup_and_dispatch helper"
```

---

## Task 5: Refactor `retry_download` to Use Shared Helper

**Files:**
- Modify: `backend/app/api/media_router.py:1001-1115` (retry_download function)

**Step 1: Replace Celery dispatch block in retry_download**

Replace the section from `# Trigger Celery download task` (around line 1062) through the background_tasks fallback with:

```python
        # ── Dispatch via shared helper ──
        dispatch_result = await _dedup_and_dispatch(
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=resource_id,
            media_type=int(media_type) if str(media_type).isdigit() else 0,
            video_title=video_title,
            download_video=request.video_bool,
            download_music=request.music_bool,
            download_cover=request.cover_bool,
            background_tasks=background_tasks,
        )
        download_task_id = dispatch_result["task_id"]
```

Also remove the hardcoded `request.cover_bool = True` on line 1031.

**Step 2: Verify**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.media_router import router; print('OK')"`

**Step 3: Commit**

```bash
git add backend/app/api/media_router.py
git commit -m "refactor: retry_download uses _dedup_and_dispatch helper"
```

---

## Task 6: Add Auto URL Re-parse in Celery Task

When download URLs are missing for a requested type, re-parse the original URL before attempting download.

**Files:**
- Modify: `backend/app/tasks/download_tasks.py:226-311` (_do_douyin_download function)

**Step 1: Add `_ensure_download_urls` helper function**

Add this function before `_do_douyin_download` (around line 224):

```python
def _ensure_download_urls(platform_id: str, media: dict, needed_types: list[str]) -> dict:
    """Check if download URLs are available for needed types. Re-parse if missing.

    Mutates and returns the media dict with refreshed URLs if re-parsed.
    """
    url_fields = {
        "video": "video_download_urls",
        "music": "music_download_urls",
        "cover": "cover_urls",
        "image": "image_download_urls",
    }

    missing_types = []
    for t in needed_types:
        field = url_fields.get(t)
        if field and not media.get(field):
            missing_types.append(t)

    if not missing_types:
        return media

    logger.info(
        f"[Download/URL] Missing URLs for {missing_types} on {platform_id}, "
        f"re-parsing original_url..."
    )

    original_url = media.get("original_url")
    if not original_url:
        logger.warning(f"[Download/URL] No original_url for {platform_id}, cannot re-parse")
        return media

    try:
        from app.services.lightweight_parser import LightweightParser
        from app.services.douyin_parser import DouyinParser

        aweme_detail = run_async(LightweightParser.parse(original_url))
        if not aweme_detail:
            logger.warning(f"[Download/URL] Re-parse returned empty for {platform_id}")
            return media

        new_parsed = run_async(DouyinParser.parse_aweme_detail(
            aweme_detail=aweme_detail,
            valid_url=original_url,
            download_video=True,
            download_music=True,
            download_cover=True,
        ))
        if not new_parsed:
            logger.warning(f"[Download/URL] Re-parse yielded no data for {platform_id}")
            return media

        # Update DB with refreshed URLs
        from app.repositories.media_repository import MediaRepository as _MR
        update_fields = {}
        for t in missing_types:
            field = url_fields.get(t)
            if field and new_parsed.get(field):
                update_fields[field] = new_parsed[field]
                media[field] = new_parsed[field]
                logger.info(f"[Download/URL] Refreshed {field} for {platform_id} ({len(new_parsed[field])} URLs)")

        if update_fields:
            run_async(_MR().update(platform_id, update_fields))
        else:
            logger.warning(f"[Download/URL] Re-parse found no new URLs for {missing_types}")

    except Exception as e:
        logger.error(f"[Download/URL] Re-parse failed for {platform_id}: {e}")

    return media
```

**Step 2: Call `_ensure_download_urls` in `_do_douyin_download`**

At the start of `_do_douyin_download` (line ~236, after `results = {...}`), add:

```python
    # ── Ensure download URLs are available (re-parse if missing) ──
    from app.repositories.media_repository import MediaRepository as _MR_urls
    media = run_async(_MR_urls().get_by_platform_id(platform_id))
    if media:
        needed = []
        if download_video:
            needed.append("image" if int(media_type) in (2, 68) else "video")
        if download_music:
            needed.append("music")
        if download_cover:
            needed.append("cover")
        media = _ensure_download_urls(platform_id, media, needed)
```

**Step 3: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.tasks.download_tasks import download_unified_task; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "feat: auto re-parse URLs when missing before download"
```

---

## Task 7: Add Structured Logging to Celery Task

**Files:**
- Modify: `backend/app/tasks/download_tasks.py:226-311` (_do_douyin_download) and `download_unified_task`

**Step 1: Add structured logs to `_do_douyin_download`**

Replace ad-hoc log lines with tagged format. In each download block, ensure:

```python
# Before download:
logger.info(f"[Download/Exec] {dtype}: downloading {platform_id}...")

# After download:
logger.info(f"[Download/Exec] {dtype}: {result_status} for {platform_id}")
```

**Step 2: Add summary log at end of `download_unified_task`**

After the `results` dict is finalized (around line 577), add:

```python
        # ── Structured summary log ──
        completed = [k for k, v in results.items() if v == "completed"]
        failed = [k for k, v in results.items() if v and v != "completed"]
        skipped = [k for k, v in results.items() if v is None]
        logger.info(
            f"[Download/Done] {platform_id}: "
            f"completed={completed}, failed={failed}, skipped={skipped}"
        )
```

**Step 3: Add DB update log**

In the resource status update section (around line 613-645), add:

```python
                logger.info(
                    f"[Download/DB] resource {resource_id}: {status_updates}"
                )
```

**Step 4: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "feat: add structured logging tags to download task"
```

---

## Task 8: Frontend — Add `fetchMediaByType` Service Function

**Files:**
- Modify: `frontend/services/parserService.ts`

**Step 1: Add new function after `parseShareLink`**

```typescript
/**
 * Fetch specific media types for an already-parsed video.
 * Used by PlayerPage's Fetch Video/Audio/Cover buttons.
 */
export const fetchMediaByType = async (
  platformId: string,
  types: string[],
): Promise<{
  success: boolean;
  message: string;
  platform_id: string;
  download_task_id: string | null;
  types_submitted: string[];
  types_skipped: string[];
  types_subscribed: string[];
}> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/videos/${platformId}/fetch`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify({ types }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
```

**Step 2: Verify frontend builds**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`

Expected: Build succeeds (new function is exported but not yet called).

**Step 3: Commit**

```bash
git add frontend/services/parserService.ts
git commit -m "feat: add fetchMediaByType service function for per-type fetch"
```

---

## Task 9: Frontend — Update PlayerPage to Use New Endpoint

**Files:**
- Modify: `frontend/pages/PlayerPage.tsx:14,126-158`

**Step 1: Update import**

Change line 14 from:

```typescript
import { parseShareLink } from '../services/parserService';
```

to:

```typescript
import { parseShareLink, fetchMediaByType } from '../services/parserService';
```

**Step 2: Rewrite `handleFetchMedia`**

Replace lines 126-158 with:

```typescript
  const handleFetchMedia = async (options: { video?: boolean; music?: boolean; cover?: boolean }) => {
    if (!video?.platform_id) {
      addToast('No platform ID available for fetch', 'error');
      return;
    }
    setShowDownloadMenu(false);
    setIsFetching(true);
    try {
      const types: string[] = [];
      if (options.video) types.push('video');
      if (options.music) types.push('music');
      if (options.cover) types.push('cover');

      const result = await fetchMediaByType(video.platform_id, types);
      const items = result.types_submitted.length > 0
        ? result.types_submitted.join(', ')
        : 'all cached';
      addToast(`Fetch submitted: ${items}. Refreshing...`, 'success');
      // Auto-reload video data after a short delay so download status reflects the fetch
      setTimeout(async () => {
        if (displayId) {
          const data = await fetchVideoByDisplayId(displayId);
          if (data) setVideo(data);
        }
      }, 3000);
    } catch (error) {
      console.error('Fetch error:', error);
      addToast(error instanceof Error ? error.message : 'Fetch failed', 'error');
    } finally {
      setIsFetching(false);
    }
  };
```

**Step 3: Verify frontend builds**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`

Expected: Build succeeds.

**Step 4: Commit**

```bash
git add frontend/pages/PlayerPage.tsx
git commit -m "feat: PlayerPage uses new per-type fetch endpoint"
```

---

## Task 10: End-to-End Verification

**Step 1: Restart backend + celery**

```bash
cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system
./scripts/start-dev.sh restart-backend
```

**Step 2: Test first-time parse (existing flow)**

Open `http://localhost:5176`, go to Parser, paste a Douyin link. Verify it parses and downloads as before.

**Step 3: Test per-type fetch (new flow)**

Go to a video detail page (PlayerPage). Click Download → Fetch Audio. Verify:
- Hits `POST /api/v1/videos/{platform_id}/fetch` (not `/videos/fetch`)
- Backend logs show `[Download/Init]`, `[Download/Dedup]`, `[Download/Exec]`, `[Download/Done]`
- If music URLs are NULL, logs show `[Download/URL] re-parsing...`
- Task appears in Task Center

**Step 4: Test dedup (click same Fetch twice quickly)**

Click Fetch Video twice rapidly. Second request should show `types_subscribed: ["video"]`.

**Step 5: Check database**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c \
  "SELECT platform_id, video_download_status, music_download_status, cover_download_status FROM parsed_media ORDER BY updated_at DESC LIMIT 3;"
```

Verify statuses match expected (completed/pending/skipped/failed).
