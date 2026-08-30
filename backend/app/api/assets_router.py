"""Asset Library — /assets (spec §5.1, P0 subset + P2 filters).

Scope = ``?scope_id=`` (a teams.id snowflake, personal scopes are the user's
personal team). Gate = team membership. Every AssetError maps to
``{success:false, error:{code, detail, ...extra}}`` with its status — typed
failure echo, never a silent no-op.

Every route declares ``response_model=Envelope[...]`` plus ``responses=_ERRORS``.
That is not decoration: it makes FastAPI VALIDATE the success payload on the way
out (a service that stops emitting ``readiness`` fails loudly instead of
shipping a half row) and it puts real schemas — success AND refusal — into the
OpenAPI document the frontend types are read off. Refusals stay raw
``JSONResponse`` (``_err``), which bypasses the response model rather than being
coerced into it. Adding a route without an envelope is caught by
``tests/api/test_assets_router_p2.py``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from pydantic import AfterValidator
from sqlalchemy import select

from app.core.deps import AuthDep
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)
from app.db.session import read_scope, unit_of_work
from app.models import Projects, TeamMembers
from app.schemas.assets import (
    SNOWFLAKE_PATTERN,
    AssetCreate,
    AssetDetailResponse,
    AssetFileResponse,
    AssetLinkResponse,
    AssetResponse,
    AssetUpdate,
    AttachFileRequest,
    AttachFilesBatchRequest,
    DeletedResponse,
    DetachedResponse,
    Envelope,
    ErrorEnvelope,
    LinkedResponse,
    LinkRequest,
    LoadoutCreate,
    LoadoutResponse,
    LoadoutUpdate,
    ProjectRefRequest,
    RemovedResponse,
    UnlinkedResponse,
    within_int64,
)
from app.services.assets.assets_service import AssetError, AssetsService
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(tags=["assets"])

# I-1: every id that reaches ``int()`` must be pinned at the boundary. A bare
# ``str`` query param accepts "abc", and the ValueError then surfaces from
# ``_is_member``/the service as an unhandled 500 instead of the 422 the caller
# earned. Mirrors ``SnowflakeId`` in app/schemas/assets.py, which does the same
# job for request bodies — including its int64 bound: "^[0-9]{1,20}$" alone
# admits values ~10x past BIGINT, which parse fine and then fail at driver BIND
# (a 500 reachable from a query string). ``within_int64`` is the SAME callable
# the body schemas use, so the two boundaries cannot drift apart.
_SNOWFLAKE = SNOWFLAKE_PATTERN
_INT64_EXCLUSIVE_MAX = 2**63

ScopeIdQuery = Annotated[str, Query(pattern=_SNOWFLAKE), AfterValidator(within_int64)]
OptSnowflakeQuery = Annotated[
    Optional[str], Query(pattern=_SNOWFLAKE), AfterValidator(within_int64)
]
SnowflakePath = Annotated[str, Path(pattern=_SNOWFLAKE), AfterValidator(within_int64)]
# Path ids declared as ``int`` have the same reachable-500: FastAPI parses any
# digit string into a Python int, which only fails once asyncpg tries to bind it.
IdPath = Annotated[int, Path(ge=0, lt=_INT64_EXCLUSIVE_MAX)]

# Every refusal on this router wears the same body. Declared on every route so
# the OpenAPI contract says so too — a client reading only the 200 schema would
# otherwise have to discover the error shape by hitting one.
_ERRORS: Dict[Union[int, str], Dict[str, Any]] = {
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}


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
    """Scope gate — raises the SAME envelope every other failure on this router
    uses. It used to raise a bare ``HTTPException``, so FastAPI answered
    ``{"detail": ...}`` and the most security-relevant 403 was the one failure
    a client could not read a ``code`` off. Callers must therefore invoke this
    INSIDE their ``try:``."""
    if not await _is_member(scope_id, auth.user_id):
        raise AssetError(403, "not_a_member", "You are not a member of this scope")
    return int(scope_id)


# The project guards predate this router and raise FastAPI HTTPExceptions.
# Translated here (status preserved) so no path on /assets can answer in the
# other shape.
_PROJECT_GUARD_CODES = {403: "project_forbidden", 404: "project_not_found"}


async def _project_gate(project_id: Any, auth, *, write: bool) -> None:
    """Run the project read/write guard and re-raise its refusal as AssetError.

    ``write=True`` is not optional for the project-ref endpoints: creating and
    deleting an ``asset_project_refs`` row IS a write, and
    ``_resolve_project_access`` grants ``can_read`` to any ``project_members``
    row (a viewer).
    """
    guard = verify_project_write_access if write else verify_project_read_access
    try:
        await guard(str(project_id), auth)
    except HTTPException as e:
        raise AssetError(
            e.status_code,
            _PROJECT_GUARD_CODES.get(e.status_code, "project_access_denied"),
            str(e.detail),
        )


def _ok(data: Any) -> Dict[str, Any]:
    """Success body. A plain dict on purpose — a ``JSONResponse`` would skip the
    route's ``response_model``, which is where the payload gets validated. The
    status code comes from the route decorator."""
    return {"success": True, "data": data}


def _err(e: AssetError) -> JSONResponse:
    return JSONResponse(
        status_code=e.status,
        content={
            "success": False,
            "error": {"code": e.code, "detail": e.detail, **e.extra},
        },
    )


# ── list / create ───────────────────────────────────────────────────────────


@router.get(
    "/assets",
    response_model=Envelope[List[AssetResponse]],
    responses=_ERRORS,
)
async def list_assets(
    auth: AuthDep,
    scope_id: ScopeIdQuery,
    type: Optional[str] = Query(
        None, pattern="^(character|location|prop|costume|prompt|audio)$"
    ),
    project_id: OptSnowflakeQuery = None,
    q: Optional[str] = Query(None, max_length=200),
    readiness: Optional[str] = Query(None, pattern="^(ready|draft)$"),
    tag: Optional[str] = Query(None, min_length=1, max_length=200),
    sort: str = Query("recent", pattern="^(recent|name|readiness)$"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """The shelf query.

    ``readiness`` / ``tag`` / ``sort`` are pinned to enumerations rather than
    accepted loosely: a filter value the server does not understand must be a
    422, because quietly ignoring it returns the whole shelf looking filtered.
    """
    try:
        sid = await _gate(scope_id, auth)
        rows = await _service().list_assets(
            sid,
            asset_type=type,
            project_id=int(project_id) if project_id else None,
            q=q,
            readiness=readiness,
            tag=tag,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except AssetError as e:
        return _err(e)
    return _ok(rows)


@router.post(
    "/assets",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AssetResponse],
    responses=_ERRORS,
)
async def create_asset(payload: AssetCreate, auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        return _ok(await _service().create_asset(sid, payload, auth.user_id))
    except AssetError as e:
        return _err(e)


@router.get(
    "/projects/{project_id}/assets",
    response_model=Envelope[List[AssetResponse]],
    responses=_ERRORS,
)
async def list_project_assets(
    project_id: SnowflakePath,
    *,
    auth: AuthDep,
    type: Optional[str] = Query(
        None, pattern="^(character|location|prop|costume|prompt|audio)$"
    ),
):
    try:
        # Guard raises 404/403; _project_gate restates it in this router's shape.
        await _project_gate(project_id, auth, write=False)
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(Projects.owner_id, Projects.team_id).where(
                        Projects.id == int(project_id)
                    )
                )
            ).first()
        if row is None:  # pragma: no cover - the guard above already 404s
            raise AssetError(404, "project_not_found", "Project not found")
        owner_id, team_id = row
        if team_id is None:
            # Personal project (projects.team_id NULL) → the OWNER's personal
            # team, not the caller's. The guard admits collaborators via
            # project_members, and resolving the caller's own team for them
            # would look up assets in the wrong scope and answer
            # {success:true, data:[]} — a wrong answer that reads exactly like
            # an empty library.
            try:
                team_id = int(await _resolve_personal_team_id(str(owner_id)))
            except ValueError:
                # Legacy owner with no personal team row. Letting the ValueError
                # out was an untyped 500 on a read whose honest answer is "this
                # project's asset scope cannot be resolved" — the write half
                # (link_project) has answered that way since P0.
                raise AssetError(
                    422,
                    "personal_team_missing",
                    "Project owner has no personal team; cannot resolve its asset scope",
                )
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


@router.get(
    "/assets/{asset_id}",
    response_model=Envelope[AssetDetailResponse],
    responses=_ERRORS,
)
async def get_asset(asset_id: IdPath, auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        return _ok(await _service().get_asset(asset_id, sid))
    except AssetError as e:
        return _err(e)


@router.patch(
    "/assets/{asset_id}",
    response_model=Envelope[AssetResponse],
    responses=_ERRORS,
)
async def update_asset(
    asset_id: IdPath,
    payload: AssetUpdate,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    """PATCH — omit a field to leave it alone, send ``null`` to clear it
    (``AssetUpdate``). Clearing is limited to the nullable columns; a null aimed
    at a NOT NULL one is a 422 ``field_not_nullable``."""
    try:
        sid = await _gate(scope_id, auth)
        return _ok(await _service().update_asset(asset_id, sid, payload))
    except AssetError as e:
        return _err(e)


@router.delete(
    "/assets/{asset_id}",
    response_model=Envelope[DeletedResponse],
    responses=_ERRORS,
)
async def delete_asset(asset_id: IdPath, auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        await _service().delete_asset(asset_id, sid)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})


# ── files ───────────────────────────────────────────────────────────────────


@router.post(
    "/assets/{asset_id}/files",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[Union[List[AssetFileResponse], AssetFileResponse]],
    responses=_ERRORS,
)
async def attach_files(
    asset_id: IdPath,
    payload: Union[AttachFilesBatchRequest, AttachFileRequest],
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    svc = _service()
    try:
        sid = await _gate(scope_id, auth)
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
            return _ok(out)
        return _ok(await svc.attach_file(asset_id, sid, payload, auth.user_id))
    except AssetError as e:
        return _err(e)


@router.delete(
    "/assets/{asset_id}/files/{resource_id}/{slot}",
    response_model=Envelope[DetachedResponse],
    responses=_ERRORS,
)
async def detach_file(
    asset_id: IdPath,
    resource_id: IdPath,
    slot: str,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        await _service().detach_file(asset_id, sid, resource_id, slot)
    except AssetError as e:
        return _err(e)
    return _ok({"detached": True})


# ── links ───────────────────────────────────────────────────────────────────


@router.post(
    "/assets/{asset_id}/links",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AssetLinkResponse],
    responses=_ERRORS,
)
async def add_link(
    asset_id: IdPath,
    payload: LinkRequest,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        return _ok(await _service().add_link(asset_id, sid, payload))
    except AssetError as e:
        return _err(e)


@router.delete(
    "/assets/{asset_id}/links/{to_asset_id}/{relation}",
    response_model=Envelope[RemovedResponse],
    responses=_ERRORS,
)
async def remove_link(
    asset_id: IdPath,
    to_asset_id: IdPath,
    relation: str,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        await _service().remove_link(asset_id, sid, to_asset_id, relation)
    except AssetError as e:
        return _err(e)
    return _ok({"removed": True})


# ── loadouts ────────────────────────────────────────────────────────────────


@router.post(
    "/assets/{asset_id}/loadouts",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[LoadoutResponse],
    responses=_ERRORS,
)
async def create_loadout(
    asset_id: IdPath,
    payload: LoadoutCreate,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        return _ok(await _service().create_loadout(asset_id, sid, payload))
    except AssetError as e:
        return _err(e)


@router.patch(
    "/assets/{asset_id}/loadouts/{loadout_id}",
    response_model=Envelope[LoadoutResponse],
    responses=_ERRORS,
)
async def update_loadout(
    asset_id: IdPath,
    loadout_id: IdPath,
    payload: LoadoutUpdate,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        return _ok(await _service().update_loadout(asset_id, sid, loadout_id, payload))
    except AssetError as e:
        return _err(e)


@router.delete(
    "/assets/{asset_id}/loadouts/{loadout_id}",
    response_model=Envelope[DeletedResponse],
    responses=_ERRORS,
)
async def delete_loadout(
    asset_id: IdPath,
    loadout_id: IdPath,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        await _service().delete_loadout(asset_id, sid, loadout_id)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})


# ── project refs ────────────────────────────────────────────────────────────


@router.post(
    "/assets/{asset_id}/project-refs",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[LinkedResponse],
    responses=_ERRORS,
)
async def link_project(
    asset_id: IdPath,
    payload: ProjectRefRequest,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        await _project_gate(payload.project_id, auth, write=True)
        await _service().link_project(
            asset_id, sid, int(payload.project_id), auth.user_id
        )
    except AssetError as e:
        return _err(e)
    return _ok({"linked": True})


@router.delete(
    "/assets/{asset_id}/project-refs/{project_id}",
    response_model=Envelope[UnlinkedResponse],
    responses=_ERRORS,
)
async def unlink_project(
    asset_id: IdPath,
    project_id: IdPath,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        await _project_gate(project_id, auth, write=True)
        await _service().unlink_project(asset_id, sid, project_id)
    except AssetError as e:
        return _err(e)
    return _ok({"unlinked": True})
