#!/usr/bin/env python3
"""
Migrate existing resource files into versioned storage (v1/ subdirectory).

For each resource with a file_path, this script:
1. Creates a v1/ subdirectory under the resource's storage directory
2. Moves the original file into v1/
3. Moves any thumbnail/cover files into v1/
4. Updates resources.file_path and resources.thumbnail_path
5. Updates resource_versions.file_path and resource_versions.thumbnail_path

Usage:
    cd backend
    uv run python scripts/migrate_to_versioned_storage.py [--dry-run]
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from dotenv import load_dotenv

load_dotenv()


async def main(dry_run: bool = False):
    from app.db.supabase_client import get_async_supabase_admin
    from app.core.config import settings

    client = await get_async_supabase_admin()
    base = Path(settings.DOWNLOAD_PATH)

    print(f"Base path: {base}")
    print(f"Dry run: {dry_run}")
    print("=" * 60)

    # Fetch all resources with file_path
    result = (
        await client.table("resources")
        .select("id, file_path, thumbnail_path, cover_image_path, source_type")
        .execute()
    )
    resources = result.data or []

    migrated = 0
    skipped = 0
    errors = 0

    for r in resources:
        resource_id = r["id"]
        file_path = r.get("file_path")
        if not file_path:
            skipped += 1
            continue

        # Skip if already versioned (path contains /v1/ or /v2/ etc.)
        if "/v1/" in file_path or "/v2/" in file_path:
            print(f"  SKIP {resource_id}: already versioned ({file_path})")
            skipped += 1
            continue

        full_path = base / file_path
        if not full_path.exists():
            print(f"  SKIP {resource_id}: file not found ({full_path})")
            skipped += 1
            continue

        # Determine the resource directory and build v1 path
        resource_dir = full_path.parent
        v1_dir = resource_dir / "v1"
        filename = full_path.name

        new_file_path = str(Path(file_path).parent / "v1" / filename)

        print(f"  MIGRATE {resource_id}:")
        print(f"    {file_path} → {new_file_path}")

        if not dry_run:
            try:
                v1_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(full_path), str(v1_dir / filename))
            except Exception as e:
                print(f"    ERROR moving file: {e}")
                errors += 1
                continue

        # Move thumbnail if it exists in the same directory
        new_thumb_path = None
        thumb_rel = r.get("thumbnail_path")
        if thumb_rel and not thumb_rel.startswith("http") and "/v1/" not in thumb_rel:
            thumb_full = base / thumb_rel
            if thumb_full.exists() and thumb_full.parent == resource_dir:
                thumb_name = thumb_full.name
                new_thumb_path = str(Path(thumb_rel).parent / "v1" / thumb_name)
                print(f"    thumb: {thumb_rel} → {new_thumb_path}")
                if not dry_run:
                    try:
                        shutil.move(str(thumb_full), str(v1_dir / thumb_name))
                    except Exception as e:
                        print(f"    ERROR moving thumbnail: {e}")
                        new_thumb_path = None

        # Move cover image if different from thumbnail
        new_cover_path = None
        cover_rel = r.get("cover_image_path")
        if (
            cover_rel
            and cover_rel != thumb_rel
            and not cover_rel.startswith("http")
            and "/v1/" not in cover_rel
        ):
            cover_full = base / cover_rel
            if cover_full.exists() and cover_full.parent == resource_dir:
                cover_name = cover_full.name
                new_cover_path = str(Path(cover_rel).parent / "v1" / cover_name)
                print(f"    cover: {cover_rel} → {new_cover_path}")
                if not dry_run:
                    try:
                        shutil.move(str(cover_full), str(v1_dir / cover_name))
                    except Exception as e:
                        print(f"    ERROR moving cover: {e}")
                        new_cover_path = None

        # Also move any preview_sprite.jpg and thumbnail/ dir
        if not dry_run:
            for extra in ("preview_sprite.jpg",):
                extra_path = resource_dir / extra
                if extra_path.exists():
                    try:
                        shutil.move(str(extra_path), str(v1_dir / extra))
                        print(f"    extra: {extra} → v1/{extra}")
                    except Exception as e:
                        print(f"    ERROR moving {extra}: {e}")

            # Move thumbnail directory if it exists (hover scrub thumbnails)
            thumb_dir = resource_dir / "thumbnail"
            if thumb_dir.exists() and thumb_dir.is_dir():
                target_dir = v1_dir / "thumbnail"
                try:
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    shutil.move(str(thumb_dir), str(target_dir))
                    print(f"    dir: thumbnail/ → v1/thumbnail/")
                except Exception as e:
                    print(f"    ERROR moving thumbnail dir: {e}")

        # Update database
        if not dry_run:
            try:
                update_data = {"file_path": new_file_path}
                if new_thumb_path:
                    update_data["thumbnail_path"] = new_thumb_path
                if new_cover_path:
                    update_data["cover_image_path"] = new_cover_path

                await client.table("resources").update(update_data).eq(
                    "id", resource_id
                ).execute()

                # Update resource_versions too
                version_update = {"file_path": new_file_path}
                if new_thumb_path:
                    version_update["thumbnail_path"] = new_thumb_path

                await client.table("resource_versions").update(version_update).eq(
                    "resource_id", resource_id
                ).eq("version_number", 1).execute()

                migrated += 1
            except Exception as e:
                print(f"    ERROR updating DB: {e}")
                errors += 1

    print("=" * 60)
    print(f"Results: {migrated} migrated, {skipped} skipped, {errors} errors")
    print(f"Total resources: {len(resources)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate to versioned storage")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
