"""Repository for inspiration_notes (migration 349).

ORM-backed (read_scope/write_scope). Ownership checks live in the service
layer — every method here trusts its caller. All bigint ids are coerced with
``_bigint`` before binding. ``InspirationNotes`` carries no scope mixin, so the
choke point stays inert.

The two aggregate helpers (``activity`` / ``tag_counts``) call the SQL
table-valued functions (mig 349) via ``text()`` on the read session — the same
functions the old ``client.rpc`` path invoked.
"""

from __future__ import annotations

import datetime
import uuid
from datetime import timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import InspirationNotes


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


def _date(value: Any) -> datetime.date:
    """Coerce an ISO 'YYYY-MM-DD' string to a date for the DATE column bind
    (asyncpg's date codec rejects a bare string). Passes a date through."""
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    return datetime.date.fromisoformat(str(value))


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """REST-shaped dict matching the old PostgREST rendering: uuid → str,
    datetime/date → ISO str. BIGINT id stays native int; ``tags`` (text[])
    stays a native list; ``ref_hotspot`` (jsonb) stays a native dict/None."""
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, uuid.UUID):
            out[key] = str(val)
        elif isinstance(val, (datetime.datetime, datetime.date)):
            out[key] = val.isoformat()
        else:
            out[key] = val
    return out


def _row_dict(obj: InspirationNotes) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


class InspirationNotesRepository:
    TABLE = "inspiration_notes"

    async def create(
        self,
        user_id: str,
        content_md: str,
        tags: List[str],
        note_date: str,
        ref_hotspot: Optional[Dict[str, Any]] = None,
        rating: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with write_scope() as session:
                obj = InspirationNotes(
                    user_id=user_id,
                    content_md=content_md,
                    tags=tags,
                    note_date=_date(note_date),
                )
                if ref_hotspot is not None:
                    obj.ref_hotspot = ref_hotspot
                # `is not None`, not truthiness: rating=0 is a real value
                # (explicitly unrated), only an omitted rating defers to the
                # server_default.
                if rating is not None:
                    obj.rating = rating
                session.add(obj)
                await session.flush()
                await session.refresh(obj)  # load server defaults
                return _serialize(_row_dict(obj))
        except Exception as e:
            logger.error(f"inspiration create failed (user={user_id}): {e}")
            return None

    async def get_by_id(self, note_id: Any) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                obj = (
                    (
                        await session.execute(
                            select(InspirationNotes)
                            .where(
                                InspirationNotes.id == _bigint(note_id),
                                InspirationNotes.deleted_at.is_(None),
                            )
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            return _serialize(_row_dict(obj)) if obj else None
        except Exception as e:
            logger.error(f"inspiration get_by_id({note_id}) failed: {e}")
            return None

    async def list(
        self,
        user_id: str,
        *,
        date: Optional[str] = None,
        tag: Optional[str] = None,
        q: Optional[str] = None,
        min_rating: Optional[int] = None,
        limit: int = 50,
        before_id: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        try:
            stmt = select(InspirationNotes).where(
                InspirationNotes.user_id == user_id,
                InspirationNotes.deleted_at.is_(None),
            )
            if date:
                stmt = stmt.where(InspirationNotes.note_date == _date(date))
            if tag:
                # text[] containment: tags @> ARRAY[:tag]
                stmt = stmt.where(InspirationNotes.tags.contains([tag]))
            if q:
                stmt = stmt.where(InspirationNotes.content_md.ilike(f"%{q}%"))
            # `0` means "any rating", not "rating >= 0" — both would return the
            # same rows today, but only the former stays correct if ratings
            # ever go negative or become nullable.
            if min_rating:
                stmt = stmt.where(InspirationNotes.rating >= min_rating)
            if before_id:
                stmt = stmt.where(InspirationNotes.id < _bigint(before_id))
            stmt = stmt.order_by(InspirationNotes.id.desc()).limit(limit)
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_serialize(_row_dict(o)) for o in result.scalars().all()]
        except Exception as e:
            logger.error(f"inspiration list failed (user={user_id}): {e}")
            return []

    async def update(
        self,
        note_id: Any,
        *,
        content_md: Optional[str] = None,
        tags: Optional[List[str]] = None,
        pinned: Optional[bool] = None,
        rating: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            values: Dict[str, Any] = {"updated_at": datetime.datetime.now(timezone.utc)}
            if content_md is not None:
                values["content_md"] = content_md
            if tags is not None:
                values["tags"] = tags
            if pinned is not None:
                values["pinned"] = pinned
            # `is not None` so clearing a rating (5 -> 0) reaches the SET clause.
            if rating is not None:
                values["rating"] = rating
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(InspirationNotes)
                    .where(InspirationNotes.id == _bigint(note_id))
                    .values(**values)
                    .returning(*InspirationNotes.__table__.columns)
                )
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except Exception as e:
            logger.error(f"inspiration update({note_id}) failed: {e}")
            return None

    async def soft_delete(self, note_id: Any) -> bool:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(InspirationNotes)
                    .where(InspirationNotes.id == _bigint(note_id))
                    .values(deleted_at=datetime.datetime.now(timezone.utc))
                    .returning(InspirationNotes.id)
                )
                return result.first() is not None
        except Exception as e:
            logger.error(f"inspiration soft_delete({note_id}) failed: {e}")
            return False

    async def activity(
        self, user_id: str, date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT day, cnt FROM inspiration_activity("
                        "CAST(:p_user_id AS uuid), CAST(:p_from AS date), "
                        "CAST(:p_to AS date))"
                    ),
                    {
                        "p_user_id": user_id,
                        "p_from": _date(date_from),
                        "p_to": _date(date_to),
                    },
                )
                return [_serialize(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"inspiration activity failed (user={user_id}): {e}")
            return []

    async def tag_counts(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT tag, cnt FROM inspiration_tag_counts("
                        "CAST(:p_user_id AS uuid))"
                    ),
                    {"p_user_id": user_id},
                )
                return [_serialize(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"inspiration tag_counts failed (user={user_id}): {e}")
            return []


_repo: Optional[InspirationNotesRepository] = None


def get_inspiration_notes_repository() -> InspirationNotesRepository:
    global _repo
    if _repo is None:
        _repo = InspirationNotesRepository()
    return _repo
