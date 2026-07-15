# backend/app/core/scope_guards.py

"""
Write-access guards for upload endpoints (FastAPI dependencies).

These are declared in route signatures so FastAPI enforces them before the
handler body runs — an endpoint author cannot forget to call them. This is a
structural fix for the 2026-05-14 finding: upload endpoints took a
user-controlled target (scope_id / project_id / resource_id) and wrote to it
without verifying the caller owned that target, because the check lived in
service code that some routes called and others didn't.

Each guard returns None on success and raises HTTPException on failure.
Routes consume them as: `_guard: None = Depends(verify_scope_access)`.
"""

from fastapi import Depends, HTTPException, Query

from app.core.deps import AuthContext, get_auth


async def verify_scope_access(
    auth: AuthContext = Depends(get_auth),
    scope_id: str = Query(...),
) -> None:
    """Guard for `?scope_id=` write targets (resources library).

    After Spec 1 PR-C, ``scope_id`` is always a ``teams.id`` snowflake —
    personal scopes resolve to the user's auto-created single-member
    personal team. Authorization collapses to a single check: the caller
    must be a member of the team.

    PR-E Phase 3: the vestigial ``scope_type`` query param has been
    dropped entirely. FastAPI ignores any leftover ``scope_type=`` a stale
    client still sends, so this is backward-compatible.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TeamMembers

    async with read_scope() as session:
        member = (
            await session.execute(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(str(scope_id)))
                .where(TeamMembers.user_id == auth.user_id)
                .limit(1)
            )
        ).first()
    if member is None:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this scope",
        )


_PROJECT_WRITE_ROLES = ("manager", "editor")


async def _check_project_access(
    project_id: str, auth: AuthContext, *, write: bool
) -> None:
    """Shared body for the project read/write guards.

    Access: owner, or member of the project's team, or a row in
    project_members — any role for read, manager/editor for write.
    project_members has a composite PK (project_id, user_id); no id column.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ProjectMembers, Projects, TeamMembers

    async with read_scope() as session:
        project = (
            await session.execute(
                select(Projects.owner_id, Projects.team_id)
                .where(Projects.id == int(str(project_id)))
                .limit(1)
            )
        ).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        # owner_id is a uuid column → ORM yields a native UUID; the auth
        # user_id is a str, so ``==`` would never match without str() (#1006).
        owner_id, team_id = project
        if str(owner_id) == auth.user_id:
            return

        if team_id:
            member = (
                await session.execute(
                    select(TeamMembers.team_id)
                    .where(TeamMembers.team_id == team_id)
                    .where(TeamMembers.user_id == auth.user_id)
                    .limit(1)
                )
            ).first()
            if member is not None:
                return

        pm = (
            await session.execute(
                select(ProjectMembers.role)
                .where(ProjectMembers.project_id == int(str(project_id)))
                .where(ProjectMembers.user_id == auth.user_id)
                .limit(1)
            )
        ).first()

    if pm is not None:
        role = pm[0]
        if not write or role in _PROJECT_WRITE_ROLES:
            return

    raise HTTPException(
        status_code=403, detail="You do not have access to this project"
    )


async def verify_project_write_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` write targets."""
    await _check_project_access(project_id, auth, write=True)


async def verify_project_read_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` read targets."""
    await _check_project_access(project_id, auth, write=False)


# ============================================================================
# Script / scene / episode guards (Phase B P2)
#
# Collapses the two private ``_verify_script_access`` copies that lived in
# script_ai_router.py and script_canvas_router.py into one shared body, and
# adds scene/episode variants that resolve the child id up to the script /
# project it belongs to before applying the same team check. Team comparison
# uses double-sided ``str()`` coercion (#1006: a snowflake row field is a
# native int, the path/body param is a str — ``!=`` would be always-true).
# 404 (row missing) is raised BEFORE 403 (wrong team), matching the Phase A
# project guards.
# ============================================================================


async def _assert_script_team_access(script_id: str, user_id: str) -> None:
    """Shared body: load ``script_projects`` by id (404 if missing), then require
    the caller to be a MEMBER of the script's team. Raises HTTPException on failure.

    Membership (``team_members``) rather than personal-team equality — the
    pre-P5 predicate compared ``get_team_id_for_user`` (the caller's personal
    workspace) against ``script_projects.team_id``, which 403'd every teammate
    on a shared-team script and made collaboration impossible at the HTTP
    layer while the realtime RLS (mig 346) was already membership-based. This
    matches ``verify_scope_access`` (Spec 1 PR-C: authorization collapses to
    "caller is a member of the team") and the ``can_read_script_op`` RLS
    function. Personal-team scripts are unaffected: the owner is the personal
    team's single member."""
    from app.repositories.script_repository import get_script_project_repository

    project = await get_script_project_repository().get_by_id(script_id)
    if not project:
        raise HTTPException(status_code=404, detail="Script project not found")
    if not await _is_team_member(str(project.get("team_id")), user_id):
        raise HTTPException(status_code=403, detail="Access denied")


async def _is_team_member(team_id: str, user_id: str) -> bool:
    """True when ``user_id`` has a ``team_members`` row for ``team_id``.

    Separate seam so authz wiring tests can stub membership without faking a
    Supabase client."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TeamMembers

    async with read_scope() as session:
        membership = (
            await session.execute(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(str(team_id)))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
        ).first()
    return membership is not None


async def verify_script_access(
    script_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/scripts/{script_id}/...` targets.

    Usable as a FastAPI dependency (``script_id`` injected from the path) and
    callable imperatively (pass the resolved ``AuthContext``) from routers whose
    ``script_id`` arrives in the body or is resolved from another id — this is
    how script_ai_router and script_canvas_router consume it after dropping
    their private copies.
    """
    await _assert_script_team_access(script_id, auth.user_id)


async def verify_scene_access(
    scene_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/scenes/{scene_id}/...` targets — resolves scene → script_id,
    then applies the same team check (404 if the scene is missing)."""
    from app.repositories.script_scene_repository import get_script_scene_repository

    scene = await get_script_scene_repository().get_by_id(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    await _assert_script_team_access(str(scene.get("script_id")), auth.user_id)


async def verify_shot_access(
    shot_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/shots/{shot_id}/...` targets — resolves shot → scene_id →
    script_id, then applies the same team check (404 if the shot is missing).

    A thin one-hop wrapper over ``verify_scene_access``'s kernel: shots hang off
    a scene which hangs off a script, so authorization collapses to the scene's
    team check once the shot is resolved to its ``scene_id``."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    shot = await get_script_shot_repository().get_by_id(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    await verify_scene_access(str(shot.get("scene_id")), auth)


async def verify_commit_access(
    commit_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/commits/{commit_id}` targets — resolves commit → script_id,
    then applies the same team check (404 if the commit is missing).

    A one-hop wrapper over the script team check for the commit-scoped route
    (``DELETE /commits/{commit_id}``) that has no ``script_id`` in its path. The
    ``str()`` coercion in ``_assert_script_team_access`` handles the #1006 trap
    (the row's ``script_id`` is a native int, the guard compares as str)."""
    from app.repositories.script_commit_repository import get_script_commit_repository

    commit = await get_script_commit_repository().get(commit_id)
    if not commit:
        raise HTTPException(status_code=404, detail="Commit not found")
    await _assert_script_team_access(str(commit.get("script_id")), auth.user_id)


async def verify_beat_access(
    beat_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/beats/{beat_id}` targets — resolves beat → script_id, then
    applies the same team check (404 if the beat is missing).

    A one-hop wrapper over the script team check for the beat-scoped routes
    (PATCH/DELETE `/beats/{beat_id}`, POST `/beats/{beat_id}/move`) that have no
    ``script_id`` in the path. The ``str()`` coercion in
    ``_assert_script_team_access`` handles the #1006 trap (the row's
    ``script_id`` is a native int, the guard compares as str)."""
    from app.repositories.script_beat_repository import get_script_beat_repository

    beat = await get_script_beat_repository().get_by_id(beat_id)
    if not beat:
        raise HTTPException(status_code=404, detail="Beat not found")
    await _assert_script_team_access(str(beat.get("script_id")), auth.user_id)


async def verify_episode_write_access(
    episode_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/episodes/{episode_id}` write targets — resolves episode →
    project, then applies project write-access semantics (404 if missing)."""
    from app.repositories.episode_repository import get_episode_repository

    episode = await get_episode_repository().get_by_id(episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    await _check_project_access(str(episode.get("project_id")), auth, write=True)


async def verify_resource_write_access(
    resource_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/resources/{resource_id}/...` write targets (e.g. new version).

    Caller must be the resource creator. Mirrors the `resources` table RLS
    policy "Creators can update resources" — backend uses the service-role
    client which bypasses RLS, so the check must be enforced here.
    `resource_id` is injected from the route's path parameter by name match.
    """
    from sqlalchemy import select

    from app.db.scope import system_request_scope
    from app.db.session import read_scope
    from app.models import Resources

    # ``resources`` carries the ``UserScoped`` mixin. This guard deliberately
    # looks up ANY resource by id (cross-user) then compares creator_id itself,
    # exactly as the old service-role client did — so it must run under a SYSTEM
    # scope. Inert today (``SCOPE_ENFORCE_RESOURCES`` off → the choke point is a
    # no-op), but keeps this cross-tenant read correct if the flag flips on.
    async with system_request_scope(reason="authz-resource-write-check"):
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(Resources.creator_id)
                    .where(Resources.id == int(str(resource_id)))
                    .limit(1)
                )
            ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    # creator_id is a uuid column → native UUID from the ORM; str() so the
    # comparison against the str auth.user_id can actually match (#1006).
    if str(row[0]) != auth.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have access to this resource"
        )
