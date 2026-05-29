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

from typing import Optional

from fastapi import Depends, HTTPException, Query

from app.core.deps import AuthContext, get_auth
from app.db.supabase_client import get_async_supabase_admin


async def verify_scope_access(
    auth: AuthContext = Depends(get_auth),
    scope_type: Optional[str] = Query(None),
    scope_id: str = Query(...),
) -> None:
    """Guard for `?scope_id=` write targets (resources library).

    After Spec 1 PR-C, ``scope_id`` is always a ``teams.id`` snowflake
    regardless of ``scope_type`` — personal scopes resolve to the user's
    auto-created single-member personal team. So authorization collapses
    to a single check: the caller must be a member of the team.

    PR-E Phase 1: ``scope_type`` is now vestigial (accepted but ignored).
    Authorization keys solely off ``scope_id``; the value is no longer
    validated.
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


async def verify_project_write_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` write targets.

    Caller must be the project owner, or a member of the project's team.
    `project_id` is injected from the route's path parameter by name match.
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

    raise HTTPException(
        status_code=403, detail="You do not have access to this project"
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
