"""Playback positions (mig 473) — per-user, cross-device resume points.

``PlaybackPositions`` carries ``UserScoped``, so the do_orm_execute choke point
injects ``user_id == scope.user_id`` on every SELECT here and stamps the owner
on insert. Callers MUST open a user scope; there is no system-scope path, on
purpose — a cross-user read of viewing history has no legitimate caller.

Why load-then-modify instead of ``INSERT ... ON CONFLICT``: the choke point
FORBIDS Core/bulk DML on scoped models outright (``_forbid_scoped_bulk_dml``),
because a Core insert never reaches the owner-stamping event and an arbitrary
UPDATE tree cannot be safely tenant-filtered. The sanctioned path is the ORM
unit of work, whose flush IS governed. The upsert's atomicity comes back as an
IntegrityError retry below — see ``upsert``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import read_scope, write_scope
from app.models import PlaybackPositions

# Cap on a single batch read. The client asks for the keys on one screen; an
# unbounded IN-list is a way for one request to scan the whole table.
MAX_KEYS_PER_READ = 100


def _row_to_dict(row: PlaybackPositions) -> Dict[str, Any]:
    return {
        "media_key": row.media_key,
        "position_seconds": float(row.position_seconds),
        "duration_seconds": float(row.duration_seconds),
        # ISO-8601 with offset. The client compares this string against the one
        # it last stored, so the exact serialization matters more than it looks:
        # it is an identity check ("is this still my own write?"), not a
        # chronological one.
        "updated_at": row.updated_at.isoformat(),
    }


class PlaybackPositionsRepository:
    """CRUD for resume points. One row per (user, media_key)."""

    async def upsert(
        self,
        *,
        user_id: str,
        media_key: str,
        position_seconds: float,
        duration_seconds: float,
    ) -> Optional[Dict[str, Any]]:
        """Write the position, returning the row including SERVER ``updated_at``.

        ``updated_at`` is never client-supplied: the table's BEFORE UPDATE
        trigger sets it, and the INSERT arm takes the column default. It is
        what the client uses to tell its own write coming back from another
        device's, and a client timestamp would let a device with a skewed clock
        pin itself as permanently newest.

        Two devices can reach the INSERT arm at the same moment; the unique
        index turns the loser into an IntegrityError, which is re-run as the
        UPDATE it should have been. One retry is enough — after it, the row
        provably exists.
        """
        try:
            return await self._write_once(
                user_id=user_id,
                media_key=media_key,
                position_seconds=position_seconds,
                duration_seconds=duration_seconds,
            )
        except IntegrityError:
            logger.info(
                "[PlaybackPositions] concurrent insert lost the race, "
                f"retrying as update user={user_id} key={media_key[:64]}"
            )
            return await self._write_once(
                user_id=user_id,
                media_key=media_key,
                position_seconds=position_seconds,
                duration_seconds=duration_seconds,
            )
        except Exception as e:
            logger.error(
                f"[PlaybackPositions] upsert failed user={user_id} "
                f"key={media_key[:64]}: {e}"
            )
            raise

    async def _write_once(
        self,
        *,
        user_id: str,
        media_key: str,
        position_seconds: float,
        duration_seconds: float,
    ) -> Optional[Dict[str, Any]]:
        async with write_scope() as session:
            existing = (
                (
                    await session.execute(
                        select(PlaybackPositions).where(
                            PlaybackPositions.media_key == media_key
                        )
                    )
                )
                .scalars()
                .first()
            )

            if existing is None:
                row = PlaybackPositions(
                    user_id=user_id,
                    media_key=media_key,
                    position_seconds=position_seconds,
                    duration_seconds=duration_seconds,
                )
                session.add(row)
            else:
                row = existing
                row.position_seconds = position_seconds
                row.duration_seconds = duration_seconds

            await session.flush()

            # Read the DB-written values back. `updated_at` comes from the
            # column default on insert and from the BEFORE UPDATE trigger on
            # update, so the in-memory value is stale either way — and that
            # value is the one the client reconciles against.
            #
            # A plain `select()` with `populate_existing`, NOT
            # `session.refresh()`: refresh emits an identity load the scope
            # choke point cannot prove is tenant-filtered, so it is rejected
            # outright. This shape is the same injectable one `get_many` uses.
            refreshed = (
                (
                    await session.execute(
                        select(PlaybackPositions)
                        .where(PlaybackPositions.media_key == media_key)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .first()
            )
            return _row_to_dict(refreshed) if refreshed else None

    async def get_many(
        self, *, user_id: str, media_keys: List[str]
    ) -> List[Dict[str, Any]]:
        """Positions for the given keys. Missing keys are simply absent."""
        keys = [k for k in dict.fromkeys(media_keys) if k][:MAX_KEYS_PER_READ]
        if not keys:
            return []
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(PlaybackPositions).where(
                        PlaybackPositions.media_key.in_(keys)
                    )
                )
                return [_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"[PlaybackPositions] get_many failed user={user_id}: {e}")
            raise

    async def delete(self, *, user_id: str, media_key: str) -> bool:
        """Forget one position (the client calls this when a video ends).

        Load-then-delete for the same reason as the upsert: a Core ``delete()``
        under a user scope is refused by the choke point, and here that refusal
        is doing real work — an ungoverned DELETE is how one user erases
        another's history.
        """
        try:
            async with write_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(PlaybackPositions).where(
                                PlaybackPositions.media_key == media_key
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if row is None:
                    return False
                await session.delete(row)
                return True
        except Exception as e:
            logger.error(
                f"[PlaybackPositions] delete failed user={user_id} "
                f"key={media_key[:64]}: {e}"
            )
            raise


_repository: Optional[PlaybackPositionsRepository] = None


def get_playback_positions_repository() -> PlaybackPositionsRepository:
    global _repository
    if _repository is None:
        _repository = PlaybackPositionsRepository()
    return _repository
