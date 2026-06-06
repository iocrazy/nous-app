# app/repositories/permission_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of PermissionRepository (Phase 2, M batch).

REST → ORM successor for the ReBAC permission read surface: five read-only
lookups over ``access_overrides`` / ``folders`` / ``libraries`` /
``team_members`` / ``resource_items`` that ``PermissionService`` walks to
resolve a user's effective role. Strangler-Fig single-inheritance:
``PermissionRepositoryOrm`` subclasses ``PermissionRepository`` and overrides
all five methods. There are NO write methods. Call sites route through
``get_permission_repository()`` (bottom of ``permission_repository.py``).

PHANTOM-COLUMN PRE-FLIGHT
=========================
This repo is READ-ONLY (no insert/update/delete), so there are NO write paths to
phantom-screen. Every SELECT projects only real mapped columns (verified against
the models + supabase/migrations):
  access_overrides : * (full row; consumer reads only ["role"])
  folders          : id / parent_id / scope_id / visibility — all mapped.
  libraries        : id / scope_type / scope_id / visibility — all mapped
                     (libraries has NO team_id column; team scope is
                     scope_type='team' + scope_id; selecting team_id would be
                     the documented PG 42703 — we do NOT, matching the already-
                     fixed legacy projection).
  team_members     : role — mapped.
  resource_items   : scope_id / folder_id — mapped.

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
REST rendered JSON: bigint → int, uuid → str, timestamptz → ISO str, text →
str. The ORM returns native types. Per-method consumer audit (the WHOLE POINT of
the M batch — every value below is traced through PermissionService):

  access_overrides.* (get_access_override) — the returned dict is consumed ONLY
    as ``override["role"]`` / ``parent_override["role"]`` / ``folder_override
    ["role"]`` (a Text column → native str, OK). The other columns are pure
    shape parity:
      - id (uuid) → str   - user_id (uuid) → str   - granted_by (uuid) → str
        (REST returned str; NO consumer compares them type-sensitively — they
        are never read out of this dict at all — so str() is shape-only.)
      - object_id (text) → str (native)   - created_at (timestamptz) → ISO str.
    Applied via the generic uuid/datetime sweep in ``_parity``.

  folders (get_folder_by_id) → {id, parent_id, scope_id, visibility}:
      - id / parent_id / scope_id are BIGINT → STAY NATIVE int (the 5.3 trap).
        CONSUMER AUDIT — ``folder["parent_id"]`` is fed back into
        ``get_access_override("folder", folder["parent_id"], user_id)`` and into
        the recursive ``_resolve_folder_role(user_id, folder["parent_id"], …)``
        (→ ``get_folder_by_id(parent_id)``). The first is a bind against the
        TEXT ``access_overrides.object_id`` column; the second a bind against
        the BIGINT ``folders.id`` column. Keeping parent_id NATIVE int is
        correct for the bigint lookup; the text lookup is handled by coercing
        ``object_id`` to str AT the get_access_override bind boundary (see
        OBJECT_ID BIND below) — NOT by str()ing parent_id (which would break the
        bigint recursion). This mirrors REST exactly: REST returned parent_id as
        a JSON number (int) and PostgREST coerced int→text in the .eq filter.
      - visibility (text) → native str (not consumed for folders).

  libraries (get_library_by_id) → {id, scope_type, scope_id, visibility}:
      - id is BIGINT → native int (not consumed type-sensitively).
      - scope_id is TEXT in this table (libraries.scope_id is text, unlike the
        bigint scope_id on folders/resource_items) → native str.
      - visibility (text) → native str, CONSUMED by ``== "restricted"`` (str==
        str, OK).

  team_members.role (get_team_member_role) → returns the bare ``role`` STRING
    (not a dict), CONSUMED by ``TEAM_ROLE_MAP.get(role, ...)`` — native str, OK.

  resource_items (get_resource_item_scope) → {scope_id, folder_id}:
      - both BIGINT → STAY NATIVE int (the 5.3 trap). CONSUMER AUDIT —
        ``scope["folder_id"]`` is fed into ``get_access_override("folder",
        scope["folder_id"], user_id)`` (TEXT object_id bind → str-coerced at
        that boundary) and ``_resolve_folder_role(user_id, scope["folder_id"],
        …)`` (BIGINT folders.id bind → native int). Same split as folders above.

OBJECT_ID BIND (the only ORM-specific hazard)
=============================================
``access_overrides.object_id`` is TEXT. ``get_access_override`` is called with
``object_id`` that is EITHER a str (router-supplied library/folder id string) OR
a native int (a bigint ``folder.parent_id`` / ``scope.folder_id`` from the
methods above, kept native per the 5.3 trap). asyncpg will NOT bind a native int
to a TEXT column. Under REST this worked because PostgREST emitted
``object_id=eq.<n>`` and PG cast the literal to text. We reproduce that exact
coercion by ``str(object_id)`` at the bind boundary — behavior-identical (the
override row stores the stringified id) and the ONLY place a coercion is needed.

No date columns and no date/timestamp RANGE filters exist in this repo (every
query is equality / limit), so there is no timestamptz<VARCHAR hazard.

READ-ONLY repo → no write_scope() needed; all five methods use ``read_scope()``.
Error handling mirrors the legacy EXACTLY: every method swallows on failure and
returns None (or None role).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope
from app.models import AccessOverrides, Folders, Libraries, ResourceItems, TeamMembers
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.permission_repository import PermissionRepository

_OVERRIDES_N2A: Dict[str, str] = _name_to_attr(AccessOverrides)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    uuid → str (REST shape), datetime → ISO str. Bigint ids / FKs and text stay
    native (the 5.3 trap). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


class PermissionRepositoryOrm(PermissionRepository):
    """ORM-backed PermissionRepository (read-only ReBAC lookups)."""

    async def get_access_override(
        self, object_type: str, object_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AccessOverrides)
                    .where(AccessOverrides.object_type == object_type)
                    # object_id is a TEXT column; callers may pass a native int
                    # (a bigint folder.parent_id / scope.folder_id). str() it so
                    # asyncpg binds it to text, matching REST's int→text cast.
                    .where(AccessOverrides.object_id == str(object_id))
                    .where(AccessOverrides.user_id == user_id)
                    .limit(1)
                )
                row = result.scalars().first()
                return _parity(_orm_obj_to_dict(row, _OVERRIDES_N2A)) if row else None
        except Exception as e:
            logger.error(f"Failed to get access override: {e}")
            return None

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Folders.id,
                        Folders.parent_id,
                        Folders.scope_id,
                        Folders.visibility,
                    )
                    .where(Folders.id == int(folder_id))
                    .limit(1)
                )
                row = result.mappings().first()
                # id / parent_id / scope_id are bigint → native int (5.3 trap);
                # visibility is text. No uuid / datetime here → no _parity sweep.
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def get_library_by_id(self, library_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Libraries.id,
                        Libraries.scope_type,
                        Libraries.scope_id,
                        Libraries.visibility,
                    )
                    .where(Libraries.id == int(library_id))
                    .limit(1)
                )
                row = result.mappings().first()
                # id bigint → native int; scope_type / scope_id (text) /
                # visibility text. No uuid / datetime → no _parity sweep.
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get library {library_id}: {e}")
            return None

    async def get_team_member_role(self, user_id: str, team_id: str) -> Optional[str]:
        try:
            async with read_scope() as session:
                role = await session.scalar(
                    select(TeamMembers.role)
                    .where(TeamMembers.user_id == user_id)
                    .where(TeamMembers.team_id == int(team_id))
                    .limit(1)
                )
            return role
        except Exception as e:
            logger.error(f"Failed to get team role for user {user_id}: {e}")
            return None

    async def get_resource_item_scope(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems.scope_id, ResourceItems.folder_id)
                    .where(ResourceItems.resource_id == int(resource_id))
                    .limit(1)
                )
                row = result.mappings().first()
                # scope_id / folder_id are bigint → native int (5.3 trap). No
                # uuid / datetime → no _parity sweep.
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource scope for {resource_id}: {e}")
            return None


__all__ = ["PermissionRepositoryOrm"]
