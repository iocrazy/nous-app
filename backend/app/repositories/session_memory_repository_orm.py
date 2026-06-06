"""SQLAlchemy 2.0 ORM implementation of SessionMemoryRepository (Phase 2, M batch).

REST → ORM successor for ``ai_session_memory`` (one row per ai_sessions row;
markdown body + parsed sections_json). Strangler-Fig single-inheritance:
``SessionMemoryRepositoryOrm`` subclasses ``SessionMemoryRepository`` and
overrides ``load`` / ``upsert`` / ``delete``. The DOMAIN-OBJECT MAPPING
(``_row_to_obj`` staticmethod) and ``_parse_ts`` are INHERITED and reused
verbatim — the ORM only changes HOW the row dict is fetched/written, not how it
becomes a ``SessionMemoryRow``. Call sites route through
``get_session_memory_repository()`` (bottom of ``session_memory_repository.py``).

OUTPUT-TYPE NOTE (why strategy-C parity is trivial here)
========================================================
Unlike the dict-returning repos, this repo returns a ``SessionMemoryRow``
dataclass, and the inherited ``_row_to_obj`` ALREADY normalises every field at
its boundary regardless of the source dict's value types:
  session_id → ``str(row["session_id"])``   (bigint → str, done by _row_to_obj)
  last_updated_at → ``_parse_ts(...)``       (datetime OR ISO str → datetime)
  version / tokens / tool_calls / turns → ``int(... or 0)``
  body_md → ``... or ""``   sections_json → ``... or {}``
So feeding ``_row_to_obj`` a native-typed ORM row dict (datetime object,
native int session_id, dict sections_json) produces the IDENTICAL
``SessionMemoryRow`` the REST path produced from a str/ISO-typed dict — the
dataclass constructor is the parity layer. We therefore build a plain
DB-column-keyed dict from the ORM row (via ``_orm_obj_to_dict``) and hand it to
the inherited ``_row_to_obj``; no per-field coercion is needed in this file.

BIGINT BIND COERCION (the only ORM-specific hazard)
===================================================
``ai_session_memory.session_id`` is a BIGINT (FK → ai_sessions.id, a snowflake
bigint), but every caller passes ``session_id`` as a STR (UUID|str signature;
in practice a snowflake-as-str). asyncpg's int8 codec is strict — binding a str
to a BIGINT column raises. We coerce the lookup/write key to int via ``_bigint``
(handles str snowflakes) at the query boundary. The PUBLIC contract is
unchanged: ``_row_to_obj`` str()s session_id back on the way out.

PHANTOM-COLUMN PRE-FLIGHT
=========================
Single write path (``upsert``). Its payload keys — session_id / body_md /
sections_json / version / last_updated_at / tokens_at_last_update /
tool_calls_at_last_update / turns_at_last_update — are ALL mapped columns on
``AiSessionMemory``. No phantom columns.

UPSERT semantics: the legacy ``upsert(payload, on_conflict="session_id")``
reproduces as ``pg_insert(...).on_conflict_do_update(index_elements=
["session_id"], set_={the non-PK columns})``. The legacy computes
``new_version`` by loading the existing row first (load → +1 or 1); we keep that
EXACT logic (call ``self.load`` first) so version-bump parity holds. ``now()``
is written as a real native UTC datetime (the legacy wrote ``datetime.now(utc).
isoformat()`` — a str PostgREST coerced; asyncpg needs the native datetime, and
``_parse_ts`` reads either back identically).

No date columns, no date/timestamp RANGE filters → no timestamptz<VARCHAR
hazard. Writes commit via ``write_scope()`` (silent-rollback P0). Reads use
``read_scope()``. Error handling mirrors the legacy EXACTLY: load swallows +
returns None; upsert swallows + returns None (must not crash the chat path);
delete swallows + returns False.
"""

from __future__ import annotations

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
from app.repositories.session_memory_repository import (
    SessionMemoryRepository,
    SessionMemoryRow,
)

_MEMORY_N2A = _name_to_attr(AiSessionMemory)


def _bigint(value: UUID | str | int) -> int:
    """Coerce a session_id to a native int for a BIGINT bind (asyncpg int8 codec
    is strict — a str snowflake must be int-coerced). Accepts int / str / UUID
    (UUID stringified then int-parsed defensively)."""
    if isinstance(value, int):
        return value
    return int(str(value))


class SessionMemoryRepositoryOrm(SessionMemoryRepository):
    """ORM-backed SessionMemoryRepository. Overrides load / upsert / delete;
    inherits _row_to_obj (the dataclass parity layer) + _parse_ts."""

    async def load(self, session_id: UUID | str) -> Optional[SessionMemoryRow]:
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


__all__ = ["SessionMemoryRepositoryOrm"]
