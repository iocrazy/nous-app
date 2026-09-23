# backend/app/api/admin/nous_model_router.py

"""
Admin API for managing Nous platform-provided AI models.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.nous_model_repository import (
    NousModelRepository,
    get_nous_model_repository,
)
from app.schemas.ai import TestConnectionResponse
from app.schemas.nous_model import (
    NousModelCreate,
    NousModelProbeRequest,
    NousModelResponse,
    NousModelTestResponse,
    NousModelUpdate,
    ProviderProtocolItem,
    ProviderProtocolListResponse,
)
from app.services.ai.model_pricing_coverage import (
    load_priced_models,
    price_coverage_for,
)

# Shared probe — same implementation the scheduled health poll uses. Aliased to
# the historical private name so existing patch targets keep working.
from app.services.ai.nous_model_health import probe_nous_model as _probe_nous_model
from app.services.ai.nous_model_health import (
    probe_result_status as _probe_result_status,
)
from app.services.ai.providers.ai_provider import AIProviderFactory

router = APIRouter()


def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _to_response(row: dict, *, price_coverage: str | None = None) -> NousModelResponse:
    """Convert DB row to admin response with masked API key.

    ``price_coverage`` is computed by the list endpoint (one price-table read
    for the whole page); single-row responses leave it ``None`` and the admin
    page refetches the list after every mutation anyway.
    """
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
        # str() not a bare pass-through: asyncpg yields uuid.UUID here and the
        # admin page treats it as text.
        owner_user_id=(str(row["owner_user_id"]) if row.get("owner_user_id") else None),
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
        last_test_status=row.get("last_test_status"),
        last_test_detail=row.get("last_test_detail"),
        last_tested_at=(
            str(row["last_tested_at"]) if row.get("last_tested_at") else None
        ),
        price_coverage=price_coverage,
    )


@router.get("", response_model=List[NousModelResponse])
async def list_nous_models(auth: AdminAuthDep):
    """List all Nous models (including disabled)."""
    repo = get_nous_model_repository()
    rows = await repo.list_all()
    priced = await load_priced_models()
    return [_to_response(r, price_coverage=price_coverage_for(r, priced)) for r in rows]


@router.get("/protocols", response_model=ProviderProtocolListResponse)
async def list_provider_protocols(auth: AdminAuthDep):
    """Provider protocols the platform can dispatch to (single source of
    truth for the admin ``actual_provider`` dropdown). Read-only."""
    from app.services.ai.provider_protocols import all_protocols

    return ProviderProtocolListResponse(
        protocols=[
            ProviderProtocolItem(
                key=p.key,
                label=p.label,
                description=p.description,
                model_types=list(p.model_types),
                aliases=list(p.aliases),
                is_default=p.is_default,
                credential_kind=p.credential_kind,
            )
            for p in all_protocols()
        ]
    )


@router.post("/probe-models", response_model=TestConnectionResponse)
async def probe_nous_models(body: NousModelProbeRequest, auth: AdminAuthDep):
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
        repo = get_nous_model_repository()
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


async def _reject_name_collision(
    repo: NousModelRepository, name: str, own_id: str | None
) -> None:
    """409 when ``name`` — or its ``mediahub-`` ↔ ``nous-`` rename twin — is
    already taken by a DIFFERENT row.

    Rejected rather than allowed: a ``nous-X`` created next to a live
    ``mediahub-X`` would make the pending rename migration collide on the
    unique ``name`` index, and until then every by-name lookup of either
    spelling would split between two rows depending on which one was typed.
    ``get_by_name`` already resolves exact-then-alias, so one lookup answers
    both questions. Renaming a row to its own twin (``own_id`` matches) is the
    rename itself and is allowed.
    """
    taken = await repo.get_by_name(name)
    if not taken or (own_id is not None and str(taken.get("id")) == str(own_id)):
        return
    taken_name = taken.get("name") or name
    if taken_name == name:
        detail = f"A model named '{name}' already exists."
    else:
        detail = (
            f"'{name}' would collide with the existing model '{taken_name}' "
            "once legacy mediahub-* names are renamed to nous-*. Edit that "
            "model instead, or pick a different name."
        )
    raise HTTPException(status_code=409, detail=detail)


@router.post("", response_model=NousModelResponse)
async def create_nous_model(body: NousModelCreate, auth: AdminAuthDep):
    """Create a new Mediahub model.

    Provider-card UX: a blank ``api_key`` inherits the key (and app_id) from an
    existing model on the same ``actual_provider`` + ``base_url`` — so the admin
    enters the key once per provider and adds more models without re-typing it.
    """
    repo = get_nous_model_repository()
    await _reject_name_collision(repo, body.name, own_id=None)
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
    logger.info(f"[Admin] Created Mediahub model: {body.name}")
    return _to_response(row)


@router.put("/{model_id}", response_model=NousModelResponse)
async def update_nous_model(model_id: str, body: NousModelUpdate, auth: AdminAuthDep):
    """Update a Mediahub model."""
    repo = get_nous_model_repository()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if updates.get("name"):
        await _reject_name_collision(repo, updates["name"], own_id=model_id)
    row = await repo.update(model_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Updated Mediahub model: {model_id}")
    return _to_response(row)


@router.delete("/{model_id}")
async def delete_nous_model(model_id: str, auth: AdminAuthDep):
    """Delete a Mediahub model."""
    repo = get_nous_model_repository()
    ok = await repo.delete(model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Deleted Mediahub model: {model_id}")
    return {"message": "Deleted"}


@router.post("/{model_id}/test", response_model=NousModelTestResponse)
async def test_nous_model(model_id: str, auth: AdminAuthDep):
    """Run a real connectivity probe for one platform model (chat/embedding/asr).

    The result is persisted on the row (last_test_status / detail / tested_at)
    so the admin's status dot + "last tested" hint survive navigation.

    Types the probe has no protocol for (video / tts) are persisted as
    ``not_probed`` rather than ``fail`` — see ``PROBEABLE_TYPES``.

    Image rows ARE probed here, unlike on the hourly poll: this endpoint fires
    once, on an explicit click, so it can afford a real generation. The probe
    also measures the produced image and fails the row when the aspect ratio it
    asked for was not honored.
    """
    repo = get_nous_model_repository()
    rows = await repo.list_all()
    row = next((r for r in rows if str(r.get("id")) == str(model_id)), None)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    # The one caller that opts into a costly probe: an admin clicked Test on
    # this specific row. The hourly poll never does — see ``allow_costly``.
    result = await _probe_nous_model(row, allow_costly=True)

    # Shared with the hourly poll: two hand-written copies of this mapping would
    # drift, and a manual Test that wrote ``fail`` where the poll writes
    # ``not_probed`` would repaint every unprobeable model red on one click.
    status = _probe_result_status(result)
    detail = result.get("detail") or (result.get("error") or "")
    saved = await repo.record_test_result(
        model_id, status, detail[:200], result.get("code")
    )
    tested_at = (
        str(saved["last_tested_at"]) if saved and saved.get("last_tested_at") else None
    )
    return NousModelTestResponse(**result, tested_at=tested_at)
