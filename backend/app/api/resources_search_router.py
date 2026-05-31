"""GET /resources/search — picker backend for chat @-reference.

Returns paginated resources the caller can read (own personal + team-shared)
with optional ``q`` (filename substring) and ``kinds`` (csv) filters.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.core.deps import AuthDep
from app.repositories.resources_repository import ResourcesRepository
from app.services.ai._mime_kind import kind_from_mime

router = APIRouter(prefix="/resources", tags=["resources"])


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

    repo = ResourcesRepository()
    rows = await repo.list_accessible_for_user(
        user_id=str(auth.user_id),
        q=q,
        kinds=kinds_list or None,
        limit=limit,
        scope_team_id=team_id,
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
        results.append(
            {
                "id": str(row["id"]),
                "name": row["name"],
                "kind": kind,
                "mime": row.get("mime"),
                "size": row.get("size"),
                "scope": {
                    "type": row["scope_type"],
                    "id": str(row["scope_id"]),
                },
                "updated_at": row["updated_at"],
                "thumbnail_url": None,
            }
        )

    return {"results": results, "counts": counts, "next_cursor": None}
