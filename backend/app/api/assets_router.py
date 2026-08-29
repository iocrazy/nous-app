"""Asset Library — /assets (spec §5.1, P0 subset).

Scope = ``?scope_id=`` (a teams.id snowflake, personal scopes are the user's
personal team). Gate = team membership. Every AssetError maps to
``{success:false, error:{code, detail, ...extra}}`` with its status — typed
failure echo, never a silent no-op.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.deps import AuthDep
from app.core.scope_guards import verify_project_read_access
from app.db.session import read_scope, unit_of_work
from app.models import Projects, TeamMembers
from app.schemas.assets import (
    AssetCreate,
    AssetUpdate,
    AttachFileRequest,
    AttachFilesBatchRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
    ProjectRefRequest,
)
from app.services.assets.assets_service import AssetError, AssetsService
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(tags=["assets"])

# I-1: every id that reaches ``int()`` must be pinned at the boundary. A bare
# ``str`` query param accepts "abc", and the ValueError then surfaces from
# ``_is_member``/the service as an unhandled 500 instead of the 422 the caller
# earned. Mirrors ``SnowflakeId`` in app/schemas/assets.py, which does the same
# job for request bodies.
_SNOWFLAKE = r"^[0-9]{1,20}$"


def _service() -> AssetsService:
    return AssetsService()


async def _is_member(scope_id: str, user_id: str) -> bool:
    async with read_scope() as session:
        row = (
            await session.execute(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(str(scope_id)))
                .where(TeamMembers.user_id == uuid.UUID(str(user_id)))
                .limit(1)
            )
        ).first()
    return row is not None


async def _gate(scope_id: str, auth) -> int:
    if not await _is_member(scope_id, auth.user_id):
        raise HTTPException(
            status_code=403, detail="You are not a member of this scope"
        )
    return int(scope_id)


def _ok(data: Any, code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=code, content={"success": True, "data": data})


def _err(e: AssetError) -> JSONResponse:
    return JSONResponse(
        status_code=e.status,
        content={
            "success": False,
            "error": {"code": e.code, "detail": e.detail, **e.extra},
        },
    )


# ── list / create ───────────────────────────────────────────────────────────


@router.get("/assets")
async def list_assets(
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
    type: Optional[str] = Query(
        None, pattern="^(character|location|prop|costume|prompt|audio)$"
    ),
    project_id: Optional[str] = Query(None, pattern=_SNOWFLAKE),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    sid = await _gate(scope_id, auth)
    try:
        rows = await _service().list_assets(
            sid,
            asset_type=type,
            project_id=int(project_id) if project_id else None,
            q=q,
            limit=limit,
            offset=offset,
        )
    except AssetError as e:
        return _err(e)
    return _ok(rows)


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetCreate, auth: AuthDep, scope_id: str = Query(..., pattern=_SNOWFLAKE)
):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().create_asset(sid, payload, auth.user_id), 201)
    except AssetError as e:
        return _err(e)


@router.get("/projects/{project_id}/assets")
async def list_project_assets(
    project_id: str = Path(..., pattern=_SNOWFLAKE),
    *,
    auth: AuthDep,
    type: Optional[str] = Query(
        None, pattern="^(character|location|prop|costume|prompt|audio)$"
    ),
):
    # Guard returns None (raises 404/403 itself); load the project's team after.
    await verify_project_read_access(project_id, auth)
    async with read_scope() as session:
        row = (
            await session.execute(
                select(Projects.owner_id, Projects.team_id).where(
                    Projects.id == int(project_id)
                )
            )
        ).first()
    if row is None:  # pragma: no cover - the guard above already 404s
        raise HTTPException(status_code=404, detail="Project not found")
    owner_id, team_id = row
    if team_id is None:
        # Personal project (projects.team_id NULL) → the OWNER's personal team,
        # not the caller's. The guard admits collaborators via project_members,
        # and resolving the caller's own team for them would look up assets in
        # the wrong scope and answer {success:true, data:[]} — a wrong answer
        # that reads exactly like an empty library.
        team_id = int(await _resolve_personal_team_id(str(owner_id)))
    try:
        rows = await _service().list_assets(
            int(team_id),
            asset_type=type,
            project_id=int(project_id),
            q=None,
            limit=200,
            offset=0,
        )
    except AssetError as e:
        return _err(e)
    return _ok(rows)


# ── single asset ────────────────────────────────────────────────────────────


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: int, auth: AuthDep, scope_id: str = Query(..., pattern=_SNOWFLAKE)
):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().get_asset(asset_id, sid))
    except AssetError as e:
        return _err(e)


@router.patch("/assets/{asset_id}")
async def update_asset(
    asset_id: int,
    payload: AssetUpdate,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().update_asset(asset_id, sid, payload))
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: int, auth: AuthDep, scope_id: str = Query(..., pattern=_SNOWFLAKE)
):
    sid = await _gate(scope_id, auth)
    try:
        await _service().delete_asset(asset_id, sid)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})


# ── files ───────────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/files", status_code=status.HTTP_201_CREATED)
async def attach_files(
    asset_id: int,
    payload: Union[AttachFilesBatchRequest, AttachFileRequest],
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    svc = _service()
    try:
        if isinstance(payload, AttachFilesBatchRequest):
            # I-2: all-or-nothing. Each attach_file goes through the repo's
            # write_scope(), which commits on its own unless an ambient
            # unit_of_work() is open — so without this block, item N failing
            # would leave items 1..N-1 committed while the caller sees only an
            # error envelope: a partial write reported as total failure.
            # unit_of_work(): "Repo writes called inside this block share ONE
            # transaction (atomic: any raise rolls back all)."
            out: List[Dict[str, Any]] = []
            async with unit_of_work():
                for item in payload.items:
                    out.append(await svc.attach_file(asset_id, sid, item, auth.user_id))
            return _ok(out, 201)
        return _ok(await svc.attach_file(asset_id, sid, payload, auth.user_id), 201)
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}/files/{resource_id}/{slot}")
async def detach_file(
    asset_id: int,
    resource_id: int,
    slot: str,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        await _service().detach_file(asset_id, sid, resource_id, slot)
    except AssetError as e:
        return _err(e)
    return _ok({"detached": True})


# ── links ───────────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/links", status_code=status.HTTP_201_CREATED)
async def add_link(
    asset_id: int,
    payload: LinkRequest,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().add_link(asset_id, sid, payload), 201)
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}/links/{to_asset_id}/{relation}")
async def remove_link(
    asset_id: int,
    to_asset_id: int,
    relation: str,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        await _service().remove_link(asset_id, sid, to_asset_id, relation)
    except AssetError as e:
        return _err(e)
    return _ok({"removed": True})


# ── loadouts ────────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/loadouts", status_code=status.HTTP_201_CREATED)
async def create_loadout(
    asset_id: int,
    payload: LoadoutCreate,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().create_loadout(asset_id, sid, payload), 201)
    except AssetError as e:
        return _err(e)


@router.patch("/assets/{asset_id}/loadouts/{loadout_id}")
async def update_loadout(
    asset_id: int,
    loadout_id: int,
    payload: LoadoutUpdate,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().update_loadout(asset_id, sid, loadout_id, payload))
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}/loadouts/{loadout_id}")
async def delete_loadout(
    asset_id: int,
    loadout_id: int,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        await _service().delete_loadout(asset_id, sid, loadout_id)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})


# ── project refs ────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/project-refs", status_code=status.HTTP_201_CREATED)
async def link_project(
    asset_id: int,
    payload: ProjectRefRequest,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    await verify_project_read_access(payload.project_id, auth)
    try:
        await _service().link_project(
            asset_id, sid, int(payload.project_id), auth.user_id
        )
    except AssetError as e:
        return _err(e)
    return _ok({"linked": True}, 201)


@router.delete("/assets/{asset_id}/project-refs/{project_id}")
async def unlink_project(
    asset_id: int,
    project_id: int,
    auth: AuthDep,
    scope_id: str = Query(..., pattern=_SNOWFLAKE),
):
    sid = await _gate(scope_id, auth)
    try:
        await _service().unlink_project(asset_id, sid, project_id)
    except AssetError as e:
        return _err(e)
    return _ok({"unlinked": True})
