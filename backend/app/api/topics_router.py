from __future__ import annotations

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
from app.repositories.signal_sources_repository import SignalSourcesRepository
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
    SourceHealthOut,
    SourceHealthResponse,
)
from app.services.storyboard.script.script_ai_service import ScriptAIService
from app.services.topics.embedding_service import TopicEmbeddingService
from app.services.topics.heat import best_rank as _best_rank

router = APIRouter(prefix="/topics")


def _to_out(
    row: dict, state: Optional[dict] = None, *, include_content: bool = False
) -> HotspotOut:
    state = state or {}
    # PostgREST embeds the related group as {"topic_groups": {"source_count": N}}
    # (or None when the hotspot isn't clustered yet).
    group = row.get("topic_groups") or {}
    source_count = group.get("source_count") if isinstance(group, dict) else None
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
        # content_* only in the detail view to keep the list payload light.
        content_original=row.get("content_original") if include_content else None,
        content_translated=(row.get("content_translated") if include_content else None),
        is_read=bool(state.get("is_read")),
        is_saved=bool(state.get("is_saved")),
        is_hidden=bool(state.get("is_hidden")),
    )


@router.get("", response_model=HotspotListResponse)
async def list_hotspots(
    auth: AuthDep,
    day: Optional[str] = Query(None, description="YYYY-MM-DD"),
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="free-text search over hotspots"),
    view: str = Query("all", description="all | saved | hidden"),
    limit: int = Query(100, ge=1, le=300),
):
    repo = HotspotsRepository()
    state_repo = HotspotUserStateRepository()

    if view == "foryou":
        # Personalized: hotspots ranked by cosine similarity to the user's
        # interest embedding. Rank ids via pgvector, then fetch (preserving the
        # similarity order). Empty when no interest embedding / no embedded rows.
        ranked = await UserTopicInterestRepository().rank_hotspot_ids(
            auth.user_id, limit=limit
        )
        fetched = await repo.list_by_ids(ranked, limit=limit)
        order = {rid: n for n, rid in enumerate(ranked)}
        rows = sorted(fetched, key=lambda r: order.get(str(r.get("id")), 1 << 30))
    elif view in ("saved", "hidden"):
        # These views span all dates: drive off the user's state table.
        flag = "is_saved" if view == "saved" else "is_hidden"
        ids = await state_repo.list_ids_where(auth.user_id, flag=flag)
        rows = await repo.list_by_ids(ids, limit=limit)
    else:
        # When searching, span all dates — a topic is found regardless of which
        # day it landed on. The day filter only applies to plain browsing.
        effective_day = None if (q and q.strip()) else day
        rows = await repo.list_for_date(effective_day, category, limit=limit, q=q)

    states = await state_repo.get_states(auth.user_id, [str(r.get("id")) for r in rows])
    items: list[HotspotOut] = []
    for r in rows:
        st = states.get(str(r.get("id")), {})
        # Browsing / search / For You hide the user's hidden items.
        if view in ("all", "foryou") and st.get("is_hidden"):
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


def _to_health_out(row: dict) -> SourceHealthOut:
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
    )


@router.get("/sources/health", response_model=SourceHealthResponse)
async def source_health(auth: AuthDep):
    """Read-only health of all signal sources (worst-first). Surfaces which
    feeds have gone degraded/dead so a maintainer can react. No mutation."""
    repo = SignalSourcesRepository()
    rows = await repo.list_all()
    sources = [_to_health_out(r) for r in rows]
    return SourceHealthResponse(count=len(sources), sources=sources)


@router.get("/{hotspot_id}", response_model=HotspotDetailResponse)
async def get_hotspot(hotspot_id: str, auth: AuthDep):
    """Full hotspot for the detail panel: original/translated body + the
    caller's read/saved/hidden state. Declared after the static GET routes so
    ``/dates`` and ``/sources/health`` are not captured by ``{hotspot_id}``."""
    repo = HotspotsRepository()
    row = await repo.get_by_id(hotspot_id)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    state_repo = HotspotUserStateRepository()
    states = await state_repo.get_states(auth.user_id, [hotspot_id])
    out = _to_out(row, states.get(hotspot_id, {}), include_content=True)
    return HotspotDetailResponse(hotspot=out)


async def _generate_script_for(title: str, summary: str, user_id: str) -> list:
    """Reuse the existing script_ai agent. Real signature (verified):
    ScriptAIService(user_id=...).generate_outline(premise) -> List[Dict[str,str]]."""
    svc = ScriptAIService(user_id=user_id)
    premise = f"Topic: {title}\n\nContext: {summary or ''}"
    return await svc.generate_outline(premise)


@router.post("/{hotspot_id}/generate-script")
async def generate_script(hotspot_id: str, auth: AuthDep):
    repo = HotspotsRepository()
    row = await repo.get_by_id(hotspot_id)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    script = await _generate_script_for(
        row.get("title") or "",
        row.get("ai_summary") or row.get("summary") or "",
        auth.user_id,
    )
    return {"success": True, "script": script}
