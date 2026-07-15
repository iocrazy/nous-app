"""Repository for inspiration_api_tokens (migration 349).

ORM-backed (read_scope/write_scope). Ownership checks live in the service
layer. Only the SHA-256 hex digest (`token_hash`) is stored — the plaintext
PAT is never persisted (spec §3.3, secret-at-rest). ``InspirationApiTokens``
carries no scope mixin, so the choke point stays inert.
"""

from __future__ import annotations

import datetime
import uuid
from datetime import timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import insert, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import InspirationApiTokens


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """REST-shaped dict matching the old PostgREST rendering: uuid → str,
    datetime → ISO str (or None). BIGINT id stays native int."""
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, uuid.UUID):
            out[key] = str(val)
        elif isinstance(val, datetime.datetime):
            out[key] = val.isoformat()
        else:
            out[key] = val
    return out


def _row_dict(obj: InspirationApiTokens) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


class InspirationTokenRepository:
    TABLE = "inspiration_api_tokens"

    async def create(
        self, user_id: str, name: str, token_hash: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(InspirationApiTokens)
                    .values(user_id=user_id, name=name, token_hash=token_hash)
                    .returning(*InspirationApiTokens.__table__.columns)
                )
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except Exception as e:
            logger.error(f"inspiration token create failed (user={user_id}): {e}")
            return None

    async def list_by_user(
        self, user_id: str, *, include_revoked: bool = False
    ) -> List[Dict[str, Any]]:
        try:
            stmt = select(InspirationApiTokens).where(
                InspirationApiTokens.user_id == user_id
            )
            if not include_revoked:
                stmt = stmt.where(InspirationApiTokens.revoked_at.is_(None))
            stmt = stmt.order_by(InspirationApiTokens.id.desc())
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_serialize(_row_dict(o)) for o in result.scalars().all()]
        except Exception as e:
            logger.error(f"inspiration token list failed (user={user_id}): {e}")
            return []

    async def find_active_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """Return the non-revoked token row for this hash, else None."""
        try:
            async with read_scope() as session:
                obj = (
                    (
                        await session.execute(
                            select(InspirationApiTokens)
                            .where(
                                InspirationApiTokens.token_hash == token_hash,
                                InspirationApiTokens.revoked_at.is_(None),
                            )
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            return _serialize(_row_dict(obj)) if obj else None
        except Exception as e:
            logger.error(f"inspiration token lookup failed: {e}")
            return None

    async def touch_last_used(self, token_id: Any) -> None:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(InspirationApiTokens)
                    .where(InspirationApiTokens.id == _bigint(token_id))
                    .values(last_used_at=datetime.datetime.now(timezone.utc))
                )
        except Exception as e:
            logger.warning(f"inspiration token touch_last_used({token_id}) failed: {e}")

    async def revoke(self, token_id: Any, user_id: str) -> bool:
        """Revoke a token owned by user_id. Returns False if not found/owned."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(InspirationApiTokens)
                    .where(
                        InspirationApiTokens.id == _bigint(token_id),
                        InspirationApiTokens.user_id == user_id,
                        InspirationApiTokens.revoked_at.is_(None),
                    )
                    .values(revoked_at=datetime.datetime.now(timezone.utc))
                    .returning(InspirationApiTokens.id)
                )
                return result.first() is not None
        except Exception as e:
            logger.error(f"inspiration token revoke({token_id}) failed: {e}")
            return False


_repo: Optional[InspirationTokenRepository] = None


def get_inspiration_token_repository() -> InspirationTokenRepository:
    global _repo
    if _repo is None:
        _repo = InspirationTokenRepository()
    return _repo
