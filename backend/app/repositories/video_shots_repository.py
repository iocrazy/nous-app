"""Data access for the shot index (migration 507): ``video_shots``,
``video_shot_embeddings``, ``video_shot_indexes``.

Two repositories, one per concern:

* :class:`VideoShotsRepository` — the cut list. ``replace`` swaps a video's
  whole list in ONE transaction (old shots go, and their vectors with them by
  FK cascade), so a reader never sees half of two cuts.
* :class:`VideoShotEmbeddingsRepository` — vectors keyed by (shot, kind,
  space) and everything the Visual layer asks of them: nearest shots
  (``match_video_shot_embeddings``), coverage, stale rows, backfill
  candidates. A vector is only comparable inside its space (499).

Deploy-order window: same contract as ``resource_embeddings_repository`` — a
missing table / function raises the typed ``EmbeddingStoreMissing`` (reason
``store_missing``), never an empty result.

The one raw ``text()`` is the pgvector RPC call (the documented structural
exception); every bind is ``CAST(:x AS type)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Literal, Sequence

from sqlalchemy import and_, case, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError

from app.db.session import read_scope, write_scope
from app.models import (
    ParsedMedia,
    Resources,
    VideoShotEmbeddings,
    VideoShotIndexes,
    VideoShots,
)
from app.repositories.resource_embeddings_repository import (
    EmbeddingStoreMissing,
    is_store_missing,
)

#: ``video_shot_embeddings.kind`` written by this PR. ``clip`` waits for PR 4.
FRAME_KIND = "frame"

#: Why a video is a backfill candidate: ``missing`` — never cut, or cut but
#: no vector of this kind in this space; ``stale_algo`` — cut by an older
#: ``algo_version`` (re-cut + re-embed).
ShotPendingReason = Literal["missing", "stale_algo"]


@dataclass(frozen=True)
class ShotRow:
    """One shot as the cutter produced it (no id yet)."""

    shot_index: int
    start_ms: int
    end_ms: int
    rep_frame_ms: int
    cut_score: float | None = None


@dataclass(frozen=True)
class ShotBackfillRow:
    resource_id: int
    media_id: int
    title: str
    #: ``parsed_media.duration`` as stored (free text: seconds or H:M:S);
    #: the endpoint parses it for the shot estimate.
    duration: str | None = None
    reason: ShotPendingReason = "missing"


@dataclass(frozen=True)
class ShotSweepRow:
    """A video the backfill sweeper would index — any user's, so it carries
    the owner the task row is created for."""

    resource_id: int
    user_id: str
    title: str
    reason: ShotPendingReason = "missing"


def _store_missing(what: str) -> EmbeddingStoreMissing:
    return EmbeddingStoreMissing(
        f"{what} does not exist in this database (migration 507 not applied)"
    )


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(map(str, embedding)) + "]"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _shot_out(row: Any) -> dict:
    return {
        "id": int(row.id),
        "resource_id": int(row.resource_id),
        "shot_index": int(row.shot_index),
        "start_ms": int(row.start_ms),
        "end_ms": int(row.end_ms),
        "rep_frame_ms": int(row.rep_frame_ms),
        "cut_score": float(row.cut_score) if row.cut_score is not None else None,
    }


def _search_rows_out(rows: Iterator[Any]) -> list[dict]:
    """bigint → int, timestamptz → ISO str; ``shot`` fields grouped so the
    search layer can lift them into ``SearchResultItem.shot`` unchanged."""
    output = []
    for row in rows:
        d = dict(row)
        for key in ("resource_id", "media_id", "view_count", "shot_id"):
            if d.get(key) is not None:
                d[key] = int(d[key])
        d["created_at"] = _iso(d.get("created_at"))
        output.append(d)
    return output


def _video_resources_all():
    """Every user's videos the Visual layer could hold: non-trashed web
    downloads with a parsed_media row that are videos (``file_type='video'``
    or a ``video/*`` mime) — the same test ``cover_frames._is_video`` applies
    before touching ffmpeg. Orphans (no creator) are excluded: nobody could
    own the task row."""
    return (
        select(Resources.id)
        .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
        .where(Resources.creator_id.isnot(None))
        .where(Resources.source_type == "web")
        .where(Resources.is_trashed.is_(False))
        .where(Resources.media_id.isnot(None))
        .where(
            or_(
                Resources.file_type == "video",
                func.lower(func.coalesce(Resources.mime_type, "")).like("video/%"),
            )
        )
    )


def _video_resources_of(user_id: str):
    """The population the Visual layer counts against: the user's
    non-trashed web downloads that have a parsed_media row AND are videos
    (``file_type='video'`` or a ``video/*`` mime) — the same test
    ``cover_frames._is_video`` applies before touching ffmpeg."""
    return (
        select(Resources.id)
        .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
        .where(Resources.creator_id == user_id)
        .where(Resources.source_type == "web")
        .where(Resources.is_trashed.is_(False))
        .where(Resources.media_id.isnot(None))
        .where(
            or_(
                Resources.file_type == "video",
                func.lower(func.coalesce(Resources.mime_type, "")).like("video/%"),
            )
        )
    )


class VideoShotsRepository:
    """The cut list of a video and the fact that it was cut."""

    async def replace(
        self,
        *,
        resource_id: int,
        shots: Sequence[ShotRow],
        algo_version: str,
        duration_ms: int | None,
    ) -> list[int]:
        """Replace the video's shots wholesale and record the cut. Returns the
        new shot ids in ``shots`` order. One transaction: a reader sees the
        old list or the new one, never a mix; old vectors go by cascade."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(VideoShots).where(VideoShots.resource_id == resource_id)
                )
                ids: list[int] = []
                if shots:
                    stmt = (
                        pg_insert(VideoShots)
                        .values(
                            [
                                {
                                    "resource_id": resource_id,
                                    "shot_index": s.shot_index,
                                    "start_ms": s.start_ms,
                                    "end_ms": s.end_ms,
                                    "rep_frame_ms": s.rep_frame_ms,
                                    "cut_score": s.cut_score,
                                }
                                for s in shots
                            ]
                        )
                        .returning(VideoShots.id, VideoShots.shot_index)
                    )
                    rows = (await session.execute(stmt)).all()
                    by_index = {int(r.shot_index): int(r.id) for r in rows}
                    ids = [by_index[s.shot_index] for s in shots]
                idx = pg_insert(VideoShotIndexes).values(
                    resource_id=resource_id,
                    algo_version=algo_version,
                    shot_count=len(shots),
                    duration_ms=duration_ms,
                )
                idx = idx.on_conflict_do_update(
                    index_elements=["resource_id"],
                    set_={
                        "algo_version": idx.excluded.algo_version,
                        "shot_count": idx.excluded.shot_count,
                        "duration_ms": idx.excluded.duration_ms,
                        "indexed_at": func.now(),
                    },
                )
                await session.execute(idx)
                return ids
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shots") from exc
            raise

    async def get_index(self, resource_id: int) -> dict | None:
        stmt = select(VideoShotIndexes).where(
            VideoShotIndexes.resource_id == resource_id
        )
        try:
            async with read_scope() as session:
                row = (await session.execute(stmt)).scalars().first()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_indexes") from exc
            raise
        if row is None:
            return None
        return {
            "resource_id": int(row.resource_id),
            "algo_version": row.algo_version,
            "shot_count": int(row.shot_count),
            "duration_ms": (
                int(row.duration_ms) if row.duration_ms is not None else None
            ),
            "indexed_at": _iso(row.indexed_at),
        }

    async def list_shots(self, resource_id: int) -> list[dict]:
        stmt = (
            select(VideoShots)
            .where(VideoShots.resource_id == resource_id)
            .order_by(VideoShots.shot_index)
        )
        try:
            async with read_scope() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shots") from exc
            raise
        return [_shot_out(r) for r in rows]


class VideoShotEmbeddingsRepository:
    """Vectors of shots, and the Visual layer's questions about them."""

    async def upsert_many(
        self,
        *,
        space_id: int,
        kind: str,
        rows: Sequence[tuple[int, list[float], str]],
    ) -> None:
        """``rows`` = (shot_id, embedding, source_hash). Insert or replace
        each (shot, kind, space) vector in one statement."""
        if not rows:
            return
        stmt = pg_insert(VideoShotEmbeddings).values(
            [
                {
                    "shot_id": shot_id,
                    "kind": kind,
                    "space_id": space_id,
                    "embedding": embedding,
                    "source_hash": source_hash,
                }
                for shot_id, embedding, source_hash in rows
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["shot_id", "kind", "space_id"],
            set_={
                "embedding": stmt.excluded.embedding,
                "source_hash": stmt.excluded.source_hash,
            },
        )
        try:
            async with write_scope() as session:
                await session.execute(stmt)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_embeddings") from exc
            raise

    async def search(
        self,
        *,
        embedding: list[float],
        space_id: int,
        kind: str,
        user_id: str,
        limit: int,
        threshold: float,
    ) -> list[dict]:
        """Best shot per video of ``user_id`` in one (space, kind), via the
        ``match_video_shot_embeddings`` RPC.

        ``user_id`` is REQUIRED: the RPC reads NULL as "every user".

        Row keys: resource_id, media_id, platform_id, title, description,
        cover_urls, author, view_count, created_at (ISO str), shot_id,
        shot_index, start_ms, end_ms, similarity.
        """
        if not user_id:
            raise ValueError("search requires a user_id; refusing an unscoped scan")
        params = {
            "q": _vector_literal(embedding),
            "s": space_id,
            "k": kind,
            "t": threshold,
            "c": limit,
            "u": user_id,
        }
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT * FROM match_video_shot_embeddings("
                        "CAST(:q AS halfvec), CAST(:s AS bigint), CAST(:k AS text), "
                        "CAST(:t AS double precision), CAST(:c AS int), "
                        "CAST(:u AS uuid))"
                    ),
                    params,
                )
                rows = result.mappings().all()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("match_video_shot_embeddings") from exc
            raise
        return _search_rows_out(rows)

    @staticmethod
    def _has_vector(space_id: int, kind: str):
        """EXISTS: the resource has at least one shot with a vector of
        ``kind`` in ``space_id``."""
        return exists().where(
            VideoShots.resource_id == Resources.id,
            VideoShotEmbeddings.shot_id == VideoShots.id,
            VideoShotEmbeddings.space_id == space_id,
            VideoShotEmbeddings.kind == kind,
        )

    async def count_in_space(self, space_id: int) -> int:
        """Shot vectors of every user and kind in one space — what deleting
        the space would cascade away."""
        stmt = (
            select(func.count())
            .select_from(VideoShotEmbeddings)
            .where(VideoShotEmbeddings.space_id == space_id)
        )
        try:
            async with read_scope() as session:
                return int((await session.execute(stmt)).scalar_one() or 0)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_embeddings") from exc
            raise

    async def coverage(
        self, *, user_id: str, space_id: int, kind: str
    ) -> tuple[int, int]:
        """``(covered, total)``: videos of the user with at least one shot
        vector of ``kind`` in ``space_id``, out of all their videos."""
        if not user_id:
            return 0, 0
        base = _video_resources_of(user_id)
        total_stmt = select(func.count()).select_from(base.subquery())
        covered_stmt = select(func.count()).select_from(
            base.where(self._has_vector(space_id, kind)).subquery()
        )
        try:
            async with read_scope() as session:
                total = int((await session.execute(total_stmt)).scalar_one() or 0)
                covered = int((await session.execute(covered_stmt)).scalar_one() or 0)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_embeddings") from exc
            raise
        return covered, total

    async def stale_count(
        self, *, user_id: str, space_id: int, kind: str, algo_version: str
    ) -> int:
        """Videos cut by another ``algo_version`` that still have a vector in
        this space — covered today, re-cut by the next backfill."""
        if not user_id:
            return 0
        base = _video_resources_of(user_id)
        stmt = select(func.count()).select_from(
            base.join(VideoShotIndexes, VideoShotIndexes.resource_id == Resources.id)
            .where(VideoShotIndexes.algo_version != algo_version)
            .where(self._has_vector(space_id, kind))
            .subquery()
        )
        try:
            async with read_scope() as session:
                return int((await session.execute(stmt)).scalar_one() or 0)
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_indexes") from exc
            raise

    async def pending_for_user(
        self,
        *,
        user_id: str,
        space_id: int,
        kind: str,
        algo_version: str,
        limit: int,
    ) -> tuple[list[ShotBackfillRow], int]:
        """Videos the next backfill would index: no vector of ``kind`` in
        ``space_id`` (``missing``, first) or cut by an older ``algo_version``
        (``stale_algo``). Returns ``(first limit rows, total pending)``."""
        if not user_id:
            return [], 0
        has_vector = self._has_vector(space_id, kind)
        stale = and_(
            exists().where(
                VideoShotIndexes.resource_id == Resources.id,
                VideoShotIndexes.algo_version != algo_version,
            ),
            has_vector,
        )
        reason = case((stale, "stale_algo"), else_="missing")
        base = (
            _video_resources_of(user_id)
            .add_columns(
                Resources.media_id,
                ParsedMedia.title,
                ParsedMedia.duration,
                reason.label("reason"),
            )
            .where(or_(~has_vector, stale))
        )
        total_stmt = select(func.count()).select_from(base.subquery())
        page_stmt = base.order_by(
            # missing first ('missing' < 'stale_algo'), newest download first
            reason.asc(),
            Resources.id.desc(),
        ).limit(limit)
        try:
            async with read_scope() as session:
                total = int((await session.execute(total_stmt)).scalar_one() or 0)
                rows = (await session.execute(page_stmt)).all()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_embeddings") from exc
            raise
        out = [
            ShotBackfillRow(
                resource_id=int(r[0]),
                media_id=int(r[1]),
                title=r[2] or "",
                duration=r[3],
                reason=r[4],
            )
            for r in rows
        ]
        return out, total

    async def pending_all(
        self, *, space_id: int, kind: str, algo_version: str, limit: int
    ) -> tuple[list[ShotSweepRow], int]:
        """:meth:`pending_for_user` across every user, for the backfill
        sweeper: missing first, then OLDEST resource first (a stable order
        the sweeper walks over successive ticks). ``limit=0`` counts only.
        Returns ``(first limit rows, total pending)``."""
        has_vector = self._has_vector(space_id, kind)
        stale = and_(
            exists().where(
                VideoShotIndexes.resource_id == Resources.id,
                VideoShotIndexes.algo_version != algo_version,
            ),
            has_vector,
        )
        reason = case((stale, "stale_algo"), else_="missing")
        base = (
            _video_resources_all()
            .add_columns(
                Resources.creator_id,
                ParsedMedia.title,
                reason.label("reason"),
            )
            .where(or_(~has_vector, stale))
        )
        total_stmt = select(func.count()).select_from(base.subquery())
        page_stmt = base.order_by(reason.asc(), Resources.id.asc()).limit(limit)
        try:
            async with read_scope() as session:
                total = int((await session.execute(total_stmt)).scalar_one() or 0)
                rows = (await session.execute(page_stmt)).all() if limit > 0 else []
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise _store_missing("video_shot_embeddings") from exc
            raise
        out = [
            ShotSweepRow(
                resource_id=int(r[0]),
                user_id=str(r[1]),
                title=r[2] or "",
                reason=r[3],
            )
            for r in rows
        ]
        return out, total


_shots_repo: VideoShotsRepository | None = None
_shot_embeddings_repo: VideoShotEmbeddingsRepository | None = None


def get_video_shots_repository() -> VideoShotsRepository:
    global _shots_repo
    if _shots_repo is None:
        _shots_repo = VideoShotsRepository()
    return _shots_repo


def get_video_shot_embeddings_repository() -> VideoShotEmbeddingsRepository:
    global _shot_embeddings_repo
    if _shot_embeddings_repo is None:
        _shot_embeddings_repo = VideoShotEmbeddingsRepository()
    return _shot_embeddings_repo
