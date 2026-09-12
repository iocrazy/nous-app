"""Repository for Video Analysis data access (异步), SQLAlchemy 2.0 ORM-backed.

Backs the ``resource_analysis`` surface (resource_id/analysis_level composite
PK, pgvector embedding column, match_videos_by_embedding RPC).

STRATEGY-C VALUE-TYPE PARITY
==============================
The public return shape is a SELECT-* dict keyed on every column of
``resource_analysis``.  Column-by-column audit (vs. app/models/media.py +
former REST PostgREST serialisation):

  resource_id (BigInteger, PK)
      REST → JSON number → Python int.  ORM → native int.  No coercion.

  analysis_level (String(10), PK)
      REST/ORM → str.  No coercion.

  visual_description / detected_text / full_text_for_embedding (Text)
      REST/ORM → str or None.  No coercion.

  detected_objects / detected_scenes / detected_people (JSONB)
      REST → JSON array → list.  ORM → native list.  No coercion.

  analysis_model (String(50))
      REST/ORM → str or None.  No coercion.

  analysis_cost (Numeric(10,6))
      DECISION: cast to ``float``.  The ``AnalysisResponse`` Pydantic model
      declares ``analysis_cost: float = 0.0`` and legacy PostgREST serialised
      Numeric as a JSON string, but Pydantic coerced it to float before the
      response left the router.  We produce a float here so downstream code
      (direct dict access or Pydantic) gets a consistent numeric type.

  analyzed_at / created_at / updated_at (DateTime(True) / timestamptz)
      ORM → ``.isoformat()`` (None stays None).

  content_embedding (Vector(1536))
      NOT projected in any SELECT-* consumer beyond a truthiness check
      (``analysis.get("content_embedding")`` in search_router.py).
      ORM → native ``list[float]`` (pgvector.sqlalchemy Vector type).

INHERIT-DON'T-FIX CALLS
========================
The following legacy REST quirks are reproduced verbatim (not repaired):

  * ``get_analysis`` selects on a composite-PK table (resource_id,
    analysis_level) — multiple rows per resource_id are possible.  We use
    ``scalars().first()`` (returns the first row; does NOT raise on
    multiple rows the way REST's ``maybe_single()`` would 406).

  * ``upsert_analysis`` uses get-then-create-or-update control flow (not ON
    CONFLICT) — reproduces the legacy REST branch logic exactly.

  * ``get_videos_by_analysis_level`` mirrors a legacy REST join to
    ``resources(id, title, description)``, but ``resources`` has no
    ``title`` or ``description`` column — this was a broken REST query.
    We replicate the nested ``"resources"`` key but return ``None`` for the
    missing columns.

  * ``search_by_embedding`` keeps the match_videos_by_embedding RPC via
    ``session.execute(text(…))``.  The RPC's RETURNS TABLE is the
    authoritative dict shape; ``created_at`` (timestamptz from parsed_media)
    is coerced to ISO string here because ``SearchResult.created_at`` is
    typed ``Optional[str]``.

Writes COMMIT via ``write_scope()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, select, text
from sqlalchemy.exc import ProgrammingError

from app.db.session import read_scope, write_scope
from app.models import ResourceAnalysis, Resources

# PostgreSQL SQLSTATE for "function does not exist".
_UNDEFINED_FUNCTION = "42883"


class EmbeddingSearchUnavailable(RuntimeError):
    """The embedding RPC is not callable in the shape this code expects.

    Raised instead of returning ``[]`` so the caller can tell "the engine is
    missing its schema" apart from "nothing matched". Those two are byte
    identical downstream otherwise, which is the failure shape CLAUDE.md warns
    about: an empty result is not a negative finding.
    """


def _is_undefined_function(exc: ProgrammingError) -> bool:
    """True when the driver reported SQLSTATE 42883 (undefined function)."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate is None:
        sqlstate = getattr(getattr(exc, "orig", None), "pgcode", None)
    return sqlstate == _UNDEFINED_FUNCTION


class AnalysisRepository:
    """Repository for video analysis CRUD operations (异步), ORM-backed."""

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: ResourceAnalysis) -> dict:
        """Convert a ResourceAnalysis ORM object to a REST-parity dict."""
        return {
            "resource_id": int(row.resource_id),
            "analysis_level": row.analysis_level,
            "visual_description": row.visual_description,
            "detected_objects": row.detected_objects,
            "detected_scenes": row.detected_scenes,
            "detected_people": row.detected_people,
            "detected_text": row.detected_text,
            "full_text_for_embedding": row.full_text_for_embedding,
            # content_embedding: list[float] or None (native Vector; see parity doc)
            "content_embedding": row.content_embedding,
            "analysis_model": row.analysis_model,
            # analysis_cost: Numeric → float (see parity doc above)
            "analysis_cost": (
                float(row.analysis_cost) if row.analysis_cost is not None else None
            ),
            "analyzed_at": (row.analyzed_at.isoformat() if row.analyzed_at else None),
            "created_at": (row.created_at.isoformat() if row.created_at else None),
            "updated_at": (row.updated_at.isoformat() if row.updated_at else None),
        }

    # ── reads ────────────────────────────────────────────────────────────────

    async def get_analysis(self, resource_id: int) -> Optional[dict]:
        """Get analysis for a resource.

        Mirrors legacy REST ``maybe_single()`` on resource_id.  The composite
        PK ``(resource_id, analysis_level)`` means multiple rows can exist
        per resource; ``scalars().first()`` reproduces maybe_single's
        effective behaviour (first row or None).  INHERIT-DON'T-FIX: we do
        NOT raise on multiple rows (REST would 406).
        """
        async with read_scope() as session:
            result = await session.execute(
                select(ResourceAnalysis).where(
                    ResourceAnalysis.resource_id == resource_id
                )
            )
            row = result.scalars().first()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def get_videos_by_analysis_level(
        self, level: str, limit: int = 100
    ) -> List[dict]:
        """Get analysis rows with a specific level, joined with resources.

        Mirrors legacy REST ``select("*, resources(id, title,
        description)")``.  INHERIT-DON'T-FIX: the ``resources`` table has no
        ``title`` or ``description`` column — the original REST query was
        broken and would 400.  We replicate the nested ``"resources"`` key
        shape with None for the missing columns.
        """
        async with read_scope() as session:
            result = await session.execute(
                select(ResourceAnalysis, Resources.id.label("res_id"))
                .outerjoin(Resources, Resources.id == ResourceAnalysis.resource_id)
                .where(ResourceAnalysis.analysis_level == level)
                .limit(limit)
            )
            rows = result.all()

        items = []
        for ra, res_id in rows:
            d = self._row_to_dict(ra)
            d["resources"] = {
                "id": int(res_id) if res_id is not None else None,
                # resources has no title/description — legacy REST query was broken
                "title": None,
                "description": None,
            }
            items.append(d)
        return items

    async def search_by_embedding(
        self,
        embedding: List[float],
        user_id: str,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> List[dict]:
        """Search for similar resources via the match_videos_by_embedding RPC.

        Keeps using the RPC via raw SQL executed through the ORM session.
        The vector is passed as a formatted string with an explicit CAST.

        ``user_id`` scopes the scan to rows the caller owns and is REQUIRED.
        It is the only cross-user control on ``/search/similar`` and
        ``/search/quick``, neither of which post-filters the RPC output, so a
        default would let one forgotten keyword silently restore the global
        scan. Before migration 463 the RPC ranked every user's analysis rows
        and spent the whole ``limit`` budget before any ownership filter ran.

        Returned dict keys: media_id, platform_id, title, description,
        cover_urls, author, view_count, created_at, similarity.
        ``created_at`` (timestamptz) is coerced to ISO str because
        ``SearchResult.created_at`` is ``Optional[str]``.
        """
        embedding_str = f"[{','.join(map(str, embedding))}]"
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT * FROM match_videos_by_embedding("
                        "CAST(:q AS vector), :t::double precision, :c::int, "
                        "CAST(:u AS uuid))"
                    ),
                    {"q": embedding_str, "t": threshold, "c": limit, "u": user_id},
                )
                rows = result.mappings().all()
        except ProgrammingError as exc:
            # Deployment-order guard. Migrations and backend code ship on
            # independent triggers (see CLAUDE.md "migration 与代码部署无顺序保证"),
            # so this code can reach a database where 463 has not run yet and the
            # 4-arg signature does not exist. Undefined-function (SQLSTATE 42883)
            # raises a typed error rather than returning an empty list: "the
            # engine has no schema" and "nothing matched" are byte identical
            # downstream, and the router turns this one into a 503 so neither
            # the user nor the post-release error funnel reads a transport
            # failure as a confident answer. Every other ProgrammingError is a
            # real defect and re-raises untouched.
            if _is_undefined_function(exc):
                logger.error(
                    "match_videos_by_embedding is missing its p_user_id argument "
                    "(SQLSTATE 42883) — migration 463 has not been applied to "
                    "this database yet. Semantic search is unavailable."
                )
                raise EmbeddingSearchUnavailable(
                    "match_videos_by_embedding/4 does not exist in this database"
                ) from exc
            raise

        output = []
        for row in rows:
            d = dict(row)
            # media_id (bigint) → int; view_count (bigint) → int.
            if d.get("media_id") is not None:
                d["media_id"] = int(d["media_id"])
            if d.get("view_count") is not None:
                d["view_count"] = int(d["view_count"])
            # created_at (timestamptz) → ISO str for SearchResult.created_at.
            ca = d.get("created_at")
            if isinstance(ca, datetime):
                d["created_at"] = ca.isoformat()
            output.append(d)
        return output

    async def get_analysis_stats(self) -> dict:
        """Get statistics about video analysis coverage.

        Selects only analysis_level, counts in Python — dict shape
        ``{none, L1, L2, L3, total}``.

        (A prior ``get_analysis_stats`` Postgres RPC was never defined in
        any migration, so the RPC round-trip always raised PGRST202 and
        fell through to this manual-count path — it was removed.)
        """
        async with read_scope() as session:
            result = await session.execute(select(ResourceAnalysis.analysis_level))
            levels = [r[0] for r in result.fetchall()]

        stats: Dict[str, Any] = {
            "none": 0,
            "L1": 0,
            "L2": 0,
            "L3": 0,
            "total": len(levels),
        }
        for level in levels:
            if level in stats:
                stats[level] += 1
        return stats

    # ── writes ───────────────────────────────────────────────────────────────

    async def create_analysis(self, resource_id: int, **kwargs) -> dict:
        """Create an analysis record.

        INHERIT-DON'T-FIX: legacy REST passed ``analyzed_at`` as an ISO
        string; ORM binds ``datetime.utcnow()`` — same semantic value,
        different type.  Filter ``v is not None`` mirrors legacy REST: keeps
        0 / [] / "none".
        """
        data: Dict[str, Any] = {
            "resource_id": resource_id,
            "analysis_level": kwargs.get("analysis_level", "none"),
            "visual_description": kwargs.get("visual_description"),
            "detected_objects": kwargs.get("detected_objects", []),
            "detected_scenes": kwargs.get("detected_scenes", []),
            "detected_people": kwargs.get("detected_people", []),
            "detected_text": kwargs.get("detected_text"),
            "full_text_for_embedding": kwargs.get("full_text_for_embedding"),
            "analysis_model": kwargs.get("analysis_model"),
            "analysis_cost": kwargs.get("analysis_cost", 0),
            "analyzed_at": datetime.utcnow(),
        }
        # Mirrors legacy REST: {k: v for k, v in data.items() if v is not None}
        data = {k: v for k, v in data.items() if v is not None}

        row = ResourceAnalysis(**data)
        async with write_scope() as session:
            session.add(row)
            await session.flush()
            # flush() triggers INSERT … RETURNING; server defaults
            # (created_at / updated_at) are populated on row.
            result = self._row_to_dict(row)
        logger.info(f"Created analysis for resource {resource_id}")
        return result

    async def update_analysis(self, resource_id: int, **kwargs) -> Optional[dict]:
        """Update an analysis record.

        Mirrors legacy REST: filter None values, add analyzed_at, then
        update.  Returns None when no row exists (legacy REST returned None
        on empty data).
        """
        update_data: Dict[str, Any] = {k: v for k, v in kwargs.items() if v is not None}
        if not update_data:
            return await self.get_analysis(resource_id)

        update_data["analyzed_at"] = datetime.utcnow()

        async with write_scope() as session:
            result = await session.execute(
                select(ResourceAnalysis).where(
                    ResourceAnalysis.resource_id == resource_id
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            for key, val in update_data.items():
                setattr(row, key, val)
            await session.flush()
            return self._row_to_dict(row)

    async def upsert_analysis(self, resource_id: int, **kwargs) -> dict:
        """Create or update analysis record.

        INHERIT-DON'T-FIX: mirrors legacy REST get-then-create-or-update flow
        exactly (no ON CONFLICT).  Checks by resource_id only, ignoring
        analysis_level.
        """
        existing = await self.get_analysis(resource_id)
        if existing:
            return await self.update_analysis(resource_id, **kwargs)
        return await self.create_analysis(resource_id, **kwargs)

    async def update_embedding(
        self, resource_id: int, embedding: List[float], full_text: str
    ) -> Optional[dict]:
        """Update the vector embedding for a resource.

        Binds the native ``list[float]`` through the Vector(1536) type
        handler.
        """
        async with write_scope() as session:
            result = await session.execute(
                select(ResourceAnalysis).where(
                    ResourceAnalysis.resource_id == resource_id
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            row.content_embedding = embedding  # list[float]; Vector type encodes
            row.full_text_for_embedding = full_text
            await session.flush()
            out = self._row_to_dict(row)
        logger.info(f"Updated embedding for resource {resource_id}")
        return out

    async def delete_analysis(self, resource_id: int) -> bool:
        """Delete analysis record(s) for a resource.  Returns True if any deleted."""
        async with write_scope() as session:
            result = await session.execute(
                delete(ResourceAnalysis).where(
                    ResourceAnalysis.resource_id == resource_id
                )
            )
            return result.rowcount > 0


def get_analysis_repository() -> "AnalysisRepository":
    """Return the ORM-backed AnalysisRepository (post-rollout, flag retired)."""
    return AnalysisRepository()
