#!/usr/bin/env python3
"""
NAS Path Migration Script

Migrates old parsed_media file paths to the unified Snowflake-ID-based structure:

  OLD patterns (various):
    2025-01/{platform_id}_title.mp4          (legacy flat month-based)
    resources/web/{platform}/{platform_id}/  (old structured, keyed by platform_id)
    global/web/{platform}/{platform_id}/     (intermediate migration)
    global/resources/web/{platform}/{platform_id}/  (structured but keyed by platform_id)

  NEW (unified):
    global/resources/web/{source_platform}/{snowflake_id}/video.{ext}
    global/resources/web/{source_platform}/{snowflake_id}/audio.{ext}
    global/resources/web/{source_platform}/{snowflake_id}/cover.{ext}

Usage:
    cd /path/to/mediahub
    python scripts/migrate_nas_paths.py --dry-run      # Preview changes
    python scripts/migrate_nas_paths.py --execute       # Run migration
    python scripts/migrate_nas_paths.py --rollback      # Revert using log

Requires:
    - pip install supabase (sync client)
    - SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in env or backend/.env
    - DOWNLOAD_PATH in env or backend/.env
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate project directories
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
LOG_FILE = SCRIPT_DIR / "migration_log.json"
BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def load_env():
    """Load environment from backend/.env (best-effort)."""
    # Try python-dotenv first
    try:
        from dotenv import load_dotenv

        env_path = BACKEND_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            return
    except ImportError:
        pass

    # Fallback: manual parse
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_supabase_client():
    """Create a synchronous Supabase client using service-role key."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get(
        "SUPABASE_ANON_KEY", ""
    )

    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        print("  Checked: environment variables and backend/.env")
        sys.exit(1)

    return create_client(url, key)


def get_download_path() -> Path:
    """Get DOWNLOAD_PATH from environment."""
    path = os.environ.get("DOWNLOAD_PATH", "")
    if not path:
        print("ERROR: DOWNLOAD_PATH not configured.")
        sys.exit(1)
    p = Path(path)
    if not p.exists():
        print(f"ERROR: DOWNLOAD_PATH does not exist: {p}")
        sys.exit(1)
    return p


# ---------------------------------------------------------------------------
# Path computation helpers
# ---------------------------------------------------------------------------
def get_extension(filepath: str) -> str:
    """Extract file extension from a path, default to 'mp4' for video."""
    ext = Path(filepath).suffix.lstrip(".")
    return ext if ext else "mp4"


def compute_new_dir(source_platform: str, media_id: str) -> str:
    """Compute the new relative directory for a media record."""
    platform = source_platform or "unknown"
    return f"global/resources/web/{platform}/{media_id}"


def compute_new_video_path(old_path: str, source_platform: str, media_id: str) -> str:
    """Compute new video path: global/resources/web/{platform}/{id}/video.{ext}"""
    ext = get_extension(old_path)
    new_dir = compute_new_dir(source_platform, media_id)
    return f"{new_dir}/video.{ext}"


def compute_new_audio_path(old_path: str, source_platform: str, media_id: str) -> str:
    """Compute new audio path."""
    ext = get_extension(old_path)
    new_dir = compute_new_dir(source_platform, media_id)
    return f"{new_dir}/audio.{ext}"


def compute_new_cover_path(old_path: str, source_platform: str, media_id: str) -> str:
    """Compute new cover path."""
    ext = get_extension(old_path)
    new_dir = compute_new_dir(source_platform, media_id)
    return f"{new_dir}/cover.{ext}"


def is_already_migrated(old_path: str, new_path: str) -> bool:
    """Check if old path already matches the new path (skip migration)."""
    return old_path == new_path


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------
def move_file(base_path: Path, old_rel: str, new_rel: str, dry_run: bool) -> dict:
    """
    Move a single file from old_rel to new_rel under base_path.

    Returns a dict describing what happened.
    """
    old_full = base_path / old_rel
    new_full = base_path / new_rel
    entry = {
        "old_path": old_rel,
        "new_path": new_rel,
    }

    if dry_run:
        entry["status"] = "dry_run"
        entry["file_exists"] = old_full.exists()
        entry["target_exists"] = new_full.exists()
        return entry

    if not old_full.exists():
        entry["status"] = "source_missing"
        return entry

    if new_full.exists():
        entry["status"] = "target_exists"
        return entry

    # Create target directory
    new_full.parent.mkdir(parents=True, exist_ok=True)

    # Move file (same partition = instant rename)
    shutil.move(str(old_full), str(new_full))
    entry["status"] = "moved"
    return entry


def move_hls_directory(base_path: Path, old_video_rel: str, new_dir_rel: str, dry_run: bool) -> dict | None:
    """
    Move the hls/ directory that sits next to the old video file.

    Returns a move entry or None if no hls/ directory exists.
    """
    old_video_parent = (base_path / old_video_rel).parent
    old_hls = old_video_parent / "hls"
    if not old_hls.exists() or not old_hls.is_dir():
        return None

    new_hls = base_path / new_dir_rel / "hls"
    entry = {
        "old_path": str(old_hls.relative_to(base_path)),
        "new_path": str(new_hls.relative_to(base_path)),
        "type": "hls_directory",
    }

    if dry_run:
        entry["status"] = "dry_run"
        return entry

    if new_hls.exists():
        entry["status"] = "target_exists"
        return entry

    new_hls.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_hls), str(new_hls))
    entry["status"] = "moved"
    return entry


def cleanup_empty_dirs(base_path: Path, rel_path: str):
    """
    Remove empty ancestor directories from the old path up to base_path.
    Walks upward and removes each directory only if it is empty.
    """
    target = (base_path / rel_path).parent
    base_resolved = base_path.resolve()

    while True:
        try:
            resolved = target.resolve()
            # Stop if we've reached or gone above base_path
            if resolved == base_resolved or not str(resolved).startswith(str(base_resolved)):
                break
            if target.exists() and target.is_dir() and not any(target.iterdir()):
                target.rmdir()
            else:
                break  # Non-empty, stop
        except Exception:
            break
        target = target.parent


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------
def fetch_all_media(client) -> list:
    """
    Fetch all parsed_media records with non-null download_path.
    Paginates in batches of BATCH_SIZE using offset-based pagination.
    """
    all_records = []
    offset = 0
    fields = "id, platform_id, source_platform, download_path, music_download_path, cover_download_path"

    while True:
        result = (
            client.table("parsed_media")
            .select(fields)
            .not_.is_("download_path", "null")
            .range(offset, offset + BATCH_SIZE - 1)
            .execute()
        )

        batch = result.data or []
        all_records.extend(batch)

        if len(batch) < BATCH_SIZE:
            break  # Last page
        offset += BATCH_SIZE

    return all_records


# ---------------------------------------------------------------------------
# Core migration logic
# ---------------------------------------------------------------------------
def migrate(client, base_path: Path, dry_run: bool) -> list:
    """
    Main migration: iterate parsed_media, compute new paths, move files, update DB.

    Returns list of log entries.
    """
    log_entries = []

    print(f"\n  Fetching parsed_media records (batch size {BATCH_SIZE})...")
    records = fetch_all_media(client)
    print(f"  Found {len(records)} records with download_path.\n")

    if not records:
        return log_entries

    migrated = 0
    skipped = 0
    errors = 0

    for i, record in enumerate(records, 1):
        media_id = str(record["id"])
        platform_id = record.get("platform_id", "")
        source_platform = record.get("source_platform") or "unknown"
        old_video = record.get("download_path") or ""
        old_audio = record.get("music_download_path") or ""
        old_cover = record.get("cover_download_path") or ""

        entry = {
            "media_id": media_id,
            "platform_id": platform_id,
            "source_platform": source_platform,
            "file_moves": [],
            "db_updates": {},
            "status": "pending",
        }

        # Compute new paths
        new_video = compute_new_video_path(old_video, source_platform, media_id) if old_video else ""
        new_audio = compute_new_audio_path(old_audio, source_platform, media_id) if old_audio else ""
        new_cover = compute_new_cover_path(old_cover, source_platform, media_id) if old_cover else ""

        # Check if already fully migrated
        video_skip = not old_video or is_already_migrated(old_video, new_video)
        audio_skip = not old_audio or is_already_migrated(old_audio, new_audio)
        cover_skip = not old_cover or is_already_migrated(old_cover, new_cover)

        if video_skip and audio_skip and cover_skip:
            skipped += 1
            if dry_run and i <= 5:
                print(f"  [{i}] SKIP {media_id} (already migrated)")
            continue

        prefix = "[DRY]" if dry_run else f"[{i}/{len(records)}]"

        try:
            # --- Move files ---
            parsed_media_updates = {}
            resource_updates = {}

            # Video
            if old_video and not video_skip:
                move_entry = move_file(base_path, old_video, new_video, dry_run)
                entry["file_moves"].append(move_entry)
                parsed_media_updates["download_path"] = new_video
                resource_updates["file_path"] = new_video
                print(f"  {prefix} VIDEO {old_video} -> {new_video} [{move_entry['status']}]")

                # Move HLS directory
                hls_entry = move_hls_directory(base_path, old_video, compute_new_dir(source_platform, media_id), dry_run)
                if hls_entry:
                    entry["file_moves"].append(hls_entry)
                    print(f"  {prefix}   HLS {hls_entry['old_path']} -> {hls_entry['new_path']} [{hls_entry['status']}]")

            # Audio
            if old_audio and not audio_skip:
                move_entry = move_file(base_path, old_audio, new_audio, dry_run)
                entry["file_moves"].append(move_entry)
                parsed_media_updates["music_download_path"] = new_audio
                print(f"  {prefix} AUDIO {old_audio} -> {new_audio} [{move_entry['status']}]")

            # Cover
            if old_cover and not cover_skip:
                move_entry = move_file(base_path, old_cover, new_cover, dry_run)
                entry["file_moves"].append(move_entry)
                parsed_media_updates["cover_download_path"] = new_cover
                resource_updates["cover_image_path"] = new_cover
                print(f"  {prefix} COVER {old_cover} -> {new_cover} [{move_entry['status']}]")

            # --- Update database ---
            if not dry_run and parsed_media_updates:
                client.table("parsed_media").update(
                    parsed_media_updates
                ).eq("id", media_id).execute()
                entry["db_updates"]["parsed_media"] = parsed_media_updates

            if not dry_run and resource_updates:
                # Update resources where media_id matches
                client.table("resources").update(
                    resource_updates
                ).eq("media_id", media_id).execute()
                entry["db_updates"]["resources"] = resource_updates

            # Store old values for rollback
            entry["old_values"] = {
                "download_path": old_video,
                "music_download_path": old_audio,
                "cover_download_path": old_cover,
            }

            # --- Cleanup empty directories ---
            if not dry_run:
                if old_video and not video_skip:
                    cleanup_empty_dirs(base_path, old_video)
                if old_audio and not audio_skip:
                    cleanup_empty_dirs(base_path, old_audio)
                if old_cover and not cover_skip:
                    cleanup_empty_dirs(base_path, old_cover)

            entry["status"] = "dry_run" if dry_run else "migrated"
            migrated += 1

        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
            errors += 1
            print(f"  {prefix} ERROR {media_id}: {e}")

        log_entries.append(entry)

    print(f"\n  --- Results ---")
    print(f"  Migrated: {migrated}")
    print(f"  Skipped (already migrated): {skipped}")
    print(f"  Errors: {errors}")

    return log_entries


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------
def rollback(base_path: Path):
    """
    Rollback file moves using migration_log.json.

    NOTE: Database rollback is not automated — use pg_dump/pg_restore for that.
    This only reverses the physical file moves.
    """
    if not LOG_FILE.exists():
        print(f"ERROR: Migration log not found: {LOG_FILE}")
        sys.exit(1)

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        log_data = json.load(f)

    if log_data.get("dry_run"):
        print("ERROR: Log was from a dry-run, nothing to rollback.")
        sys.exit(1)

    entries = log_data.get("entries", [])
    if not entries:
        print("No entries to rollback.")
        return

    reversed_count = 0
    errors = 0

    for entry in entries:
        if entry.get("status") != "migrated":
            continue

        media_id = entry.get("media_id", "?")

        # Reverse file moves (in reverse order for HLS dirs before files)
        for move in reversed(entry.get("file_moves", [])):
            if move.get("status") != "moved":
                continue

            old_path = move["old_path"]
            new_path = move["new_path"]
            is_hls = move.get("type") == "hls_directory"

            src = base_path / new_path  # current location (new)
            dst = base_path / old_path  # original location (old)

            if not src.exists():
                print(f"  [WARN] Source not found for rollback: {new_path}")
                continue

            if dst.exists():
                print(f"  [WARN] Target already exists, skip rollback: {old_path}")
                continue

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                reversed_count += 1
                label = "HLS" if is_hls else "FILE"
                print(f"  [ROLLBACK] {label} {new_path} -> {old_path}")
            except Exception as e:
                errors += 1
                print(f"  [ERROR] Failed to rollback {new_path}: {e}")

        # Cleanup empty new directories
        for move in entry.get("file_moves", []):
            if move.get("status") == "moved" and move.get("type") != "hls_directory":
                new_path = move["new_path"]
                cleanup_empty_dirs(base_path, new_path)

    print(f"\n  --- Rollback Results ---")
    print(f"  Files reversed: {reversed_count}")
    print(f"  Errors: {errors}")
    print(f"\n  NOTE: Database records were NOT rolled back.")
    print(f"  To restore DB, use: pg_dump / pg_restore or apply old values from the log.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Migrate NAS file paths to unified Snowflake-ID-based structure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/migrate_nas_paths.py --dry-run     # Preview changes
  python scripts/migrate_nas_paths.py --execute      # Run migration
  python scripts/migrate_nas_paths.py --rollback     # Revert file moves
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without making changes",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Execute the migration (moves files + updates DB)",
    )
    group.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback file moves using migration_log.json",
    )
    args = parser.parse_args()

    # Load environment
    load_env()

    mode = "DRY RUN" if args.dry_run else ("ROLLBACK" if args.rollback else "EXECUTE")
    start_time = time.time()

    print("=" * 70)
    print("  NAS Path Migration — Unified Snowflake ID Structure")
    print(f"  Mode: {mode}")
    print(f"  Time: {datetime.now().isoformat()}")
    print("=" * 70)

    base_path = get_download_path()
    print(f"  DOWNLOAD_PATH: {base_path}")

    if args.rollback:
        print(f"  Log file: {LOG_FILE}")
        rollback(base_path)
    else:
        client = get_supabase_client()

        log_entries = migrate(client, base_path, dry_run=args.dry_run)

        # Write migration log
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "mode": mode,
            "dry_run": args.dry_run,
            "download_path": str(base_path),
            "total_records": len(log_entries),
            "entries": log_entries,
        }

        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
        print(f"\n  Migration log saved: {LOG_FILE}")

    elapsed = time.time() - start_time
    print(f"\n  Elapsed: {elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
