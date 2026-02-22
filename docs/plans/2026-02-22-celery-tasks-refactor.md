# Celery Tasks Refactoring Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate code duplication, fix concurrency bugs, and improve maintainability of the `backend/app/tasks/` module.

**Architecture:** Extract shared `run_async()` to a single utility module, fix the Celery retry / TaskTracker state conflict, delete legacy dead-code tasks, and split the monolithic `parse_single_link_task` into focused sub-functions.

**Tech Stack:** Python 3.12, Celery 5, asyncio, Supabase (async client), loguru

---

## Problem Summary

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1 | RED | `run_async()` duplicated verbatim in 6 files | `download_tasks.py:21`, `parse_tasks.py:19`, `scheduled_tasks.py:20`, `transcode_tasks.py:16`, `ai_tasks.py:19`, `analysis_tasks.py:17` |
| 2 | RED | `run_async()` uses deprecated `get_event_loop()` — risks deadlock + resource leak | All 6 copies |
| 3 | YELLOW | 4 legacy download tasks (~200 lines) superseded by `download_unified_task` | `download_tasks.py:903-1099` |
| 4 | YELLOW | Celery retry re-enqueues task after TaskTracker already marked FAILED | `download_tasks.py:841-875` |
| 5 | YELLOW | `parse_single_link_task` is 265 lines with 9 responsibilities | `parse_tasks.py:35-299` |

---

## Task 1: Extract `run_async()` to shared utility

**Files:**
- Create: `backend/app/tasks/utils.py`
- Modify: `backend/app/tasks/download_tasks.py:21-36` — delete local `run_async`, add import
- Modify: `backend/app/tasks/parse_tasks.py:19-32` — delete local `run_async`, add import
- Modify: `backend/app/tasks/scheduled_tasks.py:20-33` — delete local `run_async`, add import
- Modify: `backend/app/tasks/transcode_tasks.py:16-28` — delete local `run_async`, add import
- Modify: `backend/app/tasks/ai_tasks.py:19-32` — delete local `run_async`, add import
- Modify: `backend/app/tasks/analysis_tasks.py:17-30` — delete local `run_async`, add import

**Step 1: Create `backend/app/tasks/utils.py`**

```python
"""Shared utilities for Celery tasks."""

import asyncio


def run_async(coro):
    """Run an async coroutine in a synchronous Celery worker context.

    Uses asyncio.run() which creates a fresh event loop per call.
    This is safe for Celery workers (each task runs in its own thread/process).
    """
    return asyncio.run(coro)
```

Why `asyncio.run()` instead of the old `get_event_loop()` dance:
- Celery workers run tasks in separate threads/processes — there is no pre-existing event loop.
- `asyncio.get_event_loop()` is deprecated in Python 3.10+ for this use case.
- The `ThreadPoolExecutor` fallback in the old code spawns a thread to call `asyncio.run()` anyway — just call it directly.
- `asyncio.run()` creates a clean loop, runs the coroutine, and tears it down. No leak, no deadlock.

**Step 2: Replace in all 6 files**

For each file, delete the local `run_async` function and add at the top:

```python
from app.tasks.utils import run_async
```

Files and exact lines to delete:

| File | Lines to delete |
|------|----------------|
| `download_tasks.py` | L21-36 (the `def run_async` block) |
| `parse_tasks.py` | L19-32 |
| `scheduled_tasks.py` | L20-33 |
| `transcode_tasks.py` | L16-28 |
| `ai_tasks.py` | L19-32 |
| `analysis_tasks.py` | L17-30 |

**Step 3: Verify**

Run: `cd backend && uv run python -c "from app.tasks.utils import run_async; print('OK')"`
Expected: `OK`

Run: `cd backend && uv run python -c "from app.tasks.download_tasks import download_unified_task; print('OK')"`
Expected: `OK`

Run: `cd backend && uv run python -c "from app.tasks.parse_tasks import parse_single_link_task; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/utils.py backend/app/tasks/download_tasks.py backend/app/tasks/parse_tasks.py backend/app/tasks/scheduled_tasks.py backend/app/tasks/transcode_tasks.py backend/app/tasks/ai_tasks.py backend/app/tasks/analysis_tasks.py
git commit -m "refactor: extract run_async() to shared tasks/utils.py

Replace 6 duplicate copies with a single import.
Simplify implementation to use asyncio.run() directly."
```

---

## Task 2: Delete legacy download tasks

**Files:**
- Modify: `backend/app/tasks/download_tasks.py:903-1099` — delete 4 legacy tasks

**Context:** These 4 tasks (`download_video_task`, `download_images_task`, `download_music_task`, `download_cover_task`) were superseded by `download_unified_task` (L563-896). The aliases at L898-900 confirm the migration is complete. No call sites reference these individual tasks.

**Step 1: Verify no call sites**

Run: `cd backend && grep -rn "download_video_task\|download_images_task\|download_music_task\|download_cover_task" app/ --include="*.py" | grep -v "download_tasks.py"`
Expected: No matches (or only old comments/docstrings)

If any call sites are found, update them to use `download_unified_task` instead.

**Step 2: Delete the 4 legacy tasks**

Delete lines 903-1099 from `download_tasks.py`. These are:
- `download_video_task` (L903-952)
- `download_images_task` (L955-1000)
- `download_music_task` (L1003-1049)
- `download_cover_task` (L1052-1099)

Keep the aliases at L898-900:
```python
download_media_task = download_unified_task
download_ytdlp_task = download_unified_task
```

**Step 3: Verify imports still work**

Run: `cd backend && uv run python -c "from app.tasks.download_tasks import download_unified_task, download_media_task; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "refactor: remove 4 legacy download tasks (~200 lines)

download_video_task, download_images_task, download_music_task,
download_cover_task are all superseded by download_unified_task.
Backward-compatible aliases (download_media_task, download_ytdlp_task)
remain in place."
```

---

## Task 3: Fix Celery retry / TaskTracker state conflict

**Files:**
- Modify: `backend/app/tasks/download_tasks.py` — `download_unified_task` error handler (L833-895)

**Problem:** When an exception occurs in `download_unified_task`:
1. L841: `tracker.failed(error_msg)` — marks Redis status as FAILED
2. L844-848: `tracker_unified.fail(unified_task_id, error_msg)` — marks Supabase `unified_tasks` as FAILED
3. L870-875: `self.retry(exc=e)` — Celery re-enqueues the task for retry

The frontend sees FAILED status in Task Center, but Celery is actually retrying. When the retry succeeds, the task stays "FAILED" in Supabase because the retried task creates a NEW `unified_task_id` (L620-627).

**Solution:** Only mark as FAILED after max retries are exhausted. During retries, mark as RETRYING.

**Step 1: Update the error handler**

Replace the except block (L833-895) with:

```python
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"[Download/{strategy}] Failed: {platform_id}, error: {error_msg}"
        )

        # Update user resource status to failed
        if resource_id:
            try:
                from app.repositories.resources_repository import ResourcesRepository as _RR3
                _res_repo3 = _RR3()
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
                run_async(_res_repo3.update_download_status(resource_id, fail_updates))
            except Exception:
                pass

        # Decide: retry or give up
        can_retry = self.request.retries < self.max_retries
        if can_retry:
            countdown = 30 * (2 ** self.request.retries)
            logger.info(
                f"[Download/{strategy}] Retry {self.request.retries + 1}/{self.max_retries} "
                f"for {platform_id} in {countdown}s"
            )
            # Mark as retrying (not failed) so frontend shows correct status
            if unified_task_id:
                try:
                    run_async(tracker_unified.update_progress(
                        unified_task_id,
                        progress=0,
                        subtitle=f"Retrying ({self.request.retries + 1}/{self.max_retries})...",
                    ))
                except Exception:
                    pass
            raise self.retry(exc=e, countdown=countdown)

        # Max retries exhausted — now mark as truly failed
        try:
            tracker.failed(error_msg)
        except Exception:
            pass
        if unified_task_id:
            try:
                run_async(tracker_unified.fail(unified_task_id, error_msg))
            except Exception:
                pass

        # Log failure
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download failed ({strategy}): {video_title[:30]}...",
                status="error",
                aweme_id=platform_id,
                details={"error": error_msg[:200], "retry_count": self.request.retries},
            )
        )

        logger.error(f"[Download/{strategy}] Max retries reached for {platform_id}")
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": error_msg,
            "retry_count": self.request.retries,
        }
```

Key changes:
- Moved `tracker.failed()` and `tracker_unified.fail()` to AFTER the retry check
- Added `tracker_unified.update_progress()` with "Retrying..." subtitle during retries
- Frontend now sees "Retrying (1/3)..." instead of prematurely showing "Failed"

**Step 2: Check if `update_progress` exists on TaskTracker**

Run: `cd backend && grep -n "def update_progress\|async def update_progress" app/services/task_tracker.py`

If not found, need to add it or use an alternative method (e.g., `tracker_unified.start()` with subtitle update).

**Step 3: Verify**

Run: `cd backend && uv run python -c "from app.tasks.download_tasks import download_unified_task; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "fix: only mark TaskTracker as FAILED after max retries

Previously, TaskTracker was marked FAILED before Celery retry,
causing frontend to show wrong status. Now shows 'Retrying...'
during retries and only marks FAILED when all retries exhausted."
```

---

## Task 4: Split `parse_single_link_task` into focused sub-functions

**Files:**
- Modify: `backend/app/tasks/parse_tasks.py:35-299`

**Problem:** `parse_single_link_task` is 265 lines with 9 responsibilities:
1. URL extraction/validation (L66-74)
2. Fetch video data from platform (L82-89)
3. Parse metadata (L92-107)
4. Validate + prepare DB data (L124-144)
5. Save/update to database (L146-153)
6. Auto-tagging (L155-188)
7. Dispatch download task (L190-216)
8. Trigger L1 analysis (L218-239)
9. Log user action + build response (L241-279)

**Solution:** Extract sub-functions. Keep the orchestration in `parse_single_link_task` but move logic into private helpers.

**Step 1: Extract helpers**

Add these helper functions BEFORE `parse_single_link_task`:

```python
def _extract_url(url: str) -> str:
    """Extract and validate URL. Raises ValueError on failure."""
    valid_urls = Utils.extract_valid_url(url)
    return valid_urls[0]


def _fetch_and_parse(valid_url: str, video_bool: bool, music_bool: bool,
                     cover_bool: bool, categories: str = None) -> tuple[dict, dict]:
    """Fetch video data from platform and parse metadata.

    Returns:
        (aweme_detail, parsed_data) tuple
    Raises:
        RuntimeError if fetch or parse fails
    """
    from app.services.douyin_analysis import DouyinAnalysis
    from app.services.douyin_parser import DouyinParser

    aweme_detail = run_async(DouyinAnalysis.fetch_one_video(valid_url))
    if not aweme_detail:
        raise RuntimeError("Cannot fetch video info")

    parsed_data = run_async(
        DouyinParser.parse_aweme_detail(
            aweme_detail=aweme_detail,
            valid_url=valid_url,
            download_video=video_bool,
            download_music=music_bool,
            download_cover=cover_bool,
            categories=categories,
        )
    )
    if not parsed_data:
        raise RuntimeError("Parse failed")

    return aweme_detail, parsed_data


def _save_media_to_db(parsed_data: dict, platform_id: str,
                      video_bool: bool, music_bool: bool) -> dict | None:
    """Save or update parsed media in database. Returns saved record."""
    from app.core.enums import DownloadStatus
    from app.repositories.media_repository import MediaRepository
    from app.schemas.media import MediaCreate

    repo = MediaRepository()
    existing = run_async(repo.get_by_platform_id(platform_id))

    try:
        video_data = MediaCreate(**parsed_data)
        data_dict = video_data.model_dump()
    except Exception as e:
        logger.error(f"Data validation failed: {e}")
        return None

    data_dict["video_download_status"] = (
        DownloadStatus.PENDING.value if video_bool else DownloadStatus.SKIPPED.value
    )
    data_dict["music_download_status"] = (
        DownloadStatus.PENDING.value if music_bool else DownloadStatus.SKIPPED.value
    )

    if existing:
        saved = run_async(repo.update(platform_id, data_dict))
        logger.info(f"[Parse] Updated metadata: {platform_id}")
    else:
        saved = run_async(repo.create(data_dict))
        logger.info(f"[Parse] Created metadata: {platform_id}")
    return saved


def _auto_tag_media(video_db_id, platform_id: str, aweme_detail: dict,
                    title: str, description: str):
    """Apply auto-tagging based on content classification. Non-blocking."""
    try:
        original_tags = []
        text_extra = aweme_detail.get("text_extra", [])
        if text_extra:
            original_tags = [
                tag.get("hashtag_name", "")
                for tag in text_extra
                if tag.get("hashtag_name")
            ]

        added_tags = run_async(
            ClassificationService.auto_tag_media(
                media_id=video_db_id,
                title=title or "",
                description=description,
                original_tags=original_tags,
            )
        )
        if added_tags:
            logger.info(f"[Parse] Auto-tagged {platform_id} with {len(added_tags)} tags")
    except Exception as e:
        logger.warning(f"[Parse] Auto-tagging failed for {platform_id}: {e}")


def _dispatch_download(platform_id: str, user_id: str,
                       video_bool: bool, music_bool: bool, cover_bool: bool,
                       media_type: int, video_title: str) -> str | None:
    """Dispatch download task. Returns download_task_id or None."""
    from app.services.system_monitor_service import check_worker_ready
    from app.tasks.download_tasks import download_unified_task

    ready, err_msg = check_worker_ready()
    if not ready:
        logger.error(f"[Parse] Cannot dispatch download: {err_msg}")
        raise RuntimeError(f"Download worker not ready: {err_msg}")

    download_task = download_unified_task.delay(
        platform_id=platform_id,
        user_id=user_id,
        download_video=video_bool,
        download_music=music_bool,
        download_cover=cover_bool,
        media_type=media_type,
        video_title=video_title,
    )
    logger.info(f"[Parse] Download task dispatched: {download_task.id}")
    return download_task.id


def _trigger_l1_analysis(video_db_id, platform_id: str,
                         parsed_data: dict, video_title: str):
    """Trigger L1 cover analysis. Non-blocking."""
    try:
        cover_url = (
            parsed_data.get("cover_urls", [None])[0]
            if parsed_data.get("cover_urls")
            else None
        )
        if video_db_id and cover_url:
            from app.tasks.analysis_tasks import analyze_video_l1_task
            analyze_video_l1_task.delay(
                media_id=video_db_id,
                cover_url=cover_url,
                title=video_title or "",
                description=parsed_data.get("description", ""),
            )
            logger.info(f"[Parse] Triggered L1 analysis for {platform_id}")
    except Exception as e:
        logger.warning(f"[Parse] Failed to trigger L1 analysis for {platform_id}: {e}")
```

**Step 2: Rewrite `parse_single_link_task` as orchestrator**

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def parse_single_link_task(
    self,
    url: str,
    user_id: str,
    video_bool: bool = True,
    music_bool: bool = False,
    cover_bool: bool = True,
    categories: str = None,
):
    """Parse metadata for a video link, save to DB, dispatch download + analysis."""
    logger.info(f"[Parse] Starting: {url[:50]}...")

    try:
        # 1. Validate URL
        try:
            valid_url = _extract_url(url)
        except ValueError as e:
            return {"status": "failed", "url": url, "error": f"Invalid URL: {e}"}

        # 2. Fetch + parse
        try:
            aweme_detail, parsed_data = _fetch_and_parse(
                valid_url, video_bool, music_bool, cover_bool, categories
            )
        except RuntimeError as e:
            raise self.retry(
                exc=e, countdown=30 * (2 ** self.request.retries)
            )

        platform_id = parsed_data.get("platform_id")
        media_type = parsed_data.get("media_type", 0)
        video_title = parsed_data.get("title", "undefined")
        parsed_data["user_id"] = user_id

        # 3. Save to database
        saved_video = _save_media_to_db(parsed_data, platform_id, video_bool, music_bool)
        if not saved_video:
            return {"status": "failed", "url": valid_url, "error": "Data validation failed"}

        video_db_id = saved_video.get("id")

        # 4. Auto-tag (non-blocking)
        if video_db_id:
            _auto_tag_media(video_db_id, platform_id, aweme_detail,
                           video_title, parsed_data.get("description", ""))

        # 5. Dispatch download
        download_task_id = None
        if video_bool or music_bool or cover_bool:
            download_task_id = _dispatch_download(
                platform_id, user_id, video_bool, music_bool, cover_bool,
                media_type, video_title,
            )

        # 6. Trigger L1 analysis (non-blocking)
        if video_db_id:
            _trigger_l1_analysis(video_db_id, platform_id, parsed_data, video_title)

        # 7. Log + respond
        run_async(log_user_action(
            user_id=user_id, action="fetch",
            message=f"{video_title[:20]}...: Metadata parsed",
            status="success", aweme_id=platform_id,
            details={"media_type": media_type, "platform": "douyin"},
        ))

        logger.success(f"[Parse] Complete: {platform_id}")
        return {
            "status": "success",
            "url": valid_url,
            "platform_id": platform_id,
            "download_task_id": download_task_id,
            "metadata": {
                "platform_id": platform_id,
                "title": parsed_data.get("title"),
                "author": parsed_data.get("author"),
                "duration": parsed_data.get("duration"),
                "media_type": media_type,
                "published_at": parsed_data.get("published_at"),
                "statistics": {
                    "likes": parsed_data.get("like_count", 0),
                    "comments": parsed_data.get("comment_count", 0),
                    "shares": parsed_data.get("share_count", 0),
                    "collects": parsed_data.get("favorite_count", 0),
                },
                "cover_urls": parsed_data.get("cover_urls", []),
                "description": parsed_data.get("description"),
            },
        }

    except Exception as e:
        logger.error(f"[Parse] Task error: {url}, error: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        run_async(log_user_action(
            user_id=user_id, action="fetch",
            message=f"Parse failed: {url[:30]}...",
            status="error", details={"error": str(e)[:200]},
        ))
        return {"status": "failed", "url": url, "error": str(e)}
```

**Step 3: Verify**

Run: `cd backend && uv run python -c "from app.tasks.parse_tasks import parse_single_link_task; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/parse_tasks.py
git commit -m "refactor: split parse_single_link_task into focused sub-functions

Extract 6 helper functions from the 265-line monolith:
- _extract_url: URL validation
- _fetch_and_parse: platform fetch + metadata parse
- _save_media_to_db: database save/update
- _auto_tag_media: auto-tagging (non-blocking)
- _dispatch_download: download task dispatch
- _trigger_l1_analysis: L1 analysis chain (non-blocking)

Main function reduced to ~60 lines of clean orchestration."
```

---

## Task 5: Final verification + restart services

**Step 1: Import check for all task modules**

Run:
```bash
cd backend && uv run python -c "
from app.tasks.download_tasks import download_unified_task
from app.tasks.parse_tasks import parse_single_link_task
from app.tasks.scheduled_tasks import cleanup_temp_files
from app.tasks.transcode_tasks import transcode_to_hls
from app.tasks.ai_tasks import run_async
from app.tasks.analysis_tasks import analyze_video_l1_task
print('All task imports OK')
"
```

Expected: `All task imports OK`

**Step 2: Start backend + Celery worker**

```bash
# Terminal 1: Backend
cd backend && uv run uvicorn app.main:app --reload --port 8081

# Terminal 2: Celery Worker
cd backend && uv run celery -A app.celery_app worker --loglevel=info
```

Verify worker discovers all tasks (check log for registered task names).

**Step 3: Smoke test**

Parse a test link from the frontend to verify the full pipeline (parse → download → L1 analysis) still works end-to-end.

---

## Summary of changes

| Metric | Before | After |
|--------|--------|-------|
| `run_async()` copies | 6 files | 1 file (`tasks/utils.py`) |
| `run_async()` implementation | 16 lines (fragile `get_event_loop`) | 1 line (`asyncio.run()`) |
| Legacy download tasks | 4 dead tasks (~200 lines) | 0 (deleted) |
| TaskTracker retry bug | FAILED shown during retry | "Retrying (1/3)..." shown |
| `parse_single_link_task` | 265 lines, 9 responsibilities | ~60 lines orchestrator + 6 focused helpers |
| Total lines removed | ~250+ | — |
