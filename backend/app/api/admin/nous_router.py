# backend/app/api/admin/nous_router.py

"""
Admin API for managing Nous platform-provided AI models.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.nous_repository import get_nous_repository
from app.schemas.ai import TestConnectionResponse
from app.schemas.nous import (
    NousModelCreate,
    NousModelResponse,
    NousModelUpdate,
    NousProbeRequest,
)
from app.services.ai.providers.ai_provider import AIProviderFactory

router = APIRouter()


def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _to_response(row: dict) -> NousModelResponse:
    """Convert DB row to admin response with masked API key."""
    return NousModelResponse(
        id=str(row["id"]),
        name=row["name"],
        display_name=row["display_name"],
        type=row["type"],
        description=row.get("description"),
        actual_provider=row["actual_provider"],
        actual_model=row["actual_model"],
        api_key_masked=_mask_key(row.get("api_key", "")),
        app_id=row.get("app_id"),
        base_url=row.get("base_url"),
        pricing_type=row["pricing_type"],
        pricing_value=float(row["pricing_value"]),
        is_enabled=row["is_enabled"],
        sort_order=row["sort_order"],
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )


@router.get("", response_model=List[NousModelResponse])
async def list_nous_models(auth: AdminAuthDep):
    """List all Nous models (including disabled)."""
    repo = get_nous_repository()
    rows = await repo.list_all()
    return [_to_response(r) for r in rows]


@router.post("/probe-models", response_model=TestConnectionResponse)
async def probe_nous_models(body: NousProbeRequest, auth: AdminAuthDep):
    """Self-check a provider key and return the models it exposes.

    Lets the admin pick ``actual_model`` from a fetched list instead of
    hand-typing it. Reuses the shared provider test-connection (which calls
    the provider's ``/v1/models``). Providers without an OpenAI-compatible
    model catalog (e.g. volcengine ASR) return ``success=False`` — the UI then
    falls back to manual model entry. The platform key is used server-side
    only and never echoed back.

    Edit-form fallback: the stored key is never sent to the client, so the
    edit form's API Key field is blank. When ``api_key`` is blank and ``name``
    references an existing model, reuse that model's stored key (and app_id)
    so probing works without re-typing the key.
    """
    api_key = (body.api_key or "").strip()
    app_id = body.app_id or ""
    if not api_key and body.name:
        repo = get_nous_repository()
        existing = await repo.get_by_name(body.name)
        if existing:
            api_key = existing.get("api_key") or ""
            app_id = app_id or existing.get("app_id") or ""

    result = await AIProviderFactory.test_connection(
        provider_key=body.provider_key,
        config={
            "api_key": api_key,
            "app_id": app_id,
            "base_url": body.base_url,
            "model": body.model,
        },
    )
    return TestConnectionResponse(**result)


@router.post("", response_model=NousModelResponse)
async def create_nous_model(body: NousModelCreate, auth: AdminAuthDep):
    """Create a new Nous model.

    Provider-card UX: a blank ``api_key`` inherits the key (and app_id) from an
    existing model on the same ``actual_provider`` + ``base_url`` — so the admin
    enters the key once per provider and adds more models without re-typing it.
    """
    repo = get_nous_repository()
    data = body.model_dump()
    if not (data.get("api_key") or "").strip():
        base_url = data.get("base_url") or ""
        rows = await repo.list_all()
        sibling = next(
            (
                r
                for r in rows
                if r.get("actual_provider") == data.get("actual_provider")
                and (r.get("base_url") or "") == base_url
                and (r.get("api_key") or "").strip()
            ),
            None,
        )
        if not sibling:
            raise HTTPException(
                status_code=400,
                detail=(
                    "API key required — no existing model on this provider to "
                    "inherit the key from."
                ),
            )
        data["api_key"] = sibling["api_key"]
        if not (data.get("app_id") or "") and sibling.get("app_id"):
            data["app_id"] = sibling["app_id"]

    row = await repo.create(data)
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create model")
    logger.info(f"[Admin] Created Nous model: {body.name}")
    return _to_response(row)


@router.put("/{model_id}", response_model=NousModelResponse)
async def update_nous_model(model_id: str, body: NousModelUpdate, auth: AdminAuthDep):
    """Update a Nous model."""
    repo = get_nous_repository()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = await repo.update(model_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Updated Nous model: {model_id}")
    return _to_response(row)


@router.delete("/{model_id}")
async def delete_nous_model(model_id: str, auth: AdminAuthDep):
    """Delete a Nous model."""
    repo = get_nous_repository()
    ok = await repo.delete(model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Deleted Nous model: {model_id}")
    return {"message": "Deleted"}
