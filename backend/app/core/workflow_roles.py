"""Effective-role resolution for project workflow authorization.

The single source of truth for "what can this user do in this project/team".
Collapses the pre-existing two-valued team-member gate (any member → allowed)
into a graded role so the workflow write surface (template CRUD, advance, node
tweaks = manager/editor; viewer/external = read-only) has one predicate.

Resolution order (spec §7):
  1. explicit ``project_members`` role wins — manager / editor / viewer / external
  2. otherwise fall back to the project's (or the given) team:
     team ``owner``/``admin`` → ``manager``; ``member`` → ``editor``
  3. no membership anywhere → ``None`` (caller raises 403, after its own 404)

DB reads are isolated behind three seams (``_get_project_member_role``,
``_get_project_team_id``, ``_get_team_role``) so the unit tests can exercise
every cell of the matrix without a database.
"""

from __future__ import annotations

from typing import Optional

# Graded write roles, in case callers want to gate on them.
MANAGER = "manager"
EDITOR = "editor"
VIEWER = "viewer"
EXTERNAL = "external"

WRITE_ROLES: frozenset[str] = frozenset({MANAGER, EDITOR})

# team_members.role → effective project role (fallback when no explicit row).
_TEAM_ROLE_TO_EFFECTIVE: dict[str, str] = {
    "owner": MANAGER,
    "admin": MANAGER,
    "member": EDITOR,
}


async def _get_project_member_role(project_id: str, user_id: str) -> Optional[str]:
    """Explicit ``project_members.role`` for (project, user), or None."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ProjectMembers

    async with read_scope() as session:
        row = (
            await session.execute(
                select(ProjectMembers.role)
                .where(ProjectMembers.project_id == int(str(project_id)))
                .where(ProjectMembers.user_id == user_id)
                .limit(1)
            )
        ).first()
    return row[0] if row is not None else None


async def _get_project_team_id(project_id: str) -> Optional[str]:
    """The project's ``team_id`` (str), or None for a personal project."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Projects

    async with read_scope() as session:
        row = (
            await session.execute(
                select(Projects.team_id)
                .where(Projects.id == int(str(project_id)))
                .limit(1)
            )
        ).first()
    if row is None or row[0] is None:
        return None
    return str(row[0])


async def _get_team_role(team_id: str, user_id: str) -> Optional[str]:
    """Raw ``team_members.role`` for (team, user), or None."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TeamMembers

    async with read_scope() as session:
        row = (
            await session.execute(
                select(TeamMembers.role)
                .where(TeamMembers.team_id == int(str(team_id)))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
        ).first()
    return row[0] if row is not None else None


async def resolve_effective_role(
    user_id: str,
    *,
    project_id: Optional[str] = None,
    team_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the caller's effective role, or None if they have no access.

    ``project_id`` takes precedence: an explicit project_members row wins, else
    the project's team decides. Pass ``team_id`` alone for a team-scoped surface
    (e.g. template CRUD) with no project in play.
    """
    if project_id is not None:
        explicit = await _get_project_member_role(project_id, user_id)
        if explicit is not None:
            return explicit
        if team_id is None:
            team_id = await _get_project_team_id(project_id)

    if team_id is None:
        return None

    team_role = await _get_team_role(team_id, user_id)
    if team_role is None:
        return None
    return _TEAM_ROLE_TO_EFFECTIVE.get(team_role)
