"""SQLAlchemy 2.0 ORM implementation of CollectionsRepository.

REST → ORM successor for the ``smart_collections`` surface, following the
validated ``LibrariesRepositoryOrm`` template. ``CollectionsRepositoryOrm``
subclasses ``CollectionsRepository`` and overrides every data method; the
``TABLE_NAME`` constant is inherited. Call sites route through
``get_collections_repository()``.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
Supabase REST renders ``uuid`` → STRING, ``bigint`` → int, ``timestamptz`` →
ISO string, ``jsonb`` → dict, ``ARRAY(bigint)`` → list. The ORM returns native
``uuid.UUID`` / ``int`` / ``datetime`` / ``dict`` / ``list``.

  smart_collections.id : bigint snowflake → STAYS native int. REST returned a
    JSON number → Python int. Never str() a bigint.
  smart_collections.user_id : uuid → STR (REST returned a UUID as string;
    router reads ``c["user_id"]`` directly into CollectionResponse.user_id
    which is a str field). The ORM user_id attribute is uuid.UUID → str(val).
  smart_collections.rules : jsonb → native dict. No coercion.
  smart_collections.cached_video_ids : ARRAY(BigInteger) → native list[int].
    No coercion (SQLAlchemy already decodes PG arrays to Python lists).
  smart_collections.cached_count, scope_id : int / bigint → native int.
  smart_collections.is_preset, is_active : bool → native bool.
  smart_collections.created_at / updated_at / cached_at : timestamptz →
    ``.isoformat()`` ALWAYS (REST returned ISO strings; the router reads them
    as strings and passes them straight to the response model). None → None.
  All other text columns (name, icon, description, sort_by, sort_order, color)
    → native str. No coercion.

Ownership + safety filters preserved exactly:
  - get_all_collections / get_collection_by_id / update_collection /
    delete_collection all filter ``SmartCollections.user_id == user_id`` (cast
    to UUID to satisfy the column type).
  - delete_collection additionally filters ``SmartCollections.is_preset ==
    False`` — identical to the REST ``.eq("is_preset", False)``.

Writes COMMIT via ``write_scope()`` (the silent-rollback P0 lesson). Inserts
use ``insert(...).values(...).returning(SmartCollections)`` so the server-set
defaults (snowflake id, timestamps, cached_count=0) are read back from the DB
— mirrors ``LibrariesRepositoryOrm.create``.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import SmartCollections
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.collections_repository import CollectionsRepository

# Build DB-column-name → mapped-attribute-name map once at import time.
_SC_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(SmartCollections)


def _sc_to_dict(obj: Any) -> Dict[str, Any]:
    """Return a SELECT *-shaped dict for a ``smart_collections`` ORM row.

    Strategy-C parity applied:
      - user_id (uuid.UUID) → str
      - created_at / updated_at / cached_at (datetime | None) → ISO str | None
      - id, cached_count, scope_id (int / None) → native int / None
      - rules (dict), cached_video_ids (list[int] | None) → native
      - booleans, text columns → native
    """
    out = _orm_obj_to_dict(obj, _SC_NAME_TO_ATTR)

    # user_id: uuid → str
    val = out.get("user_id")
    if val is not None:
        out["user_id"] = str(val)

    # timestamptz columns → ISO str (unconditional rule; None stays None)
    for col in ("created_at", "updated_at", "cached_at"):
        ts = out.get(col)
        if isinstance(ts, datetime):
            out[col] = ts.isoformat()

    return out


def _parse_user_id(user_id: str) -> _uuid.UUID:
    """Convert a str user_id to uuid.UUID for ORM WHERE clauses."""
    return _uuid.UUID(user_id)


def _parse_collection_id(collection_id: str) -> int:
    """Convert a str/int collection_id to int (bigint snowflake PK)."""
    return int(collection_id)


class CollectionsRepositoryOrm(CollectionsRepository):
    """ORM-backed CollectionsRepository for the smart_collections table."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_all_collections(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all collections for a user, ordered by created_at desc."""
        uid = _parse_user_id(user_id)
        async with read_scope() as session:
            result = await session.execute(
                select(SmartCollections)
                .where(SmartCollections.user_id == uid)
                .order_by(SmartCollections.created_at.desc())
            )
            return [_sc_to_dict(row) for row in result.scalars().all()]

    async def get_collection_by_id(
        self, collection_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single collection by ID, scoped to the owning user."""
        cid = _parse_collection_id(collection_id)
        uid = _parse_user_id(user_id)
        async with read_scope() as session:
            result = await session.execute(
                select(SmartCollections)
                .where(SmartCollections.id == cid)
                .where(SmartCollections.user_id == uid)
                .limit(1)
            )
            row = result.scalars().first()
            return _sc_to_dict(row) if row else None

    async def get_preset_collections(self, user_id: str) -> List[Dict[str, Any]]:
        """Get preset collections for a user."""
        uid = _parse_user_id(user_id)
        async with read_scope() as session:
            result = await session.execute(
                select(SmartCollections)
                .where(SmartCollections.user_id == uid)
                .where(SmartCollections.is_preset == True)  # noqa: E712
            )
            return [_sc_to_dict(row) for row in result.scalars().all()]

    # ------------------------------------------------------------------
    # Writes (COMMITTING via write_scope)
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        user_id: str,
        name: str,
        rules: dict,
        icon: str = "📁",
        description: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        """Insert a new smart collection and return the created row dict."""
        data: Dict[str, Any] = {
            "user_id": _parse_user_id(user_id),
            "name": name,
            "icon": icon,
            "description": description,
            "rules": rules,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "cached_count": 0,
            "is_preset": False,
        }
        async with write_scope() as session:
            result = await session.execute(
                insert(SmartCollections).values(**data).returning(SmartCollections)
            )
            row = result.scalars().first()
            out = _sc_to_dict(row) if row else {}
        logger.info(f"Created collection: {name} for user: {user_id}")
        return out

    async def update_collection(
        self, collection_id: str, user_id: str, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        """Update a collection. Returns the updated row or None if not found."""
        # Filter out None values — mirrors the REST impl.
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_collection_by_id(collection_id, user_id)

        update_data["updated_at"] = datetime.utcnow()

        cid = _parse_collection_id(collection_id)
        uid = _parse_user_id(user_id)
        async with write_scope() as session:
            result = await session.execute(
                update(SmartCollections)
                .where(SmartCollections.id == cid)
                .where(SmartCollections.user_id == uid)
                .values(**update_data)
                .returning(SmartCollections)
            )
            row = result.scalars().first()
            return _sc_to_dict(row) if row else None

    async def delete_collection(self, collection_id: str, user_id: str) -> bool:
        """Delete a non-preset collection. Returns True if a row was deleted."""
        cid = _parse_collection_id(collection_id)
        uid = _parse_user_id(user_id)
        async with write_scope() as session:
            result = await session.execute(
                delete(SmartCollections)
                .where(SmartCollections.id == cid)
                .where(SmartCollections.user_id == uid)
                .where(SmartCollections.is_preset == False)  # noqa: E712
                .returning(SmartCollections.id)
            )
            deleted_ids = result.fetchall()
            return len(deleted_ids) > 0

    async def update_cache(
        self, collection_id: str, media_ids: List[int], count: int
    ) -> Optional[Dict[str, Any]]:
        """Update cached_video_ids, cached_count, and cached_at for a collection."""
        cid = _parse_collection_id(collection_id)
        async with write_scope() as session:
            result = await session.execute(
                update(SmartCollections)
                .where(SmartCollections.id == cid)
                .values(
                    cached_video_ids=media_ids,
                    cached_count=count,
                    cached_at=datetime.utcnow(),
                )
                .returning(SmartCollections)
            )
            row = result.scalars().first()
            return _sc_to_dict(row) if row else None

    async def create_default_presets(self, user_id: str) -> List[Dict[str, Any]]:
        """Create the four default preset collections for a new user."""
        uid = _parse_user_id(user_id)
        presets = [
            {
                "name": "Recent Downloads",
                "icon": "📥",
                "description": "Videos downloaded in the last 7 days",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "date", "operator": "gte", "value": "7_days_ago"}
                    ],
                },
                "is_preset": True,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            {
                "name": "Favorites",
                "icon": "⭐",
                "description": "Videos marked as keep forever",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {
                            "field": "keep_forever",
                            "operator": "equals",
                            "value": True,
                        }
                    ],
                },
                "is_preset": True,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            {
                "name": "Most Viewed",
                "icon": "👀",
                "description": "Videos you view frequently",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "view_count", "operator": "gte", "value": 3}
                    ],
                },
                "is_preset": True,
                "sort_by": "view_count",
                "sort_order": "desc",
            },
            {
                "name": "Untagged",
                "icon": "🏷️",
                "description": "Videos without any tags",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "tag_count", "operator": "equals", "value": 0}
                    ],
                },
                "is_preset": True,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
        ]

        rows = [{"user_id": uid, "cached_count": 0, **preset} for preset in presets]
        created: List[Dict[str, Any]] = []
        async with write_scope() as session:
            result = await session.execute(
                insert(SmartCollections).values(rows).returning(SmartCollections)
            )
            created = [_sc_to_dict(row) for row in result.scalars().all()]

        logger.info(f"Created {len(created)} preset collections for user {user_id}")
        return created


__all__ = ["CollectionsRepositoryOrm"]
