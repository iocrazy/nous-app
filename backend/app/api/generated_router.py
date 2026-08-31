"""Generated inbox — /generated (spec §6.1).

Scope = ``?scope_id=`` (a ``teams.id`` snowflake; a personal scope is the
user's personal team), gated by team membership. This is deliberately NOT the
personal-only ``_scope()`` of :mod:`app.api.generated_media_router`: the inbox
is a team surface, and resolving the caller's own personal team while they ask
for a team scope would answer ``{"success": true, "data": {"items": []}}`` —
a wrong answer that reads exactly like an empty inbox.

The gate, the envelopes and the id validators are IMPORTED from
:mod:`app.api.assets_router` rather than re-implemented, so the two routers
cannot drift into answering the same failure in two shapes.

Every response body goes through its Pydantic model with
``model_dump(mode="json")``. That is not decoration:
``GeneratedInboxService`` builds items with plain ``model_dump()``, so
``created_at`` is a ``datetime`` object, and ``JSONResponse`` renders with a
bare ``json.dumps`` — handing it the service's dict raises ``TypeError`` at
render time, i.e. a 500 on the happy path.
"""

from __future__ import annotations

import datetime
from typing import Annotated, List, Optional

from fastapi import APIRouter, Query
from pydantic import StringConstraints

from app.api.assets_router import (
    IdPath,
    OptSnowflakeQuery,
    ScopeIdQuery,
    _err,
    _gate,
    _ok,
)
from app.core.deps import AuthDep
from app.schemas.generated import (
    BatchRequest,
    BatchResult,
    CleanupRequest,
    CleanupResponse,
    CountsResponse,
    GeneratedItem,
    GeneratedPage,
    SaveAsAssetRequest,
)
from app.services.assets.assets_service import AssetError
from app.services.library.generated_inbox_service import GeneratedInboxService

router = APIRouter(prefix="/generated", tags=["generated"])

# ``deleted`` is a real ``review_state`` but not a tab — see CountsResponse.
# ``all`` is a router-level alias for "no state filter"; the service takes
# ``None`` for that and accepts no other spelling.
StateQuery = Annotated[str, Query(pattern="^(unreviewed|saved|in_assets|all)$")]

# ``Query(max_length=...)`` on a ``List[str]`` constrains the LIST (how many
# items), never an item — so the bound has to live on the item type. Every
# origin_kind the writers emit is lowercase-with-underscores; anything else is
# a caller typo or an injection probe, and answering it with an empty inbox
# would read exactly like "nothing generated here yet".
OriginKindQuery = Annotated[
    List[
        Annotated[
            str, StringConstraints(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
        ]
    ],
    Query(),
]


def _service() -> GeneratedInboxService:
    return GeneratedInboxService()


@router.get("")
async def list_generated(
    auth: AuthDep,
    scope_id: ScopeIdQuery,
    state: StateQuery = "unreviewed",
    origin_kind: OriginKindQuery = [],
    project_id: OptSnowflakeQuery = None,
    media_kind: Optional[str] = Query(None, max_length=40),
    model: Optional[str] = Query(None, max_length=200),
    since: Optional[datetime.datetime] = Query(None),
    source_asset_id: OptSnowflakeQuery = None,
    cursor: Optional[str] = Query(None, max_length=500),
    limit: int = Query(60, ge=1, le=200),
):
    # A naive ``since`` is read as UTC. Falling through to the DB with the
    # server's local zone would silently shift the window by hours — a wrong
    # answer that looks like a correct one.
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=datetime.timezone.utc)
    try:
        sid = await _gate(scope_id, auth)
        page = await _service().list(
            sid,
            str(scope_id),
            state=None if state == "all" else state,
            # ``[]`` would be an empty IN () at the repo, not "no filter".
            origin_kinds=origin_kind or None,
            project_id=int(project_id) if project_id else None,
            media_kind=media_kind,
            model=model,
            since=since,
            # The asset a run was launched FROM. Validated as a snowflake like
            # every other id query param: a bare str would reach ``int()`` and
            # surface as a 500, and an unvalidated one that silently dropped
            # would answer with the scope's WHOLE inbox under one asset's name.
            source_asset_id=int(source_asset_id) if source_asset_id else None,
            cursor=cursor,
            limit=limit,
        )
    except AssetError as e:
        return _err(e)
    return _ok(GeneratedPage.model_validate(page).model_dump(mode="json"))


@router.get("/counts")
async def counts(auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        out = await _service().counts(sid)
    except AssetError as e:
        return _err(e)
    return _ok(CountsResponse.model_validate(out).model_dump(mode="json"))


@router.post("/batch")
async def batch(payload: BatchRequest, auth: AuthDep, scope_id: ScopeIdQuery):
    """Declared BEFORE ``/{gen_id}/...`` so the literal paths win the match."""
    try:
        sid = await _gate(scope_id, auth)
        out = await _service().batch(payload, sid, auth.user_id)
    except AssetError as e:
        return _err(e)
    return _ok(BatchResult.model_validate(out).model_dump(mode="json"))


@router.post("/cleanup")
async def cleanup(payload: CleanupRequest, auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        out = await _service().cleanup(payload, sid)
    except AssetError as e:
        return _err(e)
    return _ok(CleanupResponse.model_validate(out).model_dump(mode="json"))


@router.post("/{gen_id}/save")
async def save(gen_id: IdPath, auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        row = await _service().save(gen_id, sid, auth.user_id)
    except AssetError as e:
        return _err(e)
    return _ok(GeneratedItem.model_validate(row).model_dump(mode="json"))


@router.post("/{gen_id}/save-as-asset", status_code=201)
async def save_as_asset(
    gen_id: IdPath,
    payload: SaveAsAssetRequest,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    try:
        sid = await _gate(scope_id, auth)
        out = await _service().save_as_asset(gen_id, sid, auth.user_id, payload)
    except AssetError as e:
        return _err(e)
    return _ok(
        {
            "generation": GeneratedItem.model_validate(out["generation"]).model_dump(
                mode="json"
            ),
            "asset_id": out["asset_id"],
            "resource_id": out["resource_id"],
        }
    )


@router.delete("/{gen_id}")
async def delete(gen_id: IdPath, auth: AuthDep, scope_id: ScopeIdQuery):
    try:
        sid = await _gate(scope_id, auth)
        await _service().delete(gen_id, sid)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})
