from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.deps import (  # noqa: F401 — get_auth re-exported for test override
    AuthDep,
    get_auth,
)
from app.repositories.hotspot_user_state_repository import (
    HotspotUserStateRepository,
)
from app.repositories.hotspots_repository import HotspotsRepository
from app.repositories.signal_sources_repository import (
    ALLOWED_KINDS,
    SignalSourcesRepository,
)
from app.repositories.tags_repository import get_tags_repository
from app.repositories.user_hidden_sources_repository import (
    UserHiddenSourcesRepository,
)
from app.repositories.user_topic_interest_repository import (
    UserTopicInterestRepository,
)
from app.schemas.topics import (
    DatesResponse,
    HotspotDetailResponse,
    HotspotListResponse,
    HotspotOut,
    HotspotStateRequest,
    HotspotStateResponse,
    InterestRequest,
    InterestResponse,
    ModuleStatusResponse,
    SourceCreateRequest,
    SourceHealthOut,
    SourceHealthResponse,
    SourceMutationResponse,
)
from app.services.ai.providers.ai_provider_helpers import (
    resolve_script_provider_config,
)
from app.services.storyboard.script.script_ai_service import ScriptAIService
from app.services.topics.embedding_service import TopicEmbeddingService
from app.services.topics.heat import best_rank as _best_rank
from app.services.topics.scoring import load_scoring_config

router = APIRouter(prefix="/topics")


def _to_out(
    row: dict, state: Optional[dict] = None, *, include_content: bool = False
) -> HotspotOut:
    state = state or {}
    # PostgREST embeds the related group as
    # {"topic_groups": {"source_count": N, "source_labels": [...]}} (or None
    # when the hotspot isn't clustered yet).
    group = row.get("topic_groups") or {}
    source_count = group.get("source_count") if isinstance(group, dict) else None
    source_names = (
        [str(s) for s in (group.get("source_labels") or [])]
        if isinstance(group, dict)
        else []
    )
    return HotspotOut(
        id=str(row.get("id")),
        title=row.get("title") or "",
        url=row.get("url"),
        origin_url=row.get("origin_url"),
        source_label=row.get("source_label"),
        summary=row.get("summary"),
        ai_summary=row.get("ai_summary"),
        reason=row.get("reason"),
        score=row.get("score"),
        tags=row.get("tags") or [],
        category=row.get("category"),
        media_url=row.get("media_url"),
        cover_url=row.get("cover_url"),
        captured_at=row.get("captured_at"),
        heat=row.get("heat"),
        best_rank=_best_rank(row.get("rank_timeline") or []),
        source_count=source_count,
        source_names=source_names,
        # score_dims + content_* only in the detail view (keep the list light).
        score_dims=(row.get("score_dims") if include_content else None),
        content_original=row.get("content_original") if include_content else None,
        content_translated=(row.get("content_translated") if include_content else None),
        is_read=bool(state.get("is_read")),
        is_saved=bool(state.get("is_saved")),
        is_hidden=bool(state.get("is_hidden")),
    )


async def _visible_source_ids(user_id: str) -> list[str]:
    """The caller's feed allowlist: system + own sources, minus the ones they've
    hidden. Every feed read scopes ``source_id IN (...)`` to this set, so a
    deleted/other-user/hidden source's hotspots never surface.

    The two lookups (hidden ids + visible sources) are independent, so we fire
    them concurrently — one round-trip of latency instead of two before the
    main feed query runs."""
    hidden, sources = await asyncio.gather(
        UserHiddenSourcesRepository().list_hidden_ids(user_id),
        SignalSourcesRepository().list_visible(user_id),
    )
    hidden_set = set(hidden)
    # Exclude admin-disabled (enabled=false) sources too — disabling a source
    # stops collection AND drops its existing hotspots from every user's feed.
    return [
        str(s["id"])
        for s in sources
        if s.get("enabled", True) and str(s["id"]) not in hidden_set
    ]


# Sentinel word set for a tag filter that resolved to nothing (invalid / unknown
# ids). It can never match a real hotspot tag, so the feed comes back empty
# instead of silently unfiltered.
_NO_TAG_MATCH: list[str] = ["__no_match__"]


async def _resolve_tag_words(
    tag_id: Optional[str], user_id: str
) -> Optional[list[str]]:
    """Resolve a comma-separated pool tag-id list to the lower-cased set of
    name/name_zh words those tags carry, scoped to the caller-visible pool
    (spec §5) so a raw tag id can't pull another user's private tag name into
    the filter word set.

    Returns None when no filter is requested (feed stays unfiltered), or the
    ``_NO_TAG_MATCH`` sentinel when ids were given but resolved to nothing — so
    an invalid/unknown/not-visible tag filter yields an empty feed, never all
    rows."""
    if not tag_id:
        return None
    ids = [int(x) for x in tag_id.split(",") if x.strip().isdigit()][:20]
    words: list[str] = []
    if ids:
        tag_rows = await get_tags_repository().get_tags_by_ids(ids, user_id)
        words = [
            w.lower() for t in tag_rows for w in (t.get("name"), t.get("name_zh")) if w
        ]
    return words or _NO_TAG_MATCH


def _filter_rows_by_tag_words(rows: list[dict], tag_words: Optional[list[str]]) -> list:
    """Router-level tag filter for the id-driven views (saved/hidden/foryou),
    which fetch by hotspot id and so can't push the array overlap into the
    query: keep hotspots whose ``tags`` intersect the word set (case-folded).
    No-op when no filter is requested."""
    if not tag_words:
        return rows
    wanted = set(tag_words)
    return [r for r in rows if wanted & {str(t).lower() for t in (r.get("tags") or [])}]


@router.get("", response_model=HotspotListResponse)
async def list_hotspots(
    auth: AuthDep,
    day: Optional[str] = Query(None, description="YYYY-MM-DD"),
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="free-text search over hotspots"),
    view: str = Query("all", description="all | saved | hidden | foryou"),
    source: Optional[str] = Query(
        None, description="comma-separated source ids to narrow the feed to"
    ),
    tag_id: Optional[str] = Query(None, description="comma-separated tag ids"),
    limit: int = Query(100, ge=1, le=300),
):
    repo = HotspotsRepository()
    state_repo = HotspotUserStateRepository()
    visible = await _visible_source_ids(auth.user_id)
    if source:
        # Narrow the feed to the user-picked sources, intersected with what
        # they're allowed to see (a picked id outside the allowlist is dropped).
        picked = {s.strip() for s in source.split(",") if s.strip()}
        visible = [sid for sid in visible if sid in picked]

    # Pool-tag filter: resolve the picked tag ids to their name/name_zh words.
    # The date-window views push this into the query (array overlap); the
    # id-driven views (saved/hidden/foryou) filter the fetched rows below.
    tag_words = await _resolve_tag_words(tag_id, auth.user_id)

    if view == "featured":
        # Curated high-value board: score floor + best-first, spanning all
        # dates. Respects category/source/search filters but not the day window.
        # The floor is admin-tunable (system_settings), code default otherwise.
        cfg = await load_scoring_config()
        rows = await repo.list_for_date(
            None,
            category,
            limit=limit,
            q=q,
            source_ids=visible,
            min_score=cfg.featured_min_score,
            order_score=True,
            tag_words=tag_words,
        )
    elif view == "foryou":
        # Personalized: hotspots ranked by cosine similarity to the user's
        # interest embedding. Rank ids via pgvector, then fetch (preserving the
        # similarity order). Empty when no interest embedding / no embedded rows.
        ranked = await UserTopicInterestRepository().rank_hotspot_ids(
            auth.user_id, limit=limit
        )
        fetched = await repo.list_by_ids(ranked, limit=limit, source_ids=visible)
        order = {rid: n for n, rid in enumerate(ranked)}
        rows = sorted(fetched, key=lambda r: order.get(str(r.get("id")), 1 << 30))
        rows = _filter_rows_by_tag_words(rows, tag_words)
    elif view in ("saved", "hidden"):
        # These views span all dates: drive off the user's state table.
        flag = "is_saved" if view == "saved" else "is_hidden"
        ids = await state_repo.list_ids_where(auth.user_id, flag=flag)
        rows = await repo.list_by_ids(ids, limit=limit, source_ids=visible)
        rows = _filter_rows_by_tag_words(rows, tag_words)
    else:
        # When searching, span all dates — a topic is found regardless of which
        # day it landed on. The day filter only applies to plain browsing.
        effective_day = None if (q and q.strip()) else day
        rows = await repo.list_for_date(
            effective_day,
            category,
            limit=limit,
            q=q,
            source_ids=visible,
            tag_words=tag_words,
        )

    states = await state_repo.get_states(auth.user_id, [str(r.get("id")) for r in rows])
    items: list[HotspotOut] = []
    for r in rows:
        st = states.get(str(r.get("id")), {})
        # Browsing / search / For You / Featured hide the user's hidden items.
        if view in ("all", "foryou", "featured") and st.get("is_hidden"):
            continue
        items.append(_to_out(r, st))
    return HotspotListResponse(count=len(items), hotspots=items)


@router.get("/interest", response_model=InterestResponse)
async def get_interest(auth: AuthDep):
    """The caller's interest profile for the For You view."""
    row = await UserTopicInterestRepository().get_interest(auth.user_id)
    if not row:
        return InterestResponse(interest_text="", has_embedding=False)
    return InterestResponse(
        interest_text=row.get("interest_text") or "",
        has_embedding=bool(row.get("has_embedding")),
    )


@router.put("/interest", response_model=InterestResponse)
async def set_interest(body: InterestRequest, auth: AuthDep):
    """Save the caller's interest text and embed it (Volcengine, via the
    admin-governed embedding provider). Stored embedding may be NULL when the
    provider is unconfigured — the text is still saved; For You stays empty
    until an embedding exists."""
    text = (body.interest_text or "").strip()
    vec = None
    if text:
        embedding = await TopicEmbeddingService().embed_text(text)
        if embedding:
            vec = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
    repo = UserTopicInterestRepository()
    await repo.set_interest(auth.user_id, interest_text=text, vec=vec)
    return InterestResponse(interest_text=text, has_embedding=vec is not None)


@router.patch("/{hotspot_id}/state", response_model=HotspotStateResponse)
async def set_hotspot_state(hotspot_id: str, body: HotspotStateRequest, auth: AuthDep):
    """Toggle the caller's read/saved/hidden flags on a hotspot (upsert)."""
    repo = HotspotUserStateRepository()
    flags = await repo.set_state(
        auth.user_id,
        hotspot_id,
        is_read=body.is_read,
        is_saved=body.is_saved,
        is_hidden=body.is_hidden,
    )
    return HotspotStateResponse(**flags)


@router.get("/dates", response_model=DatesResponse)
async def hotspot_dates(auth: AuthDep, limit_days: int = Query(60, ge=1, le=180)):
    repo = HotspotsRepository()
    return DatesResponse(dates=await repo.distinct_dates(limit_days))


@router.get("/module-status", response_model=ModuleStatusResponse)
async def module_status(auth: AuthDep):
    """Topic Inspiration module switches. The frontend hides the page/nav when
    ``visible`` is false and shows a paused notice when ``enabled`` is false."""
    from app.services.topics.module_config import is_module_enabled, is_module_visible

    return ModuleStatusResponse(
        enabled=await is_module_enabled(),
        visible=await is_module_visible(),
    )


def _to_health_out(
    row: dict, *, is_owner: bool = False, is_hidden: bool = False
) -> SourceHealthOut:
    return SourceHealthOut(
        id=str(row.get("id")),
        name=row.get("name") or "",
        kind=row.get("kind") or "",
        category=row.get("category"),
        enabled=bool(row.get("enabled", True)),
        health=row.get("health") or "ok",
        consecutive_failures=int(row.get("consecutive_failures") or 0),
        last_error=row.get("last_error"),
        last_fetched_at=row.get("last_fetched_at"),
        last_ok_at=row.get("last_ok_at"),
        is_owner=is_owner,
        is_hidden=is_hidden,
    )


@router.get("/sources/health", response_model=SourceHealthResponse)
async def source_health(auth: AuthDep):
    """The caller's manageable signal sources (worst-health-first): system
    sources + their own. Each row carries ``is_owner`` (deletable) and
    ``is_hidden`` (closed from this user's feed). Other users' private sources
    are not listed."""
    repo = SignalSourcesRepository()
    rows = await repo.list_visible(auth.user_id)
    hidden = set(await UserHiddenSourcesRepository().list_hidden_ids(auth.user_id))
    sources = [
        _to_health_out(
            r,
            is_owner=str(r.get("user_id")) == auth.user_id,
            is_hidden=str(r.get("id")) in hidden,
        )
        for r in rows
    ]
    return SourceHealthResponse(count=len(sources), sources=sources)


@router.post("/sources", response_model=SourceMutationResponse)
async def create_source(body: SourceCreateRequest, auth: AuthDep):
    """Add a user-owned signal source. It starts collecting on the next fetch
    cycle; its hotspots are private to this caller (scoped by the feed's
    source-id filter — other clients never see them)."""
    kind = (body.kind or "").strip()
    name = (body.name or "").strip()
    if kind not in ALLOWED_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {', '.join(ALLOWED_KINDS)}",
        )
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if not isinstance(body.config, dict):
        raise HTTPException(status_code=422, detail="config must be an object")
    row = await SignalSourcesRepository().create_source(
        user_id=auth.user_id,
        kind=kind,
        name=name,
        config=body.config,
        category=(body.category or None),
    )
    return SourceMutationResponse(source=_to_health_out(row, is_owner=True))


@router.delete("/sources/{source_id}", response_model=SourceMutationResponse)
async def delete_source(source_id: str, auth: AuthDep):
    """Delete a source the caller OWNS (stops collection; its hotspots drop out
    of the feed). System sources can't be deleted — only hidden (use
    ``POST /sources/{id}/hide``). 404 when not found or not owned."""
    deleted = await SignalSourcesRepository().delete_source(
        user_id=auth.user_id, source_id=source_id
    )
    if not deleted:
        raise HTTPException(
            status_code=404, detail="source not found or not owned by you"
        )
    # Clean up any stale hide row so it doesn't dangle after the source is gone.
    await UserHiddenSourcesRepository().unhide(auth.user_id, source_id)
    return SourceMutationResponse(source=None)


@router.post("/sources/{source_id}/hide", response_model=SourceMutationResponse)
async def hide_source(source_id: str, auth: AuthDep):
    """Close a source for THIS caller: exclude its hotspots from their feed. The
    source keeps collecting globally; other clients are unaffected. Works on any
    source the caller can see (system or own). 404 when not visible to them."""
    repo = SignalSourcesRepository()
    visible = {str(r.get("id")) for r in await repo.list_visible(auth.user_id)}
    if source_id not in visible:
        raise HTTPException(status_code=404, detail="source not found")
    await UserHiddenSourcesRepository().hide(auth.user_id, source_id)
    return SourceMutationResponse(source=None)


@router.delete("/sources/{source_id}/hide", response_model=SourceMutationResponse)
async def unhide_source(source_id: str, auth: AuthDep):
    """Re-open a previously closed source for this caller (idempotent)."""
    await UserHiddenSourcesRepository().unhide(auth.user_id, source_id)
    return SourceMutationResponse(source=None)


@router.get("/{hotspot_id}", response_model=HotspotDetailResponse)
async def get_hotspot(hotspot_id: str, auth: AuthDep):
    """Full hotspot for the detail panel: original/translated body + the
    caller's read/saved/hidden state. Declared after the static GET routes so
    ``/dates`` and ``/sources/health`` are not captured by ``{hotspot_id}``."""
    repo = HotspotsRepository()
    visible = await _visible_source_ids(auth.user_id)
    row = await repo.get_by_id(hotspot_id, source_ids=visible)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    state_repo = HotspotUserStateRepository()
    states = await state_repo.get_states(auth.user_id, [hotspot_id])
    out = _to_out(row, states.get(hotspot_id, {}), include_content=True)
    return HotspotDetailResponse(hotspot=out)


async def _generate_script_for(
    title: str,
    summary: str,
    user_id: str,
    *,
    agent_slug: Optional[str] = None,
    provider_key: Optional[str] = None,
    provider_config: Optional[dict] = None,
) -> list:
    """Run the resolved script-generation agent (governed via task_assignment —
    the user picks the agent/model in Settings → AI → Storyboard → Script;
    defaults to the script_ai system agent). Its model AND skills come from the
    chosen agent — nothing hardcoded."""
    svc = ScriptAIService(
        user_id=user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )
    premise = f"Topic: {title}\n\nContext: {summary or ''}"
    return await svc.generate_outline(premise)


@router.post("/{hotspot_id}/generate-script")
async def generate_script(hotspot_id: str, auth: AuthDep):
    repo = HotspotsRepository()
    visible = await _visible_source_ids(auth.user_id)
    row = await repo.get_by_id(hotspot_id, source_ids=visible)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    # Resolve the user-assigned script agent (task_assignment.script_generation,
    # default script_ai) + their BYO provider config — governed like every other
    # AI task, not hardcoded.
    provider_key, provider_config, _model, agent_slug = (
        await resolve_script_provider_config(auth.user_id)
    )
    script = await _generate_script_for(
        row.get("title") or "",
        row.get("ai_summary") or row.get("summary") or "",
        auth.user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )
    return {"success": True, "script": script}
