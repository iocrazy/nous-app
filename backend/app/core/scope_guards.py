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

from typing import NamedTuple, Optional

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


class ProjectAccess(NamedTuple):
    """What a caller may do with one project — the raw verdict, no HTTP."""

    can_read: bool
    can_write: bool


async def _resolve_project_access(
    project_id: str, user_id: str
) -> Optional[ProjectAccess]:
    """THE project read/write verdict. ``None`` when the project row is gone.

    Single source for the raising guards (``_check_project_access`` →
    ``verify_project_{read,write}_access``) AND for the ``can_edit`` the
    canvas GET ships so the UI knows upfront whether its writes would be
    accepted. Keeping one body is the point: a second, UI-only copy of the
    role comparison is exactly how a client ends up offering (or hiding) an
    action the real gate then disagrees with — the failure mode this repo
    has hit repeatedly.

    One call answers BOTH questions (that is what the two-field
    ``ProjectAccess`` is for), so a read endpoint that also wants the write
    verdict must go through ``resolve_project_read_access`` and read
    ``.can_write`` off the returned tuple — never gate first and then ask a
    second predicate, which used to re-run every query below for the same
    (project, user) pair inside one request.

    Access: owner, or member of the project's team, or a row in
    project_members — any role for read, manager/editor for write.
    project_members has a composite PK (project_id, user_id); no id column.

    Takes a plain ``user_id`` (not ``AuthContext``) so non-route callers —
    e.g. the script/scene/shot read guards' project_members fallback below
    (#1742 four-level order: explicit member row > owner > team > none) —
    can reuse this body without fabricating an ``AuthContext``.
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
            return None

        # owner_id is a uuid column → ORM yields a native UUID; user_id is a
        # str, so ``==`` would never match without str() (#1006).
        owner_id, team_id = project
        if str(owner_id) == user_id:
            return ProjectAccess(True, True)

        if team_id:
            member = (
                await session.execute(
                    select(TeamMembers.team_id)
                    .where(TeamMembers.team_id == team_id)
                    .where(TeamMembers.user_id == user_id)
                    .limit(1)
                )
            ).first()
            if member is not None:
                return ProjectAccess(True, True)

        pm = (
            await session.execute(
                select(ProjectMembers.role)
                .where(ProjectMembers.project_id == int(str(project_id)))
                .where(ProjectMembers.user_id == user_id)
                .limit(1)
            )
        ).first()

    if pm is not None:
        return ProjectAccess(True, pm[0] in _PROJECT_WRITE_ROLES)

    return ProjectAccess(False, False)


async def _check_project_access(
    project_id: str, user_id: str, *, write: bool
) -> ProjectAccess:
    """Raising form of ``_resolve_project_access`` — 404 before 403, matching
    the rest of this module.

    Returns the resolved verdict rather than ``None`` so a caller that needs
    the OTHER half of it (a read endpoint reporting ``can_edit``) can take it
    from here instead of resolving the same (project, user) pair twice.
    """
    access = await _resolve_project_access(project_id, user_id)
    if access is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if access.can_write if write else access.can_read:
        return access
    raise HTTPException(
        status_code=403, detail="You do not have access to this project"
    )


async def resolve_project_read_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> ProjectAccess:
    """``verify_project_read_access`` that also HANDS BACK what it resolved.

    Same gate, same 404-before-403 ordering; the difference is that the
    ``can_write`` half of the verdict — already computed to answer the read
    question — is returned instead of discarded. Read endpoints that ship
    ``can_edit`` (canvas GET, storyboard GET) use this: gating and reporting
    then cost ONE ``_resolve_project_access``, not two identical ones.

    The reported verdict is therefore the very same value the write guard
    would branch on, which is the property that must not be traded away for
    the saved query: a UI-side re-derivation of the role comparison is how
    the client ends up disagreeing with the server.
    """
    return await _check_project_access(project_id, auth.user_id, write=False)


async def verify_project_write_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` write targets."""
    await _check_project_access(project_id, auth.user_id, write=True)


async def verify_project_read_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` read targets."""
    await _check_project_access(project_id, auth.user_id, write=False)


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


async def _assert_script_access(script_id: str, user_id: str, *, write: bool) -> None:
    """Shared body: load ``script_projects`` by id (404 if missing), then
    require either team membership or — for read-only callers — an explicit
    ``project_members`` row on the parent project. Raises HTTPException on
    failure.

    Membership (``team_members``) rather than personal-team equality — the
    pre-P5 predicate compared ``get_team_id_for_user`` (the caller's personal
    workspace) against ``script_projects.team_id``, which 403'd every teammate
    on a shared-team script and made collaboration impossible at the HTTP
    layer while the realtime RLS (mig 346) was already membership-based. This
    matches ``verify_scope_access`` (Spec 1 PR-C: authorization collapses to
    "caller is a member of the team") and the ``can_read_script_op`` RLS
    function.

    ⚠️ ``script_projects.team_id`` is NOT ``projects.team_id`` — it is
    ``NOT NULL`` on every row, including scripts under a personal
    (``projects.team_id`` NULL) project: those get the owner's
    auto-created single-member personal team. So an invited
    ``project_members`` row (any role — e.g. a viewer added to someone
    else's personal project) is never a member of THAT team and was always
    403'd here, even after #1742 folded the same "explicit member row wins"
    order into ``resolve_effective_role`` — this family never consulted it
    (2026-08-12 finding, personal-project viewer 403 on scenes/shots/beats/
    commits/memos despite an explicit ``project_members`` row).
    ``write=False`` read callers now fall back to ``_check_project_access``
    (owner / team / any explicit project_members row) once the team check
    fails. ``write=True`` callers keep the original team-only gate
    unchanged: this family has never graded ``project_members`` roles for
    writes (any team member, any role, can write) — extending the fallback
    to writes would silently WIDEN write access for viewer-role members,
    not fix a read gap, so it's deliberately left alone here."""
    from app.repositories.script_repository import get_script_project_repository

    project = await get_script_project_repository().get_by_id(script_id)
    if not project:
        raise HTTPException(status_code=404, detail="Script project not found")
    if await _is_team_member(str(project.get("team_id")), user_id):
        return
    if not write:
        project_id = project.get("project_id")
        if project_id is not None:
            await _check_project_access(str(project_id), user_id, write=False)
            return
    raise HTTPException(status_code=403, detail="Access denied")


async def _assert_script_team_access(script_id: str, user_id: str) -> None:
    """Back-compat name for the write-semantics (team-only, no
    project_members fallback) gate — kept for this module's write call
    sites and the imperative caller in
    ``beat_memos_router.get_memo_image``."""
    await _assert_script_access(script_id, user_id, write=True)


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
    """Guard for `/scripts/{script_id}/...` WRITE targets (team-only — see
    ``verify_script_read_access`` for the GET/read variant with the
    project_members fallback).

    Usable as a FastAPI dependency (``script_id`` injected from the path) and
    callable imperatively (pass the resolved ``AuthContext``) from routers whose
    ``script_id`` arrives in the body or is resolved from another id — this is
    how script_ai_router and script_canvas_router consume it after dropping
    their private copies.
    """
    await _assert_script_access(script_id, auth.user_id, write=True)


async def verify_script_read_access(
    script_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/scripts/{script_id}/...` READ (GET-only) targets — team
    membership, OR (2026-08-12 fix) an explicit ``project_members`` row on
    the parent project (any role), so a personal-project viewer added via
    ``project_members`` isn't locked out just because they aren't in the
    owner's personal team. See ``_assert_script_access`` for the full
    rationale."""
    await _assert_script_access(script_id, auth.user_id, write=False)


async def verify_scene_access(
    scene_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/scenes/{scene_id}/...` WRITE targets — resolves scene →
    script_id, then applies the same team-only check (404 if the scene is
    missing). See ``verify_scene_read_access`` for the GET/read variant."""
    from app.repositories.script_scene_repository import get_script_scene_repository

    scene = await get_script_scene_repository().get_by_id(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    await _assert_script_access(str(scene.get("script_id")), auth.user_id, write=True)


async def verify_scene_read_access(
    scene_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/scenes/{scene_id}/...` READ (GET-only) targets — resolves
    scene → script_id, then applies the team-membership-or-project_members
    check (404 if the scene is missing). See ``_assert_script_access``."""
    from app.repositories.script_scene_repository import get_script_scene_repository

    scene = await get_script_scene_repository().get_by_id(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    await _assert_script_access(str(scene.get("script_id")), auth.user_id, write=False)


async def verify_shot_access(
    shot_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/shots/{shot_id}/...` WRITE targets — resolves shot →
    scene_id → script_id, then applies the same team-only check (404 if the
    shot is missing).

    A thin one-hop wrapper over ``verify_scene_access``'s kernel: shots hang off
    a scene which hangs off a script, so authorization collapses to the scene's
    team check once the shot is resolved to its ``scene_id``. See
    ``verify_shot_read_access`` for the GET/read variant."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    shot = await get_script_shot_repository().get_by_id(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    await verify_scene_access(str(shot.get("scene_id")), auth)


async def verify_shot_read_access(
    shot_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/shots/{shot_id}/...` READ (GET-only) targets — resolves
    shot → scene_id, then applies ``verify_scene_read_access`` (404 if the
    shot is missing). One-hop wrapper, same shape as ``verify_shot_access``."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    shot = await get_script_shot_repository().get_by_id(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    await verify_scene_read_access(str(shot.get("scene_id")), auth)


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


async def verify_memo_access(
    memo_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/memos/{memo_id}` targets — resolves memo → script_id, then
    applies the same team check (404 if the memo is missing).

    A one-hop wrapper over the script team check for the memo-scoped routes
    (PATCH/DELETE `/memos/{memo_id}`) that have no ``script_id`` in the path. The
    ``str()`` coercion in ``_assert_script_team_access`` handles the #1006 trap
    (the row's ``script_id`` is a native int, the guard compares as str)."""
    from app.repositories.beat_memo_repository import get_beat_memo_repository

    memo = await get_beat_memo_repository().get_by_id(memo_id)
    if not memo:
        raise HTTPException(status_code=404, detail="Memo not found")
    await _assert_script_team_access(str(memo.get("script_id")), auth.user_id)


async def verify_beat_template_access(
    template_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/beat-templates/{template_id}` targets (PUT/DELETE) — resolves
    the template → owner ``user_id`` and requires the caller to be that owner
    (404 if the template is missing, before the 403).

    A beat template is a private, per-user artifact (no team sharing), so
    ownership collapses to a straight ``user_id`` equality — unlike the script
    guards, which resolve to a team membership check. ``str()`` coercion handles
    the #1006 trap (the repo returns ``user_id`` as a str; auth.user_id is a
    str, so this matches, but the coercion keeps it robust to a native-UUID
    read)."""
    from app.repositories.beat_template_repository import (
        get_beat_template_repository,
    )

    template = await get_beat_template_repository().get_by_id(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Beat template not found")
    if str(template.get("user_id")) != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")


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
    await _check_project_access(
        str(episode.get("project_id")), auth.user_id, write=True
    )


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
