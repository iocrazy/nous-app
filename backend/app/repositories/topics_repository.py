"""Data access for the ideation topic pool (mig 382).

ORM-backed (read_scope / write_scope). Snowflake BIGINT ids ride as strings at
the API boundary (bigIntSafeFetch discipline); ``_topic_row`` renders ids/UUIDs
as str and timestamps as ISO strings. No scope mixin — ownership is a
service-role model gated by the explicit ``team_id`` predicate in every method
plus the router's ``resolve_effective_role`` guard.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.db.session import read_scope, write_scope
from app.models import Topics


def _as_uuid(val: Any) -> Optional[uuid.UUID]:
    """Coerce a str/UUID/None to uuid.UUID (or None)."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _as_int(val: Any) -> Optional[int]:
    """Coerce a snowflake str/int/None to int (or None). asyncpg's int8 codec is
    strict, so a source id arriving as a string must be widened here."""
    if val is None:
        return None
    return int(str(val))


def _s(val: Any) -> Any:
    """Stringify ids/UUIDs; ISO-format datetimes; pass the rest through."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    return val


def _topic_row(obj: Topics) -> Dict[str, Any]:
    return {
        "id": str(obj.id),
        "team_id": str(obj.team_id),
        "title": obj.title,
        "cover_url": obj.cover_url,
        "excerpt": obj.excerpt,
        "status": obj.status,
        "note_id": str(obj.note_id) if obj.note_id is not None else None,
        "resource_id": (str(obj.resource_id) if obj.resource_id is not None else None),
        "media_id": str(obj.media_id) if obj.media_id is not None else None,
        "inspiration_topic_id": (
            str(obj.inspiration_topic_id)
            if obj.inspiration_topic_id is not None
            else None
        ),
        "created_by": _s(obj.created_by),
        "created_at": _s(obj.created_at),
        "updated_at": _s(obj.updated_at),
    }


class TopicsRepository:
    async def list_topics(
        self, team_id: str, *, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Topics for a team, newest first, optionally filtered by status."""
        async with read_scope() as session:
            query = select(Topics).where(Topics.team_id == int(str(team_id)))
            if status is not None:
                query = query.where(Topics.status == status)
            query = query.order_by(Topics.created_at.desc())
            rows = (await session.execute(query)).scalars().all()
            return [_topic_row(t) for t in rows]

    async def get_topic(self, topic_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        """One topic scoped to its team, or None."""
        async with read_scope() as session:
            obj = await self._fetch(session, topic_id, team_id)
            return _topic_row(obj) if obj is not None else None

    async def get_topic_team_id(self, topic_id: str) -> Optional[str]:
        """The owning ``team_id`` (str) for a topic, or None if it doesn't exist.

        Lets the router resolve authz from the id alone while still 404-ing
        before it leaks existence to a non-member."""
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(Topics.team_id)
                    .where(Topics.id == int(str(topic_id)))
                    .limit(1)
                )
            ).first()
        return str(row[0]) if row is not None else None

    async def create_topic(
        self,
        team_id: str,
        *,
        title: str,
        created_by: Optional[str],
        cover_url: Optional[str] = None,
        excerpt: Optional[str] = None,
        note_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        media_id: Optional[str] = None,
        inspiration_topic_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with write_scope() as session:
            obj = Topics(
                team_id=int(str(team_id)),
                title=title,
                cover_url=cover_url,
                excerpt=excerpt,
                note_id=_as_int(note_id),
                resource_id=_as_int(resource_id),
                media_id=_as_int(media_id),
                inspiration_topic_id=_as_int(inspiration_topic_id),
                created_by=_as_uuid(created_by),
            )
            session.add(obj)
            await session.flush()
            await session.refresh(obj)
            return _topic_row(obj)

    async def update_topic(
        self,
        topic_id: str,
        team_id: str,
        *,
        title: Optional[str] = None,
        cover_url: Optional[str] = None,
        excerpt: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Patch a topic; None when it isn't in this team. Only the passed
        fields change (the router forwards ``exclude_unset`` values)."""
        async with write_scope() as session:
            obj = await self._fetch(session, topic_id, team_id)
            if obj is None:
                return None
            if title is not None:
                obj.title = title
            if cover_url is not None:
                obj.cover_url = cover_url
            if excerpt is not None:
                obj.excerpt = excerpt
            if status is not None:
                obj.status = status
            obj.updated_at = datetime.datetime.now(datetime.timezone.utc)
            await session.flush()
            await session.refresh(obj)
            return _topic_row(obj)

    async def delete_topic(self, topic_id: str, team_id: str) -> bool:
        async with write_scope() as session:
            obj = await self._fetch(session, topic_id, team_id)
            if obj is None:
                return False
            await session.delete(obj)
            return True

    async def mark_produced(self, topic_id: str, team_id: str) -> bool:
        """Flip a topic to ``produced`` (best-effort project-creation linkage).

        Scoped to the team so a create in one team can't flip another team's
        topic. Returns whether a row was updated."""
        async with write_scope() as session:
            obj = await self._fetch(session, topic_id, team_id)
            if obj is None:
                return False
            obj.status = "produced"
            obj.updated_at = datetime.datetime.now(datetime.timezone.utc)
            return True

    async def _fetch(
        self, session: Any, topic_id: str, team_id: str
    ) -> Optional[Topics]:
        return (
            (
                await session.execute(
                    select(Topics)
                    .where(Topics.id == int(str(topic_id)))
                    .where(Topics.team_id == int(str(team_id)))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )


_repo: Optional[TopicsRepository] = None


def get_topics_repository() -> TopicsRepository:
    global _repo
    if _repo is None:
        _repo = TopicsRepository()
    return _repo
