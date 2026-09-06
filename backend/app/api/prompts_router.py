"""GET /prompts — the unified prompt catalog (spec 2026-09-05 §3.1).

Same gate, same envelope, same error shape as /assets: the two are read by the
same pages and a client must not need two error parsers.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.api.assets_router import OptSnowflakeQuery, ScopeIdQuery, _err
from app.api.assets_router import _is_member as _assets_is_member
from app.api.assets_router import _ok
from app.core.deps import AuthDep
from app.schemas.assets import Envelope, ErrorEnvelope
from app.schemas.prompts import PromptCounts, PromptPage
from app.services.assets.assets_service import AssetError
from app.services.prompts.catalog_service import PromptCatalogService

router = APIRouter(tags=["prompts"])
_ERRORS = {403: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}}


def _service() -> PromptCatalogService:
    return PromptCatalogService()


async def _is_member(scope_id: str, user_id: str) -> bool:
    """Module-local indirection so THIS router's tests can patch the gate."""
    return await _assets_is_member(scope_id, user_id)


async def _gate(scope_id: str, auth) -> int:
    if not await _is_member(scope_id, auth.user_id):
        raise AssetError(403, "not_a_member", "You are not a member of this scope")
    return int(scope_id)


@router.get("/prompts", response_model=Envelope[PromptPage], responses=_ERRORS)
async def list_prompts(
    auth: AuthDep,
    scope_id: ScopeIdQuery,
    segment: str = Query("mine", pattern="^(mine|project|system)$"),
    project_id: OptSnowflakeQuery = None,
    form: Optional[str] = Query(None, pattern="^(template|image|album)$"),
    origin: Optional[str] = Query(None, pattern="^(typed|extracted|captioned)$"),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        sid = await _gate(scope_id, auth)
        page = await _service().list(
            sid,
            segment=segment,
            project_id=int(project_id) if project_id else None,
            form=form,
            origin=origin,
            q=q,
            limit=limit,
            offset=offset,
        )
    except AssetError as e:
        return _err(e)
    return _ok(page)


@router.get("/prompts/counts", response_model=Envelope[PromptCounts], responses=_ERRORS)
async def prompt_counts(
    auth: AuthDep,
    scope_id: ScopeIdQuery,
    project_id: OptSnowflakeQuery = None,
):
    try:
        sid = await _gate(scope_id, auth)
        data = await _service().counts(
            sid, project_id=int(project_id) if project_id else None
        )
    except AssetError as e:
        return _err(e)
    return _ok(data)
