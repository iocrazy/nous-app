"""Notes service — ownership checks, tag parsing, attachment folding.

Ownership contract: any access to a note the caller does not own raises
NoteNotFound (mapped to HTTP 404 by the router) — existence is never leaked.
note_date is computed here in Asia/Shanghai, never by DB CURRENT_DATE.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.repositories.inspiration_repository import get_inspiration_notes_repository
from app.repositories.note_tags_repository import get_note_tags_repository
from app.repositories.tags_repository import get_tags_repository
from app.services.inspiration.note_tags import parse_tags

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class NoteNotFound(Exception):
    pass


class NotePersistFailed(Exception):
    """Raised when a repo write reports failure — mapped to HTTP 502 by the router."""

    pass


def _today_shanghai() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%d")


class NotesService:
    def __init__(self) -> None:
        self._notes = get_inspiration_notes_repository()
        self._attachments = get_inspiration_attachments_repository()

    async def _owned(self, user_id: str, note_id: Any) -> Dict[str, Any]:
        row = await self._notes.get_by_id(note_id)
        if not row or str(row.get("user_id")) != str(user_id):
            raise NoteNotFound()
        return row

    async def assert_owned(self, user_id: str, note_id: Any) -> None:
        """Read-only ownership probe (router upload uses this; raises NoteNotFound)."""
        await self._owned(user_id, note_id)

    async def create_note(
        self,
        user_id: str,
        content_md: str,
        ref_hotspot: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        row = await self._notes.create(
            user_id=user_id,
            content_md=content_md,
            tags=parse_tags(content_md),
            note_date=_today_shanghai(),
            ref_hotspot=ref_hotspot,
        )
        if row is not None:
            row.setdefault("attachments", [])
            await self._sync_pool_tags(user_id, row)
        return row

    async def list_notes(
        self,
        user_id: str,
        *,
        date: Optional[str],
        tag: Optional[str],
        q: Optional[str],
        limit: int,
        before_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        rows = await self._notes.list(
            user_id, date=date, tag=tag, q=q, limit=limit, before_id=before_id
        )
        atts = (
            await self._attachments.list_for_notes([r["id"] for r in rows])
            if rows
            else []
        )
        by_note: Dict[Any, List[Dict[str, Any]]] = {}
        for att in atts:
            by_note.setdefault(att["note_id"], []).append(att)
        for row in rows:
            row["attachments"] = by_note.get(row["id"], [])
        return rows

    async def update_note(
        self,
        user_id: str,
        note_id: Any,
        *,
        content_md: Optional[str] = None,
        pinned: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        await self._owned(user_id, note_id)
        tags = parse_tags(content_md) if content_md is not None else None
        row = await self._notes.update(
            note_id, content_md=content_md, tags=tags, pinned=pinned
        )
        if row is not None:
            atts = await self._attachments.list_for_notes([row["id"]])
            row["attachments"] = atts
            if content_md is not None:
                await self._sync_pool_tags(user_id, row)
        return row

    async def _sync_pool_tags(self, user_id: str, row: Dict[str, Any]) -> None:
        """Keep the global tag pool + note_tags junction in step with the note
        body (spec §4.1). content_md is the source of truth; note_tags is derived
        data — any drift self-heals on the next save.

        The note row write and this tag sync are two separate transactions (the
        repos follow the per-call session idiom, no cross-repo session passing).
        A failure here propagates to the caller by design: the note row is
        already persisted and reconverges on the next save.
        """
        names = list(row.get("tags") or [])
        tag_ids = await get_tags_repository().resolve_note_tags(user_id, names)
        await get_note_tags_repository().sync_for_note(int(row["id"]), tag_ids)

    async def delete_note(self, user_id: str, note_id: Any) -> None:
        await self._owned(user_id, note_id)
        if not await self._notes.soft_delete(note_id):
            raise NotePersistFailed()

    async def activity(
        self, user_id: str, date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        return await self._notes.activity(user_id, date_from, date_to)

    async def tag_counts(self, user_id: str) -> List[Dict[str, Any]]:
        return await self._notes.tag_counts(user_id)


_svc: Optional[NotesService] = None


def get_notes_service() -> NotesService:
    global _svc
    if _svc is None:
        _svc = NotesService()
    return _svc
