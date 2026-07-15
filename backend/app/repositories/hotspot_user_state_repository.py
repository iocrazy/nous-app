from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import HotspotUserState

# The three personal flags a user can toggle on a hotspot.
STATE_FLAGS = ("is_read", "is_saved", "is_hidden")


class HotspotUserStateRepository:
    """Per-user read/saved/hidden state for global hotspots.

    ORM-backed (read_scope/write_scope). ``HotspotUserState`` carries no scope
    mixin: ownership is scoped explicitly by the ``user_id`` predicate in every
    method (the service-role/RLS-bypass model, unchanged), so the choke point
    stays inert.
    """

    TABLE = "hotspot_user_state"

    async def get_states(
        self, user_id: str, hotspot_ids: list[str]
    ) -> dict[str, dict[str, bool]]:
        """Batch-fetch state for a set of hotspot ids → ``{hotspot_id: flags}``.

        Missing rows simply don't appear (caller treats them as all-false).
        """
        if not hotspot_ids:
            return {}
        async with read_scope() as session:
            result = await session.execute(
                select(
                    HotspotUserState.hotspot_id,
                    HotspotUserState.is_read,
                    HotspotUserState.is_saved,
                    HotspotUserState.is_hidden,
                ).where(
                    HotspotUserState.user_id == user_id,
                    HotspotUserState.hotspot_id.in_([int(h) for h in hotspot_ids]),
                )
            )
            rows = result.all()
        out: dict[str, dict[str, bool]] = {}
        for hotspot_id, is_read, is_saved, is_hidden in rows:
            out[str(hotspot_id)] = {
                "is_read": bool(is_read),
                "is_saved": bool(is_saved),
                "is_hidden": bool(is_hidden),
            }
        return out

    async def list_ids_where(self, user_id: str, *, flag: str) -> list[str]:
        """Hotspot ids where ``flag`` is true for this user (saved/hidden views)."""
        if flag not in STATE_FLAGS:
            raise ValueError(f"unknown flag: {flag}")
        col = getattr(HotspotUserState, flag)
        async with read_scope() as session:
            result = await session.execute(
                select(HotspotUserState.hotspot_id).where(
                    HotspotUserState.user_id == user_id,
                    col.is_(True),
                )
            )
            return [str(hid) for hid in result.scalars().all()]

    async def set_state(
        self,
        user_id: str,
        hotspot_id: str,
        *,
        is_read: Optional[bool] = None,
        is_saved: Optional[bool] = None,
        is_hidden: Optional[bool] = None,
    ) -> dict[str, bool]:
        """Upsert the given flags (only the non-None ones). Returns the row's
        resulting flags. Defaults absent flags to false on first insert."""
        provided = {
            "is_read": is_read,
            "is_saved": is_saved,
            "is_hidden": is_hidden,
        }
        set_flags: dict[str, Any] = {k: v for k, v in provided.items() if v is not None}
        now = datetime.now(timezone.utc)
        insert_values = {
            "user_id": user_id,
            "hotspot_id": int(hotspot_id),
            "updated_at": now,
            **set_flags,
        }
        try:
            stmt = pg_insert(HotspotUserState).values(**insert_values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "hotspot_id"],
                set_={**set_flags, "updated_at": now},
            ).returning(
                HotspotUserState.is_read,
                HotspotUserState.is_saved,
                HotspotUserState.is_hidden,
            )
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.first()
            if row is None:
                return {f: bool(insert_values.get(f, False)) for f in STATE_FLAGS}
            return {
                "is_read": bool(row[0]),
                "is_saved": bool(row[1]),
                "is_hidden": bool(row[2]),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"set_state failed for {user_id}/{hotspot_id}: {e}")
            raise
