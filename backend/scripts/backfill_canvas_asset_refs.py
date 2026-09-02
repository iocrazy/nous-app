# backend/scripts/backfill_canvas_asset_refs.py
"""One-time backfill: rebuild canvas_asset_refs from every canvas's nodes_json.

Mirrors ``backfill_canvas_resource_refs.py``. Idempotent (replace-all per
canvas) — safe to re-run, and safe to run BEFORE any canvas has an asset node:
it then rewrites every canvas's (empty) ref set, which is a no-op on the data
and a real exercise of the write path.

Reports the malformed-node count separately from the ref count. A run that
wrote 0 refs because every asset node was unreadable must not look like a run
over a library with no asset nodes — those are different facts.

Usage:  cd backend && uv run python scripts/backfill_canvas_asset_refs.py
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope
from app.models import Canvases
from app.repositories.canvas_asset_refs_repository import CanvasAssetRefsRepository
from app.services.canvas.asset_node_refs import extract_asset_node_refs


async def main() -> None:
    async with read_scope() as session:
        rows = (
            (await session.execute(select(Canvases.id, Canvases.nodes_json)))
            .mappings()
            .all()
        )

    refs_repo = CanvasAssetRefsRepository()
    total_canvases = 0
    total_refs = 0
    total_skipped = 0
    for row in rows:
        refs, skipped = extract_asset_node_refs(row["nodes_json"])
        await refs_repo.replace_for_canvas(str(row["id"]), refs)
        total_canvases += 1
        total_refs += len(refs)
        total_skipped += skipped
    logger.info(
        "backfill done: {} canvases, {} refs, {} skipped asset nodes",
        total_canvases,
        total_refs,
        total_skipped,
    )


if __name__ == "__main__":
    asyncio.run(main())
