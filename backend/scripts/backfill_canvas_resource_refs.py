# backend/scripts/backfill_canvas_resource_refs.py
"""One-time backfill: rebuild canvas_resource_refs from every canvas's
nodes_json. Idempotent (replace-all per canvas) — safe to re-run.

Usage:  cd backend && uv run python scripts/backfill_canvas_resource_refs.py
"""

from __future__ import annotations

import asyncio

from loguru import logger

from app.db import engine as db_engine
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.services.canvas.asset_refs import extract_asset_refs


async def main() -> None:
    rows = await db_engine.fetch_all("SELECT id::text AS id, nodes_json FROM canvases")
    refs_repo = CanvasRefsRepository()
    total_canvases = 0
    total_refs = 0
    for row in rows or []:
        refs = extract_asset_refs(row.get("nodes_json"))
        await refs_repo.replace_for_canvas(row["id"], refs)
        total_canvases += 1
        total_refs += len(refs)
    logger.info("backfill done: {} canvases, {} refs", total_canvases, total_refs)


if __name__ == "__main__":
    asyncio.run(main())
