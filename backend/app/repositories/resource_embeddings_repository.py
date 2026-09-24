"""Data access for ``resource_embeddings`` (migration 499).

One ``halfvec(2048)`` per (resource, layer, space), HNSW-indexed. Readers and
writers always name the space and the layer: a vector is only comparable to
vectors of the same space (``app.core.embedding_space``).

Deploy-order window. Migrations and code ship on independent triggers, so
this code can reach a database where 499 has not run. A missing table
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
from typing import Any, Iterator, Literal

from sqlalchemy import and_, case, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError

from app.db.session import read_scope, write_scope
from app.models import (
    ParsedMedia,
    ResourceAnalysis,
    ResourceEmbeddings,
    Resources,
    ResourceSummaries,
    ResourceTranscripts,
)
from app.services.library.like_escape import LIKE_ESCAPE_CHAR, escape_like

# SQLSTATEs that mean "migration 499 has not run on this database".
_UNDEFINED_TABLE = "42P01"
_UNDEFINED_FUNCTION = "42883"
_STORE_MISSING_STATES = frozenset({_UNDEFINED_TABLE, _UNDEFINED_FUNCTION})

# The only analysis level analyze_l1 writes (see embedding_backfill).
_ANALYSIS_LEVEL = "L1"


class EmbeddingStoreMissing(RuntimeError):
    """``resource_embeddings`` / ``embedding_spaces`` / the RPC does not exist
    in this database (migration 499 not applied yet). Callers map it to the
    typed reason ``store_missing``; readers fall back to the legacy store."""


#: Why a resource is up for (re-)embedding:
#: ``missing`` — no vector in the (space, layer);
#: ``stale_version`` — its ``source_hash`` was written by another
#: ``DOC_VERSION`` (or predates the ``"<version>:<sha1>"`` format);
#: ``stale_source`` — a summary / transcript arrived after the vector.
PendingReason = Literal["missing", "stale_version", "stale_source"]


@dataclass(frozen=True)
class BackfillRow:
    """A resource whose vector in the requested (space, layer) is missing or
    stale (``reason``)."""

    resource_id: int
    media_id: int
    platform_id: str
    title: str
    description: str
    has_analysis: bool
    reason: PendingReason = "missing"


def _stale_version(doc_version: str):
    """The row's hash was not written by ``doc_version``. The version is a
    LIKE pattern prefix, so its ``_`` must be escaped to match itself."""
    return ResourceEmbeddings.source_hash.notlike(
        f"{escape_like(doc_version)}:%", escape=LIKE_ESCAPE_CHAR
    )


def _stale_source():
    """A summary or transcript of the resource is newer than its vector.

    Neither table has an ``updated_at``: ``created_at`` serves as the last
    write time (``AIRepository.save_transcript`` / ``save_summary`` move it on
    every upsert, so a re-run counts too)."""
    newer_summary = exists().where(
        ResourceSummaries.resource_id == Resources.id,
        ResourceSummaries.created_at > ResourceEmbeddings.updated_at,
    )
    newer_transcript = exists().where(
        ResourceTranscripts.resource_id == Resources.id,
        ResourceTranscripts.created_at > ResourceEmbeddings.updated_at,
    )
    return or_(newer_summary, newer_transcript)


def is_store_missing(exc: ProgrammingError) -> bool:
    """True when the driver reported 42P01 (table) or 42883 (function)."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in _STORE_MISSING_STATES


def _store_missing(what: str) -> EmbeddingStoreMissing:
    return EmbeddingStoreMissing(
        f"{what} does not exist in this database (migration 499 not applied)"
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
        """The population coverage, stale_count and pending_for_user all count
        against: the user's non-trashed web downloads that have a
        parsed_media row (INNER JOIN, same predicate in all three)."""
        return (
            select(Resources.id)
            .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
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

    async def pending_for_user(
        self,
        *,
        user_id: str,
        space_id: int,
        layer: str,
        limit: int,
        doc_version: str,
    ) -> tuple[list[BackfillRow], int]:
        """Up to ``limit`` resources to (re-)embed in (space, layer), plus the
        TOTAL pending. Two kinds, each row tagged with its ``reason``:

        * ``missing`` — no row in (space, layer);
        * stale — a row exists but its ``source_hash`` does not start with
          ``"<doc_version>:"`` (``stale_version``), or a summary / transcript
          was created after the row's ``updated_at`` (``stale_source``).

        Missing rows first (they have no vector at all), then resources with
        an L1 analysis, then newest."""
        if not user_id:
            # A falsy owner would render as ``creator_id IS NULL`` and select
            # exactly the orphan rows nobody should be billed for.
            return [], 0
        missing = ResourceEmbeddings.resource_id.is_(None)
        stale_version = _stale_version(doc_version)
        reason = case(
            (missing, "missing"),
            (stale_version, "stale_version"),
            else_="stale_source",
        )
        stmt = (
            select(
                Resources.id,
                ParsedMedia.id.label("media_id"),
                ParsedMedia.platform_id,
                ParsedMedia.title,
                ParsedMedia.description,
                ResourceAnalysis.resource_id.label("analysis_rid"),
                reason.label("reason"),
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
            .where(Resources.media_id.isnot(None))
            .where(or_(missing, stale_version, _stale_source()))
        )
        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Resources.id).subquery()
        )
        ordered = stmt.order_by(
            ResourceEmbeddings.resource_id.isnot(None),
            ResourceAnalysis.resource_id.is_(None),
            Resources.id.desc(),
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
                reason=r[6],
            )
            for r in rows
        ], total

    async def stale_count(
        self, *, user_id: str, space_id: int, layer: str, doc_version: str
    ) -> int:
        """How many of the user's vectors in (space, layer) are stale (the
        ``stale_version`` / ``stale_source`` half of :meth:`pending_for_user`).
        """
        if not user_id:
            return 0
        stmt = select(func.count()).select_from(
            self._owned_web_resources(user_id)
            .join(
                ResourceEmbeddings,
                and_(
                    ResourceEmbeddings.resource_id == Resources.id,
                    ResourceEmbeddings.space_id == space_id,
                    ResourceEmbeddings.layer == layer,
                ),
            )
            .where(or_(_stale_version(doc_version), _stale_source()))
            .subquery()
        )
        try:
            async with read_scope() as session:
                return int((await session.execute(stmt)).scalar_one() or 0)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise

    async def rewrite_hash(
        self,
        resource_id: int,
        layer: str,
        space_id: int,
        *,
        old_hash: str,
        new_hash: str,
    ) -> None:
        """Relabel a current vector: set ``source_hash`` to ``new_hash`` (and
        move ``updated_at``) without touching the embedding. Compare-and-set
        on ``old_hash``: if a concurrent write already replaced the row, this
        is a no-op."""
        stmt = (
            update(ResourceEmbeddings)
            .where(
                ResourceEmbeddings.resource_id == resource_id,
                ResourceEmbeddings.layer == layer,
                ResourceEmbeddings.space_id == space_id,
                ResourceEmbeddings.source_hash == old_hash,
            )
            .values(source_hash=new_hash, updated_at=func.now())
        )
        try:
            async with write_scope() as session:
                await session.execute(stmt)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise

    async def touch(self, resource_id: int, layer: str, space_id: int) -> None:
        """Move ``updated_at`` without rewriting the vector: a ``stale_source``
        row whose recomposed document hashes the same is current again, and
        must stop being listed."""
        stmt = (
            update(ResourceEmbeddings)
            .where(
                ResourceEmbeddings.resource_id == resource_id,
                ResourceEmbeddings.layer == layer,
                ResourceEmbeddings.space_id == space_id,
            )
            .values(updated_at=func.now())
        )
        try:
            async with write_scope() as session:
                await session.execute(stmt)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("resource_embeddings") from exc
            raise


_repository: ResourceEmbeddingsRepository | None = None


def get_resource_embeddings_repository() -> ResourceEmbeddingsRepository:
    global _repository
    if _repository is None:
        _repository = ResourceEmbeddingsRepository()
    return _repository
