from __future__ import annotations

import datetime
import uuid
from datetime import timezone
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, or_, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import SignalSources


def compute_health(*, prev_failures: int, ok: bool, dead_threshold: int = 3) -> dict:
    """Pure state machine for source health. flipped_to_dead = crossed threshold this call."""
    if ok:
        return {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}
    failures = prev_failures + 1
    was_dead = prev_failures >= dead_threshold
    is_dead = failures >= dead_threshold
    health = "dead" if is_dead else "degraded"
    return {
        "health": health,
        "consecutive_failures": failures,
        "flipped_to_dead": is_dead and not was_dead,
    }


# Source kinds permitted by the signal_sources CHECK constraint (migration 304).
ALLOWED_KINDS = ("newsnow", "rss", "http_api", "custom")
# Kinds that actually have a working adapter (registry.get_adapter). http_api /
# custom are reserved in the DB CHECK but unimplemented — creating one yields a
# source that fails every fetch, so source-creation is restricted to these.
IMPLEMENTED_KINDS = ("newsnow", "rss")


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """REST-shaped dict matching the old PostgREST rendering: uuid → str,
    datetime → ISO str. BIGINT id stays native int; ``config`` (jsonb) stays a
    native dict; ``user_id`` NULL stays None."""
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, uuid.UUID):
            out[key] = str(val)
        elif isinstance(val, datetime.datetime):
            out[key] = val.isoformat()
        else:
            out[key] = val
    return out


def _row_dict(obj: SignalSources) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


class SignalSourcesRepository:
    TABLE = "signal_sources"

    async def tier_map(self) -> dict[str, int]:
        """``{source_id(str): tier}`` for every source — the credibility prior
        the code scorer multiplies in. Missing/NULL tier defaults to 2."""
        async with read_scope() as session:
            result = await session.execute(select(SignalSources.id, SignalSources.tier))
            return {str(sid): int(tier or 2) for sid, tier in result.all()}

    async def list_enabled(self) -> list[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(SignalSources).where(SignalSources.enabled.is_(True))
            )
            return [_serialize(_row_dict(o)) for o in result.scalars().all()]

    async def list_all(self) -> list[dict[str, Any]]:
        """All sources (enabled + disabled) for the read-only health surface.

        Ordered worst-health-first: 'dead' < 'degraded' < 'ok' sorts ascending,
        so failing sources surface at the top; ties broken by name.
        """
        async with read_scope() as session:
            result = await session.execute(
                select(SignalSources).order_by(SignalSources.health, SignalSources.name)
            )
            return [_serialize(_row_dict(o)) for o in result.scalars().all()]

    async def list_visible(self, user_id: str) -> list[dict[str, Any]]:
        """Sources this user may see/manage: system sources (user_id IS NULL)
        plus their own. Other users' private sources are excluded. Ordered
        worst-health-first, then by name.

        The owner filter is a parametrized ``OR`` (was a PostgREST filter
        STRING that interpolated user_id — no injection surface here)."""
        async with read_scope() as session:
            result = await session.execute(
                select(SignalSources)
                .where(
                    or_(
                        SignalSources.user_id.is_(None),
                        SignalSources.user_id == user_id,
                    )
                )
                .order_by(SignalSources.health, SignalSources.name)
            )
            return [_serialize(_row_dict(o)) for o in result.scalars().all()]

    async def feed_source_ids(self, user_id: str, hidden_ids: list[str]) -> list[str]:
        """Source ids whose hotspots belong in this user's feed: ENABLED visible
        (system + own) minus the ones they've hidden. The feed query filters
        ``source_id IN (...)`` on this set, so a deleted/disabled (admin-closed)/
        other-user/hidden source's hotspots never surface."""
        hidden = set(hidden_ids)
        rows = await self.list_visible(user_id)
        return [
            str(r["id"])
            for r in rows
            if r.get("enabled", True) and str(r["id"]) not in hidden
        ]

    async def get_source(self, source_id: str) -> dict | None:
        """A single source by id (any owner) — for ownership/existence checks."""
        async with read_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(SignalSources)
                        .where(SignalSources.id == _bigint(source_id))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            return _serialize(_row_dict(obj)) if obj else None

    async def create_source(
        self,
        *,
        user_id: str,
        kind: str,
        name: str,
        config: dict[str, Any],
        category: Optional[str],
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Insert a user-owned source. Its hotspots are scoped to this user via
        the feed's source-id filter (other clients never see them)."""
        async with write_scope() as session:
            result = await session.execute(
                insert(SignalSources)
                .values(
                    user_id=user_id,
                    kind=kind,
                    name=name,
                    config=config or {},
                    category=category,
                    enabled=enabled,
                )
                .returning(*SignalSources.__table__.columns)
            )
            row = result.mappings().first()
        return _serialize(dict(row))

    async def admin_create_source(
        self,
        *,
        kind: str,
        name: str,
        config: dict[str, Any],
        category: Optional[str] = None,
        tier: int = 2,
    ) -> dict[str, Any]:
        """Create a SYSTEM source (user_id NULL → visible to everyone). Distinct
        from create_source, which makes a user-private source. Admin-only."""
        async with write_scope() as session:
            result = await session.execute(
                insert(SignalSources)
                .values(
                    user_id=None,
                    kind=kind,
                    name=name,
                    config=config or {},
                    category=category,
                    enabled=True,
                    tier=int(tier),
                )
                .returning(*SignalSources.__table__.columns)
            )
            row = result.mappings().first()
        return _serialize(dict(row))

    async def admin_update(
        self,
        source_id: str,
        *,
        enabled: Optional[bool] = None,
        tier: Optional[int] = None,
    ) -> dict | None:
        """Admin-set a source's ``enabled`` (global on/off — disabling stops
        collection AND drops it from every feed) and/or ``tier`` (credibility
        prior). No ownership restriction — admin acts on any source. Returns the
        updated row, or the existing row when nothing changed."""
        patch: dict[str, Any] = {}
        if enabled is not None:
            patch["enabled"] = bool(enabled)
        if tier is not None:
            patch["tier"] = int(tier)
        if not patch:
            return await self.get_source(source_id)
        async with write_scope() as session:
            result = await session.execute(
                sa_update(SignalSources)
                .where(SignalSources.id == _bigint(source_id))
                .values(**patch)
                .returning(*SignalSources.__table__.columns)
            )
            row = result.mappings().first()
        return _serialize(dict(row)) if row else None

    async def delete_source(self, *, user_id: str, source_id: str) -> bool:
        """Delete a source the user OWNS (stops collection). Returns False when
        nothing was deleted (not found, or not owned by this user — the
        ``user_id`` predicate makes deleting others'/system sources a no-op)."""
        async with write_scope() as session:
            result = await session.execute(
                sa_delete(SignalSources)
                .where(
                    SignalSources.id == _bigint(source_id),
                    SignalSources.user_id == user_id,
                )
                .returning(SignalSources.id)
            )
            return result.first() is not None

    async def mark_health(
        self,
        source_id: str,
        *,
        ok: bool,
        error: Optional[str] = None,
        dead_threshold: int = 3,
    ) -> dict:
        async with read_scope() as session:
            prev_row = (
                await session.execute(
                    select(SignalSources.consecutive_failures).where(
                        SignalSources.id == _bigint(source_id)
                    )
                )
            ).first()
        prev = (prev_row[0] if prev_row else 0) or 0
        state = compute_health(prev_failures=prev, ok=ok, dead_threshold=dead_threshold)
        now = datetime.datetime.now(timezone.utc)
        patch: dict[str, Any] = {
            "health": state["health"],
            "consecutive_failures": state["consecutive_failures"],
            "last_fetched_at": now,
        }
        if ok:
            patch["last_ok_at"] = now
            patch["last_error"] = None
        else:
            patch["last_error"] = (error or "")[:500]
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(SignalSources)
                    .where(SignalSources.id == _bigint(source_id))
                    .values(**patch)
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"mark_health failed for {source_id}: {e}")
        return state
