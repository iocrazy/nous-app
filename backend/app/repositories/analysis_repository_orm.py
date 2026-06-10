"""SQLAlchemy 2.0 ORM implementation of AnalysisRepository (resource_analysis).

REST → ORM successor for the ``resource_analysis`` surface, following the
validated ``TagPreferencesRepositoryOrm`` template.  ``AnalysisRepositoryOrm``
subclasses ``AnalysisRepository`` and overrides the data methods.  Call sites
route through ``get_analysis_repository()`` (bottom of
``analysis_repository.py``).

STRATEGY-C VALUE-TYPE PARITY
==============================
The public return shape is a SELECT-* dict keyed on every column of
``resource_analysis``.  Column-by-column audit (vs. app/models/media.py +
REST PostgREST serialisation):

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
      declares ``analysis_cost: float = 0.0`` and PostgREST serialises
      Numeric as a JSON string, but Pydantic coerces it to float before the
      response leaves the router.  The task spec says "cast to float to
      match" — we produce a float here so downstream code (direct dict
      access or Pydantic) gets a consistent numeric type regardless of path.

  analyzed_at / created_at / updated_at (DateTime(True) / timestamptz)
      REST → ISO-8601 string.  ORM → ``.isoformat()`` (None stays None).

  content_embedding (Vector(1536))
      NOT projected in any SELECT-* consumer beyond a truthiness check
      (``analysis.get("content_embedding")`` in search_router.py).
      ORM → native ``list[float]`` (pgvector.sqlalchemy Vector type).
      REST → formatted string ``"[0.1,0.2,…]"``.  Both are truthy when
      non-empty; no consumer performs string operations on the value.
      INHERIT-DON'T-FIX: the format difference is acceptable; documented
      here.

INHERIT-DON'T-FIX CALLS
========================
The following REST quirks are reproduced verbatim (not repaired):

  * ``maybe_single()`` on a composite-PK table (resource_id, analysis_level)
    — multiple rows per resource_id are possible; REST would 406 on >1.  We
    replicate with ``scalars().first()`` (returns the first row, same
    effective behaviour for the callers who expect 0 or 1).

  * ``analyzed_at = datetime.utcnow().isoformat()`` in create_analysis —
    REST passed an ISO string to PostgREST.  ORM binds ``datetime.utcnow()``
    (a datetime object); the semantic result is identical.  Output dict is
    ``.isoformat()`` in both paths.

  * ``upsert_analysis`` uses get-then-create-or-update control flow (not ON
    CONFLICT) — reproduces REST's exact branch logic.

  * ``update_embedding`` in REST built a ``"[…]"`` string for PostgREST;
    ORM binds the native ``list[float]`` through the Vector type handler.
    Same DB outcome; RETURNING gives back a list.  No string conversion.

  * ``get_videos_by_analysis_level`` selects ``resources(id, title,
    description)`` via PostgREST join, but ``resources`` has no ``title`` or
    ``description`` column — this is a broken REST query.  ORM replicates the
    nested ``"resources"`` key but returns ``None`` for the missing columns.

  * ``search_by_embedding`` keeps the match_videos_by_embedding RPC via
    ``session.execute(text(…))`` — parity with the REST ``.rpc()`` path.
    The RPC's RETURNS TABLE is the authoritative dict shape; ``created_at``
    (timestamptz from parsed_media) is coerced to ISO string here because
    ``SearchResult.created_at`` is typed ``Optional[str]``.

Writes COMMIT via ``write_scope()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, select, text

from app.db.session import read_scope, write_scope
from app.models import ResourceAnalysis, Resources
from app.repositories.analysis_repository import AnalysisRepository


class AnalysisRepositoryOrm(AnalysisRepository):
    """ORM-backed AnalysisRepository for resource_analysis."""

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

        Mirrors REST ``maybe_single()`` on resource_id.  The composite PK
        ``(resource_id, analysis_level)`` means multiple rows can exist per
        resource; ``scalars().first()`` reproduces maybe_single's effective
        behaviour (first row or None).  INHERIT-DON'T-FIX: we do NOT raise
        on multiple rows (REST would 406).
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

        Mirrors REST ``select("*, resources(id, title, description)")``.
        INHERIT-DON'T-FIX: the ``resources`` table has no ``title`` or
        ``description`` column — the REST query is broken and would 400.
        We replicate the nested ``"resources"`` key shape with None for the
        missing columns.
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
                # resources has no title/description — REST query is broken
                "title": None,
                "description": None,
            }
            items.append(d)
        return items

    async def search_by_embedding(
        self, embedding: List[float], limit: int = 10, threshold: float = 0.7
    ) -> List[dict]:
        """Search for similar resources via the match_videos_by_embedding RPC.

        Keeps using the RPC (parity with REST ``.rpc()`` path) via raw SQL
        executed through the ORM session.  The vector is passed as a formatted
        string with an explicit CAST (same approach as REST's embedding_str).

        Returned dict keys: media_id, platform_id, title, description,
        cover_urls, author, view_count, created_at, similarity.
        ``created_at`` (timestamptz) is coerced to ISO str because
        ``SearchResult.created_at`` is ``Optional[str]``.
        """
        embedding_str = f"[{','.join(map(str, embedding))}]"
        async with read_scope() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM match_videos_by_embedding("
                    "CAST(:q AS vector), :t::double precision, :c::int)"
                ),
                {"q": embedding_str, "t": threshold, "c": limit},
            )
            rows = result.mappings().all()

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

        Mirrors REST: selects only analysis_level, counts in Python — same
        dict shape ``{none, L1, L2, L3, total}``.
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
        """Create an analysis record.  Mirrors REST insert + data[0] return.

        INHERIT-DON'T-FIX: REST passed ``analyzed_at`` as an ISO string; ORM
        binds ``datetime.utcnow()`` — same semantic value, different type.
        Filter ``v is not None`` mirrors REST: keeps 0 / [] / "none".
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
        # Mirrors REST: {k: v for k, v in data.items() if v is not None}
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

        Mirrors REST: filter None values, add analyzed_at, then update.
        Returns None when no row exists (REST returns None on empty data).
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

        INHERIT-DON'T-FIX: mirrors REST get-then-create-or-update flow exactly
        (no ON CONFLICT).  The REST path checks by resource_id only, ignoring
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

        INHERIT-DON'T-FIX: REST built a ``"[…]"`` string for PostgREST;
        ORM binds the native ``list[float]`` through the Vector(1536) type
        handler — same DB outcome.
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


__all__ = ["AnalysisRepositoryOrm"]
