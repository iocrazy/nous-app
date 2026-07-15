"""Repository for inspiration_attachments (migration 349). Pure data access.

ORM-backed (read_scope/write_scope). ``storage_backend`` / ``bucket`` have DB
defaults and are not set here — this repo's callers only ever write through the
Supabase ObjectStore (see attachment_service.py). ``InspirationAttachments``
carries no scope mixin: ownership is scoped in the service layer, so the choke
point stays inert.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select

from app.db.session import read_scope, write_scope
from app.models import InspirationAttachments


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """REST-shaped dict matching the old PostgREST rendering: uuid → str,
    datetime → ISO str. BIGINT ids stay native int (as PostgREST's JSON
    number deserialized)."""
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, uuid.UUID):
            out[key] = str(val)
        elif isinstance(val, datetime.datetime):
            out[key] = val.isoformat()
        else:
            out[key] = val
    return out


def _row_dict(obj: InspirationAttachments) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


class InspirationAttachmentsRepository:
    TABLE = "inspiration_attachments"

    async def create(
        self,
        note_id: Any,
        user_id: str,
        bucket: str,
        path: str,
        mime: str,
        size_bytes: int,
        original_name: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(InspirationAttachments)
                    .values(
                        note_id=_bigint(note_id),
                        user_id=user_id,
                        bucket=bucket,
                        path=path,
                        mime=mime,
                        size_bytes=size_bytes,
                        original_name=original_name,
                    )
                    .returning(*InspirationAttachments.__table__.columns)
                )
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except Exception as e:
            logger.error(f"inspiration attachment create failed (note={note_id}): {e}")
            return None

    async def get_by_id(self, attachment_id: Any) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                obj = (
                    (
                        await session.execute(
                            select(InspirationAttachments)
                            .where(InspirationAttachments.id == _bigint(attachment_id))
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            return _serialize(_row_dict(obj)) if obj else None
        except Exception as e:
            logger.error(f"inspiration attachment get({attachment_id}) failed: {e}")
            return None

    async def list_for_notes(self, note_ids: List[Any]) -> List[Dict[str, Any]]:
        if not note_ids:
            return []
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(InspirationAttachments)
                    .where(
                        InspirationAttachments.note_id.in_(
                            [_bigint(n) for n in note_ids]
                        )
                    )
                    .order_by(InspirationAttachments.id)
                )
                return [_serialize(_row_dict(o)) for o in result.scalars().all()]
        except Exception as e:
            logger.error(f"inspiration attachments list_for_notes failed: {e}")
            return []

    async def delete(self, attachment_id: Any) -> bool:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_delete(InspirationAttachments)
                    .where(InspirationAttachments.id == _bigint(attachment_id))
                    .returning(InspirationAttachments.id)
                )
                return result.first() is not None
        except Exception as e:
            logger.error(f"inspiration attachment delete({attachment_id}) failed: {e}")
            return False


_repo: Optional[InspirationAttachmentsRepository] = None


def get_inspiration_attachments_repository() -> InspirationAttachmentsRepository:
    global _repo
    if _repo is None:
        _repo = InspirationAttachmentsRepository()
    return _repo
