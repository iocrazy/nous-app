from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

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

# End-to-end freshness thresholds for the collection pipeline. The tick runs
# every 30 min against 20+ continuously-refreshing hot lists, so a full day
# without a single new row is already pathological and three days is an outage.
# Generous on purpose: a short admin pause for maintenance must not cry wolf,
# but a module left off for weeks has to be impossible to miss.
_STALE_WARN_HOURS = 24
_STALE_ERROR_HOURS = 72


async def check_collection_freshness(
    *,
    hotspots_repo: HotspotsRepository | None = None,
    module_enabled: bool = True,
    now: datetime | None = None,
) -> dict:
    """Report how long since a hotspot actually landed, and name the CAUSE.

    The probe this pipeline was missing. Between 2026-06-30 and 2026-08-13 the
    collection was dead for 44 days while every observable signal said fine:
    ``topic_fetch_workflow`` reported 2224 consecutive DBOS SUCCESSes (the tick
    short-circuits on a disabled module and returns normally), the newsnow
    container was Up, and ``signal_sources.health`` still read 'ok' on every
    row. The only honest signal is the age of the newest row, because nothing
    but a real ingest can advance it — that is what makes this falsifiable
    where a "is the workflow running?" check is not.

    Escalates by age and distinguishes the two causes that need opposite
    responses: an admin PAUSED the module (flip it back on) versus collection
    is RUNNING BUT PRODUCING NOTHING (a real defect to debug). Never raises —
    it is a reporter, and it must still report when the caller is a tick that
    is otherwise doing nothing.
    """
    hotspots_repo = hotspots_repo or HotspotsRepository()
    now = now or datetime.now(timezone.utc)
    try:
        latest = await hotspots_repo.latest_created_at()
    except Exception as e:  # noqa: BLE001 — a reporter must not break the tick
        logger.error(f"topic freshness probe failed: {e}")
        return {"ok": False, "probe_failed": True}

    if latest is None:
        # No hotspot has EVER landed. Normal on a fresh deploy, so this is not
        # an outage — but it is still worth stating out loud rather than
        # reporting nothing at all.
        logger.warning(
            "topic collection freshness: hotspots table is EMPTY "
            f"(module_enabled={module_enabled})"
        )
        return {"ok": False, "empty": True, "module_enabled": module_enabled}

    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    stale = now - latest
    stale_hours = stale / timedelta(hours=1)
    summary = {
        "ok": stale_hours < _STALE_WARN_HOURS,
        "latest_created_at": latest.isoformat(),
        "stale_hours": round(stale_hours, 1),
        "module_enabled": module_enabled,
    }

    # The cause decides the remedy, so it goes in the message, not just the dict.
    cause = (
        "module is DISABLED (topics.module.enabled=false) — no tick collects "
        "anything while it is off, and the feed UI stays visible serving a "
        "frozen feed"
        if not module_enabled
        else "module is ENABLED but nothing is landing — collection is broken"
    )
    msg = (
        f"topic collection stale: no new hotspot for {stale_hours:.1f}h "
        f"(newest {latest.isoformat()}); {cause}"
    )
    if stale_hours >= _STALE_ERROR_HOURS:
        logger.error(msg)
    elif stale_hours >= _STALE_WARN_HOURS:
        logger.warning(msg)
    else:
        logger.info(
            f"topic collection fresh: newest hotspot {stale_hours:.1f}h old "
            f"(module_enabled={module_enabled})"
        )
    return summary


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

    if not sources:
        # Zero enabled sources collects zero items forever, and silently. Say so
        # instead of reporting a clean "written: 0" tick.
        logger.warning("topic fetch: no ENABLED signal sources — nothing to collect")

    ok = failed = written = dropped = upstream = 0
    # "Nothing was written" has several very different causes that used to be
    # indistinguishable in the summary. Count them apart so the log says which:
    upstream_items = 0  # raw items upstream handed us, across all groups
    empty_upstream = 0  # groups where the UPSTREAM returned nothing
    dropped_all = 0  # sources where WE dropped every item at the L0 gate
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
        upstream_items += len(fetched)
        if not fetched:
            # An adapter that returns [] rather than raising (the newsnow one
            # raises, but that is per-adapter policy) still means the upstream
            # gave us nothing. Record it as its OWN outcome so it can never be
            # confused with "we fetched fine and then discarded everything".
            empty_upstream += 1
            logger.warning(
                f"topic source group ({kind}, {group[0].get('name')}) returned "
                "0 items: UPSTREAM gave us nothing"
            )
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
                if fetched and not candidates:
                    # The opposite failure from the one above: upstream DID give
                    # us items and the L0 keyword gate discarded all of them. A
                    # mistuned keyword list looks exactly like a dead source
                    # unless the two are reported separately.
                    dropped_all += 1
                    logger.warning(
                        f"topic source {sid} ({src.get('name')}): L0 prefilter "
                        f"dropped ALL {len(fetched)} fetched items — WE discarded "
                        "them, the upstream was fine"
                    )
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
        "upstream_items": upstream_items,  # what upstream actually gave us
        "empty_upstream": empty_upstream,  # groups where upstream gave 0
        "ok": ok,
        "failed": failed,
        "written": written,
        "prefiltered": dropped,  # items the L0 AI-relevance gate dropped
        "dropped_all": dropped_all,  # sources where WE dropped 100%
    }
    logger.info(f"topic_fetch done: {summary}")

    # A tick where EVERY upstream fetch failed collected nothing at all.
    # Returning normally would let DBOS record SUCCESS for a run that did
    # nothing — the exact "reports success but nothing happened" shape route C
    # forbids. PARTIAL failure deliberately stays isolated (per-source health
    # carries it, one dead feed must not stop the other 21); only TOTAL failure
    # raises, because then there is no success left to report.
    if groups and upstream == 0:
        raise RuntimeError(
            f"topic fetch collected nothing: all {len(groups)} upstream fetch(es) "
            f"failed across {len(sources)} enabled source(s)"
        )
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
        # A paused module still has to report its own staleness. Before this,
        # the ONLY trace was the INFO line above: 1878 identical copies piled up
        # between 2026-06-30 and 2026-08-13, indistinguishable from the ~7k
        # INFO rows/day around them, while the feed UI stayed visible (the
        # stored blob was {"enabled": false} with no "visible" key, so `visible`
        # fell back to its default true) and served a frozen feed. Escalating by
        # AGE is what turns "paused for maintenance" — fine, stays quiet — into
        # "forgotten off for six weeks", which is now an ERROR nobody can miss.
        await check_collection_freshness(module_enabled=False)
        return
    await run_topic_fetch_once()
    try:
        # Backstop for the enabled path: run_topic_fetch_once raises only on
        # TOTAL upstream failure, so subtler ways of landing nothing (every item
        # deduped, the L0 gate dropping 100%, a write failing) still need the
        # end-to-end signal.
        await check_collection_freshness(module_enabled=True)
    except Exception as e:  # noqa: BLE001 — a reporter must never break the tick
        logger.warning(f"topic freshness check failed: {e}")
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
