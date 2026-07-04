"""Repository for user tag picker preferences.

ORM 2.0: ``TagPreferencesRepository`` is the SQLAlchemy 2.0 implementation for
the ``user_tag_preferences`` surface (starred tag ids + picker settings + panel
size). Call sites go through ``get_tag_preferences_repository()`` (bottom of this
file), which unconditionally returns the class — the legacy supabase-py REST path
and the engine-presence fallback were retired once prod ran 100% ORM.

STRATEGY-C VALUE-TYPE PARITY
===========================
The PUBLIC return shape is NOT a SELECT-* dict — both methods return a fixed
3-key dict ``{starred_tag_ids, picker_settings, panel_size}`` (defaults merged).
None of those keys is a type-sensitive value:

  - starred_tag_ids : ARRAY(text) → native ``list[str]`` (same Python type REST
    returned). No coercion.
  - picker_settings / panel_size : JSONB → native ``dict`` (same Python type REST
    returned). No coercion.

The only uuid column (``user_id``, the PK) is an INPUT (the caller passes a str
user_id) and is NEVER part of the returned dict, so there is no uuid→str boundary
coercion to do. timestamptz columns (created_at / updated_at) are not returned
either. The ``user_id`` write input binds fine through SQLAlchemy's Uuid type
processor (accepts a str).

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). The upsert
is idempotent (ON CONFLICT (user_id) DO UPDATE — SET-based).
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import UserTagPreferences


class TagPreferencesRepository:
    """Data access for user_tag_preferences table."""

    DEFAULTS = {
        "starred_tag_ids": [],
        "picker_settings": {
            "layout": "list",
            "columnWidth": "medium",
            "showStarred": True,
            "showRecently": True,
            "showRecommended": False,
            "showCount": True,
        },
        "panel_size": {"width": 480, "height": 400},
    }

    async def get_preferences(self, user_id: str) -> dict:
        """Get preferences for a user. Returns defaults (merged) if not found.

        On any failure (e.g. table missing) returns a fresh copy of DEFAULTS."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        UserTagPreferences.starred_tag_ids,
                        UserTagPreferences.picker_settings,
                        UserTagPreferences.panel_size,
                    ).where(UserTagPreferences.user_id == user_id)
                )
                row = result.mappings().first()

            if not row:
                return dict(self.DEFAULTS)

            return {
                "starred_tag_ids": row.get("starred_tag_ids") or [],
                "picker_settings": {
                    **self.DEFAULTS["picker_settings"],
                    **(row.get("picker_settings") or {}),
                },
                "panel_size": {
                    **self.DEFAULTS["panel_size"],
                    **(row.get("panel_size") or {}),
                },
            }
        except Exception:
            # Table may not exist yet — return defaults gracefully.
            return dict(self.DEFAULTS)

    async def upsert_preferences(self, user_id: str, updates: dict) -> dict:
        """Upsert preferences. Merges picker_settings at field level.

        Builds the upsert row from only the keys present in ``updates``, then
        ON CONFLICT (user_id) DO UPDATE the provided columns. Committing +
        idempotent. Re-reads to return the defaults-merged shape."""
        try:
            current = await self.get_preferences(user_id)

            data: Dict[str, Any] = {"user_id": user_id}

            if "starred_tag_ids" in updates and updates["starred_tag_ids"] is not None:
                data["starred_tag_ids"] = updates["starred_tag_ids"]

            if "picker_settings" in updates and updates["picker_settings"] is not None:
                data["picker_settings"] = {
                    **current["picker_settings"],
                    **updates["picker_settings"],
                }

            if "panel_size" in updates and updates["panel_size"] is not None:
                size = updates["panel_size"]
                data["panel_size"] = (
                    size if isinstance(size, dict) else size.model_dump()
                )

            # ON CONFLICT (user_id) DO UPDATE only the columns we are setting
            # (everything except the PK) — mirrors supabase upsert semantics.
            update_cols = {k: v for k, v in data.items() if k != "user_id"}
            stmt = pg_insert(UserTagPreferences).values(**data)
            if update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[UserTagPreferences.user_id],
                    set_=update_cols,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[UserTagPreferences.user_id]
                )

            async with write_scope() as session:
                await session.execute(stmt)

            return await self.get_preferences(user_id)
        except Exception:
            # Table may not exist yet — return defaults.
            return dict(self.DEFAULTS)


def get_tag_preferences_repository() -> TagPreferencesRepository:
    """Return the TagPreferencesRepository (ORM-backed, the only path)."""
    return TagPreferencesRepository()
