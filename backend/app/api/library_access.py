"""Who may read or change one ``libraries`` row.

``libraries`` has no ``team_id`` column: ownership is the ``scope_type`` +
``scope_id`` pair (``team`` / ``user`` / ``project``). Until this module the
``/libraries`` routes checked nothing at all — any signed-in user could list a
team's libraries by passing its id, create one inside a team they were not in,
and read, rename or delete any library by id.

The role comes from ``resolve_effective_role``, the single source the ideation
and workflow surfaces already use: team owner/admin → manager, member → editor,
and for a project scope the explicit ``project_members`` row / owner rule. A
``user`` scope belongs to that user alone.

A scope id that is not a number (a dirty row, or a client typo) resolves to
"no role" instead of a 500.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import HTTPException

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from app.core.workflow_roles import MANAGER, WRITE_ROLES, resolve_effective_role


async def team_role(team_id: str, user_id: str) -> Optional[str]:
    """The caller's effective role in ``team_id``, or None (also for junk ids)."""
    try:
        int(str(team_id))
    except (TypeError, ValueError):
        return None
    return await resolve_effective_role(user_id, team_id=str(team_id))


async def library_role(library: Mapping[str, Any], user_id: str) -> Optional[str]:
    """The caller's effective role on one library row, or None."""
    scope_type = library.get("scope_type")
    scope_id = str(library.get("scope_id") or "")
    if scope_type == "team":
        return await team_role(scope_id, user_id)
    if scope_type == "user":
        return MANAGER if scope_id == str(user_id) else None
    if scope_type == "project":
        try:
            int(scope_id)
        except ValueError:
            return None
        return await resolve_effective_role(user_id, project_id=scope_id)
    return None


def library_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": NOT_FOUND_OR_OUT_OF_SCOPE,
            "message": "Library not found or outside your scope.",
        },
    )


async def require_library_access(
    library: Mapping[str, Any] | None, user_id: str, *, write: bool
) -> Mapping[str, Any]:
    """Return ``library`` if the caller may read it (and write it, when asked).

    A library the caller cannot read answers exactly like a missing one, so
    existence never leaks across scopes. A readable library the caller may not
    change is a 403.
    """
    if not library:
        raise library_not_found()
    role = await library_role(library, user_id)
    if role is None:
        raise library_not_found()
    if write and role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")
    return library
