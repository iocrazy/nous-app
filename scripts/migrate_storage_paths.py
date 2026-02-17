#!/usr/bin/env python3
"""
Storage Path Migration Script

Migrates file storage from flat structure to team-isolated structure:

  BEFORE:
    resources/{resource_id}/{filename}          (uploads)
    resources/web/{platform}/{ext_id}/...       (downloads)

  AFTER:
    teams/{team_id}/uploads/{resource_id}/{filename}  (uploads)
    global/web/{platform}/{ext_id}/...                (downloads)

Usage:
    cd backend
    uv run python ../scripts/migrate_storage_paths.py [--dry-run]

Requires:
    - DOWNLOAD_PATH env var or backend/.env configured
    - Supabase connection configured
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Add backend to path so we can import app modules
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))


def get_supabase_client():
    """Get a synchronous Supabase client."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get(
        "SUPABASE_ANON_KEY", ""
    )
    if not url or not key:
        # Try loading from .env
        env_file = backend_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get(
            "SUPABASE_ANON_KEY", ""
        )

    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be set")
        sys.exit(1)

    return create_client(url, key)


def get_download_path() -> Path:
    """Get the DOWNLOAD_PATH from env or .env file."""
    path = os.environ.get("DOWNLOAD_PATH", "")
    if not path:
        env_file = backend_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("DOWNLOAD_PATH="):
                    path = line.split("=", 1)[1].strip()
                    break
    if not path:
        print("ERROR: DOWNLOAD_PATH not configured")
        sys.exit(1)
    return Path(path)


def migrate_uploads(client, base_path: Path, dry_run: bool) -> list:
    """Migrate uploaded files: resources/{id}/ -> teams/{team_id}/uploads/{id}/"""
    log = []

    # Get all uploaded resources with their resource_items
    result = (
        client.table("resources")
        .select("id, file_path, filename")
        .eq("source_type", "upload")
        .not_.is_("file_path", "null")
        .execute()
    )

    if not result.data:
        print("  No uploaded resources found.")
        return log

    for resource in result.data:
        resource_id = str(resource["id"])
        old_path = resource.get("file_path", "")

        if not old_path or not old_path.startswith("resources/"):
            # Already migrated or unknown format
            continue

        # Get scope from resource_items
        item_result = (
            client.table("resource_items")
            .select("scope_id")
            .eq("resource_id", resource_id)
            .limit(1)
            .execute()
        )

        if not item_result.data:
            print(f"  WARN: Resource {resource_id} has no resource_item, skipping")
            continue

        scope_id = item_result.data[0]["scope_id"]

        # Build new path
        # Old: resources/{resource_id}/{filename}
        # New: teams/{scope_id}/uploads/{resource_id}/{filename}
        old_rel = old_path  # e.g. resources/123/file.mp4
        parts = old_rel.split("/")  # ['resources', '123', 'file.mp4']
        if len(parts) < 3:
            print(f"  WARN: Unexpected path format: {old_rel}, skipping")
            continue

        # Reconstruct: everything after resources/{resource_id}/
        sub_path = "/".join(parts[2:])  # file.mp4 or versions/v2_file.mp4
        new_rel = f"teams/{scope_id}/uploads/{resource_id}/{sub_path}"

        old_full = base_path / old_rel
        new_full = base_path / new_rel

        entry = {
            "resource_id": resource_id,
            "old_path": old_rel,
            "new_path": new_rel,
            "status": "pending",
        }

        if dry_run:
            exists = old_full.exists()
            entry["status"] = "dry_run"
            entry["file_exists"] = exists
            print(f"  [DRY] {old_rel} -> {new_rel} (exists={exists})")
        else:
            if old_full.exists():
                new_full.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_full), str(new_full))

                # Update database
                client.table("resources").update(
                    {"file_path": new_rel}
                ).eq("id", resource_id).execute()

                # Update version records too
                versions = (
                    client.table("resource_versions")
                    .select("id, file_path")
                    .eq("resource_id", resource_id)
                    .not_.is_("file_path", "null")
                    .execute()
                )
                for ver in versions.data or []:
                    ver_old = ver.get("file_path", "")
                    if ver_old.startswith("resources/"):
                        ver_parts = ver_old.split("/")
                        ver_sub = "/".join(ver_parts[2:])
                        ver_new = f"teams/{scope_id}/uploads/{resource_id}/{ver_sub}"
                        client.table("resource_versions").update(
                            {"file_path": ver_new}
                        ).eq("id", ver["id"]).execute()

                entry["status"] = "migrated"
                print(f"  [OK] {old_rel} -> {new_rel}")
            else:
                entry["status"] = "file_not_found"
                print(f"  [MISS] {old_rel} (file not found, updating DB only)")
                # Still update DB path
                client.table("resources").update(
                    {"file_path": new_rel}
                ).eq("id", resource_id).execute()

        log.append(entry)

    # Clean up empty directories
    if not dry_run:
        old_resources_dir = base_path / "resources"
        if old_resources_dir.exists():
            # Only remove resource ID dirs (not 'web' subdirectory)
            for child in old_resources_dir.iterdir():
                if child.is_dir() and child.name != "web":
                    try:
                        shutil.rmtree(child)
                        print(f"  [CLEANUP] Removed empty dir: {child.name}")
                    except Exception as e:
                        print(f"  [WARN] Failed to remove {child}: {e}")

    return log


def migrate_downloads(client, base_path: Path, dry_run: bool) -> list:
    """Migrate downloads: resources/web/ -> global/web/"""
    log = []

    old_web_dir = base_path / "resources" / "web"
    if not old_web_dir.exists():
        print("  No resources/web/ directory found, skipping.")
        return log

    new_web_dir = base_path / "global" / "web"

    if dry_run:
        print(f"  [DRY] Would move {old_web_dir} -> {new_web_dir}")
        log.append({
            "type": "web_directory",
            "old_path": "resources/web",
            "new_path": "global/web",
            "status": "dry_run",
        })
    else:
        new_web_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_web_dir.exists():
            # Merge into existing
            for platform_dir in old_web_dir.iterdir():
                target = new_web_dir / platform_dir.name
                if target.exists():
                    for item in platform_dir.iterdir():
                        shutil.move(str(item), str(target / item.name))
                else:
                    shutil.move(str(platform_dir), str(target))
        else:
            shutil.move(str(old_web_dir), str(new_web_dir))
        print(f"  [OK] resources/web/ -> global/web/")
        log.append({
            "type": "web_directory",
            "old_path": "resources/web",
            "new_path": "global/web",
            "status": "migrated",
        })

    # Update resources table: only file_path and cover_image_path exist
    for field in ["file_path", "cover_image_path"]:
        try:
            result = (
                client.table("resources")
                .select(f"id, {field}")
                .like(field, "resources/web/%")
                .execute()
            )
            for row in result.data or []:
                old_val = row[field]
                new_val = old_val.replace("resources/web/", "global/web/", 1)
                if dry_run:
                    print(f"  [DRY] resources.{row['id']}.{field}: {old_val} -> {new_val}")
                else:
                    client.table("resources").update(
                        {field: new_val}
                    ).eq("id", row["id"]).execute()
                    print(f"  [OK] resources.{row['id']}.{field} updated")
        except Exception as e:
            print(f"  [WARN] Failed to update resources.{field}: {e}")

    # Update douyin_videos table paths
    for field in ["video_path", "music_path", "cover_path"]:
        try:
            result = (
                client.table("douyin_videos")
                .select(f"id, {field}")
                .like(field, "resources/web/%")
                .execute()
            )
            for row in result.data or []:
                old_val = row[field]
                new_val = old_val.replace("resources/web/", "global/web/", 1)
                if dry_run:
                    print(f"  [DRY] douyin_videos {row['id']}.{field}: {old_val} -> {new_val}")
                else:
                    client.table("douyin_videos").update(
                        {field: new_val}
                    ).eq("id", row["id"]).execute()
                    print(f"  [OK] douyin_videos {row['id']}.{field} updated")
        except Exception as e:
            print(f"  [WARN] Failed to update douyin_videos.{field}: {e}")

    # Clean up empty resources/ directory
    if not dry_run:
        old_resources_dir = base_path / "resources"
        if old_resources_dir.exists():
            try:
                # Only remove if empty
                if not any(old_resources_dir.iterdir()):
                    old_resources_dir.rmdir()
                    print("  [CLEANUP] Removed empty resources/ directory")
            except Exception:
                pass

    return log


def main():
    parser = argparse.ArgumentParser(
        description="Migrate storage paths to team-isolated structure"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Storage Path Migration")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    base_path = get_download_path()
    print(f"DOWNLOAD_PATH: {base_path}")

    if not base_path.exists():
        print(f"ERROR: DOWNLOAD_PATH does not exist: {base_path}")
        sys.exit(1)

    client = get_supabase_client()
    all_logs = []

    # Phase 1: Migrate uploads
    print("\n--- Phase 1: Migrate uploaded files ---")
    upload_logs = migrate_uploads(client, base_path, args.dry_run)
    all_logs.extend(upload_logs)

    # Phase 2: Migrate downloads
    print("\n--- Phase 2: Migrate downloaded files ---")
    download_logs = migrate_downloads(client, base_path, args.dry_run)
    all_logs.extend(download_logs)

    # Save migration log
    log_file = Path(__file__).parent / "migration_log.json"
    with open(log_file, "w") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "dry_run": args.dry_run,
                "download_path": str(base_path),
                "entries": all_logs,
            },
            f,
            indent=2,
        )
    print(f"\nMigration log saved to: {log_file}")

    # Summary
    migrated = sum(1 for e in all_logs if e.get("status") == "migrated")
    skipped = sum(1 for e in all_logs if e.get("status") == "file_not_found")
    dry = sum(1 for e in all_logs if e.get("status") == "dry_run")

    print(f"\n--- Summary ---")
    print(f"  Migrated: {migrated}")
    print(f"  File not found (DB updated): {skipped}")
    if args.dry_run:
        print(f"  Dry run entries: {dry}")
    print("=" * 60)


if __name__ == "__main__":
    main()
