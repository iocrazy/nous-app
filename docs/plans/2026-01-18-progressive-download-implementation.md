# Progressive Download Progress Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve UX by returning video metadata immediately while showing download progress with switchable animated progress bar styles (Neon/Wave).

**Architecture:** Split the current monolithic parse task into two phases - Task 1 (parse_metadata_task) returns metadata immediately, Task 2 (download_media_task) handles downloads with progress tracking via Redis. Frontend polls progress and displays animated progress bars.

**Tech Stack:** Python/Celery/Redis (backend), React/TypeScript/CSS animations (frontend), Supabase (database)

---

## Phase 1: Backend - Task Splitting & Progress Tracking

### Task 1: Add Download Progress API Endpoint

**Files:**
- Modify: `backend/app/api/task_router.py`
- Modify: `backend/app/services/taskService.ts` (later in frontend)

**Step 1: Add progress endpoint to task router**

In `backend/app/api/task_router.py`, add after the existing `get_task_status` endpoint:

```python
@router.get("/{task_id}/progress", summary="Get download progress")
async def get_download_progress(task_id: str):
    """
    Get download progress for a task from Redis.
    Returns percent, speed, downloaded bytes, total bytes.
    """
    from app.celery_app import celery_app

    redis_client = celery_app.backend.client
    progress_key = f"download_progress:{task_id}"

    progress_data = redis_client.get(progress_key)

    if progress_data:
        import json
        data = json.loads(progress_data)
        return {
            "task_id": task_id,
            "status": "downloading",
            "percent": data.get("percent", 0),
            "downloaded": data.get("downloaded", 0),
            "total": data.get("total", 0),
            "speed": data.get("speed", "0 B/s"),
        }

    # Check if task is complete
    result = celery_app.AsyncResult(task_id)
    if result.state == "SUCCESS":
        return {
            "task_id": task_id,
            "status": "completed",
            "percent": 100,
        }
    elif result.state == "FAILURE":
        return {
            "task_id": task_id,
            "status": "failed",
            "error": str(result.result),
        }

    return {
        "task_id": task_id,
        "status": "pending",
        "percent": 0,
    }
```

**Step 2: Verify endpoint works**

Run: `curl http://localhost:8000/api/v1/tasks/test-id/progress`
Expected: JSON response with status "pending"

**Step 3: Commit**

```bash
git add backend/app/api/task_router.py
git commit -m "feat(api): add download progress endpoint"
```

---

### Task 2: Create Progress-Tracking Download Helper

**Files:**
- Create: `backend/app/services/download_progress.py`

**Step 1: Create the progress tracking download helper**

Create new file `backend/app/services/download_progress.py`:

```python
# backend/app/services/download_progress.py

"""
Download with progress tracking.
Streams file download and reports progress to Redis.
"""

import os
import json
import aiofiles
import httpx
from loguru import logger
from typing import Optional, Callable


class DownloadProgressTracker:
    """Track download progress and report to callback."""

    def __init__(
        self,
        task_id: str,
        redis_client,
        update_interval: float = 0.5
    ):
        self.task_id = task_id
        self.redis_client = redis_client
        self.update_interval = update_interval
        self.last_update = 0
        self.downloaded = 0
        self.total = 0
        self.speed = 0
        self._last_downloaded = 0
        self._last_time = 0

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed as human readable string."""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def update(self, downloaded: int, total: int):
        """Update progress and push to Redis."""
        import time

        self.downloaded = downloaded
        self.total = total

        current_time = time.time()

        # Calculate speed
        if self._last_time > 0:
            time_diff = current_time - self._last_time
            if time_diff > 0:
                bytes_diff = downloaded - self._last_downloaded
                self.speed = bytes_diff / time_diff

        # Only update Redis at intervals to reduce overhead
        if current_time - self.last_update >= self.update_interval:
            percent = int((downloaded / total) * 100) if total > 0 else 0

            progress_data = {
                "percent": percent,
                "downloaded": downloaded,
                "total": total,
                "speed": self._format_speed(self.speed),
            }

            self.redis_client.setex(
                f"download_progress:{self.task_id}",
                300,  # 5 min TTL
                json.dumps(progress_data)
            )

            self.last_update = current_time
            self._last_downloaded = downloaded
            self._last_time = current_time

    def complete(self):
        """Mark download as complete."""
        progress_data = {
            "percent": 100,
            "downloaded": self.total,
            "total": self.total,
            "speed": "0 B/s",
            "status": "completed",
        }
        self.redis_client.setex(
            f"download_progress:{self.task_id}",
            60,  # Keep for 1 min after complete
            json.dumps(progress_data)
        )

    def failed(self, error: str):
        """Mark download as failed."""
        progress_data = {
            "percent": self.downloaded / self.total * 100 if self.total > 0 else 0,
            "downloaded": self.downloaded,
            "total": self.total,
            "speed": "0 B/s",
            "status": "failed",
            "error": error,
        }
        self.redis_client.setex(
            f"download_progress:{self.task_id}",
            300,
            json.dumps(progress_data)
        )


async def download_file_with_progress(
    url: str,
    file_path: str,
    tracker: DownloadProgressTracker,
    headers: dict = None,
    timeout: float = 300,
) -> bool:
    """
    Download file with progress tracking.

    Args:
        url: Download URL
        file_path: Save path
        tracker: Progress tracker instance
        headers: Request headers
        timeout: Download timeout

    Returns:
        bool: Success status
    """
    if headers is None:
        from app.core.utils import Utils
        headers = Utils.get_headers()

    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if os.path.exists(file_path):
            logger.info(f"File exists, skipping: {os.path.basename(file_path)}")
            tracker.complete()
            return True

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                follow_redirects=True,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    logger.warning(f"Download failed {url}, status: {response.status_code}")
                    return False

                total = int(response.headers.get("content-length", 0))
                downloaded = 0

                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        tracker.update(downloaded, total)

                tracker.complete()
                logger.success(f"Downloaded: {os.path.basename(file_path)}")
                return True

    except Exception as e:
        logger.error(f"Download error {url}: {str(e)}")
        tracker.failed(str(e))
        return False
```

**Step 2: Commit**

```bash
git add backend/app/services/download_progress.py
git commit -m "feat(backend): add download progress tracker"
```

---

### Task 3: Create Separate Download Media Task

**Files:**
- Modify: `backend/app/tasks/parse_tasks.py`
- Create: `backend/app/tasks/download_tasks.py` (if not exists, enhance existing)

**Step 1: Create download_media_task in download_tasks.py**

Update `backend/app/tasks/download_tasks.py`:

```python
# backend/app/tasks/download_tasks.py

"""
Download tasks module.
Handles media file downloads with progress tracking.
"""

import asyncio
from celery import shared_task
from loguru import logger


def run_async(coro):
    """Run async coroutine in sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_media_task(
    self,
    aweme_id: str,
    user_id: str,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    aweme_type: int = 0,
    video_title: str = "undefined",
):
    """
    Download media files with progress tracking.

    This task runs after metadata is parsed and saved.
    Progress is tracked in Redis and can be polled by frontend.

    Args:
        aweme_id: Video ID
        user_id: User ID
        download_video: Whether to download video
        download_music: Whether to download music
        download_cover: Whether to download cover
        aweme_type: Media type (0=video, 2/68=images)
        video_title: Video title for logging

    Returns:
        dict: Download result
    """
    task_id = self.request.id
    logger.info(f"[Celery] Starting download task {task_id} for {aweme_id}")

    try:
        from app.celery_app import celery_app
        from app.services.download_progress import DownloadProgressTracker, download_file_with_progress
        from app.services.downloader import DownloaderService
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository

        # Get Redis client from Celery backend
        redis_client = celery_app.backend.client

        # Create progress tracker
        tracker = DownloadProgressTracker(
            task_id=task_id,
            redis_client=redis_client,
        )

        # Execute downloads based on type
        results = {
            "video": None,
            "music": None,
            "cover": None,
        }

        if int(aweme_type) in (0, 4, 61):  # Video types
            if download_video:
                logger.info(f"[Celery] Downloading video: {aweme_id}")
                result = run_async(
                    DownloaderService.download_video_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["video"] = result.video_download_status.value if hasattr(result, 'video_download_status') else "unknown"

            if download_music:
                logger.info(f"[Celery] Downloading music: {aweme_id}")
                result = run_async(
                    DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id)
                )
                results["music"] = result.music_download_status.value if hasattr(result, 'music_download_status') else "unknown"

            if download_cover:
                logger.info(f"[Celery] Downloading cover: {aweme_id}")
                result = run_async(
                    DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["cover"] = result.cover_download_status.value if hasattr(result, 'cover_download_status') else "unknown"

        elif int(aweme_type) in (2, 68):  # Image types
            if download_video:  # "video" flag used for images too
                logger.info(f"[Celery] Downloading images: {aweme_id}")
                result = run_async(
                    DownloaderService.download_images_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["video"] = result.video_download_status.value if hasattr(result, 'video_download_status') else "unknown"

            if download_music:
                logger.info(f"[Celery] Downloading music: {aweme_id}")
                result = run_async(
                    DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id)
                )
                results["music"] = result.music_download_status.value if hasattr(result, 'music_download_status') else "unknown"

            if download_cover:
                logger.info(f"[Celery] Downloading cover: {aweme_id}")
                result = run_async(
                    DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["cover"] = result.cover_download_status.value if hasattr(result, 'cover_download_status') else "unknown"

        # Mark complete in Redis
        tracker.complete()

        logger.success(f"[Celery] Download task completed: {aweme_id}")
        return {
            "status": "success",
            "aweme_id": aweme_id,
            "results": results,
        }

    except Exception as e:
        logger.error(f"[Celery] Download task failed: {aweme_id}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": str(e),
        }
```

**Step 2: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "feat(backend): add download_media_task with progress"
```

---

### Task 4: Modify Parse Task to Return Metadata First

**Files:**
- Modify: `backend/app/tasks/parse_tasks.py`

**Step 1: Update parse_single_link_task to split into two phases**

Replace the content of `parse_single_link_task` in `backend/app/tasks/parse_tasks.py`:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def parse_single_link_task(
    self,
    url: str,
    user_id: str,
    video_bool: bool = True,
    music_bool: bool = False,
    cover_bool: bool = True,
    categories: str = None
):
    """
    Parse metadata for a Douyin link (Phase 1).

    This task quickly parses video metadata and returns it immediately.
    Downloads are handled by a separate download_media_task (Phase 2).

    Args:
        url: Douyin URL
        user_id: User ID
        video_bool: Whether to download video
        music_bool: Whether to download music
        cover_bool: Whether to download cover
        categories: Video categories

    Returns:
        dict: Metadata + download_task_id for progress tracking
    """
    logger.info(f"[Celery] Starting metadata parse: {url[:50]}...")

    try:
        # Extract valid URL
        try:
            valid_urls = Utils.extract_valid_url(url)
            valid_url = valid_urls[0]
        except ValueError as e:
            return {
                "status": "failed",
                "url": url,
                "error": f"Invalid URL: {str(e)}",
            }

        from app.services.douyin_analysis import DouyinAnalysis
        from app.services.douyin_parser import DouyinParser
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
        from app.tasks.download_tasks import download_media_task

        # Fetch video data from Douyin
        aweme_detail = run_async(DouyinAnalysis.fetch_one_video(valid_url))

        if not aweme_detail:
            logger.warning(f"[Celery] Cannot fetch video info: {valid_url}")
            raise self.retry(
                exc=Exception("Cannot fetch video info"),
                countdown=30 * (2 ** self.request.retries)
            )

        # Parse metadata (without downloading files)
        parsed_data = run_async(
            DouyinParser.parse_aweme_detail(
                aweme_detail=aweme_detail,
                valid_url=valid_url,
                download_video=video_bool,
                download_music=music_bool,
                download_cover=cover_bool,
                categories=categories
            )
        )

        if not parsed_data:
            logger.warning(f"[Celery] Parse failed: {valid_url}")
            raise self.retry(
                exc=Exception("Parse failed"),
                countdown=30 * (2 ** self.request.retries)
            )

        aweme_id = parsed_data.get("aweme_id")
        aweme_type = parsed_data.get("aweme_type", 0)
        video_title = parsed_data.get("video_title", "undefined")
        parsed_data["user_id"] = user_id

        # Save metadata to database (without downloading)
        from app.schemas.douyin import DouyinCreate
        from app.core.enums import DownloadStatus

        repo = SupabaseDouyinRepository()

        # Check if exists
        existing = run_async(repo.get_by_aweme_id(aweme_id, user_id=user_id))

        # Prepare data
        try:
            douyin_data = DouyinCreate(**parsed_data)
            data_dict = douyin_data.model_dump()
        except Exception as e:
            logger.error(f"Data validation failed: {str(e)}")
            return {
                "status": "failed",
                "url": valid_url,
                "error": f"Data validation failed: {str(e)}",
            }

        # Set download status to pending
        if video_bool:
            data_dict["video_download_status"] = DownloadStatus.PENDING.value
        else:
            data_dict["video_download_status"] = DownloadStatus.SKIPPED.value

        if music_bool:
            data_dict["music_download_status"] = DownloadStatus.PENDING.value
        else:
            data_dict["music_download_status"] = DownloadStatus.SKIPPED.value

        # Save or update
        if existing:
            run_async(repo.update(aweme_id, data_dict))
            logger.info(f"[Celery] Updated metadata: {aweme_id}")
        else:
            run_async(repo.create(data_dict))
            logger.info(f"[Celery] Created metadata: {aweme_id}")

        # Determine what needs downloading
        need_download = video_bool or music_bool or cover_bool
        download_task_id = None

        if need_download:
            # Trigger download task (Phase 2)
            download_task = download_media_task.delay(
                aweme_id=aweme_id,
                user_id=user_id,
                download_video=video_bool,
                download_music=music_bool,
                download_cover=cover_bool,
                aweme_type=aweme_type,
                video_title=video_title,
            )
            download_task_id = download_task.id
            logger.info(f"[Celery] Download task triggered: {download_task_id}")

        # Log user action
        run_async(log_user_action(
            user_id=user_id,
            action="fetch",
            message=f"{video_title[:20]}...: Metadata parsed",
            status="success",
            aweme_id=aweme_id
        ))

        # Build metadata response
        metadata = {
            "aweme_id": aweme_id,
            "video_title": parsed_data.get("video_title"),
            "author": parsed_data.get("author"),
            "author_avatar": parsed_data.get("author_avatar"),
            "duration": parsed_data.get("duration"),
            "aweme_type": aweme_type,
            "create_time": parsed_data.get("create_time"),
            "statistics": {
                "likes": parsed_data.get("likes", 0),
                "comments": parsed_data.get("comments", 0),
                "shares": parsed_data.get("shares", 0),
                "collects": parsed_data.get("collects", 0),
            },
            "cover_urls": parsed_data.get("cover_urls", []),
            "description": parsed_data.get("description"),
        }

        logger.success(f"[Celery] Metadata parse complete: {aweme_id}")

        return {
            "status": "success",
            "url": valid_url,
            "aweme_id": aweme_id,
            "download_task_id": download_task_id,
            "metadata": metadata,
        }

    except Exception as e:
        logger.error(f"[Celery] Parse task error: {url}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "url": url,
            "error": str(e),
        }
```

**Step 2: Commit**

```bash
git add backend/app/tasks/parse_tasks.py
git commit -m "feat(backend): split parse task to return metadata first"
```

---

## Phase 2: Frontend - Progress Components

### Task 5: Create Progress Bar Type Definitions

**Files:**
- Create: `frontend/components/DownloadProgress/types.ts`

**Step 1: Create type definitions**

Create directory and file:

```typescript
// frontend/components/DownloadProgress/types.ts

export type DownloadStatus = 'pending' | 'downloading' | 'completed' | 'failed' | 'retrying';

export interface DownloadProgressProps {
  /** Progress percentage 0-100 */
  percent: number;
  /** Current status */
  status: DownloadStatus;
  /** Download speed string e.g. "2.5 MB/s" */
  speed?: string;
  /** Retry callback when failed */
  onRetry?: () => void;
  /** Size variant */
  size?: 'normal' | 'mini';
  /** Thumbnail URL for background */
  thumbnailUrl?: string;
  /** Retry count for display */
  retryCount?: number;
  /** Max retries */
  maxRetries?: number;
}

export type ProgressStyleType = 'neon' | 'wave';
```

**Step 2: Commit**

```bash
git add frontend/components/DownloadProgress/types.ts
git commit -m "feat(frontend): add download progress types"
```

---

### Task 6: Create Neon Border Progress Component

**Files:**
- Create: `frontend/components/DownloadProgress/NeonBorder.tsx`

**Step 1: Create NeonBorder component**

```tsx
// frontend/components/DownloadProgress/NeonBorder.tsx

import React from 'react';
import { DownloadProgressProps } from './types';
import { RefreshCw, AlertCircle, Check } from 'lucide-react';

export const NeonBorder: React.FC<DownloadProgressProps> = ({
  percent,
  status,
  speed,
  onRetry,
  size = 'normal',
  thumbnailUrl,
  retryCount,
  maxRetries = 3,
}) => {
  const isNormal = size === 'normal';
  const circumference = isNormal ? 2 * Math.PI * 45 : 2 * Math.PI * 20;
  const strokeDashoffset = circumference - (percent / 100) * circumference;

  const containerSize = isNormal ? 'w-full aspect-video' : 'w-24 h-24';
  const svgSize = isNormal ? 120 : 50;
  const radius = isNormal ? 45 : 20;
  const strokeWidth = isNormal ? 4 : 3;

  return (
    <div className={`relative ${containerSize} rounded-xl overflow-hidden group`}>
      {/* Background thumbnail with blur */}
      {thumbnailUrl && (
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: `url(${thumbnailUrl})` }}
        >
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
        </div>
      )}

      {/* Neon border animation */}
      <svg
        className="absolute inset-0 w-full h-full"
        style={{ filter: status === 'downloading' ? 'drop-shadow(0 0 8px rgba(139, 92, 246, 0.8))' : undefined }}
      >
        <defs>
          <linearGradient id="neonGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#a855f7" />
            <stop offset="50%" stopColor="#6366f1" />
            <stop offset="100%" stopColor="#a855f7" />
          </linearGradient>
        </defs>
        <rect
          x="2"
          y="2"
          width="calc(100% - 4px)"
          height="calc(100% - 4px)"
          rx="12"
          fill="none"
          stroke="rgba(139, 92, 246, 0.2)"
          strokeWidth="2"
        />
        <rect
          x="2"
          y="2"
          width="calc(100% - 4px)"
          height="calc(100% - 4px)"
          rx="12"
          fill="none"
          stroke="url(#neonGradient)"
          strokeWidth="3"
          strokeDasharray={`${percent * 3.6} 360`}
          strokeLinecap="round"
          className="transition-all duration-300"
          style={{
            animation: status === 'downloading' ? 'pulse 2s ease-in-out infinite' : undefined,
          }}
        />
      </svg>

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
        {status === 'downloading' && (
          <>
            <span className={`font-bold text-white ${isNormal ? 'text-4xl' : 'text-lg'}`}>
              {percent}%
            </span>
            {speed && isNormal && (
              <span className="text-purple-300 text-sm mt-1">{speed}</span>
            )}
          </>
        )}

        {status === 'completed' && (
          <div className={`${isNormal ? 'p-4' : 'p-2'} rounded-full bg-green-500/20 animate-in zoom-in duration-300`}>
            <Check className={`text-green-400 ${isNormal ? 'w-12 h-12' : 'w-6 h-6'}`} />
          </div>
        )}

        {status === 'failed' && (
          <div className="flex flex-col items-center gap-2">
            <AlertCircle className={`text-red-400 ${isNormal ? 'w-10 h-10' : 'w-5 h-5'}`} />
            {isNormal && <span className="text-red-400 text-sm">Download failed</span>}
            {onRetry && (
              <button
                onClick={onRetry}
                className="flex items-center gap-1 px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 rounded-lg text-red-300 text-sm transition-colors"
              >
                <RefreshCw size={14} />
                Retry
              </button>
            )}
          </div>
        )}

        {status === 'retrying' && (
          <div className="flex flex-col items-center gap-2">
            <RefreshCw className={`text-orange-400 animate-spin ${isNormal ? 'w-8 h-8' : 'w-5 h-5'}`} />
            {isNormal && (
              <span className="text-orange-300 text-sm">
                Retrying ({retryCount}/{maxRetries})...
              </span>
            )}
          </div>
        )}

        {status === 'pending' && (
          <div className={`${isNormal ? 'w-8 h-8' : 'w-4 h-4'} rounded-full border-2 border-purple-400 border-t-transparent animate-spin`} />
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.7; }
        }
      `}</style>
    </div>
  );
};

export default NeonBorder;
```

**Step 2: Commit**

```bash
git add frontend/components/DownloadProgress/NeonBorder.tsx
git commit -m "feat(frontend): add NeonBorder progress component"
```

---

### Task 7: Create Wave Liquid Progress Component

**Files:**
- Create: `frontend/components/DownloadProgress/WaveLiquid.tsx`

**Step 1: Create WaveLiquid component**

```tsx
// frontend/components/DownloadProgress/WaveLiquid.tsx

import React from 'react';
import { DownloadProgressProps } from './types';
import { RefreshCw, AlertCircle, Check } from 'lucide-react';

export const WaveLiquid: React.FC<DownloadProgressProps> = ({
  percent,
  status,
  speed,
  onRetry,
  size = 'normal',
  thumbnailUrl,
  retryCount,
  maxRetries = 3,
}) => {
  const isNormal = size === 'normal';
  const containerSize = isNormal ? 'w-full aspect-video' : 'w-24 h-24';
  const waveHeight = 100 - percent;

  return (
    <div className={`relative ${containerSize} rounded-xl overflow-hidden group`}>
      {/* Background thumbnail */}
      {thumbnailUrl && (
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: `url(${thumbnailUrl})` }}
        >
          <div className="absolute inset-0 bg-black/40" />
        </div>
      )}

      {/* Wave container */}
      {status === 'downloading' && (
        <div
          className="absolute inset-x-0 bottom-0 transition-all duration-500 ease-out"
          style={{ height: `${percent}%` }}
        >
          {/* Wave SVG */}
          <svg
            className="absolute -top-4 left-0 w-[200%] h-8"
            style={{ animation: 'wave 3s linear infinite' }}
            viewBox="0 0 1200 120"
            preserveAspectRatio="none"
          >
            <path
              d="M0,60 C150,120 350,0 600,60 C850,120 1050,0 1200,60 L1200,120 L0,120 Z"
              fill="rgba(139, 92, 246, 0.6)"
            />
          </svg>
          <svg
            className="absolute -top-4 left-0 w-[200%] h-8"
            style={{ animation: 'wave 4s linear infinite reverse', animationDelay: '-2s' }}
            viewBox="0 0 1200 120"
            preserveAspectRatio="none"
          >
            <path
              d="M0,60 C150,120 350,0 600,60 C850,120 1050,0 1200,60 L1200,120 L0,120 Z"
              fill="rgba(99, 102, 241, 0.4)"
            />
          </svg>

          {/* Liquid body */}
          <div className="absolute inset-x-0 top-4 bottom-0 bg-gradient-to-b from-purple-500/60 to-indigo-600/80" />
        </div>
      )}

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
        {status === 'downloading' && (
          <>
            <span className={`font-bold text-white drop-shadow-lg ${isNormal ? 'text-4xl' : 'text-lg'}`}>
              {percent}%
            </span>
            {speed && isNormal && (
              <span className="text-white/80 text-sm mt-1 drop-shadow">{speed}</span>
            )}
          </>
        )}

        {status === 'completed' && (
          <div className={`${isNormal ? 'p-4' : 'p-2'} rounded-full bg-green-500/20 animate-in zoom-in duration-300`}>
            <Check className={`text-green-400 ${isNormal ? 'w-12 h-12' : 'w-6 h-6'}`} />
          </div>
        )}

        {status === 'failed' && (
          <div className="flex flex-col items-center gap-2">
            <AlertCircle className={`text-red-400 ${isNormal ? 'w-10 h-10' : 'w-5 h-5'}`} />
            {isNormal && <span className="text-red-400 text-sm">Download failed</span>}
            {onRetry && (
              <button
                onClick={onRetry}
                className="flex items-center gap-1 px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 rounded-lg text-red-300 text-sm transition-colors"
              >
                <RefreshCw size={14} />
                Retry
              </button>
            )}
          </div>
        )}

        {status === 'retrying' && (
          <div className="flex flex-col items-center gap-2">
            <RefreshCw className={`text-orange-400 animate-spin ${isNormal ? 'w-8 h-8' : 'w-5 h-5'}`} />
            {isNormal && (
              <span className="text-orange-300 text-sm">
                Retrying ({retryCount}/{maxRetries})...
              </span>
            )}
          </div>
        )}

        {status === 'pending' && (
          <div className={`${isNormal ? 'w-8 h-8' : 'w-4 h-4'} rounded-full border-2 border-purple-400 border-t-transparent animate-spin`} />
        )}
      </div>

      <style>{`
        @keyframes wave {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  );
};

export default WaveLiquid;
```

**Step 2: Commit**

```bash
git add frontend/components/DownloadProgress/WaveLiquid.tsx
git commit -m "feat(frontend): add WaveLiquid progress component"
```

---

### Task 8: Create Simple Bar Progress Component (Mini)

**Files:**
- Create: `frontend/components/DownloadProgress/SimpleBar.tsx`

**Step 1: Create SimpleBar component for mini cards**

```tsx
// frontend/components/DownloadProgress/SimpleBar.tsx

import React from 'react';
import { DownloadProgressProps } from './types';
import { RefreshCw, AlertCircle, Check } from 'lucide-react';

export const SimpleBar: React.FC<DownloadProgressProps> = ({
  percent,
  status,
  onRetry,
  retryCount,
  maxRetries = 3,
}) => {
  return (
    <div className="w-full">
      {status === 'downloading' && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs">
            <span className="text-zinc-400">Downloading</span>
            <span className="text-purple-400 font-medium">{percent}%</span>
          </div>
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-purple-500 to-indigo-500 rounded-full transition-all duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      )}

      {status === 'completed' && (
        <div className="flex items-center gap-1 text-green-400 text-xs">
          <Check size={12} />
          <span>Done</span>
        </div>
      )}

      {status === 'failed' && (
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 text-red-400 text-xs">
            <AlertCircle size={12} />
            <span>Failed</span>
          </div>
          {onRetry && (
            <button
              onClick={onRetry}
              className="p-1 hover:bg-zinc-800 rounded text-zinc-400 hover:text-zinc-200"
            >
              <RefreshCw size={12} />
            </button>
          )}
        </div>
      )}

      {status === 'retrying' && (
        <div className="flex items-center gap-1 text-orange-400 text-xs">
          <RefreshCw size={12} className="animate-spin" />
          <span>Retry {retryCount}/{maxRetries}</span>
        </div>
      )}

      {status === 'pending' && (
        <div className="flex items-center gap-1 text-zinc-500 text-xs">
          <div className="w-3 h-3 rounded-full border border-zinc-600 border-t-transparent animate-spin" />
          <span>Queued</span>
        </div>
      )}
    </div>
  );
};

export default SimpleBar;
```

**Step 2: Commit**

```bash
git add frontend/components/DownloadProgress/SimpleBar.tsx
git commit -m "feat(frontend): add SimpleBar progress component for mini cards"
```

---

### Task 9: Create Main DownloadProgress Index Component

**Files:**
- Create: `frontend/components/DownloadProgress/index.tsx`

**Step 1: Create index component that switches between styles**

```tsx
// frontend/components/DownloadProgress/index.tsx

import React from 'react';
import { DownloadProgressProps, ProgressStyleType } from './types';
import { NeonBorder } from './NeonBorder';
import { WaveLiquid } from './WaveLiquid';
import { SimpleBar } from './SimpleBar';

interface DownloadProgressComponentProps extends DownloadProgressProps {
  /** Progress bar style */
  style?: ProgressStyleType;
}

export const DownloadProgress: React.FC<DownloadProgressComponentProps> = ({
  style = 'neon',
  size = 'normal',
  ...props
}) => {
  // Mini size always uses SimpleBar for space efficiency
  if (size === 'mini') {
    return <SimpleBar size="mini" {...props} />;
  }

  // Normal size uses selected style
  switch (style) {
    case 'wave':
      return <WaveLiquid size="normal" {...props} />;
    case 'neon':
    default:
      return <NeonBorder size="normal" {...props} />;
  }
};

// Re-export types and individual components
export * from './types';
export { NeonBorder } from './NeonBorder';
export { WaveLiquid } from './WaveLiquid';
export { SimpleBar } from './SimpleBar';

export default DownloadProgress;
```

**Step 2: Commit**

```bash
git add frontend/components/DownloadProgress/index.tsx
git commit -m "feat(frontend): add DownloadProgress index with style switching"
```

---

## Phase 3: Frontend - Settings & Integration

### Task 10: Add Progress Style Setting to Settings View

**Files:**
- Modify: `frontend/components/SettingsView.tsx`
- Modify: `frontend/types.ts`

**Step 1: Add progressStyle to UserSettings type in types.ts**

Find the `UserSettings` interface and add:

```typescript
export interface UserSettings {
  downloadPath: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
  progressStyle?: 'neon' | 'wave';  // Add this line
}
```

**Step 2: Add settings section to SettingsView.tsx**

In `SettingsView.tsx`, add after the Database Configuration section (around line 217):

```tsx
{/* Section 3: Download Progress Style */}
<div className="px-6 py-4 border-y border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
   <div className="p-2 bg-purple-500/10 rounded-lg text-purple-400">
      <Zap size={20} />
   </div>
   <h2 className="font-semibold text-zinc-200">Download Progress Style</h2>
</div>
<div className="p-6">
   <div className="grid grid-cols-2 gap-4">
      {/* Neon Style */}
      <label
         className={`relative cursor-pointer rounded-xl border-2 p-4 transition-all ${
            localSettings.progressStyle === 'neon' || !localSettings.progressStyle
               ? 'border-purple-500 bg-purple-500/10'
               : 'border-zinc-800 hover:border-zinc-700'
         }`}
         onClick={() => setLocalSettings({...localSettings, progressStyle: 'neon'})}
      >
         <div className="flex flex-col items-center gap-3">
            <div className="w-full aspect-video rounded-lg bg-zinc-950 border border-purple-500/50 flex items-center justify-center relative overflow-hidden">
               {/* Mini preview of neon style */}
               <div className="absolute inset-2 rounded border-2 border-purple-500" style={{
                  background: 'linear-gradient(90deg, transparent 70%, rgba(139, 92, 246, 0.3) 70%)',
               }} />
               <span className="text-2xl font-bold text-white z-10">45%</span>
            </div>
            <div className="text-center">
               <div className="font-medium text-zinc-200">Neon Border</div>
               <div className="text-xs text-zinc-500">Glowing border animation</div>
            </div>
         </div>
         {(localSettings.progressStyle === 'neon' || !localSettings.progressStyle) && (
            <div className="absolute top-2 right-2 w-5 h-5 bg-purple-500 rounded-full flex items-center justify-center">
               <Check size={12} className="text-white" />
            </div>
         )}
      </label>

      {/* Wave Style */}
      <label
         className={`relative cursor-pointer rounded-xl border-2 p-4 transition-all ${
            localSettings.progressStyle === 'wave'
               ? 'border-purple-500 bg-purple-500/10'
               : 'border-zinc-800 hover:border-zinc-700'
         }`}
         onClick={() => setLocalSettings({...localSettings, progressStyle: 'wave'})}
      >
         <div className="flex flex-col items-center gap-3">
            <div className="w-full aspect-video rounded-lg bg-zinc-950 border border-indigo-500/50 flex items-center justify-center relative overflow-hidden">
               {/* Mini preview of wave style */}
               <div className="absolute inset-x-0 bottom-0 h-[45%] bg-gradient-to-t from-indigo-600/80 to-purple-500/60" />
               <span className="text-2xl font-bold text-white z-10">45%</span>
            </div>
            <div className="text-center">
               <div className="font-medium text-zinc-200">Wave Liquid</div>
               <div className="text-xs text-zinc-500">Rising wave animation</div>
            </div>
         </div>
         {localSettings.progressStyle === 'wave' && (
            <div className="absolute top-2 right-2 w-5 h-5 bg-purple-500 rounded-full flex items-center justify-center">
               <Check size={12} className="text-white" />
            </div>
         )}
      </label>
   </div>
</div>
```

**Step 3: Add Zap import at the top of the file**

```tsx
import {
  Save, FolderOpen, Key, Plus, Trash2, Copy, Calendar, Shield, X, CheckSquare, Square, Edit2,
  Clock, CheckCircle, Power, Database, Zap, Check  // Add Zap and Check
} from 'lucide-react';
```

**Step 4: Commit**

```bash
git add frontend/components/SettingsView.tsx frontend/types.ts
git commit -m "feat(frontend): add progress style setting to Settings page"
```

---

### Task 11: Add Download Progress Service Functions

**Files:**
- Modify: `frontend/services/taskService.ts`

**Step 1: Add getDownloadProgress function**

Add to `frontend/services/taskService.ts`:

```typescript
// Download progress response
export interface DownloadProgressResponse {
  task_id: string;
  status: 'pending' | 'downloading' | 'completed' | 'failed';
  percent: number;
  downloaded?: number;
  total?: number;
  speed?: string;
  error?: string;
}

/**
 * Get download progress for a task
 */
export const getDownloadProgress = async (taskId: string): Promise<DownloadProgressResponse> => {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/progress`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to get download progress: ${response.status}`);
  }

  return response.json();
};

/**
 * Poll download progress
 */
export const pollDownloadProgress = (
  taskId: string,
  onUpdate: (progress: DownloadProgressResponse) => void,
  interval: number = 1000
): (() => void) => {
  let isPolling = true;

  const poll = async () => {
    if (!isPolling) return;

    try {
      const progress = await getDownloadProgress(taskId);
      onUpdate(progress);

      // Stop polling if completed or failed
      if (['completed', 'failed'].includes(progress.status)) {
        isPolling = false;
        return;
      }

      if (isPolling) {
        setTimeout(poll, interval);
      }
    } catch (error) {
      console.error('Failed to poll download progress:', error);
      if (isPolling) {
        setTimeout(poll, interval * 2);
      }
    }
  };

  poll();

  return () => {
    isPolling = false;
  };
};
```

**Step 2: Commit**

```bash
git add frontend/services/taskService.ts
git commit -m "feat(frontend): add download progress service functions"
```

---

### Task 12: Create useDownloadProgress Hook

**Files:**
- Create: `frontend/hooks/useDownloadProgress.ts`

**Step 1: Create the hook**

```typescript
// frontend/hooks/useDownloadProgress.ts

import { useState, useEffect, useCallback } from 'react';
import {
  DownloadProgressResponse,
  getDownloadProgress,
  pollDownloadProgress
} from '../services/taskService';
import { DownloadStatus } from '../components/DownloadProgress/types';

interface UseDownloadProgressOptions {
  interval?: number;
  autoStart?: boolean;
  onComplete?: () => void;
  onError?: (error: string) => void;
}

interface UseDownloadProgressReturn {
  status: DownloadStatus;
  percent: number;
  speed: string | undefined;
  isPolling: boolean;
  startPolling: () => void;
  stopPolling: () => void;
  retry: () => void;
}

export const useDownloadProgress = (
  taskId: string | null,
  options: UseDownloadProgressOptions = {}
): UseDownloadProgressReturn => {
  const {
    interval = 1000,
    autoStart = true,
    onComplete,
    onError,
  } = options;

  const [status, setStatus] = useState<DownloadStatus>('pending');
  const [percent, setPercent] = useState(0);
  const [speed, setSpeed] = useState<string | undefined>();
  const [isPolling, setIsPolling] = useState(false);
  const [stopFn, setStopFn] = useState<(() => void) | null>(null);

  const handleProgressUpdate = useCallback((progress: DownloadProgressResponse) => {
    setPercent(progress.percent);
    setSpeed(progress.speed);

    // Map backend status to frontend status
    switch (progress.status) {
      case 'downloading':
        setStatus('downloading');
        break;
      case 'completed':
        setStatus('completed');
        setIsPolling(false);
        onComplete?.();
        break;
      case 'failed':
        setStatus('failed');
        setIsPolling(false);
        onError?.(progress.error || 'Download failed');
        break;
      default:
        setStatus('pending');
    }
  }, [onComplete, onError]);

  const startPolling = useCallback(() => {
    if (!taskId || isPolling) return;

    setIsPolling(true);
    setStatus('downloading');

    const stop = pollDownloadProgress(taskId, handleProgressUpdate, interval);
    setStopFn(() => stop);
  }, [taskId, isPolling, interval, handleProgressUpdate]);

  const stopPolling = useCallback(() => {
    if (stopFn) {
      stopFn();
      setStopFn(null);
    }
    setIsPolling(false);
  }, [stopFn]);

  const retry = useCallback(() => {
    setStatus('retrying');
    setPercent(0);
    // The actual retry logic would trigger a new download task
    // This is handled by the parent component
  }, []);

  // Auto-start polling
  useEffect(() => {
    if (taskId && autoStart && !isPolling && status !== 'completed' && status !== 'failed') {
      startPolling();
    }

    return () => {
      if (stopFn) {
        stopFn();
      }
    };
  }, [taskId, autoStart]); // eslint-disable-line

  // Reset on taskId change
  useEffect(() => {
    if (!taskId) {
      setStatus('pending');
      setPercent(0);
      setSpeed(undefined);
      setIsPolling(false);
    }
  }, [taskId]);

  return {
    status,
    percent,
    speed,
    isPolling,
    startPolling,
    stopPolling,
    retry,
  };
};

export default useDownloadProgress;
```

**Step 2: Commit**

```bash
git add frontend/hooks/useDownloadProgress.ts
git commit -m "feat(frontend): add useDownloadProgress hook"
```

---

## Phase 4: Integration & Testing

### Task 13: Update i18n Translation Files

**Files:**
- Modify: `frontend/public/locales/en.json`
- Modify: `frontend/public/locales/zh.json`

**Step 1: Add English translations**

Add to `frontend/public/locales/en.json`:

```json
{
  "settings": {
    "progressStyle": "Download Progress Style",
    "neonBorder": "Neon Border",
    "neonBorderDesc": "Glowing border animation",
    "waveLiquid": "Wave Liquid",
    "waveLiquidDesc": "Rising wave animation"
  },
  "download": {
    "downloading": "Downloading",
    "completed": "Done",
    "failed": "Failed",
    "retry": "Retry",
    "retrying": "Retrying",
    "queued": "Queued"
  }
}
```

**Step 2: Add Chinese translations**

Add to `frontend/public/locales/zh.json`:

```json
{
  "settings": {
    "progressStyle": "下载进度样式",
    "neonBorder": "霓虹边框",
    "neonBorderDesc": "发光边框动画",
    "waveLiquid": "波浪液体",
    "waveLiquidDesc": "上升波浪动画"
  },
  "download": {
    "downloading": "下载中",
    "completed": "已完成",
    "failed": "失败",
    "retry": "重试",
    "retrying": "重试中",
    "queued": "排队中"
  }
}
```

**Step 3: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(i18n): add download progress translations"
```

---

### Task 14: Final Integration - Update App.tsx Parse Flow

**Files:**
- Modify: `frontend/App.tsx`

**Step 1: Update parse result handling to show metadata first**

This task involves updating the parse flow in App.tsx to:
1. Show metadata immediately when Task 1 completes
2. Start polling download progress using download_task_id
3. Update the video display area with progress component

The specific changes depend on the current App.tsx structure. Key changes:

```tsx
// In the parse result handler, after receiving task result:

// 1. Check if result has metadata and download_task_id
if (result.status === 'success' && result.metadata) {
  // Show metadata immediately
  setVideoMetadata(result.metadata);
  setShowResult(true);

  // If there's a download task, start polling its progress
  if (result.download_task_id) {
    setDownloadTaskId(result.download_task_id);
    // The useDownloadProgress hook will handle polling
  }
}
```

**Step 2: Add progress component to video display area**

```tsx
// In the video display section:
{downloadTaskId && downloadStatus !== 'completed' ? (
  <DownloadProgress
    style={settings.progressStyle || 'neon'}
    percent={downloadPercent}
    status={downloadStatus}
    speed={downloadSpeed}
    thumbnailUrl={videoMetadata?.cover_urls?.[0]}
    onRetry={handleRetryDownload}
  />
) : (
  <video src={videoUrl} controls className="w-full rounded-xl" />
)}
```

**Step 3: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat(frontend): integrate progressive download in parse flow"
```

---

## Summary Checklist

### Backend
- [x] Task 1: Add download progress API endpoint
- [x] Task 2: Create progress-tracking download helper
- [x] Task 3: Create separate download_media_task
- [x] Task 4: Modify parse task to return metadata first

### Frontend Components
- [x] Task 5: Create progress bar type definitions
- [x] Task 6: Create NeonBorder progress component
- [x] Task 7: Create WaveLiquid progress component
- [x] Task 8: Create SimpleBar progress component (mini)
- [x] Task 9: Create main DownloadProgress index component

### Frontend Integration
- [x] Task 10: Add progress style setting to Settings
- [x] Task 11: Add download progress service functions
- [x] Task 12: Create useDownloadProgress hook
- [x] Task 13: Update i18n translation files
- [x] Task 14: Final integration in App.tsx

---

## Testing Checklist

1. **Backend Tests**
   - [ ] Test progress endpoint returns correct data
   - [ ] Test parse task returns metadata with download_task_id
   - [ ] Test download task updates Redis progress

2. **Frontend Tests**
   - [ ] Test NeonBorder renders at all states
   - [ ] Test WaveLiquid renders at all states
   - [ ] Test SimpleBar renders at all states
   - [ ] Test style switching in Settings

3. **Integration Tests**
   - [ ] Test full flow: parse → metadata shown → progress → video
   - [ ] Test retry functionality
   - [ ] Test batch download with mini cards
