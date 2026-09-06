"""GET /resources/search — picker backend for chat @-reference.

Returns paginated resources the caller can read (own personal + team-shared)
with optional ``q`` (filename substring), ``kinds`` (csv) and ``sources``
(csv over ``source_type``) filters.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.deps import AuthDep
from app.core.scope_dep import scoped_request
from app.models.media import RESOURCE_SOURCE_TYPES
from app.repositories.resources_repository import ResourcesRepository
from app.services.ai._mime_kind import kind_from_mime
from app.services.ai.resource_ai_status import effective_ai_statuses

# All endpoints here require auth (AuthDep) and are resources-dedicated, so the
# ambient tenant Scope is established at the ROUTER level. Inert until
# SCOPE_ENFORCE_RESOURCES flips on (the choke point ignores _scope while off).
router = APIRouter(
    prefix="/resources",
    tags=["resources"],
    dependencies=[Depends(scoped_request)],
)


def _thumbnail_url(row: dict, resource_id: str) -> Optional[str]:
    """Relative cover URL when the resource can plausibly render one.

    Same ladder the frontend's ``buildThumbnailSrc`` walks and the same one
    ``serve_resource_cover`` resolves against: an explicit thumbnail, an
    explicit cover, a parsed_media backing row (cover lives there for
    downloads), or an image whose original file *is* the cover.

    Returning a URL is a bet that the endpoint will find something, and the
    bet can lose: ``serve_resource_cover``'s inline-SVG placeholder sits
    behind a ``not media_id`` guard, so it is unreachable for exactly the
    parsed_media-backed arm this function most often bets on — that arm
    resolves ``parsed_media.cover_download_path`` against the local download
    root with no ``sb://`` branch, and every non-null cover path in
    production is ``sb://`` (1187/1187, measured 2026-08-17). Those requests
    fall through to a 404. The frontend handles it with an ``onError``
    fallback, so a false positive costs one failed image request, not a
    broken-looking tile — but it is a 404, not a placeholder.

    The endpoint is intentionally unauthenticated (RECON#8) so the URL can go
    straight into ``<img src>``; the search results themselves are already
    scoped to what the caller may read, so this adds no exposure.
    """
    if (
        row.get("thumbnail_path")
        or row.get("cover_image_path")
        or row.get("media_id")
        or (row.get("mime") or "").startswith("image/")
    ):
        return f"/api/v1/resources/{resource_id}/cover"
    return None


@router.get("/search")
async def search_resources(
    auth: AuthDep,
    q: str = Query("", max_length=128),
    kinds: Optional[str] = Query(None, description="csv of video,image,doc,audio,pdf"),
    sources: Optional[str] = Query(
        None, description="csv of upload,web,generated,derived"
    ),
    team_id: Optional[str] = Query(None, description="narrow to this team + personal"),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """Search visible resources for the @-reference picker."""
    kinds_list: list[str] = []
    if kinds:
        kinds_list = [
            k.strip()
            for k in kinds.split(",")
            if k.strip() in {"video", "image", "doc", "audio", "pdf"}
        ]

    # Same parse as ``kinds``, against the four values the DB CHECK admits
    # (migration 363). Unknown values are DROPPED rather than passed through:
    # ``source_type.in_([..., "bogus"])`` would look filtered while narrowing
    # nothing. An all-unknown list collapses to None below — no filter — so a
    # junk query string shows everything rather than an empty shelf.
    sources_list: list[str] = []
    if sources:
        sources_list = [
            src.strip()
            for src in sources.split(",")
            if src.strip() in RESOURCE_SOURCE_TYPES
        ]

    # Treat empty-string team_id (e.g. `?team_id=`) as absent → global search.
    scope_team_id = team_id or None

    repo = ResourcesRepository()
    rows = await repo.list_accessible_for_user(
        user_id=str(auth.user_id),
        q=q,
        kinds=kinds_list or None,
        sources=sources_list or None,
        limit=limit,
        scope_team_id=scope_team_id,
    )

    # Tab badges come from their own aggregate over the WHOLE visible set —
    # deliberately not from ``rows``. ``rows`` is one tab's slice, cut off at
    # ``limit``; tallying it made every badge describe the page instead of the
    # library (open the Video tab and Image read 0; "All" never passed 50).
    #
    # ``sources`` DOES reach the counts, unlike ``kinds``. The distinction is
    # the axis each control sits on: the kind tabs are the thing the badges
    # describe (a Video badge counted under a Video filter always reads its own
    # page size), while the source chips are a filter ACROSS all of them — a
    # badge that ignored the chip would promise 40 files over a shelf of 3.
    counts = await repo.count_accessible_by_kind_for_user(
        user_id=str(auth.user_id),
        q=q,
        sources=sources_list or None,
        scope_team_id=scope_team_id,
    )

    # The status columns only ever hold terminal values, so the picker's
    # "being processed" chip has to come from task_tracking. One batched
    # query for the whole page — see services/ai/resource_ai_status.
    effective = await effective_ai_statuses({str(row["id"]): row for row in rows})

    results = []
    for row in rows:
        kind = kind_from_mime(row.get("mime"))
        rid = str(row["id"])
        _eff = effective.get(rid, {})
        # Whitelist, never a `**row` spread: the repo now selects storage
        # paths and the media FK purely so the ladder below can be walked
        # here. They are inputs, not output — pinned by the tripwire in
        # tests/api/test_resources_search_status_fields.py.
        results.append(
            {
                "id": rid,
                "name": row["name"],
                "kind": kind,
                "mime": row.get("mime"),
                "size": row.get("size"),
                "scope": {
                    "type": row["scope_type"],
                    "id": str(row["scope_id"]),
                },
                "updated_at": row["updated_at"],
                "thumbnail_url": _thumbnail_url(row, rid),
                "transcript_status": _eff.get("transcript_status"),
                "summary_status": _eff.get("summary_status"),
            }
        )

    return {"results": results, "counts": counts, "next_cursor": None}
