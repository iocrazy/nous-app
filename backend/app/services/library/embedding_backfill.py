"""Backfill ``resource_analysis.content_embedding`` for resources that lack it.

Why this exists: ``analyze_l1`` ran for months with the embedder unconfigured
and swallowed the ``None`` — so hundreds of downloads have a VLM analysis
row (visual_description, detected_*) but no vector, and hundreds more never
ran at all. Two buckets, two prices:

* ``has_analysis`` — re-embed from the stored fields. One embedding call,
  no VLM, fractions of a cent. Done inline by :func:`reembed_existing`.
* ``no_analysis``  — dispatch the full ``analyze_l1`` workflow (VLM + embed),
  one Task Center row each. The dispatch itself lives in the router because
  it needs the task manager / DBOS wiring; this module only decides WHO.

Candidates are listed cheapest-first and capped, so the caller can stage the
spend (20 first, then the rest) and see ``remaining`` after each batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Tuple

from sqlalchemy import and_, func, or_, select

from app.db.session import read_scope
from app.models.media import ParsedMedia, ResourceAnalysis, Resources

# The only level analyze_l1 writes; the composite PK allows others.
ANALYSIS_LEVEL = "L1"


@dataclass(frozen=True)
class BackfillCandidate:
    resource_id: int
    media_id: int
    platform_id: str
    title: str
    description: str
    cover_url: Optional[str]
    has_analysis: bool


class _AnalysisRepo(Protocol):
    async def get_analysis(
        self, resource_id: int, analysis_level: Optional[str] = None
    ) -> Optional[dict]: ...

    async def update_embedding(
        self,
        resource_id: int,
        embedding: List[float],
        full_text: str,
        analysis_level: Optional[str] = None,
    ) -> Any: ...


class _TagsRepo(Protocol):
    async def get_resource_tags(self, resource_id: Any) -> List[dict]: ...


class _Embedder(Protocol):
    def build_embedding_text(self, **kwargs: Any) -> str: ...

    async def try_embed(
        self, text: str
    ) -> Tuple[Optional[List[float]], Optional[str]]: ...


# ---------------------------------------------------------------------------
# pure partitioning
# ---------------------------------------------------------------------------
def partition(
    rows: List[BackfillCandidate], limit: int
) -> Tuple[List[BackfillCandidate], List[BackfillCandidate]]:
    """Split candidates into (re-embed, dispatch), cheap ones first, capped.

    Rows without analysis AND without a cover cannot be dispatched (the VLM
    needs an image); they are excluded here and surfaced by
    :func:`undispatchable` so the caller reports rather than drops them.
    """
    reembed = [c for c in rows if c.has_analysis][:limit]
    room = max(limit - len(reembed), 0)
    dispatch = [c for c in rows if not c.has_analysis and c.cover_url][:room]
    return reembed, dispatch


def undispatchable(rows: List[BackfillCandidate]) -> List[BackfillCandidate]:
    return [c for c in rows if not c.has_analysis and not c.cover_url]


# ---------------------------------------------------------------------------
# re-embed from stored analysis
# ---------------------------------------------------------------------------
def _names(tag_rows: List[dict]) -> List[str]:
    out: List[str] = []
    for row in tag_rows or []:
        tag = row.get("tags") if isinstance(row, dict) else None
        name = (tag or {}).get("name") if isinstance(tag, dict) else None
        if name:
            out.append(str(name))
    return out


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


async def reembed_existing(
    cand: BackfillCandidate,
    embedding_service: _Embedder,
    analysis_repo: _AnalysisRepo,
    tags_repo: _TagsRepo,
) -> Tuple[bool, Optional[str]]:
    """Rebuild the embedding text from the stored analysis row and write the
    vector. Returns ``(True, None)`` or ``(False, reason)``; never raises for
    the expected shapes (missing row, embedder unconfigured/failed)."""
    analysis = await analysis_repo.get_analysis(
        cand.resource_id, analysis_level=ANALYSIS_LEVEL
    )
    if analysis is None:
        return False, "analysis_row_missing"

    tags = _names(await tags_repo.get_resource_tags(cand.resource_id))
    text = embedding_service.build_embedding_text(
        title=cand.title or "",
        description=cand.description or "",
        tags=tags,
        visual_description=analysis.get("visual_description") or "",
        detected_objects=_as_list(analysis.get("detected_objects")),
        detected_scenes=_as_list(analysis.get("detected_scenes")),
        detected_text=analysis.get("detected_text") or "",
    )
    vector, reason = await embedding_service.try_embed(text)
    if vector is None:
        return False, reason or "provider_error: empty result"

    written = await analysis_repo.update_embedding(
        cand.resource_id, vector, text, analysis_level=ANALYSIS_LEVEL
    )
    if written is None:
        # The row vanished between the read and the write; nothing landed.
        return False, "analysis_row_missing"
    return True, None


# ---------------------------------------------------------------------------
# candidate listing (DB)
# ---------------------------------------------------------------------------
def _first_cover(cover_urls: Any) -> Optional[str]:
    if isinstance(cover_urls, list) and cover_urls:
        first = cover_urls[0]
        return str(first) if first else None
    return None


def _missing_embedding_stmt(user_id: str):
    """resources ⋈ parsed_media ⟕ resource_analysis(L1), owner-scoped, web
    downloads only, not trashed, and either no L1 row or an L1 row whose
    vector is NULL. ``has_analysis`` is derived from the LEFT JOIN.

    The join pins ``analysis_level = 'L1'`` because the table's PK is
    ``(resource_id, analysis_level)``: without it a resource with an embedded
    L1 row plus a NULL-vector row at another level would re-qualify on every
    call and be re-billed forever. Rows with no analysis AND no cover are
    excluded here rather than after the fetch: the VLM cannot run on them, so
    they must not occupy the candidate window or inflate ``total_missing``.
    """
    first_cover = ParsedMedia.cover_urls.op("->>")(0)  # jsonb ->> int: index
    return (
        select(
            Resources.id,
            ParsedMedia.id.label("media_id"),
            ParsedMedia.platform_id,
            ParsedMedia.title,
            ParsedMedia.description,
            ParsedMedia.cover_urls,
            ResourceAnalysis.resource_id.label("analysis_rid"),
        )
        .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
        .outerjoin(
            ResourceAnalysis,
            and_(
                ResourceAnalysis.resource_id == Resources.id,
                ResourceAnalysis.analysis_level == ANALYSIS_LEVEL,
            ),
        )
        .where(Resources.creator_id == user_id)
        .where(Resources.source_type == "web")
        .where(Resources.is_trashed.is_(False))
        .where(ResourceAnalysis.content_embedding.is_(None))
        .where(
            or_(
                ResourceAnalysis.resource_id.isnot(None),
                # Agrees with _first_cover: a falsy first element is no cover.
                func.coalesce(first_cover, "") != "",
            )
        )
    )


async def list_candidates(
    user_id: str, limit: int
) -> Tuple[List[BackfillCandidate], int]:
    """Return up to ``limit`` candidates (analysis rows first, then newest
    downloads) plus the TOTAL count of resources still missing a vector."""
    if not user_id:
        # A falsy owner would render as ``creator_id IS NULL`` and select
        # exactly the orphan rows nobody should be billed for.
        return [], 0
    stmt = _missing_embedding_stmt(user_id)
    count_stmt = select(func.count()).select_from(
        stmt.with_only_columns(Resources.id).subquery()
    )
    ordered = stmt.order_by(
        ResourceAnalysis.resource_id.is_(None), Resources.id.desc()
    ).limit(limit)
    async with read_scope() as session:
        total = int((await session.execute(count_stmt)).scalar_one() or 0)
        rows = (await session.execute(ordered)).all()
    cands = [
        BackfillCandidate(
            resource_id=int(r[0]),
            media_id=int(r[1]),
            platform_id=str(r[2]),
            title=r[3] or "",
            description=r[4] or "",
            cover_url=_first_cover(r[5]),
            has_analysis=r[6] is not None,
        )
        for r in rows
    ]
    return cands, total
