# Unified NAS File Architecture — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify NAS file paths to use `parsed_media.id` (Snowflake) as folder name, standardize file naming (`video.mp4` / `audio.mp3` / `cover.jpg`), fix the hardcoded "douyin" bug, and migrate all 98 existing records.

**Architecture:** Replace `platform_id` with `str(parsed_media.id)` in all path-creation calls. Standardize yt-dlp output templates to generic names. Write a one-shot migration script (dry-run + execute modes) to move old files and update DB paths.

**Tech Stack:** Python 3.12, FastAPI, Celery, Supabase (PostgreSQL), yt-dlp, asyncio

**Design Doc:** `docs/plans/2026-02-25-unified-nas-file-architecture.md`

---

## Task 1: Rename `utils.py` parameter for clarity

**Files:**
- Modify: `backend/app/core/utils.py:192-210`

**Context:** `create_web_resource_path(platform, platform_id)` is the single path-creation function. We rename the parameter from `platform_id` to `identifier` so callers know they can pass any ID (Snowflake ID, not just platform-specific ID). The function body stays the same.

**Step 1: Update the function signature and docstring**

Change `platform_id` → `identifier` in function signature and docstring:

```python
# backend/app/core/utils.py:192
@classmethod
def create_web_resource_path(cls, platform: str, identifier: str) -> tuple[Path, str]:
    """
    Create storage path for Parser downloads.

    Path structure: {DOWNLOAD_PATH}/global/resources/web/{platform}/{identifier}/

    Args:
        platform: Source platform (e.g. 'douyin', 'bilibili')
        identifier: Content identifier (Snowflake media ID or legacy platform_id)

    Returns:
        tuple[Path, str]: (full_path, relative_path_prefix)
        e.g. (/Volumes/.../global/resources/web/douyin/12345/, global/resources/web/douyin/12345)
    """
    base_path = cls.get_download_base_path()
    relative = f"global/resources/web/{platform}/{identifier}"
    full_path = Path(base_path) / relative
    os.makedirs(full_path, exist_ok=True)
    return full_path, relative
```

**Step 2: Verify no other code references the old param name as kwarg**

Run: `grep -rn "platform_id=" backend/app/core/utils.py`
Expected: No matches (callers use positional args)

**Step 3: Commit**

```bash
git add backend/app/core/utils.py
git commit -m "refactor: rename create_web_resource_path param to identifier"
```

---

## Task 2: Update `ytdlp_service.py` — generic filenames + fix `_find_downloaded_file`

**Files:**
- Modify: `backend/app/services/ytdlp_service.py:78-253, 398-406`

**Context:** yt-dlp currently names files as `{platform_id}.mp4` / `{platform_id}_audio.mp3`. We change to `video.%(ext)s` / `audio.%(ext)s`. The `_find_downloaded_file` method uses prefix matching — we update it to match the new generic names.

**Step 1: Update `download_video()` output template**

At line 99, change:
```python
# OLD
output_template = os.path.join(output_dir, f"{platform_id}.%(ext)s")
```
to:
```python
# NEW
output_template = os.path.join(output_dir, "video.%(ext)s")
```

At line 177, change:
```python
# OLD
file_path = YtdlpService._find_downloaded_file(output_dir, platform_id)
```
to:
```python
# NEW
file_path = YtdlpService._find_downloaded_file(output_dir, "video")
```

**Step 2: Update `download_audio()` output template**

At line 204, change:
```python
# OLD
output_template = os.path.join(output_dir, f"{platform_id}_audio.%(ext)s")
```
to:
```python
# NEW
output_template = os.path.join(output_dir, "audio.%(ext)s")
```

At lines 241-242, change:
```python
# OLD
file_path = YtdlpService._find_downloaded_file(
    output_dir, f"{platform_id}_audio"
)
```
to:
```python
# NEW
file_path = YtdlpService._find_downloaded_file(output_dir, "audio")
```

**Step 3: Remove `platform_id` param from function signatures**

`download_video` and `download_audio` no longer need `platform_id` for naming. But callers still pass it for logging. Keep the param for backward compat but stop using it for filenames. Update log messages:

Line 118:
```python
# OLD
logger.info(f"[yt-dlp] Downloading video: {platform_id}")
# NEW
logger.info(f"[yt-dlp] Downloading video to {output_dir}")
```

Line 221:
```python
# OLD
logger.info(f"[yt-dlp] Extracting audio: {platform_id}")
# NEW
logger.info(f"[yt-dlp] Extracting audio to {output_dir}")
```

**Step 4: Commit**

```bash
git add backend/app/services/ytdlp_service.py
git commit -m "refactor: ytdlp_service use generic filenames video.mp4/audio.mp3"
```

---

## Task 3: Update `downloader.py` — Douyin downloads use Snowflake ID + fix bugs

**Files:**
- Modify: `backend/app/services/downloader.py:370-430, 515-555, 730-800, 853-942`

**Context:** Douyin downloader uses `platform_id` for path creation. We switch to `str(video_data["id"])` (Snowflake BIGINT). Also fix the "douyin" hardcode bug in music download and rename `music.mp3` → `audio.mp3`.

### 3a: Fix `download_video_by_platform_id()` (lines 388-396)

**Step 1: Use Snowflake ID for path**

Change lines 388-396:
```python
# OLD
# Create structured path: global/resources/web/{platform}/{platform_id}/
source_platform = video_data.get("source_platform", "douyin")
full_path, relative_prefix = Utils.create_web_resource_path(
    source_platform, platform_id
)
```
to:
```python
# NEW — use Snowflake media ID as folder name
source_platform = video_data.get("source_platform", "douyin")
media_id = str(video_data["id"])
full_path, relative_prefix = Utils.create_web_resource_path(
    source_platform, media_id
)
```

### 3b: Fix `download_images_by_platform_id()` (lines 551-555)

**Step 1: Use Snowflake ID for path**

Change lines 551-555:
```python
# OLD
source_platform = video_data.get("source_platform", "douyin")
sub_download_full_path, sub_download_relative_path = (
    Utils.create_web_resource_path(source_platform, platform_id)
)
```
to:
```python
# NEW
source_platform = video_data.get("source_platform", "douyin")
media_id = str(video_data["id"])
sub_download_full_path, sub_download_relative_path = (
    Utils.create_web_resource_path(source_platform, media_id)
)
```

### 3c: Fix `download_music_by_platform_id()` (lines 755-763) — **BUG FIX + rename**

**Step 1: Fix hardcoded "douyin" and rename `music.mp3` → `audio.mp3`**

The function calls `repo.get_music_data(platform_id)` which returns the parsed_media record. We need the record's `id` and `source_platform`.

Change lines 755-763:
```python
# OLD — BUG: hardcoded "douyin", uses music.mp3
full_path, relative_prefix = Utils.create_web_resource_path(
    "douyin", platform_id
)
music_full_path = os.path.join(full_path, "music.mp3")
music_relative_path = f"{relative_prefix}/music.mp3"
```
to:
```python
# NEW — uses actual platform, Snowflake ID, and audio.mp3
source_platform = music_data.get("source_platform", "douyin")
media_id = str(music_data["id"])
full_path, relative_prefix = Utils.create_web_resource_path(
    source_platform, media_id
)
music_full_path = os.path.join(full_path, "audio.mp3")
music_relative_path = f"{relative_prefix}/audio.mp3"
```

> **Note:** `get_music_data()` must return `id` and `source_platform` fields. Check that the query in `media_repository.py` selects these. If not, add them.

### 3d: Fix `download_cover_by_platform_id()` (lines 909-912)

**Step 1: Use Snowflake ID for path**

Change lines 909-912:
```python
# OLD
full_path, relative_prefix = Utils.create_web_resource_path(
    source_platform, platform_id
)
```
to:
```python
# NEW
media_id = str(video_data["id"])
full_path, relative_prefix = Utils.create_web_resource_path(
    source_platform, media_id
)
```

**Step 2: Commit**

```bash
git add backend/app/services/downloader.py
git commit -m "fix: use Snowflake ID for NAS paths, fix hardcoded douyin bug, rename music→audio"
```

---

## Task 4: Update `media_repository.py` — ensure `get_music_data` returns required fields

**Files:**
- Modify: `backend/app/repositories/media_repository.py` (the `get_music_data` method)

**Context:** `get_music_data()` is used by `download_music_by_platform_id()`. We now need it to return `id` and `source_platform` so we can build the correct path.

**Step 1: Check current `get_music_data` select fields**

Read the method and verify it includes `id` and `source_platform` in its `.select()`. If not, add them.

**Step 2: If missing, update the select to include `id, source_platform`**

```python
# Ensure these fields are in the select:
.select("id, source_platform, music_download_urls, music_name, ...")
```

**Step 3: Commit (if changed)**

```bash
git add backend/app/repositories/media_repository.py
git commit -m "fix: include id and source_platform in get_music_data query"
```

---

## Task 5: Update `download_tasks.py` — yt-dlp path uses Snowflake ID

**Files:**
- Modify: `backend/app/tasks/download_tasks.py:462-546`

**Context:** `_do_ytdlp_download()` currently uses `platform_id` for path creation. We need to look up `parsed_media` to get the Snowflake ID, then use it for path creation. Also update the relative path stored in DB to use the new generic filename.

**Step 1: Look up media record and use Snowflake ID**

At lines 478-481, change:
```python
# OLD
detected_platform, _ = URLRouter.detect_platform(url)
storage_dir, relative_prefix = Utils.create_web_resource_path(
    detected_platform, platform_id
)
```
to:
```python
# NEW — look up media to get Snowflake ID
detected_platform, _ = URLRouter.detect_platform(url)
repo = MediaRepository()
media = run_async(repo.get_by_platform_id(platform_id))
media_id = str(media["id"]) if media else platform_id  # fallback
storage_dir, relative_prefix = Utils.create_web_resource_path(
    detected_platform, media_id
)
```

**Step 2: Fix video relative path (use generic filename)**

At lines 494-498, change:
```python
# OLD
file_name = os.path.basename(result["file_path"])
relative_path = f"{relative_prefix}/{file_name}"
```
to:
```python
# NEW — file is now always video.{ext}
file_name = os.path.basename(result["file_path"])
relative_path = f"{relative_prefix}/{file_name}"
# (file_name will be "video.mp4" due to ytdlp_service change)
```

> This line doesn't actually change because `os.path.basename` will now correctly get `video.mp4` from the new template. No code change needed here.

**Step 3: Commit**

```bash
git add backend/app/tasks/download_tasks.py
git commit -m "refactor: download_tasks use Snowflake ID for NAS path creation"
```

---

## Task 6: Write the NAS migration script

**Files:**
- Create: `scripts/migrate_nas_paths.py`

**Context:** A standalone Python script that runs on the NAS (where files physically exist) to move old files to the new structure and update DB paths. Supports dry-run mode.

**Step 1: Write the migration script**

```python
#!/usr/bin/env python3
"""
NAS File Path Migration Script

Migrates all parsed_media files from old path structure to unified structure:
  global/resources/web/{platform}/{parsed_media.id}/video.mp4

Usage:
  python migrate_nas_paths.py --dry-run     # Preview changes
  python migrate_nas_paths.py --execute     # Execute migration
  python migrate_nas_paths.py --rollback    # Rollback using log file

Requires:
  - SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars
  - Run on the NAS where DOWNLOAD_PATH is mounted
  - pip install supabase python-dotenv
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Load env from backend/.env if available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))
except ImportError:
    pass

from supabase import create_client

# ── Config ──
DOWNLOAD_PATH = os.environ.get("DOWNLOAD_PATH", "/volume2/media/downloads")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
LOG_FILE = os.path.join(os.path.dirname(__file__), "migration_log.json")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_all_parsed_media():
    """Fetch all parsed_media records that have download paths."""
    records = []
    offset = 0
    batch_size = 100
    while True:
        result = (
            supabase.table("parsed_media")
            .select("id, platform_id, source_platform, download_path, music_download_path, cover_download_path")
            .not_.is_("download_path", "null")
            .range(offset, offset + batch_size - 1)
            .execute()
        )
        if not result.data:
            break
        records.extend(result.data)
        if len(result.data) < batch_size:
            break
        offset += batch_size
    return records


def compute_new_paths(media):
    """Compute new paths for a media record. Returns dict of changes."""
    platform = media.get("source_platform") or "unknown"
    media_id = str(media["id"])
    new_dir = f"global/resources/web/{platform}/{media_id}"

    changes = {"media_id": media_id, "platform_id": media.get("platform_id"), "moves": [], "db_updates": {}}

    # Video
    old_video = media.get("download_path")
    if old_video:
        old_ext = Path(old_video).suffix or ".mp4"
        new_video = f"{new_dir}/video{old_ext}"
        if old_video != new_video:
            changes["moves"].append({"old": old_video, "new": new_video, "type": "video"})
            changes["db_updates"]["download_path"] = new_video

    # Audio
    old_audio = media.get("music_download_path")
    if old_audio:
        old_ext = Path(old_audio).suffix or ".mp3"
        new_audio = f"{new_dir}/audio{old_ext}"
        if old_audio != new_audio:
            changes["moves"].append({"old": old_audio, "new": new_audio, "type": "audio"})
            changes["db_updates"]["music_download_path"] = new_audio

    # Cover
    old_cover = media.get("cover_download_path")
    if old_cover:
        old_ext = Path(old_cover).suffix or ".jpg"
        new_cover = f"{new_dir}/cover{old_ext}"
        if old_cover != new_cover:
            changes["moves"].append({"old": old_cover, "new": new_cover, "type": "cover"})
            changes["db_updates"]["cover_download_path"] = new_cover

    return changes


def move_file(old_rel, new_rel, dry_run=True):
    """Move a file from old relative path to new relative path."""
    old_abs = os.path.join(DOWNLOAD_PATH, old_rel)
    new_abs = os.path.join(DOWNLOAD_PATH, new_rel)

    if not os.path.exists(old_abs):
        return "skip_missing"

    if os.path.exists(new_abs):
        return "skip_exists"

    if dry_run:
        return "would_move"

    os.makedirs(os.path.dirname(new_abs), exist_ok=True)
    shutil.move(old_abs, new_abs)
    return "moved"


def move_hls_dir(old_video_rel, new_dir_rel, dry_run=True):
    """Move HLS directory if it exists next to old video file."""
    old_video_abs = os.path.join(DOWNLOAD_PATH, old_video_rel)
    old_hls = os.path.join(os.path.dirname(old_video_abs), "hls")
    new_hls = os.path.join(DOWNLOAD_PATH, new_dir_rel, "hls")

    if not os.path.isdir(old_hls):
        return "no_hls"

    if os.path.exists(new_hls):
        return "hls_exists"

    if dry_run:
        return "would_move_hls"

    os.makedirs(os.path.dirname(new_hls), exist_ok=True)
    shutil.move(old_hls, new_hls)
    return "moved_hls"


def update_db(media_id, db_updates, dry_run=True):
    """Update parsed_media and linked resources in DB."""
    if dry_run or not db_updates:
        return

    # Update parsed_media
    supabase.table("parsed_media").update(db_updates).eq("id", media_id).execute()

    # Update linked resources
    resource_updates = {}
    if "download_path" in db_updates:
        resource_updates["file_path"] = db_updates["download_path"]
    if "cover_download_path" in db_updates:
        resource_updates["cover_image_path"] = db_updates["cover_download_path"]

    if resource_updates:
        supabase.table("resources").update(resource_updates).eq("media_id", media_id).execute()


def cleanup_empty_dirs(dry_run=True):
    """Remove empty directories left by migration."""
    base = Path(DOWNLOAD_PATH)
    removed = []

    # Walk bottom-up to remove empty dirs
    for dirpath, dirnames, filenames in os.walk(str(base / "global" / "resources" / "web"), topdown=False):
        if not dirnames and not filenames:
            if dry_run:
                removed.append(f"[DRY] Would remove empty dir: {dirpath}")
            else:
                os.rmdir(dirpath)
                removed.append(f"Removed empty dir: {dirpath}")

    # Also check old date-based dirs
    for pattern in ["2025-*", "2026-*"]:
        import glob
        for d in glob.glob(str(base / pattern)):
            if os.path.isdir(d) and not os.listdir(d):
                if dry_run:
                    removed.append(f"[DRY] Would remove empty dir: {d}")
                else:
                    os.rmdir(d)
                    removed.append(f"Removed empty dir: {d}")

    return removed


def run_migration(dry_run=True):
    """Main migration logic."""
    mode = "DRY RUN" if dry_run else "EXECUTE"
    print(f"\n{'='*60}")
    print(f"NAS Path Migration — {mode}")
    print(f"DOWNLOAD_PATH: {DOWNLOAD_PATH}")
    print(f"{'='*60}\n")

    records = get_all_parsed_media()
    print(f"Found {len(records)} parsed_media records with download paths\n")

    log = []
    stats = {"total": len(records), "migrated": 0, "skipped": 0, "errors": 0}

    for media in records:
        changes = compute_new_paths(media)
        if not changes["moves"]:
            stats["skipped"] += 1
            continue

        entry = {"media_id": changes["media_id"], "platform_id": changes["platform_id"], "moves": [], "status": "ok"}

        print(f"Media {changes['media_id']} (platform_id={changes['platform_id']}):")

        for mv in changes["moves"]:
            result = move_file(mv["old"], mv["new"], dry_run=dry_run)
            entry["moves"].append({**mv, "result": result})
            print(f"  [{result}] {mv['type']}: {mv['old']} → {mv['new']}")

        # Move HLS dir if video was moved
        old_video = media.get("download_path")
        if old_video:
            new_dir = f"global/resources/web/{media.get('source_platform', 'unknown')}/{changes['media_id']}"
            hls_result = move_hls_dir(old_video, new_dir, dry_run=dry_run)
            if hls_result != "no_hls":
                entry["hls"] = hls_result
                print(f"  [{hls_result}] hls directory")

        # Update DB
        try:
            update_db(changes["media_id"], changes["db_updates"], dry_run=dry_run)
            if not dry_run and changes["db_updates"]:
                print(f"  [db_updated] {list(changes['db_updates'].keys())}")
        except Exception as e:
            entry["status"] = f"db_error: {e}"
            entry["error"] = str(e)
            stats["errors"] += 1
            print(f"  [ERROR] DB update failed: {e}")

        log.append(entry)
        stats["migrated"] += 1
        print()

    # Cleanup empty dirs
    print("Cleaning up empty directories...")
    removed = cleanup_empty_dirs(dry_run=dry_run)
    for r in removed:
        print(f"  {r}")

    # Write log
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "stats": stats,
        "entries": log,
        "cleanup": removed,
    }

    with open(LOG_FILE, "w") as f:
        json.dump(log_data, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"Migration {mode} complete:")
    print(f"  Total: {stats['total']}")
    print(f"  Migrated: {stats['migrated']}")
    print(f"  Skipped (already correct): {stats['skipped']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Log: {LOG_FILE}")
    print(f"{'='*60}\n")

    return stats


def run_rollback():
    """Rollback migration using the log file."""
    if not os.path.exists(LOG_FILE):
        print(f"No log file found: {LOG_FILE}")
        sys.exit(1)

    with open(LOG_FILE) as f:
        log_data = json.load(f)

    print(f"\nRolling back migration from {log_data['timestamp']}...")

    for entry in log_data.get("entries", []):
        for mv in entry.get("moves", []):
            if mv["result"] == "moved":
                old_abs = os.path.join(DOWNLOAD_PATH, mv["new"])
                new_abs = os.path.join(DOWNLOAD_PATH, mv["old"])
                if os.path.exists(old_abs):
                    os.makedirs(os.path.dirname(new_abs), exist_ok=True)
                    shutil.move(old_abs, new_abs)
                    print(f"  [reverted] {mv['new']} → {mv['old']}")

    # Note: DB rollback needs manual pg_dump restore
    print("\nFile rollback complete. DB rollback requires restoring from pg_dump backup.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate NAS file paths to unified structure")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview changes without executing")
    group.add_argument("--execute", action="store_true", help="Execute migration")
    group.add_argument("--rollback", action="store_true", help="Rollback using log file")
    args = parser.parse_args()

    if args.rollback:
        run_rollback()
    else:
        run_migration(dry_run=args.dry_run)
```

**Step 2: Test dry-run locally**

```bash
cd scripts
python migrate_nas_paths.py --dry-run
```

Expected: Prints all planned moves without executing. Generates `migration_log.json` with `"mode": "DRY RUN"`.

**Step 3: Commit**

```bash
git add scripts/migrate_nas_paths.py
git commit -m "feat: add NAS path migration script (dry-run + execute + rollback)"
```

---

## Task 7: End-to-end verification

**Step 1: Start backend and test a new bilibili download**

Trigger a bilibili video parse + download. Verify:
- NAS path: `global/resources/web/bilibili/{snowflake_id}/video.mp4`
- DB `download_path`: `global/resources/web/bilibili/{snowflake_id}/video.mp4`
- No `bilibili_BV1xxx` prefix in folder name

**Step 2: Test a new douyin download**

Trigger a douyin video + music + cover download. Verify:
- NAS path: `global/resources/web/douyin/{snowflake_id}/video.mp4`
- Audio: `global/resources/web/douyin/{snowflake_id}/audio.mp3` (not `music.mp3`)
- Cover: `global/resources/web/douyin/{snowflake_id}/cover.jpg`
- `source_platform` field used correctly (not hardcoded "douyin")

**Step 3: Run migration script on NAS**

```bash
# SSH to NAS
ssh user@nas

# Dry run first
cd /path/to/mediahub/scripts
python migrate_nas_paths.py --dry-run

# Review migration_log.json
cat migration_log.json | python -m json.tool | head -50

# Execute if dry run looks good
python migrate_nas_paths.py --execute
```

**Step 4: Verify old bilibili files moved**

```bash
# Should no longer exist:
ls global/resources/web/bilibili/bilibili_BV1*/
# Should exist:
ls global/resources/web/bilibili/2782*/
```

**Step 5: Commit migration log**

```bash
git add scripts/migration_log.json
git commit -m "chore: NAS path migration completed (98 records)"
```

---

## Execution Order & Dependencies

```
Task 1 (utils.py rename) ─────────────┐
                                       ├── can run in parallel
Task 2 (ytdlp_service.py) ────────────┘

Task 3 (downloader.py) ──── depends on Task 4 (repo.get_music_data fields)
Task 4 (media_repository.py) ────┘

Task 5 (download_tasks.py) ──── depends on Task 1, 2
Task 6 (migration script) ──── depends on Task 1-5 (deploy first, then migrate)
Task 7 (verification) ──── depends on all above
```

Tasks 1 and 2 can be done in parallel. Task 4 should be done before Task 3. Task 6 creates a standalone script. Task 7 is integration verification.
