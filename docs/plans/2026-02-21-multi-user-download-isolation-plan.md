# Multi-User Download Isolation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor parsed_media into a global content table (no user_id) and isolate per-user download status into the resources table, so multiple users' fetch requests never interfere with each other.

**Architecture:** Two-layer status model — `parsed_media` stores global physical file state (is the file on the server?), `resources` stores per-user request lifecycle (did this user request this media type, and what's the progress?). Fetch flow checks global cache first (instant completion if file exists), falls back to actual download.

**Tech Stack:** PostgreSQL (Supabase), FastAPI, Celery, React 19 + TypeScript

**Design doc:** `docs/plans/2026-02-21-multi-user-download-isolation-design.md`

---

## Task 1: Database Migration 082 — Add New Columns

**Files:**
- Create: `supabase/migrations/082_download_isolation.sql`

**Step 1: Write migration SQL**

```sql
-- 082_download_isolation.sql
-- Multi-user download isolation: add per-user download status to resources,
-- add image_download_status to parsed_media.

-- ============================================================
-- 1. parsed_media: add image support
-- ============================================================
ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS image_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS image_download_path TEXT;

-- For image-type records, migrate video_download_status → image_download_status
-- (images currently piggyback on video_download_status)
UPDATE parsed_media
SET image_download_status = video_download_status,
    image_download_path = download_path,
    video_download_status = 'skipped',
    download_path = NULL
WHERE media_type IN ('images', 'image');

-- ============================================================
-- 2. resources: add per-user download status columns
-- ============================================================
ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS video_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS music_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS cover_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS image_download_status download_status DEFAULT 'skipped';

-- ============================================================
-- 3. Data migration: copy existing statuses from parsed_media to resources
-- ============================================================
UPDATE resources r
SET
  video_download_status = pm.video_download_status,
  music_download_status = pm.music_download_status,
  cover_download_status = pm.cover_download_status,
  image_download_status = pm.image_download_status
FROM parsed_media pm
WHERE r.media_id = pm.id
  AND r.media_id IS NOT NULL;

-- ============================================================
-- 4. Indexes
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_resources_video_dl_status
  ON resources (creator_id, video_download_status)
  WHERE video_download_status <> 'skipped';

CREATE INDEX IF NOT EXISTS idx_parsed_media_image_status
  ON parsed_media (image_download_status)
  WHERE image_download_status <> 'skipped';
```

**Step 2: Execute migration on local Supabase**

Run:
```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/082_download_isolation.sql
```

**Step 3: Verify columns exist**

Run:
```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -c "SELECT video_download_status, music_download_status, cover_download_status, image_download_status FROM resources LIMIT 1;"
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -c "SELECT image_download_status, image_download_path FROM parsed_media LIMIT 1;"
```

**Step 4: Commit**

```bash
git add supabase/migrations/082_download_isolation.sql
git commit -m "feat(db): migration 082 — add per-user download status to resources"
```

---

## Task 2: Backend Schemas — Add New Fields

**Files:**
- Modify: `backend/app/schemas/media.py:19-98` (MediaBase)
- Modify: `backend/app/schemas/resources.py:17-25` (ResourceUpdate)

**Step 1: Update MediaBase schema**

In `backend/app/schemas/media.py`, add `image_download_status` and `image_download_path` to `MediaBase` (after line 96):

```python
    # Image download info (for image carousel content)
    image_download_status: Optional[DownloadStatus] = Field(
        None, description="Image download status"
    )
    image_download_path: Optional[str] = Field(None, description="Image download path")
```

**Step 2: Update ResourceUpdate schema**

In `backend/app/schemas/resources.py`, add download status fields to `ResourceUpdate` (after `is_trashed` field):

```python
    # Per-user download status
    video_download_status: Optional[str] = Field(None, description="User video download status")
    music_download_status: Optional[str] = Field(None, description="User music download status")
    cover_download_status: Optional[str] = Field(None, description="User cover download status")
    image_download_status: Optional[str] = Field(None, description="User image download status")
```

**Step 3: Verify backend starts**

Run:
```bash
cd backend && uv run python -c "from app.schemas.media import MediaBase; from app.schemas.resources import ResourceUpdate; print('OK')"
```

**Step 4: Commit**

```bash
git add backend/app/schemas/media.py backend/app/schemas/resources.py
git commit -m "feat(schemas): add image_download_status and per-user download status fields"
```

---

## Task 3: Backend Repositories — New Methods + Remove user_id

**Files:**
- Modify: `backend/app/repositories/resources_repository.py:38-116`
- Modify: `backend/app/repositories/media_repository.py:75-171`

**Step 1: Add ResourcesRepository methods for download status**

In `backend/app/repositories/resources_repository.py`, add after `get_resource_by_platform_id` (after line ~99):

```python
    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get user's resource for a specific media item."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("media_id", media_id)
                .eq("creator_id", creator_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource for media={media_id}, creator={creator_id}: {e}")
            return None

    async def update_download_status(
        self, resource_id: str, statuses: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Update one or more download status fields on a resource.

        Args:
            resource_id: Resource ID.
            statuses: Dict of status fields, e.g. {"video_download_status": "completed"}.
        """
        valid_fields = {
            "video_download_status", "music_download_status",
            "cover_download_status", "image_download_status",
        }
        data = {k: v for k, v in statuses.items() if k in valid_fields}
        if not data:
            return None
        return await self.update_resource(resource_id, data)
```

**Step 2: Remove user_id from MediaRepository queries**

In `backend/app/repositories/media_repository.py`:

**`get_by_platform_id` (line 75-100):** Remove `user_id` parameter. The method becomes:

```python
    async def get_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get parsed_media record by platform_id (global, no user filtering)."""
        client = await self._get_client()
        result = (
            await client.table("parsed_media")
            .select("*")
            .eq("platform_id", platform_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
```

**`update` (line 124-171):** Remove `user_id` parameter. The method becomes:

```python
    async def update(
        self, platform_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update parsed_media record by platform_id (global)."""
        try:
            # Handle enums
            for field in ("video_download_status", "music_download_status",
                          "cover_download_status", "image_download_status"):
                if field in data and isinstance(data[field], DownloadStatus):
                    data[field] = data[field].value

            if "published_at" in data and isinstance(data["published_at"], datetime):
                data["published_at"] = data["published_at"].isoformat()
            if "download_time" in data and isinstance(data["download_time"], datetime):
                data["download_time"] = data["download_time"].isoformat()

            data["updated_at"] = datetime.now().isoformat()

            table = await self._get_table()
            result = await table.update(data).eq("platform_id", platform_id).execute()
            logger.info(f"Updated parsed_media: {platform_id}")
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to update parsed_media: {e}")
            raise
```

**`delete` (line 173+):** Remove `user_id` parameter similarly.

**`mark_media_as_downloaded` (line ~250):** Remove `user_id` parameter.

**Step 3: Fix all callers that pass user_id to these methods**

Search for all call sites in the backend that pass `user_id=` to `repo.get_by_platform_id()`, `repo.update()`, `repo.delete()`, and `repo.mark_media_as_downloaded()`. Remove the `user_id` argument from each call.

Key locations:
- `media_service.py:save_metadata_only()` — line ~473
- `media_service.py:_create_resource_record()` — line ~390
- `media_router.py` — various endpoints
- `download_tasks.py:_do_douyin_download()` — DownloaderService calls (these pass user_id to DownloaderService, not repo directly — check if DownloaderService passes it through)

**Step 4: Verify backend starts**

Run:
```bash
cd backend && uv run python -c "from app.repositories.media_repository import MediaRepository; from app.repositories.resources_repository import ResourcesRepository; print('OK')"
```

**Step 5: Commit**

```bash
git add backend/app/repositories/media_repository.py backend/app/repositories/resources_repository.py
git commit -m "refactor(repos): remove user_id from MediaRepository, add download status to ResourcesRepository"
```

---

## Task 4: Backend MediaService — Rewrite save_metadata_only + Resource Creation

**Files:**
- Modify: `backend/app/services/media_service.py:439-573`

This is the most critical change. The method must now:
1. Create/update `parsed_media` as a **global** record (no user_id)
2. Create/update `resources` with **per-user** download statuses
3. Return `resource_id` for Celery task

**Step 1: Rewrite `save_metadata_only` method**

Replace `save_metadata_only` (lines 439-573) with:

```python
    @staticmethod
    async def save_metadata_only(platform_id: str, parsed_data: dict) -> dict:
        """Save parsed metadata and create/update user resource.

        Two-layer write:
        1. parsed_media — global content record (one per platform_id)
        2. resources — per-user record with download request statuses

        Returns dict with keys: success, message, platform_id, id (media_id),
        resource_id, dedup_hit.
        """
        try:
            repo = MediaRepository()
            resources_repo = ResourcesRepository()

            # Extract request flags (per-user, not stored in parsed_media)
            user_id = parsed_data.pop("user_id", None)
            need_download_video = parsed_data.pop("need_download_video", False)
            need_download_music = parsed_data.pop("need_download_music", False)
            need_download_cover = parsed_data.pop("need_download_cover", True)  # cover always true

            title = parsed_data.get("title", "")
            media_type = parsed_data.get("media_type", 0)
            is_image_type = str(media_type) in ("images", "image", "2", "68")

            # ── Dedup check: does ANY completed download exist? ──
            dedup_hit = False
            existing = await repo.get_by_platform_id(platform_id)
            if existing:
                if is_image_type:
                    dedup_hit = existing.get("image_download_status") == "completed"
                else:
                    dedup_hit = existing.get("video_download_status") == "completed"

            # ── Build parsed_media data dict ──
            schema = MediaCreate(**parsed_data)
            data_dict = schema.model_dump(exclude_unset=True)

            # Remove per-user fields that don't belong in global table
            for key in ("user_id", "need_download_video", "need_download_music",
                        "need_download_cover", "notes", "rating"):
                data_dict.pop(key, None)

            # Don't overwrite existing download statuses from global table
            for status_field in ("video_download_status", "music_download_status",
                                 "cover_download_status", "image_download_status"):
                data_dict.pop(status_field, None)

            # ── Write to parsed_media ──
            media_id = None
            if existing:
                media_id = existing.get("id")
                # Only update metadata, never overwrite download statuses/paths
                exclude_keys = {
                    "video_download_status", "music_download_status",
                    "cover_download_status", "image_download_status",
                    "download_path", "cover_download_path", "image_download_path",
                    "download_duration", "download_time",
                }
                update_data = {
                    k: v for k, v in data_dict.items()
                    if k not in exclude_keys and v is not None
                }
                if update_data:
                    await repo.update(platform_id, update_data)
                message = f"Media {platform_id} metadata updated"
            else:
                # New record: set initial global download statuses
                if is_image_type:
                    data_dict["image_download_status"] = "pending" if need_download_video else "skipped"
                    data_dict["video_download_status"] = "skipped"
                else:
                    data_dict["video_download_status"] = "pending" if need_download_video else "skipped"
                    data_dict["image_download_status"] = "skipped"
                data_dict["music_download_status"] = "pending" if need_download_music else "skipped"
                data_dict["cover_download_status"] = "pending"  # cover always downloads

                result = await repo.create(data_dict)
                media_id = result.get("id") if result else None
                message = f"Media {platform_id} metadata created"

            # ── Write to resources (per-user) ──
            resource_id = None
            if user_id and media_id:
                resource_id = await MediaService._ensure_user_resource(
                    resources_repo=resources_repo,
                    media_id=media_id,
                    user_id=user_id,
                    parsed_data=parsed_data,
                    need_download_video=need_download_video,
                    need_download_music=need_download_music,
                    need_download_cover=need_download_cover,
                    is_image_type=is_image_type,
                    dedup_hit=dedup_hit,
                    existing_media=existing,
                )

            logger.info(message)
            return {
                "success": True,
                "message": message,
                "platform_id": platform_id,
                "id": media_id,
                "resource_id": resource_id,
                "dedup_hit": dedup_hit,
            }

        except Exception as e:
            logger.error(f"Failed to save metadata: {str(e)}")
            return {"success": False, "message": f"Save failed: {str(e)}"}
```

**Step 2: Add `_ensure_user_resource` helper**

Add this new static method right after `save_metadata_only` in `media_service.py`:

```python
    @staticmethod
    async def _ensure_user_resource(
        resources_repo: "ResourcesRepository",
        media_id: str,
        user_id: str,
        parsed_data: dict,
        need_download_video: bool,
        need_download_music: bool,
        need_download_cover: bool,
        is_image_type: bool,
        dedup_hit: bool,
        existing_media: Optional[dict],
    ) -> Optional[str]:
        """Create or update user's resource record with download statuses.

        Returns resource_id.
        """
        existing_resource = await resources_repo.get_resource_by_media_id_and_creator(
            media_id, user_id
        )

        # Determine per-user statuses
        # If global file already exists (dedup_hit), user status = completed immediately
        if is_image_type:
            video_status = "skipped"
            image_status = "completed" if dedup_hit else ("pending" if need_download_video else "skipped")
        else:
            video_status = "completed" if dedup_hit else ("pending" if need_download_video else "skipped")
            image_status = "skipped"
        music_status = "pending" if need_download_music else "skipped"
        cover_status = "pending" if need_download_cover else "skipped"
        # If cover file already exists globally, mark completed
        if existing_media and existing_media.get("cover_download_status") == "completed":
            cover_status = "completed"

        if existing_resource:
            # Update only the statuses for newly requested types
            update_data = {}
            if need_download_video and existing_resource.get("video_download_status") == "skipped":
                update_data["video_download_status"] = video_status
            if need_download_video and is_image_type and existing_resource.get("image_download_status") == "skipped":
                update_data["image_download_status"] = image_status
            if need_download_music and existing_resource.get("music_download_status") == "skipped":
                update_data["music_download_status"] = music_status
            if need_download_cover and existing_resource.get("cover_download_status") == "skipped":
                update_data["cover_download_status"] = cover_status
            if update_data:
                await resources_repo.update_resource(existing_resource["id"], update_data)
            return existing_resource["id"]
        else:
            # Parse duration
            duration_seconds = None
            dur = parsed_data.get("duration")
            if dur:
                try:
                    parts = str(dur).split(":")
                    if len(parts) == 3:
                        duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    elif len(parts) == 2:
                        duration_seconds = int(parts[0]) * 60 + int(parts[1])
                    else:
                        duration_seconds = int(parts[0])
                except (ValueError, IndexError):
                    pass

            resource_data = {
                "creator_id": user_id,
                "media_id": media_id,
                "source_type": "web",
                "filename": parsed_data.get("title") or "Untitled",
                "file_type": parsed_data.get("media_type") or "video",
                "file_size_bytes": parsed_data.get("datasize_bytes"),
                "duration_seconds": duration_seconds,
                "resolution": parsed_data.get("resolution"),
                "cover_image_path": existing_media.get("cover_download_path") if existing_media else None,
                "file_path": existing_media.get("download_path") if existing_media and dedup_hit else None,
                "video_download_status": video_status,
                "music_download_status": music_status,
                "cover_download_status": cover_status,
                "image_download_status": image_status,
            }
            result = await resources_repo.create_resource(resource_data)
            return result.get("id") if result else None
```

**Step 3: Remove `_create_resource_record` method**

Delete `_create_resource_record` (lines 376-429) and `_create_resource_record_sync` (lines 431-436) — resource creation is now handled by `_ensure_user_resource` at parse time.

**Step 4: Verify backend starts**

Run:
```bash
cd backend && uv run uvicorn app.main:app --port 8081 &
sleep 3
kill %1
```

**Step 5: Commit**

```bash
git add backend/app/services/media_service.py
git commit -m "refactor(service): rewrite save_metadata_only for two-layer download isolation"
```

---

## Task 5: Backend Celery — Update Download Completion to Update Both Tables

**Files:**
- Modify: `backend/app/tasks/download_tasks.py:412-590`

The Celery task must now:
1. Accept `resource_id` parameter
2. Before downloading, check global cache (parsed_media.xxx_download_status == completed)
3. After download, update BOTH parsed_media (global status) AND resources (user status)

**Step 1: Update `download_unified_task` signature**

In `download_tasks.py`, update the function signature (line 413-422) to add `resource_id`:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_unified_task(
    self,
    platform_id: str,
    user_id: str,
    url: str = None,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: str = None,  # NEW: user's resource record ID
):
```

**Step 2: Add global cache check before download**

After TaskTracker initialization (after line ~481), add:

```python
        # ── Check global cache: skip download if file already on server ──
        if resource_id:
            from app.repositories.resources_repository import ResourcesRepository
            res_repo = ResourcesRepository()
            media_repo = MediaRepository()
            global_media = run_async(media_repo.get_by_platform_id(platform_id))

            if global_media:
                cache_updates = {}
                if download_video and global_media.get("video_download_status") == "completed":
                    cache_updates["video_download_status"] = "completed"
                if download_music and global_media.get("music_download_status") == "completed":
                    cache_updates["music_download_status"] = "completed"
                if download_cover and global_media.get("cover_download_status") == "completed":
                    cache_updates["cover_download_status"] = "completed"
                if download_video and int(media_type) in (2, 68) and global_media.get("image_download_status") == "completed":
                    cache_updates["image_download_status"] = "completed"

                if cache_updates:
                    run_async(res_repo.update_download_status(resource_id, cache_updates))

                # If ALL requested types are cached, skip download entirely
                all_cached = True
                if download_video:
                    if int(media_type) in (2, 68):
                        all_cached = all_cached and global_media.get("image_download_status") == "completed"
                    else:
                        all_cached = all_cached and global_media.get("video_download_status") == "completed"
                if download_music:
                    all_cached = all_cached and global_media.get("music_download_status") == "completed"
                if download_cover:
                    all_cached = all_cached and global_media.get("cover_download_status") == "completed"

                if all_cached:
                    logger.info(f"[Download] All requested types cached for {platform_id}, skipping download")
                    tracker.complete()
                    if unified_task_id:
                        run_async(tracker_unified.complete(unified_task_id))
                    # Update resource file paths from global media
                    path_updates = {}
                    if global_media.get("download_path"):
                        path_updates["file_path"] = global_media["download_path"]
                    if global_media.get("cover_download_path"):
                        path_updates["cover_image_path"] = global_media["cover_download_path"]
                    if path_updates:
                        run_async(res_repo.update_resource(resource_id, path_updates))
                    return {"status": "success", "platform_id": platform_id, "cache_hit": True}
```

**Step 3: After download, update resources status**

In the success block (around line 505-533), after `tracker.complete()`, add resource status update:

```python
        # ── Update user resource download statuses ──
        if resource_id:
            from app.repositories.resources_repository import ResourcesRepository
            res_repo = ResourcesRepository()
            status_updates = {}
            path_updates = {}

            if download_video:
                if int(media_type) in (2, 68):
                    status_updates["image_download_status"] = "completed"
                else:
                    status_updates["video_download_status"] = "completed"
            if download_music:
                status_updates["music_download_status"] = "completed"
            if download_cover:
                status_updates["cover_download_status"] = "completed"

            # Also update file paths on resource
            fresh_media = run_async(MediaRepository().get_by_platform_id(platform_id))
            if fresh_media:
                if fresh_media.get("download_path"):
                    path_updates["file_path"] = fresh_media["download_path"]
                if fresh_media.get("cover_download_path"):
                    path_updates["cover_image_path"] = fresh_media["cover_download_path"]

            run_async(res_repo.update_resource(resource_id, {**status_updates, **path_updates}))
```

**Step 4: On failure, update resource status to 'failed'**

In the failure block (around line 547-590), add:

```python
        if resource_id:
            try:
                from app.repositories.resources_repository import ResourcesRepository
                res_repo = ResourcesRepository()
                fail_updates = {}
                if download_video:
                    if int(media_type) in (2, 68):
                        fail_updates["image_download_status"] = "failed"
                    else:
                        fail_updates["video_download_status"] = "failed"
                if download_music:
                    fail_updates["music_download_status"] = "failed"
                if download_cover:
                    fail_updates["cover_download_status"] = "failed"
                run_async(res_repo.update_download_status(resource_id, fail_updates))
            except Exception:
                pass
```

**Step 5: Remove old `_create_resource_record` call**

Delete lines 526-533 (the "Auto-create resource record" block). Resources are now created in `save_metadata_only`.

**Step 6: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "feat(celery): update download task for two-layer status with global cache check"
```

---

## Task 6: Backend Router — Wire resource_id into Celery Task

**Files:**
- Modify: `backend/app/api/media_router.py:270-365`

**Step 1: Pass resource_id from save_result to Celery**

In `media_router.py` around line 279-321, update the flow:

```python
        # Save metadata to database (creates parsed_media + resources)
        save_result = await MediaService.save_metadata_only(platform_id, parsed_data)
        if not save_result.get("success"):
            raise HTTPException(status_code=500, detail=save_result.get("message"))

        resource_id = save_result.get("resource_id")  # NEW
        dedup_hit = save_result.get("dedup_hit", False)
        need_download_video = request.video_bool and not dedup_hit
        need_download = need_download_video or request.music_bool or request.cover_bool
```

Remove the old dedup `_create_resource_record` block (lines 294-300).

Update Celery dispatch (line 312-321) to pass `resource_id`:

```python
                download_task = download_unified_task.delay(
                    platform_id=platform_id,
                    user_id=auth.user_id,
                    download_video=need_download_video,
                    download_music=request.music_bool,
                    download_cover=True,  # cover always true
                    media_type=media_type,
                    video_title=video_title[:50] if video_title else "undefined",
                    resource_id=resource_id,  # NEW
                )
```

Update FastAPI background tasks fallback (lines 328-364) similarly to pass `resource_id`.

**Step 2: Remove user_id from parsed_data**

At line 276, remove `parsed_data["user_id"] = auth.user_id`. Instead, pass user_id separately:

```python
        # user_id is passed inside parsed_data for save_metadata_only to extract
        parsed_data["user_id"] = auth.user_id
```

(Keep this line — `save_metadata_only` pops it out internally.)

**Step 3: Force cover_bool = True**

At the top of the endpoint handler, force cover to always be true:

```python
        # Cover is always downloaded (mandatory)
        request.cover_bool = True
```

**Step 4: Update the yt-dlp handler similarly**

In `_handle_ytdlp_fetch()` (around line 1312-1320), pass `resource_id` to `download_unified_task.delay()`.

**Step 5: Update retry endpoint**

In the retry endpoint (`POST /videos/retry/{platform_id}`, around line 936-1025):
- Look up the user's resource by platform_id + user_id
- Pass resource_id to Celery
- Update resource statuses to `pending`

**Step 6: Verify backend starts**

Run:
```bash
cd backend && uv run uvicorn app.main:app --port 8081 &
sleep 3
kill %1
```

**Step 7: Commit**

```bash
git add backend/app/api/media_router.py
git commit -m "feat(router): wire resource_id through fetch/retry endpoints, force cover=true"
```

---

## Task 7: Backend — Update List/Detail Endpoints for Resource-Based Queries

**Files:**
- Modify: `backend/app/api/media_router.py` (list endpoint)
- Modify: `backend/app/repositories/media_repository.py` (list method)

**Step 1: Update the videos list endpoint**

The `GET /api/v1/videos` endpoint currently filters by `user_id`. Change it to join through `resources`:

```sql
-- Old: SELECT * FROM parsed_media WHERE user_id = ?
-- New: SELECT pm.*, r.id as resource_id,
--             r.video_download_status as user_video_status, ...
--      FROM resources r
--      JOIN parsed_media pm ON r.media_id = pm.id
--      WHERE r.creator_id = ? AND r.source_type = 'web'
```

Add a new repository method `get_user_media_list` in `media_repository.py` that performs this join using Supabase's `select("*, resources!inner(id, video_download_status, ...)")` syntax or via RPC.

**Step 2: Update the video detail endpoint**

`GET /api/v1/videos/{id}` should also return user's download statuses. After fetching parsed_media, also fetch the user's resource record and merge statuses into the response.

**Step 3: Verify list endpoint**

Run:
```bash
cd backend && uv run uvicorn app.main:app --reload --port 8081
# In another terminal:
curl -s http://localhost:8081/api/v1/videos -H "Authorization: Bearer <token>" | python -m json.tool | head -30
```

**Step 4: Commit**

```bash
git add backend/app/api/media_router.py backend/app/repositories/media_repository.py
git commit -m "refactor(api): videos list/detail query through resources for user isolation"
```

---

## Task 8: Frontend — Update Types and Services

**Files:**
- Modify: `frontend/types.ts:2-93` (ParsedMedia), `289-318` (Resource)
- Modify: `frontend/services/parserService.ts:92-115`
- Modify: `frontend/services/resourceService.ts`

**Step 1: Update Resource interface**

In `frontend/types.ts`, add download status fields to `Resource` (after line 310):

```typescript
  // Per-user download status
  video_download_status: DownloadStatus | null;
  music_download_status: DownloadStatus | null;
  cover_download_status: DownloadStatus | null;
  image_download_status: DownloadStatus | null;
```

**Step 2: Update ParsedMedia interface**

In `frontend/types.ts`, mark `user_id` as deprecated and add `resource_id`:

```typescript
  /** @deprecated — parsed_media is now global, user_id will be removed */
  user_id?: string;
```

Remove `need_download_video`, `need_download_music`, `need_download_cover` (lines 42, 49, 54).

Add `image_download_status`:
```typescript
  image_download_status?: DownloadStatus;
  image_download_path?: string;
```

**Step 3: Update parserService — remove cover_bool**

In `frontend/services/parserService.ts:parseShareLink()` (line ~108), force `cover_bool` to always be `true`:

```typescript
    body: JSON.stringify({
      url: text,
      video_bool: options?.video_bool ?? true,
      music_bool: options?.music_bool ?? false,
      cover_bool: true,  // cover always downloaded
    }),
```

**Step 4: Add fetchMediaForResource to resourceService**

In `frontend/services/resourceService.ts`, add:

```typescript
/**
 * Trigger a fetch (download) for a specific media type on a resource.
 * Creates a backend task and returns the task ID.
 */
export async function fetchMediaForResource(
  resourceId: string,
  mediaType: 'video' | 'audio' | 'images',
): Promise<{ task_id?: string }> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/fetch`, {
    method: 'POST',
    headers: { ...await getAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ media_type: mediaType }),
  });
  if (!response.ok) throw new Error('Failed to fetch media');
  return response.json();
}
```

**Step 5: Verify frontend builds**

Run:
```bash
cd frontend && npm run build
```

**Step 6: Commit**

```bash
git add frontend/types.ts frontend/services/parserService.ts frontend/services/resourceService.ts
git commit -m "feat(frontend): update types and services for per-user download status"
```

---

## Task 9: Frontend — PlayerPage Download Menu

**Files:**
- Modify: `frontend/pages/PlayerPage.tsx:251-299`

**Step 1: Read download statuses from resource instead of parsed_media**

The PlayerPage needs access to the user's resource record. Currently it reads `video.video_download_status` from parsed_media. After the change, it should read from the resource.

In the download menu section (lines 251-299), the `onServer` check should use the resource's download status:

```typescript
{(() => {
  // User-level download status (from resource, not parsed_media)
  const userVideoStatus = resource?.video_download_status?.toLowerCase();
  const userAudioStatus = resource?.music_download_status?.toLowerCase();
  const userCoverStatus = resource?.cover_download_status?.toLowerCase();
  const userImageStatus = resource?.image_download_status?.toLowerCase();

  const isCompleted = (s?: string) => s === 'completed';
  const isPending = (s?: string) => s === 'pending' || s === 'downloading';
  const isFailed = (s?: string) => s === 'failed';
  const isSkipped = (s?: string) => s === 'skipped' || !s;

  // Determine which content type applies
  const isImageContent = video.media_type === 'images' || video.media_type === 'image';
  const primaryStatus = isImageContent ? userImageStatus : userVideoStatus;
```

Then use `primaryStatus`, `userAudioStatus`, `userCoverStatus` to show the correct buttons:
- `isCompleted` → "Download X"
- `isPending` → "Downloading..."
- `isFailed` → "Failed - Retry"
- `isSkipped` → "Fetch X"

**Step 2: Pass resource data to PlayerPage**

The PlayerPage needs the user's resource record. This can come from:
- The API response (if the list endpoint returns resource data alongside parsed_media)
- A separate API call to fetch the resource by media_id

Update the PlayerPage to fetch/receive the resource data. The simplest approach: the video detail API returns `resource_id` and resource download statuses as part of the response.

**Step 3: Fetch buttons trigger resource-level fetch**

When user clicks "Fetch Video":

```typescript
const handleFetchMedia = async (type: 'video' | 'audio' | 'images') => {
  if (!resource?.id) return;
  try {
    await fetchMediaForResource(resource.id, type);
    // Optimistic UI update
    setResource(prev => prev ? {
      ...prev,
      [`${type === 'audio' ? 'music' : type}_download_status`]: 'PENDING'
    } : null);
  } catch (err) {
    console.error('Fetch failed:', err);
  }
};
```

**Step 4: Verify frontend builds**

Run:
```bash
cd frontend && npm run build
```

**Step 5: Commit**

```bash
git add frontend/pages/PlayerPage.tsx
git commit -m "feat(PlayerPage): download menu reads per-user resource status"
```

---

## Task 10: Frontend — ParserPage Query Through Resources

**Files:**
- Modify: `frontend/pages/ParserPage.tsx` or the library hook it uses
- Modify: `frontend/services/parserService.ts` (fetchVideosFromApi)

**Step 1: Update data fetching**

The Parser list currently fetches from `GET /api/v1/videos` which was filtered by user_id. After Task 7, this endpoint returns data joined through resources. The frontend just needs to use the `user_video_status` etc. fields from the response.

If the backend response format changed (e.g., nested `resource` object), update the frontend to read from the correct path.

**Step 2: Display user-level statuses in media cards**

Update any MediaCard or CompactMediaCard components that show download status icons/badges to read from the resource-level status fields rather than parsed_media fields.

**Step 3: Remove cover option from Parser form**

In the ParserPage download options, remove the cover checkbox (cover is always downloaded):

```tsx
{/* Remove cover_bool checkbox - cover is always downloaded */}
```

**Step 4: Verify frontend builds**

Run:
```bash
cd frontend && npm run build
```

**Step 5: Commit**

```bash
git add frontend/
git commit -m "feat(ParserPage): display per-user download status, remove cover option"
```

---

## Task 11: Database Migration 083 — Cleanup Old Columns

**Files:**
- Create: `supabase/migrations/083_cleanup_parsed_media_user_fields.sql`

**Only do this after all backend/frontend code is deployed and verified.**

**Step 1: Write cleanup migration**

```sql
-- 083_cleanup_parsed_media_user_fields.sql
-- Remove user-specific columns from parsed_media (now global table).
-- Per-user data is in resources table.

-- Drop indexes that reference user_id
DROP INDEX IF EXISTS idx_parsed_media_user_id;
DROP INDEX IF EXISTS idx_parsed_media_user_created;
DROP INDEX IF EXISTS idx_parsed_media_datasize_bytes;

-- Drop FK constraint
ALTER TABLE parsed_media DROP CONSTRAINT IF EXISTS douyin_videos_user_id_fkey;

-- Drop columns
ALTER TABLE parsed_media DROP COLUMN IF EXISTS user_id;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_video;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_music;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_cover;

-- Update RLS policy for UPDATE (remove user_id check)
DROP POLICY IF EXISTS "Users can update own videos or admin" ON parsed_media;
CREATE POLICY "Authenticated users can update videos" ON parsed_media
  FOR UPDATE
  USING (auth.role() = 'authenticated');
```

**Step 2: Execute migration**

Run:
```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/083_cleanup_parsed_media_user_fields.sql
```

**Step 3: Verify no references remain**

Run:
```bash
cd backend && grep -r "user_id" app/repositories/media_repository.py app/services/media_service.py | grep -v "creator_id"
```
Expected: no matches (or only comments).

**Step 4: Commit**

```bash
git add supabase/migrations/083_cleanup_parsed_media_user_fields.sql
git commit -m "feat(db): migration 083 — remove user_id from parsed_media (now global)"
```

---

## Verification Checklist

After all tasks, verify end-to-end:

1. **Parser Page**: Parse a new URL → check resources table has new record with correct statuses
2. **Same URL, different user**: Parse same URL with test user B → check resources table has SECOND record, parsed_media stays unchanged
3. **PlayerPage download menu**: Video with `resource.video_download_status = skipped` → shows "Fetch Video"
4. **Fetch Video**: Click Fetch → creates unified_task → status changes to pending → completed
5. **Download Video**: Video with `resource.video_download_status = completed` → shows "Download Video" → downloads file
6. **Global cache**: User B fetches same video User A already downloaded → instant completion (no re-download)
7. **Music fetch**: Click "Fetch Audio" → only music status changes, video/cover untouched
8. **Image content**: Parse image carousel → image_download_status used instead of video

```bash
# Quick DB verification
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "
  SELECT r.id, r.creator_id, r.video_download_status, r.music_download_status,
         r.cover_download_status, pm.video_download_status as global_video
  FROM resources r
  JOIN parsed_media pm ON r.media_id = pm.id
  WHERE r.media_id IS NOT NULL
  LIMIT 5;
"
```
