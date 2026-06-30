from __future__ import annotations

import asyncio
import json
from datetime import datetime

from dbos import DBOS
from loguru import logger

from app.repositories.hotspots_repository import HotspotsRepository
from app.repositories.signal_sources_repository import SignalSourcesRepository
from app.repositories.topic_groups_repository import TopicGroupRepository
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.clustering import (
    CLUSTER_MAX_ITEMS,
    WINDOW_HOURS,
    is_match,
)
from app.services.topics.content_fetcher import (
    fetch_article_text,
    load_content_fetch_config,
)
from app.services.topics.embedding_service import TopicEmbeddingService
from app.services.topics.keyword_filter import (
    load_prefilter_config,
    relevance_filter,
)
from app.services.topics.scoring import compute_quality, load_scoring_config
from app.services.topics.topic_scorer import TopicScorerService

# Phase 2 scoring bounds: how many unscored hotspots to enrich per tick, and the
# per-LLM-call batch size. Keeps cost/latency bounded; the feed catches up over
# successive ticks.
_SCORE_MAX_ITEMS = 60
# Small batches keep each LLM call's JSON array short enough that qwen3-6-35b
# finishes the response without hitting the output-token ceiling and returning
# truncated JSON ("Unterminated string"). Larger batches dropped the
# reason/ai_summary for most items. See topic_scorer._MAX_OUTPUT_TOKENS.
_SCORE_BATCH_SIZE = 6

# Embedding pass bound: vectors per tick (one Ark call each). The feed catches
# up over successive ticks; embeddings feed the Phase-3 cross-source clustering.
_EMBED_MAX_ITEMS = 40


def _embed_text(row: dict) -> str:
    """Text to embed for a hotspot: title + AI summary (or raw content)."""
    title = (row.get("title") or "").strip()
    body = (row.get("ai_summary") or row.get("content_original") or "").strip()
    return f"{title}\n{body}".strip() if body else title


async def embed_unembedded_once(
    *,
    hotspots_repo: HotspotsRepository | None = None,
    embedder: TopicEmbeddingService | None = None,
    max_items: int = _EMBED_MAX_ITEMS,
) -> dict:
    """Compute + store embeddings for hotspots that lack one.

    Additive + isolated: never blocks fetching/scoring. Skips silently when the
    embedding provider isn't admin-configured (embed_text returns None).
    """
    hotspots_repo = hotspots_repo or HotspotsRepository()
    embedder = embedder or TopicEmbeddingService()
    rows = await hotspots_repo.list_unembedded(limit=max_items)
    if not rows:
        return {"unembedded": 0, "embedded": 0}
    embedded = 0
    for r in rows:
        vec = await embedder.embed_text(_embed_text(r))
        if vec:
            await hotspots_repo.patch_embedding(str(r["id"]), vec)
            embedded += 1
    summary = {"unembedded": len(rows), "embedded": embedded}
    logger.info(f"topic_embed done: {summary}")
    return summary


async def enrich_content_once(
    *,
    hotspots_repo: HotspotsRepository | None = None,
    sources_repo: SignalSourcesRepository | None = None,
    max_items: int | None = None,
) -> dict:
    """L0.5: backfill the article body for curated (tier ≤ tier_max) news whose
    body is still empty, via trafilatura. Backfilled rows clear their embedding
    so the embed pass recomputes on the richer text.

    Admin-gated (``topics.content_fetch``), DISABLED by default. Additive +
    isolated: never blocks the rest of the tick. Politeness-bounded by the
    configured concurrency. Returns a summary; empty when disabled."""
    cfg = await load_content_fetch_config()
    if not cfg.enabled:
        return {"enabled": False, "needing": 0, "filled": 0}

    hotspots_repo = hotspots_repo or HotspotsRepository()
    sources_repo = sources_repo or SignalSourcesRepository()

    tiers = await sources_repo.tier_map()
    article_source_ids = [sid for sid, t in tiers.items() if t <= cfg.tier_max]
    rows = await hotspots_repo.list_needing_content(
        article_source_ids, limit=cfg.max_items if max_items is None else max_items
    )
    if not rows:
        return {"enabled": True, "needing": 0, "filled": 0}

    sem = asyncio.Semaphore(cfg.concurrency)
    filled = 0

    async def _one(row: dict) -> bool:
        async with sem:
            text = await fetch_article_text(
                str(row.get("url") or ""), timeout_s=cfg.timeout_s
            )
        if text and len(text) >= cfg.min_chars:
            await hotspots_repo.patch_content(str(row["id"]), text)
            return True
        return False

    results = await asyncio.gather(*(_one(r) for r in rows), return_exceptions=True)
    filled = sum(1 for r in results if r is True)
    summary = {"enabled": True, "needing": len(rows), "filled": filled}
    logger.info(f"topic_enrich done: {summary}")
    return summary


async def cluster_unassigned_once(
    *,
    repo: TopicGroupRepository | None = None,
    window_hours: int = WINDOW_HOURS,
    max_items: int = CLUSTER_MAX_ITEMS,
) -> dict:
    """Group embedded-but-unclustered hotspots by cosine similarity.

    Each unclustered hotspot in the window joins the most-similar existing group
    if similarity clears the threshold, else seeds a new group. Cross-source
    membership (``source_count``) is what powers the "seen on N platforms"
    signal + dedup. Additive + isolated: never blocks fetch/score/embed.
    """
    repo = repo or TopicGroupRepository()
    rows = await repo.list_unclustered(window_hours=window_hours, limit=max_items)
    if not rows:
        return {"processed": 0, "clustered": 0, "new_groups": 0}
    clustered = new_groups = 0
    for r in rows:
        vec = r.get("vec")
        if not vec:
            continue
        try:
            nearest = await repo.nearest_group(vec, window_hours=window_hours)
            if nearest and is_match(nearest.get("sim")):
                gid = nearest["id"]
                await repo.assign_hotspot(r["id"], gid)
                await repo.recompute_group(gid)
                clustered += 1
            else:
                gid = await repo.create_group(label=r.get("title") or "", vec=vec)
                if gid is not None:
                    await repo.assign_hotspot(r["id"], gid)
                    new_groups += 1
        except Exception as e:  # noqa: BLE001 — per-item isolation
            logger.warning(f"topic clustering item {r.get('id')} failed: {e}")
    summary = {
        "processed": len(rows),
        "clustered": clustered,
        "new_groups": new_groups,
    }
    logger.info(f"topic_cluster done: {summary}")
    return summary


async def run_topic_fetch_once(
    *,
    sources_repo: SignalSourcesRepository | None = None,
    hotspots_repo: HotspotsRepository | None = None,
) -> dict:
    sources_repo = sources_repo or SignalSourcesRepository()
    hotspots_repo = hotspots_repo or HotspotsRepository()
    sources = await sources_repo.list_enabled()

    # Dedup the EXPENSIVE upstream fetch by (kind, config): N users subscribing
    # to the same platform_id / feed url hit the upstream ONCE per cycle, not N
    # times. This caps NAS + upstream load at O(distinct sources) instead of
    # O(total sources) and removes the anti-scraping amplification. Per-source
    # work (tier-specific L0 filter, per-user hotspot rows, health) stays per
    # source — only the network fetch is shared.
    groups: dict[tuple[str, str], list[dict]] = {}
    for src in sources:
        key = (src["kind"], json.dumps(src.get("config") or {}, sort_keys=True))
        groups.setdefault(key, []).append(src)

    # L0 pre-filter config (admin-tuned: enabled / keywords / tier_from). Loaded
    # ONCE per tick — same gate applies to every source this run.
    prefilter_cfg = await load_prefilter_config()

    ok = failed = written = dropped = upstream = 0
    for (kind, _cfg), group in groups.items():
        try:
            fetched = await get_adapter(kind).fetch(group[0])  # one upstream call
            upstream += 1
        except Exception as e:  # noqa: BLE001 — shared upstream: whole group fails
            for src in group:
                await sources_repo.mark_health(str(src["id"]), ok=False, error=str(e))
                failed += 1
            logger.warning(
                f"topic source group ({kind}, {group[0].get('name')}) failed: {e}"
            )
            continue
        for src in group:
            sid = str(src["id"])
            try:
                # L0 pre-filter is tier-specific, so it runs per source.
                candidates = relevance_filter(
                    fetched,
                    tier=int(src.get("tier") or 2),
                    config=prefilter_cfg,
                )
                dropped += len(fetched) - len(candidates)
                rows = hotspots_repo.build_rows(
                    candidates,
                    source_id=sid,
                    category=src.get("category"),
                    source_label=src.get("name"),
                )
                written += await hotspots_repo.upsert_with_heat(rows)
                await sources_repo.mark_health(sid, ok=True)
                ok += 1
            except Exception as e:  # noqa: BLE001 — per-source isolation
                logger.warning(f"topic source {sid} ({src.get('name')}) failed: {e}")
                await sources_repo.mark_health(sid, ok=False, error=str(e))
                failed += 1

    summary = {
        "sources": len(sources),
        "groups": len(groups),  # distinct (kind, config) = upstream fetch count
        "upstream_fetches": upstream,
        "ok": ok,
        "failed": failed,
        "written": written,
        "prefiltered": dropped,  # items the L0 AI-relevance gate dropped
    }
    logger.info(f"topic_fetch done: {summary}")
    return summary


async def score_unscored_once(
    *,
    hotspots_repo: HotspotsRepository | None = None,
    scorer: TopicScorerService | None = None,
    sources_repo: SignalSourcesRepository | None = None,
    max_items: int = _SCORE_MAX_ITEMS,
    batch_size: int = _SCORE_BATCH_SIZE,
) -> dict:
    """Enrich score-less hotspots, in batches.

    Two-layer scoring (Phase 1): the agent emits raw per-dimension scores; CODE
    computes the composite ``score`` here via ``compute_quality(dims, tier)`` —
    the source tier is a credibility prior the agent never sees, so source fame
    can't inflate a weak item. Raw dims are persisted (``score_dims``) for a
    later calibration layer. Additive + isolated: a scoring failure never blocks
    fetching.
    """
    hotspots_repo = hotspots_repo or HotspotsRepository()
    scorer = scorer or TopicScorerService()
    sources_repo = sources_repo or SignalSourcesRepository()
    cfg = await load_scoring_config()  # admin-tuned weights, else code defaults
    if not cfg.enabled:  # admin kill switch — stop scoring new hotspots
        return {"unscored": 0, "scored": 0, "enabled": False}
    rows = await hotspots_repo.list_unscored(limit=max_items)
    if not rows:
        return {"unscored": 0, "scored": 0}
    tiers = await sources_repo.tier_map()
    scored = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        items = [
            {
                "i": idx,
                "source": r.get("source_label"),
                "title": r.get("title"),
                "content": r.get("content_original"),
            }
            for idx, r in enumerate(chunk)
        ]
        try:
            enrich = await scorer.score_items(items)
        except Exception as e:  # noqa: BLE001 — scoring is best-effort, never fatal
            logger.warning(f"topic scoring batch failed: {e}")
            continue
        for idx, r in enumerate(chunk):
            e = enrich.get(idx)
            if not e or not e.get("dims"):
                continue
            tier = tiers.get(str(r.get("source_id")), 2)
            patch = {
                "score": compute_quality(
                    e["dims"],
                    tier,
                    weights=cfg.dim_weights,
                    tier_weights=cfg.tier_weights,
                ),
                "score_dims": e["dims"],
                "reason": e.get("reason"),
                "ai_summary": e.get("ai_summary"),
                "category": e.get("category"),
                "tags": e.get("tags"),
            }
            await hotspots_repo.patch_enrichment(str(r["id"]), patch)
            scored += 1
    summary = {"unscored": len(rows), "scored": scored}
    logger.info(f"topic_score done: {summary}")
    return summary


@DBOS.scheduled("*/30 * * * *")  # every 30 min
@DBOS.workflow()
async def topic_fetch_workflow(scheduled_time: datetime, actual_time: datetime) -> None:
    # Module master switch — off = the whole feature is paused (no fetch / score
    # / embed / cluster this tick).
    from app.services.topics.module_config import is_module_enabled

    if not await is_module_enabled():
        logger.info("topic module disabled — skipping tick")
        return
    await run_topic_fetch_once()
    try:
        # L0.5 before scoring so the scorer (and embedder) see real article text.
        await enrich_content_once()
    except Exception as e:  # noqa: BLE001 — never let enrichment break the schedule
        logger.warning(f"topic content-enrich pass failed: {e}")
    try:
        await score_unscored_once()
    except Exception as e:  # noqa: BLE001 — never let scoring break the schedule
        logger.warning(f"topic scoring pass failed: {e}")
    try:
        await embed_unembedded_once()
    except Exception as e:  # noqa: BLE001 — never let embedding break the schedule
        logger.warning(f"topic embedding pass failed: {e}")
    try:
        await cluster_unassigned_once()
    except Exception as e:  # noqa: BLE001 — never let clustering break the schedule
        logger.warning(f"topic clustering pass failed: {e}")
