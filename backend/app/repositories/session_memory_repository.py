"""Repository for ai_session_memory (Wave 5b / B2).

One row per ai_sessions row. Body is markdown source-of-truth + parsed
sections_json for query convenience.

ORM 2.0 migration (Phase 2, M batch): ``SessionMemoryRepository`` is the legacy
supabase-py REST implementation; ``SessionMemoryRepositoryOrm`` (in
``session_memory_repository_orm.py``) is the SQLAlchemy 2.0 ORM successor. Call
sites go through ``get_session_memory_repository()`` (bottom of this file),
which picks the ORM subclass when ``USE_ORM_SESSION_MEMORY`` is on AND the
engine is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Union
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.session_memory_repository_orm import (
        SessionMemoryRepositoryOrm,
    )


@dataclass
class SessionMemoryRow:
    """In-memory representation of an ai_session_memory row."""

    session_id: str
    body_md: str = ""
    sections_json: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    last_updated_at: Optional[datetime] = None
    tokens_at_last_update: int = 0
    tool_calls_at_last_update: int = 0
    turns_at_last_update: int = 0


class SessionMemoryRepository:
    TABLE = "ai_session_memory"

    async def _get_client(self):
        return await get_async_supabase_admin()

    @staticmethod
    def _row_to_obj(row: dict[str, Any]) -> SessionMemoryRow:
        return SessionMemoryRow(
            session_id=str(row["session_id"]),
            body_md=row.get("body_md") or "",
            sections_json=row.get("sections_json") or {},
            version=int(row.get("version") or 1),
            last_updated_at=_parse_ts(row.get("last_updated_at")),
            tokens_at_last_update=int(row.get("tokens_at_last_update") or 0),
            tool_calls_at_last_update=int(row.get("tool_calls_at_last_update") or 0),
            turns_at_last_update=int(row.get("turns_at_last_update") or 0),
        )

    async def load(self, session_id: UUID | str) -> Optional[SessionMemoryRow]:
        """Return the row, or None if not yet created."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("session_id", str(session_id))
                .maybe_single()
                .execute()
            )
            if not (result and result.data):
                return None
            return self._row_to_obj(result.data)
        except Exception as exc:
            logger.error(f"session_memory load {session_id} failed: {exc}")
            return None

    async def upsert(
        self,
        session_id: UUID | str,
        *,
        body_md: str,
        sections_json: dict[str, Any],
        tokens_at_update: int,
        tool_calls_at_update: int,
        turns_at_update: int,
        bump_version: bool = True,
    ) -> Optional[SessionMemoryRow]:
        """Insert or update by session_id. Bumps version + sets
        last_updated_at on every successful upsert.

        Returns the resulting row, or None on failure (logged + swallowed —
        background updater must not crash chat path)."""
        try:
            client = await self._get_client()
            now_iso = datetime.now(timezone.utc).isoformat()
            existing = await self.load(session_id)
            new_version = (existing.version + 1) if (existing and bump_version) else 1
            payload = {
                "session_id": str(session_id),
                "body_md": body_md,
                "sections_json": sections_json or {},
                "version": new_version,
                "last_updated_at": now_iso,
                "tokens_at_last_update": int(tokens_at_update),
                "tool_calls_at_last_update": int(tool_calls_at_update),
                "turns_at_last_update": int(turns_at_update),
            }
            result = (
                await client.table(self.TABLE)
                .upsert(payload, on_conflict="session_id")
                .execute()
            )
            if not result.data:
                return None
            return self._row_to_obj(result.data[0])
        except Exception as exc:
            logger.error(f"session_memory upsert {session_id} failed: {exc}")
            return None

    async def delete(self, session_id: UUID | str) -> bool:
        """Hard-delete a session memory row. Returns True if a row was
        removed (best-effort signal — Supabase client doesn't always
        report delete row count reliably)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .delete()
                .eq("session_id", str(session_id))
                .execute()
            )
            return bool(result.data)
        except Exception as exc:
            logger.error(f"session_memory delete {session_id} failed: {exc}")
            return False


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def get_session_memory_repository() -> (
    Union["SessionMemoryRepository", "SessionMemoryRepositoryOrm"]
):
    """Return the right SessionMemoryRepository implementation per env.

    ORM when ``USE_ORM_SESSION_MEMORY`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_SESSION_MEMORY:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.session_memory_repository_orm import (
                SessionMemoryRepositoryOrm,
            )

            return SessionMemoryRepositoryOrm()
        logger.warning(
            "USE_ORM_SESSION_MEMORY=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return SessionMemoryRepository()


__all__ = [
    "SessionMemoryRepository",
    "SessionMemoryRow",
    "get_session_memory_repository",
]
