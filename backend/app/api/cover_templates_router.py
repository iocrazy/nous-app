"""Cover-template library — the 样图模板库 behind Cover Studio (migration 435).

Routes (all under prefix /cover-templates, registered in app/api/__init__.py):
  GET    /cover-templates              → {data: {items: [...]}}
  POST   /cover-templates              → {data: row}   (idempotent per image)
  PATCH  /cover-templates/{id}         → {data: row}   (rename only)
  DELETE /cover-templates/{id}         → {data: {deleted: bool}}
  POST   /cover-templates/use          → {data: {counted: int}}

Scope = the caller's personal team, resolved exactly like generated_media's
router does (``_resolve_personal_team_id``), because a template's image lives
in that same scope and the two must not disagree.

A template never owns bytes. Both creation paths hand us a generated_media id
that the caller already obtained from ``/generated-media/import`` (upload) or
``/generated-media/import-from-resource`` (library pick). That is what makes
the resulting ``image_url`` guaranteed-readable by the generation bridge —
see migration 435 for why any other anchor fails silently.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.core.deps import AuthDep
from app.db.scope import Scope, request_scope
from app.repositories.cover_templates_repository import CoverTemplatesRepository
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.schemas.cover_template import (
    CoverTemplateCreateFromMedia,
    CoverTemplateRename,
    CoverTemplateUseRequest,
)
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(prefix="/cover-templates", tags=["cover-templates"])


async def _scope(auth) -> int:
    """Resolve the caller's personal team id as an int scope key."""
    return int(await _resolve_personal_team_id(str(auth.user_id)))


def _as_int(value: str, field: str) -> int:
    """Snowflake string → int, with a 400 instead of a 500 on garbage."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be a snowflake id")


def _present(row: dict) -> dict:
    """Repository row → wire shape, adding the derived image URL.

    ``image_url`` is computed here rather than stored: it is a route, and a
    stored route is a stale route the day the prefix changes. It is also the
    exact string the generation bridge accepts (``/cover`` for images), so the
    frontend never has to build it and never has to know the rule.
    """
    return {
        "id": row["id"],
        "name": row["name"],
        "generated_media_id": row["generated_media_id"],
        "image_url": f"/api/v1/generated-media/{row['generated_media_id']}/cover",
        "source_kind": row["source_kind"],
        "source_resource_id": row.get("source_resource_id"),
        "usage_count": row["usage_count"],
        "last_used_at": row.get("last_used_at"),
        "created_at": row["created_at"],
    }


@router.get("")
async def list_cover_templates(auth: AuthDep) -> dict:
    scope_id = await _scope(auth)
    rows = await CoverTemplatesRepository().list_for_scope(scope_id)
    return {"data": {"items": [_present(r) for r in rows]}}


@router.post("")
async def create_cover_template(
    payload: CoverTemplateCreateFromMedia, auth: AuthDep
) -> dict:
    """Save a picture as a template.

    Idempotent per image: adding one you already saved returns the existing
    row instead of a 409. Two identical cards differing only in name is not a
    state the user asked for, and an error on a button that visibly did
    nothing wrong is worse than a no-op that shows them the card they meant.
    """
    scope_id = await _scope(auth)
    gen_id = _as_int(payload.generated_media_id, "generated_media_id")
    repo = CoverTemplatesRepository()

    # The image must exist AND be in the caller's scope. Skipping this check
    # would surface as a raw FK IntegrityError (500) for a missing row, and —
    # worse — would let a caller mint a template over someone else's image by
    # guessing a snowflake, since the FK itself does not check scope.
    media = await GeneratedMediaRepository().get(gen_id, scope_id)
    if not media:
        raise HTTPException(status_code=404, detail="generated media not found")
    if (media.get("media_kind") or "") != "image":
        raise HTTPException(
            status_code=400, detail="only image generations can be cover templates"
        )

    existing = await repo.find_by_media(scope_id, gen_id)
    if existing:
        return {"data": _present(existing)}

    source_resource_id = (
        _as_int(payload.source_resource_id, "source_resource_id")
        if payload.source_resource_id
        else None
    )
    async with request_scope(Scope(user_id=str(auth.user_id))):
        row = await repo.create(
            scope_id=scope_id,
            creator_id=str(auth.user_id),
            name=payload.name,  # 已由 schema 的验证器 trim 过，此处不再二次处理
            generated_media_id=gen_id,
            source_kind=payload.source_kind,
            source_resource_id=source_resource_id,
        )
    return {"data": _present(row)}


@router.patch("/{template_id}")
async def rename_cover_template(
    payload: CoverTemplateRename,
    auth: AuthDep,
    template_id: str = Path(...),
) -> dict:
    scope_id = await _scope(auth)
    async with request_scope(Scope(user_id=str(auth.user_id))):
        row = await CoverTemplatesRepository().rename(
            _as_int(template_id, "template_id"), scope_id, payload.name
        )
    if not row:
        raise HTTPException(status_code=404, detail="cover template not found")
    return {"data": _present(row)}


@router.delete("/{template_id}")
async def delete_cover_template(auth: AuthDep, template_id: str = Path(...)) -> dict:
    """Remove the template. The picture it cited is left alone.

    Hard delete on purpose — see migration 435. A soft delete would keep the
    RESTRICT foreign key alive and make the image permanently undeletable,
    blocked by a row the user can no longer see anywhere.
    """
    scope_id = await _scope(auth)
    async with request_scope(Scope(user_id=str(auth.user_id))):
        deleted = await CoverTemplatesRepository().delete(
            _as_int(template_id, "template_id"), scope_id
        )
    return {"data": {"deleted": deleted}}


@router.post("/use")
async def mark_cover_templates_used(
    payload: CoverTemplateUseRequest, auth: AuthDep
) -> dict:
    """Bump usage counts after a generation was dispatched with these templates.

    Best-effort by design: the counter drives sort order and a "used N×"
    caption, so losing one tick is cosmetic. It must never be able to fail the
    generation that triggered it — which is why it is a separate call the
    frontend makes after dispatch succeeds, not a step inside dispatch.
    """
    scope_id = await _scope(auth)
    ids = [_as_int(t, "template_ids") for t in payload.template_ids]
    async with request_scope(Scope(user_id=str(auth.user_id))):
        await CoverTemplatesRepository().bump_usage(ids, scope_id)
    return {"data": {"counted": len(ids)}}
