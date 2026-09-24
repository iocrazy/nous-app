"""media_id -> the caller's own resource row, for search hydration.

Shared by ``app.api.search_router`` (card hydration) and the LibrarySearch
agent tool (``app.services.ai.tools.library_search_tool``): a search hit is a
``parsed_media`` row, but everything a user can open is a ``resources`` row,
so both need the same owner-filtered lookup. One query, one place.

The lookup filters ``creator_id == user_id`` explicitly; callers outside a
request scope (DBOS workers) wrap it in ``system_request_scope`` themselves.
"""

from __future__ import annotations

from typing import Any, Dict, List


async def fetch_user_resources_by_media_id(
    user_id: str,
    media_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Return a ``{media_id: {resource_id, ai_status_fields}}`` map.

    Search hydration needs more than ``parsed_media`` columns:
      * ``resource_id`` so the card click can navigate to the right
        ``/resources/file/<id>`` URL (search hits without it would
        fall back to ``parsed_media.id`` and 404 on detail load).
      * AI status fields (``transcript_status`` / ``summary_status`` /
        ``visual_analysis_status``) so the card's AI dot icons reflect
        the per-user processing state instead of always rendering grey.
      * ``has_prompt`` — same signal the library list overlays, so the
        card's Prompt icon lights on search hits too. Computed in SQL
        (``has_prompt_expr``) rather than selecting the prompt text: the
        columns are capped at 20 000 chars each and would dominate the
        payload for a page of hits.

    Empty ``media_ids`` short-circuits to avoid an unnecessary query.
    """
    if not media_ids:
        return {}
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Resources
    from app.repositories._orm_helpers import _plain
    from app.repositories.media_repository import has_prompt_expr

    async with read_scope() as session:
        result = await session.execute(
            select(
                Resources.id,
                Resources.media_id,
                Resources.transcript_status,
                Resources.summary_status,
                Resources.visual_analysis_status,
                has_prompt_expr().label("has_prompt"),
            )
            .where(Resources.creator_id == user_id)
            .where(Resources.source_type == "web")
            .where(Resources.is_trashed.is_(False))
            .where(Resources.media_id.in_([int(m) for m in media_ids]))
        )
        rows = result.mappings().all()
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        mid = row.get("media_id")
        if mid is None:
            continue
        out[int(mid)] = {
            "resource_id": str(row["id"]) if row.get("id") is not None else None,
            "transcript_status": _plain(row.get("transcript_status")),
            "summary_status": _plain(row.get("summary_status")),
            "visual_analysis_status": _plain(row.get("visual_analysis_status")),
            "has_prompt": bool(row.get("has_prompt")),
        }
    return out
