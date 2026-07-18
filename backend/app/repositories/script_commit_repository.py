# app/repositories/script_commit_repository.py

"""Script Commit Repository — SQLAlchemy 2.0 ORM data access for the version
tier (``script_commits``), a manual "tag" over the append-only op ledger.

ORM-only (read_scope / write_scope), matching the house idiom in
``script_shot_repository.py``: reads swallow + return None/[] on failure; writes
log + re-raise (write_scope rolls back on any raise — the silent-rollback P0
lesson). Every bigint id/FK is ``_bigint``-coerced at the boundary (the 5.3 trap
— a snowflake compared/bound as str silently misses), and read dicts go through
strategy-C value-type parity (uuid → str, datetime → ISO str; bigint ids STAY
native int; JSONB ``watermarks`` / ``scene_ids`` stay a dict / list).

A commit is immutable once written: no ``update`` lane. ``delete`` removes only
the tag — the ops it referenced live on in ``script_ops`` untouched (spec §5-1:
tags are droppable, history is not).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select

from app.db.session import read_scope, write_scope
from app.models import ScriptCommits
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_COMMITS_N2A: Dict[str, str] = _name_to_attr(ScriptCommits)
_COMMITS_ATTRS = {p.key for p in ScriptCommits.__mapper__.column_attrs}

# bigint columns coerced on write. Ids/FKs stay native int on read (5.3 trap).
_COMMIT_BIGINT_FIELDS = ("script_id",)


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime/date → ISO
    str. Bigint ids/FKs and JSONB (dict/list) stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ScriptCommits row."""
    return _parity(_orm_obj_to_dict(obj, _COMMITS_N2A))


def _commit_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the write ``values()`` dict: keep only mapped columns (graceful
    no-op for unknown keys, REST parity) and bigint-coerce the id/FK fields."""
    known = {k: v for k, v in data.items() if k in _COMMITS_ATTRS}
    for field in _COMMIT_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    return known


class ScriptCommitRepository:
    """Commit-tag data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a commit tag. ``data`` carries script_id / message / watermarks
        / scene_ids / created_by; the watermark map + scene snapshot are computed
        by the version service before this call."""
        try:
            values = _commit_write_values(data)
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptCommits).values(**values).returning(ScriptCommits)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created commit for script {data.get('script_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create commit: {e}")
            raise

    async def list_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        """All commits for a script, newest first (created_at DESC) — the version
        history list order. Each row is enriched with ``author_name`` resolved
        from ``created_by`` (service-role read; the client falls back to a short
        id / "You" when it's blank)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptCommits)
                    .where(ScriptCommits.script_id == _bigint(script_id))
                    .order_by(ScriptCommits.created_at.desc())
                )
                rows = [_row(r) for r in result.scalars().all()]
            names = await self.resolve_usernames(
                [r["created_by"] for r in rows if r.get("created_by")]
            )
            return [{**r, "author_name": names.get(r.get("created_by"))} for r in rows]
        except Exception as e:
            logger.error(f"Failed to list commits for script {script_id}: {e}")
            return []

    async def get(self, commit_id: str) -> Optional[Dict[str, Any]]:
        """A single commit by id, or None."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptCommits)
                    .where(ScriptCommits.id == _bigint(commit_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get commit {commit_id}: {e}")
            return None

    async def resolve_usernames(self, user_ids: List[str]) -> Dict[str, str]:
        """Batch-resolve author uuids → ``user_profiles.username`` (service-role
        read; ``user_profiles`` RLS only exposes the caller's own row, so this
        must run server-side). Ids with no profile / blank username are omitted —
        the client falls back to a short id. Returns ``{}`` on any failure so
        author enrichment never sinks the diff."""
        if not user_ids:
            return {}
        try:
            from app.models.users import UserProfiles

            async with read_scope() as session:
                result = await session.execute(
                    select(UserProfiles.id, UserProfiles.username).where(
                        UserProfiles.id.in_([str(u) for u in user_ids])
                    )
                )
                return {str(uid): name for uid, name in result.all() if name}
        except Exception as e:
            logger.error(f"Failed to resolve usernames: {e}")
            return {}

    async def delete(self, commit_id: str) -> bool:
        """Delete a commit tag (the referenced ops in script_ops are untouched)."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ScriptCommits).where(
                        ScriptCommits.id == _bigint(commit_id)
                    )
                )
            logger.info(f"Deleted commit {commit_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete commit {commit_id}: {e}")
            raise


def get_script_commit_repository() -> "ScriptCommitRepository":
    """Return the ScriptCommitRepository (ORM-only)."""
    return ScriptCommitRepository()
