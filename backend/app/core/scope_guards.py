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

from app.core.deps import AuthContext, get_auth, get_team_id_for_user
from app.db.supabase_client import get_async_supabase_admin


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
    client = await get_async_supabase_admin()
    result = (
        await client.table("team_members")
        .select("team_id")
        .eq("team_id", scope_id)
        .eq("user_id", auth.user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
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
    client = await get_async_supabase_admin()
    project_res = (
        await client.table("projects")
        .select("owner_id, team_id")
        .eq("id", project_id)
        .limit(1)
        .execute()
    )
    if not project_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    project = project_res.data[0]
    if project.get("owner_id") == auth.user_id:
        return

    team_id = project.get("team_id")
    if team_id:
        member_res = (
            await client.table("team_members")
            .select("team_id")
            .eq("team_id", team_id)
            .eq("user_id", auth.user_id)
            .limit(1)
            .execute()
        )
        if member_res.data:
            return

    pm_res = (
        await client.table("project_members")
        .select("role")
        .eq("project_id", project_id)
        .eq("user_id", auth.user_id)
        .limit(1)
        .execute()
    )
    if pm_res.data:
        role = pm_res.data[0].get("role")
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
    """Shared body: load ``script_projects`` by id (404 if missing), then compare
    its ``team_id`` with the caller's team. Raises HTTPException on failure."""
    from app.repositories.script_repository import get_script_project_repository

    project = await get_script_project_repository().get_by_id(script_id)
    if not project:
        raise HTTPException(status_code=404, detail="Script project not found")
    user_team = await get_team_id_for_user(user_id)
    if str(user_team) != str(project.get("team_id")):
        raise HTTPException(status_code=403, detail="Access denied")


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
    client = await get_async_supabase_admin()
    result = (
        await client.table("resources")
        .select("creator_id")
        .eq("id", resource_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Resource not found")

    if result.data[0].get("creator_id") != auth.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have access to this resource"
        )
