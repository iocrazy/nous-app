"""Repository for ai_session_memory (Wave 5b / B2).

One row per ai_sessions row. Body is markdown source-of-truth + parsed
sections_json for query convenience.

ORM 2.0 (Phase 2, M batch — post-rollout collapse): ``SessionMemoryRepository``
is the SQLAlchemy 2.0 ORM implementation. The legacy supabase-py REST path and
the ``USE_ORM_SESSION_MEMORY`` flag were retired once prod ran 100% ORM; call
sites still route through ``get_session_memory_repository()`` (bottom of this
file), which now unconditionally returns this class.

VALUE-TYPE PARITY (why strategy-C is trivial here)
==================================================
Unlike the dict-returning repos, this repo returns a ``SessionMemoryRow``
dataclass, and ``_row_to_obj`` ALREADY normalises every field at its boundary
regardless of the source dict's value types:
  session_id → ``str(row["session_id"])``   (bigint → str)
  last_updated_at → ``_parse_ts(...)``       (datetime OR ISO str → datetime)
  version / tokens / tool_calls / turns → ``int(... or 0)``
  body_md → ``... or ""``   sections_json → ``... or {}``
So feeding ``_row_to_obj`` a native-typed ORM row dict (datetime object, native
int session_id, dict sections_json) produces the IDENTICAL ``SessionMemoryRow``
the retired REST path produced from a str/ISO-typed dict — the dataclass
constructor is the parity layer. We build a plain DB-column-keyed dict from the
ORM row (via ``_orm_obj_to_dict``) and hand it to ``_row_to_obj``; no per-field
coercion is needed here.

BIGINT BIND COERCION (the only ORM-specific hazard)
===================================================
``ai_session_memory.session_id`` is a BIGINT (FK → ai_sessions.id, a snowflake
bigint), but every caller passes ``session_id`` as a STR (UUID|str signature;
in practice a snowflake-as-str). asyncpg's int8 codec is strict — binding a str
to a BIGINT column raises. We coerce the lookup/write key to int via ``_bigint``
at the query boundary. The PUBLIC contract is unchanged: ``_row_to_obj`` str()s
session_id back on the way out.

UPSERT semantics: ``upsert`` reproduces the legacy ``upsert(payload,
on_conflict="session_id")`` as ``pg_insert(...).on_conflict_do_update(
index_elements=["session_id"], set_={the non-PK columns})``. ``new_version`` is
computed by loading the existing row first (load → +1 or 1). ``now()`` is
written as a real native UTC datetime (asyncpg needs the native datetime;
``_parse_ts`` reads it back identically). Writes commit via ``write_scope()``
(silent-rollback P0). Reads use ``read_scope()``. Error handling: load swallows
+ returns None; upsert swallows + returns None (must not crash the chat path);
delete swallows + returns False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import AiSessionMemory
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_MEMORY_N2A = _name_to_attr(AiSessionMemory)


def _bigint(value: UUID | str | int) -> int:
    """Coerce a session_id to a native int for a BIGINT bind (asyncpg int8 codec
    is strict — a str snowflake must be int-coerced). Accepts int / str / UUID
    (UUID stringified then int-parsed defensively)."""
    if isinstance(value, int):
        return value
    return int(str(value))


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
    """SQLAlchemy 2.0 repository for ``ai_session_memory`` (ORM-only).

    Overrides load / upsert / delete via ``read_scope()`` / ``write_scope()``.
    The DOMAIN-OBJECT MAPPING (``_row_to_obj`` staticmethod) and ``_parse_ts``
    are the parity layer — the ORM only changes HOW the row dict is
    fetched/written, not how it becomes a ``SessionMemoryRow``.
    """

    TABLE = "ai_session_memory"

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
            async with read_scope() as session:
                result = await session.execute(
                    select(AiSessionMemory)
                    .where(AiSessionMemory.session_id == _bigint(session_id))
                    .limit(1)
                )
                row = result.scalars().first()
            if row is None:
                return None
            return self._row_to_obj(_orm_obj_to_dict(row, _MEMORY_N2A))
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
            now = datetime.now(timezone.utc)
            existing = await self.load(session_id)
            new_version = (existing.version + 1) if (existing and bump_version) else 1
            payload = {
                "session_id": _bigint(session_id),
                "body_md": body_md,
                "sections_json": sections_json or {},
                "version": new_version,
                "last_updated_at": now,
                "tokens_at_last_update": int(tokens_at_update),
                "tool_calls_at_last_update": int(tool_calls_at_update),
                "turns_at_last_update": int(turns_at_update),
            }
            stmt = pg_insert(AiSessionMemory).values(**payload)
            # ON CONFLICT (session_id) DO UPDATE for every non-PK column.
            update_cols = {k: v for k, v in payload.items() if k != "session_id"}
            stmt = stmt.on_conflict_do_update(
                index_elements=[AiSessionMemory.session_id],
                set_=update_cols,
            ).returning(AiSessionMemory)
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
            if row is None:
                return None
            return self._row_to_obj(_orm_obj_to_dict(row, _MEMORY_N2A))
        except Exception as exc:
            logger.error(f"session_memory upsert {session_id} failed: {exc}")
            return None

    async def delete(self, session_id: UUID | str) -> bool:
        """Hard-delete a session memory row. Returns True if a row was
        removed."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_delete(AiSessionMemory)
                    .where(AiSessionMemory.session_id == _bigint(session_id))
                    .returning(AiSessionMemory.session_id)
                )
                deleted = result.scalars().first()
            return deleted is not None
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


def get_session_memory_repository() -> "SessionMemoryRepository":
    """Return the SessionMemoryRepository (SQLAlchemy 2.0, ORM-only).

    The legacy supabase-py REST path and the ``USE_ORM_SESSION_MEMORY`` flag
    were retired post-rollout; prod runs 100% ORM. Kept as a factory so call
    sites remain import-stable.
    """
    return SessionMemoryRepository()


__all__ = [
    "SessionMemoryRepository",
    "SessionMemoryRow",
    "get_session_memory_repository",
]
