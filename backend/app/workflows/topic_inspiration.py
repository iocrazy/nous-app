from __future__ import annotations

from datetime import datetime

from dbos import DBOS
from loguru import logger

from app.repositories.hotspots_repository import HotspotsRepository
from app.repositories.signal_sources_repository import SignalSourcesRepository
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.keyword_filter import keyword_filter

# Phase 1: no per-user interest yet -> global keep-all pre-filter.
_GLOBAL_INCLUDE: list[str] = []
_GLOBAL_EXCLUDE: list[str] = []


async def run_topic_fetch_once(
    *,
    sources_repo: SignalSourcesRepository | None = None,
    hotspots_repo: HotspotsRepository | None = None,
) -> dict:
    sources_repo = sources_repo or SignalSourcesRepository()
    hotspots_repo = hotspots_repo or HotspotsRepository()
    sources = await sources_repo.list_enabled()
    ok = failed = written = 0
    for src in sources:
        sid = str(src["id"])
        try:
            adapter = get_adapter(src["kind"])
            candidates = await adapter.fetch(src)
            candidates = keyword_filter(
                candidates, include=_GLOBAL_INCLUDE, exclude=_GLOBAL_EXCLUDE
            )
            rows = hotspots_repo.build_rows(
                candidates, source_id=sid, category=src.get("category")
            )
            written += await hotspots_repo.upsert_ignore(rows)
            await sources_repo.mark_health(sid, ok=True)
            ok += 1
        except (
            Exception
        ) as e:  # noqa: BLE001 — per-source isolation, never break the batch
            logger.warning(f"topic source {sid} ({src.get('name')}) failed: {e}")
            await sources_repo.mark_health(sid, ok=False, error=str(e))
            failed += 1
    summary = {"sources": len(sources), "ok": ok, "failed": failed, "written": written}
    logger.info(f"topic_fetch done: {summary}")
    return summary


@DBOS.scheduled("*/30 * * * *")  # every 30 min
@DBOS.workflow()
async def topic_fetch_workflow(scheduled_time: datetime, actual_time: datetime) -> None:
    await run_topic_fetch_once()
