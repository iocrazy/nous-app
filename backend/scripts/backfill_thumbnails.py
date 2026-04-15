#!/usr/bin/env python3
"""Backfill thumbnails (webp) for resources that lack thumbnail_path.

Usage:
    cd backend && uv run python scripts/backfill_thumbnails.py
    cd backend && uv run python scripts/backfill_thumbnails.py --dry-run
    cd backend && uv run python scripts/backfill_thumbnails.py --limit 50
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.core.config import settings
from app.db.supabase_client import get_async_supabase_admin
from app.services.thumbnail_service import ThumbnailService


async def find_candidates(limit: int | None) -> list[dict]:
    """Find resources missing thumbnail_path with a local file_path."""
    supabase = await get_async_supabase_admin()
    query = (
        supabase.table("resources")
        .select("id,file_path,mime_type,thumbnail_path")
        .is_("thumbnail_path", "null")
        .not_.is_("file_path", "null")
        .eq("is_trashed", False)
    )
    if limit:
        query = query.limit(limit)
    res = await query.execute()
    return res.data or []


async def main(dry_run: bool, limit: int | None) -> None:
    candidates = await find_candidates(limit)
    total = len(candidates)
    logger.info(f"Found {total} resources missing thumbnails")

    if dry_run:
        for row in candidates[:20]:
            logger.info(f"[dry-run] would process {row['id']}: {row.get('file_path')}")
        if total > 20:
            logger.info(f"... and {total - 20} more")
        return

    svc = ThumbnailService()
    ok_count = 0
    fail_count = 0
    skip_count = 0

    for i, row in enumerate(candidates, 1):
        rid = row["id"]
        file_path = row.get("file_path")
        mime = row.get("mime_type") or ""

        if not file_path or file_path.startswith("http"):
            skip_count += 1
            continue

        abs_path = Path(settings.DOWNLOAD_PATH) / file_path
        if not abs_path.exists():
            logger.warning(f"[{i}/{total}] {rid}: source missing {abs_path}")
            skip_count += 1
            continue

        try:
            result = await svc.generate_thumbnail(rid, file_path, mime)
            if result:
                ok_count += 1
                logger.info(f"[{i}/{total}] ✓ {rid} → {result}")
            else:
                fail_count += 1
                logger.warning(f"[{i}/{total}] ✗ {rid} (gen returned None)")
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
