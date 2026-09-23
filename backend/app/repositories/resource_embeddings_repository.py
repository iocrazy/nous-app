"""Data access for ``resource_embeddings`` (migration 494).

One ``halfvec(2048)`` per (resource, layer, space), HNSW-indexed. Readers and
writers always name the space and the layer: a vector is only comparable to
vectors of the same space (``app.core.embedding_space``).

Deploy-order window. Migrations and code ship on independent triggers, so
this code can reach a database where 494 has not run. A missing table
(SQLSTATE 42P01) or function (42883) raises the typed
:class:`EmbeddingStoreMissing` — never ``[]`` / ``None``, because "the store
does not exist yet" and "nothing matched" would otherwise be byte identical
downstream. Every other ProgrammingError is a real defect and re-raises
untouched (42703 undefined column also says "does not exist", which is why the
classification reads the SQLSTATE, not the message).

The one raw ``text()`` here is the pgvector RPC call, the same structural
exception as ``AnalysisRepository.search_by_embedding``; every bind is
``CAST(:x AS type)`` (SQLAlchemy does not bind ``:x::type``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError

from app.db.session import read_scope, write_scope
from app.models import ParsedMedia, ResourceAnalysis, ResourceEmbeddings, Resources

# SQLSTATEs that mean "migration 494 has not run on this database".
_UNDEFINED_TABLE = "42P01"
_UNDEFINED_FUNCTION = "42883"
_STORE_MISSING_STATES = frozenset({_UNDEFINED_TABLE, _UNDEFINED_FUNCTION})

# The only analysis level analyze_l1 writes (see embedding_backfill).
_ANALYSIS_LEVEL = "L1"


class EmbeddingStoreMissing(RuntimeError):
    """``resource_embeddings`` / ``embedding_spaces`` / the RPC does not exist
    in this database (migration 494 not applied yet). Callers map it to the
    typed reason ``store_missing``; readers fall back to the legacy store."""


@dataclass(frozen=True)
class BackfillRow:
    """A resource that has no vector in the requested (space, layer)."""

    resource_id: int
    media_id: int
    platform_id: str
    title: str
    description: str
    has_analysis: bool


def is_store_missing(exc: ProgrammingError) -> bool:
    """True when the driver reported 42P01 (table) or 42883 (function)."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in _STORE_MISSING_STATES


def _store_missing(what: str) -> EmbeddingStoreMissing:
    return EmbeddingStoreMissing(
        f"{what} does not exist in this database (migration 494 not applied)"
    )


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(map(str, embedding)) + "]"


def _as_floats(value: Any) -> list[float]:
    """HALFVEC reads back as ``pgvector.HalfVector``; callers get plain floats."""
    if value is None:
        return []
    raw = value.to_list() if hasattr(value, "to_list") else value
    return [float(x) for x in raw]


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _search_rows_out(rows: Iterator[Any]) -> list[dict]:
    """Same coercions as ``AnalysisRepository._search_rows_out`` plus
    ``resource_id``: bigint → int, timestamptz → ISO str."""
    output = []
    for row in rows:
        d = dict(row)
        for key in ("resource_id", "media_id", "view_count"):
            if d.get(key) is not None:
                d[key] = int(d[key])
        d["created_at"] = _iso(d.get("created_at"))
        output.append(d)
    return output


class ResourceEmbeddingsRepository:
    """CRUD + nearest-neighbour search on ``resource_embeddings``."""

    async def upsert(
        self,
        *,
        resource_id: int,
        layer: str,
        space_id: int,
        embedding: list[float],
        source_hash: str,
        source_text: str | None,
    ) -> None:
        """Insert or replace the vector of (resource, layer, space).

        ``created_at`` keeps the first write; ``updated_at`` moves on every
        replace."""
        stmt = pg_insert(ResourceEmbeddings).values(
            resource_id=resource_id,
            layer=layer,
            space_id=space_id,
            embedding=embedding,
            source_hash=source_hash,
            source_text=source_text,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["resource_id", "layer", "space_id"],
            set_={
                "embedding": stmt.excluded.embedding,
                "source_hash": stmt.excluded.source_hash,
                "source_text": stmt.excluded.source_text,
                "updated_at": func.now(),
            },
        )
        try:
            async with write_scope() as session:
                await session.execute(stmt)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise

    async def get(self, resource_id: int, layer: str, space_id: int) -> dict | None:
        stmt = select(ResourceEmbeddings).where(
            ResourceEmbeddings.resource_id == resource_id,
            ResourceEmbeddings.layer == layer,
            ResourceEmbeddings.space_id == space_id,
        )
        try:
            async with read_scope() as session:
                row = (await session.execute(stmt)).scalars().first()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise
        if row is None:
            return None
        return {
            "resource_id": int(row.resource_id),
            "layer": row.layer,
            "space_id": int(row.space_id),
            "embedding": _as_floats(row.embedding),
            "source_hash": row.source_hash,
            "source_text": row.source_text,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    async def search(
        self,
        *,
        embedding: list[float],
        space_id: int,
        layer: str,
        user_id: str,
        limit: int,
        threshold: float,
    ) -> list[dict]:
        """Nearest resources of ``user_id`` in one (space, layer), via the
        ``match_resource_embeddings`` RPC.

        ``user_id`` is REQUIRED and non-empty: the RPC reads a NULL owner as
        "every user", so a falsy one raises instead of widening the scan.

        Row keys: resource_id, media_id, platform_id, title, description,
        cover_urls, author, view_count, created_at (ISO str), similarity.
        """
        if not user_id:
            raise ValueError("search requires a user_id; refusing an unscoped scan")
        params = {
            "q": _vector_literal(embedding),
            "s": space_id,
            "l": layer,
            "t": threshold,
            "c": limit,
            "u": user_id,
        }
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT * FROM match_resource_embeddings("
                        "CAST(:q AS halfvec), CAST(:s AS bigint), CAST(:l AS text), "
                        "CAST(:t AS double precision), CAST(:c AS int), "
                        "CAST(:u AS uuid))"
                    ),
                    params,
                )
                rows = result.mappings().all()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("match_resource_embeddings") from exc
            raise
        return _search_rows_out(rows)

    @staticmethod
    def _owned_web_resources(user_id: str):
        """The population both coverage and backfill count against: the
        user's non-trashed web downloads that have a parsed_media row."""
        return (
            select(Resources.id)
            .where(Resources.creator_id == user_id)
            .where(Resources.source_type == "web")
            .where(Resources.is_trashed.is_(False))
            .where(Resources.media_id.isnot(None))
        )

    async def coverage(
        self, *, user_id: str, space_id: int, layer: str
    ) -> tuple[int, int]:
        """``(covered, total)``: how many of the user's non-trashed web
        resources have a vector in (space, layer), out of how many."""
        if not user_id:
            return 0, 0
        base = self._owned_web_resources(user_id)
        total_stmt = select(func.count()).select_from(base.subquery())
        covered_stmt = select(func.count()).select_from(
            base.join(
                ResourceEmbeddings,
                and_(
                    ResourceEmbeddings.resource_id == Resources.id,
                    ResourceEmbeddings.space_id == space_id,
                    ResourceEmbeddings.layer == layer,
                ),
            ).subquery()
        )
        try:
            async with read_scope() as session:
                total = int((await session.execute(total_stmt)).scalar_one() or 0)
                covered = int((await session.execute(covered_stmt)).scalar_one() or 0)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise
        return covered, total

    async def missing_for_user(
        self, *, user_id: str, space_id: int, layer: str, limit: int
    ) -> tuple[list[BackfillRow], int]:
        """Up to ``limit`` resources without a vector in (space, layer),
        resources with an L1 analysis first, then newest; plus the TOTAL
        still missing."""
        if not user_id:
            # A falsy owner would render as ``creator_id IS NULL`` and select
            # exactly the orphan rows nobody should be billed for.
            return [], 0
        stmt = (
            select(
                Resources.id,
                ParsedMedia.id.label("media_id"),
                ParsedMedia.platform_id,
                ParsedMedia.title,
                ParsedMedia.description,
                ResourceAnalysis.resource_id.label("analysis_rid"),
            )
            .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
            .outerjoin(
                ResourceEmbeddings,
                and_(
                    ResourceEmbeddings.resource_id == Resources.id,
                    ResourceEmbeddings.space_id == space_id,
                    ResourceEmbeddings.layer == layer,
                ),
            )
            .outerjoin(
                ResourceAnalysis,
                and_(
                    ResourceAnalysis.resource_id == Resources.id,
                    ResourceAnalysis.analysis_level == _ANALYSIS_LEVEL,
                ),
            )
            .where(Resources.creator_id == user_id)
            .where(Resources.source_type == "web")
            .where(Resources.is_trashed.is_(False))
            .where(ResourceEmbeddings.resource_id.is_(None))
        )
        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Resources.id).subquery()
        )
        ordered = stmt.order_by(
            ResourceAnalysis.resource_id.is_(None), Resources.id.desc()
        ).limit(limit)
        try:
            async with read_scope() as session:
                total = int((await session.execute(count_stmt)).scalar_one() or 0)
                rows = (await session.execute(ordered)).all()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise
        return [
            BackfillRow(
                resource_id=int(r[0]),
                media_id=int(r[1]),
                platform_id=str(r[2]),
                title=r[3] or "",
                description=r[4] or "",
                has_analysis=r[5] is not None,
            )
            for r in rows
        ], total


_repository: ResourceEmbeddingsRepository | None = None


def get_resource_embeddings_repository() -> ResourceEmbeddingsRepository:
    global _repository
    if _repository is None:
        _repository = ResourceEmbeddingsRepository()
    return _repository
