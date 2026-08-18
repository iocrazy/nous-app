"""GET /resources/search — picker backend for chat @-reference.

Returns paginated resources the caller can read (own personal + team-shared)
with optional ``q`` (filename substring) and ``kinds`` (csv) filters.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.deps import AuthDep
from app.core.scope_dep import scoped_request
from app.repositories.resources_repository import ResourcesRepository
from app.services.ai._mime_kind import kind_from_mime
from app.utils.ai_status import ai_status_str

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
    downloads), or an image whose original file *is* the cover. Returning a
    URL is a bet that the endpoint will find something — it falls back to an
    inline SVG placeholder on a miss, so a false positive costs a placeholder,
    never a broken image.

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

    # Treat empty-string team_id (e.g. `?team_id=`) as absent → global search.
    scope_team_id = team_id or None

    repo = ResourcesRepository()
    rows = await repo.list_accessible_for_user(
        user_id=str(auth.user_id),
        q=q,
        kinds=kinds_list or None,
        limit=limit,
        scope_team_id=scope_team_id,
    )

    results = []
    counts: dict[str, int] = {
        "all": 0,
        "video": 0,
        "image": 0,
        "doc": 0,
        "audio": 0,
        "pdf": 0,
    }
    for row in rows:
        kind = kind_from_mime(row.get("mime"))
        counts[kind] += 1
        counts["all"] += 1
        rid = str(row["id"])
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
                "transcript_status": ai_status_str(row.get("transcript_status")),
                "summary_status": ai_status_str(row.get("summary_status")),
            }
        )

    return {"results": results, "counts": counts, "next_cursor": None}
