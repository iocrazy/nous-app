#!/usr/bin/env python3
"""Backfill resolution (WxH) for existing image resources that lack it.

Image uploads only started recording resolution recently (Pillow probe in
ResourcesService). Images uploaded before that have resolution = NULL, so the
Justified view can't size them by aspect ratio. This one-off reads each image
off disk with Pillow and writes resolution = "WxH" (same format as videos).

Run on a host where the resource files are mounted (the backend container has
/app/downloads):

    cd backend && uv run python scripts/backfill_image_resolution.py --dry-run
    cd backend && uv run python scripts/backfill_image_resolution.py
    cd backend && uv run python scripts/backfill_image_resolution.py --limit 100

    # inside the deployed container:
    docker exec mediahub-app-backend python scripts/backfill_image_resolution.py
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.core.config import settings
from app.db.supabase_client import get_async_supabase_admin


async def find_candidates(limit: int | None) -> list[dict]:
    """Image resources with a local file_path and no resolution yet."""
    supabase = await get_async_supabase_admin()
    query = (
        supabase.table("resources")
        .select("id,file_path,mime_type,file_type,resolution")
        .is_("resolution", "null")
        .like("mime_type", "image/%")
        .neq("file_path", "")
        .eq("is_trashed", False)
    )
    if limit:
        query = query.limit(limit)
    res = await query.execute()
    return res.data or []


def _probe_size(abs_path: Path) -> str | None:
    """Return 'WxH' from an image file via Pillow, or None on failure."""
    from PIL import Image

    with Image.open(abs_path) as img:
        w, h = img.size
    if w and h:
        return f"{w}x{h}"
    return None


async def main(dry_run: bool, limit: int | None) -> None:
    candidates = await find_candidates(limit)
    total = len(candidates)
    logger.info(f"Found {total} image resources missing resolution")

    if dry_run:
        for row in candidates[:20]:
            logger.info(f"[dry-run] would probe {row['id']}: {row.get('file_path')}")
        if total > 20:
            logger.info(f"... and {total - 20} more")
        return

    supabase = await get_async_supabase_admin()
    ok_count = 0
    fail_count = 0
    skip_count = 0

    for i, row in enumerate(candidates, 1):
        rid = row["id"]
        file_path = row.get("file_path")

        if not file_path or file_path.startswith("http"):
            skip_count += 1
            continue

        abs_path = Path(settings.DOWNLOAD_PATH) / file_path
        if not abs_path.exists():
            logger.warning(f"[{i}/{total}] {rid}: source missing {abs_path}")
            skip_count += 1
            continue

        try:
            wxh = await asyncio.to_thread(_probe_size, abs_path)
            if not wxh:
                fail_count += 1
                logger.warning(f"[{i}/{total}] ✗ {rid} (no dimensions)")
                continue
            await (
                supabase.table("resources")
                .update({"resolution": wxh})
                .eq("id", rid)
                .execute()
            )
            ok_count += 1
            logger.info(f"[{i}/{total}] ✓ {rid} → {wxh}")
        except Exception as e:
            fail_count += 1
            logger.error(f"[{i}/{total}] ✗ {rid}: {e}")

    logger.info(
        f"Done. ok={ok_count} fail={fail_count} skip={skip_count} total={total}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    asyncio.run(main(args.dry_run, args.limit))
