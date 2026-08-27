"""Cover-template library — a system folder in the resource library (mig 441).

Routes (prefix /cover-templates, registered in app/api/__init__.py):
  GET  /cover-templates/folder  → {data: {folder_id, name, adopted}}
  GET  /cover-templates         → {data: {folder: {...}, items: [...]}}
  POST /cover-templates/use     → {data: {counted: int}}

There is deliberately NO create / rename / delete here. Membership is the
folder: upload into it, drag into it, remove from it — all through the library
the user already knows. Cover Studio only READS the folder and counts usage.

Scope = the caller's personal team, same resolver generated_media uses.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.db.scope import Scope, request_scope
from app.repositories.cover_templates_repository import CoverTemplatesRepository
from app.schemas.cover_template import CoverTemplateUseRequest
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(prefix="/cover-templates", tags=["cover-templates"])


async def _scope(auth) -> int:
    return int(await _resolve_personal_team_id(str(auth.user_id)))


def _as_int(value: str, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be a snowflake id")


def _present(item: dict) -> dict:
    return {
        "resource_id": item["resource_id"],
        "name": item["name"],
        "mime_type": item.get("mime_type"),
        "thumb_url": f"/api/v1/resources/{item['resource_id']}/cover",
        "usage_count": item["usage_count"],
        "last_used_at": item.get("last_used_at"),
    }


@router.get("/folder")
async def get_cover_template_folder(auth: AuthDep) -> dict:
    """The scope's template folder — created or adopted on first call."""
    scope_id = await _scope(auth)
    async with request_scope(Scope(user_id=str(auth.user_id))):
        folder = await CoverTemplatesRepository().ensure_folder(
            scope_id, str(auth.user_id)
        )
    return {
        "data": {
            "folder_id": folder["id"],
            "name": folder["name"],
            "adopted": folder["adopted"],
        }
    }


@router.get("")
async def list_cover_templates(auth: AuthDep) -> dict:
    scope_id = await _scope(auth)
    repo = CoverTemplatesRepository()
    async with request_scope(Scope(user_id=str(auth.user_id))):
        folder = await repo.ensure_folder(scope_id, str(auth.user_id))
        items = await repo.list_images(scope_id, int(folder["id"]))
    return {
        "data": {
            "folder": {
                "folder_id": folder["id"],
                "name": folder["name"],
                "adopted": folder["adopted"],
            },
            "items": [_present(i) for i in items],
        }
    }


@router.post("/use")
async def mark_cover_templates_used(
    payload: CoverTemplateUseRequest, auth: AuthDep
) -> dict:
    """Bump usage after a generation was dispatched with these templates.

    Best-effort by design — the counter only drives ordering, and it must never
    be able to fail the generation that triggered it, which is why the frontend
    calls it AFTER dispatch succeeds, not as a step inside dispatch.
    """
    scope_id = await _scope(auth)
    ids = [_as_int(r, "resource_ids") for r in payload.resource_ids]
    async with request_scope(Scope(user_id=str(auth.user_id))):
        await CoverTemplatesRepository().bump_usage(ids, scope_id)
    return {"data": {"counted": len(ids)}}
