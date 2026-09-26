# backend/app/api/admin/nous_model_router.py

"""
Admin API for managing Nous platform-provided AI models.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.agent_framework.catalog_windows import refresh_catalog_windows
from app.api.admin.settings_validation import (
    AI_PROVIDER_CARD_LABELS_KEY,
    SettingValidationError,
    validate_setting_value,
)
from app.api.row_guard import require_row
from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.repositories.nous_model_repository import (
    NousModelRepository,
    get_nous_model_repository,
)
from app.schemas.admin_settings_catalog import AdminNousModelDeleteResult
from app.schemas.ai import TestConnectionResponse
from app.schemas.nous_model import (
    CardLabelsResponse,
    CardLabelsUpdate,
    NousEngineSyncResponse,
    NousEngineSyncSkipped,
    NousModelCreate,
    NousModelProbeRequest,
    NousModelResponse,
    NousModelTestResponse,
    NousModelUpdate,
    ProviderProtocolItem,
    ProviderProtocolListResponse,
)
from app.services.ai.engine_catalog import (
    NOUS_ENGINE_PROVIDER,
    engine_credential,
    engine_presence,
    snapshots_for,
)
from app.services.ai.model_pricing_coverage import (
    load_priced_models,
    price_coverage_for,
)
from app.services.ai.nous_engine_sync import (
    engine_endpoints,
    merge_reports,
    sync_all_engines,
)

# Shared probe — same implementation the scheduled health poll uses. Aliased to
# the historical private name so existing patch targets keep working.
from app.services.ai.nous_model_health import probe_nous_model as _probe_nous_model
from app.services.ai.nous_model_health import (
    probe_result_status as _probe_result_status,
)
from app.services.ai.providers.ai_provider import AIProviderFactory
from app.utils.admin_helpers import create_audit_log

router = APIRouter()


def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _to_response(
    row: dict,
    *,
    price_coverage: str | None = None,
    engine: tuple[str, bool | None] | None = None,
) -> NousModelResponse:
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
        context_window_tokens=row.get("context_window_tokens"),
        price_coverage=price_coverage,
        engine_status=engine[0] if engine else None,
        engine_ready=engine[1] if engine else None,
    )


async def _engine_overlay(rows: list[dict]) -> dict[str, tuple[str, bool | None]]:
    """``name → (engine_status, engine_ready)`` for every nous-engine row, read
    with each row's own credential (one read per distinct credential). Rows of
    other providers are absent. Never raises."""
    credentials = {
        r["name"]: engine_credential(r)
        for r in rows
        if r.get("actual_provider") == NOUS_ENGINE_PROVIDER
    }
    snapshots = await snapshots_for(credentials)
    by_name = {r["name"]: r for r in rows}
    return {
        name: engine_presence(snap, str(by_name[name].get("actual_model") or ""))
        for name, snap in snapshots.items()
    }


@router.get("", response_model=List[NousModelResponse])
async def list_nous_models(auth: AdminAuthDep):
    """List all Nous models (including disabled)."""
    repo = get_nous_model_repository()
    rows = await repo.list_all()
    priced = await load_priced_models()
    engine = await _engine_overlay(rows)
    return [
        _to_response(
            r,
            price_coverage=price_coverage_for(r, priced),
            engine=engine.get(r["name"]),
        )
        for r in rows
    ]


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


async def _read_card_labels() -> dict[str, str]:
    """Stored card names; a row that fails validation reads as no names.

    The page falls back to the protocol label, so garbage here costs a
    custom name, never the page."""
    raw = await get_system_settings_repository().get_value(AI_PROVIDER_CARD_LABELS_KEY)
    if raw is None:
        return {}
    try:
        return validate_setting_value(AI_PROVIDER_CARD_LABELS_KEY, raw)
    except SettingValidationError as exc:
        logger.error(f"[Admin] stored {AI_PROVIDER_CARD_LABELS_KEY} is invalid: {exc}")
        return {}


@router.get("/card-labels", response_model=CardLabelsResponse)
async def get_card_labels(auth: AdminAuthDep):
    """Admin-chosen provider card names, keyed ``"<provider>|<base_url>"``."""
    return CardLabelsResponse(labels=await _read_card_labels())


@router.put("/card-labels", response_model=CardLabelsResponse)
async def update_card_labels(body: CardLabelsUpdate, auth: AdminAuthDep):
    """Merge ``body.labels`` into the stored names; a blank name removes one.

    A patch rather than a full replace, so two admins renaming different
    cards do not overwrite each other."""
    # Blank names in the patch survive the merge and are then dropped by the
    # validator, which is what makes "" mean "remove this override".
    merged_raw = {**await _read_card_labels(), **body.labels}
    try:
        merged = validate_setting_value(AI_PROVIDER_CARD_LABELS_KEY, merged_raw)
    except SettingValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "setting_invalid", "key": exc.key, "reason": exc.reason},
        ) from exc
    await get_system_settings_repository().upsert_setting(
        AI_PROVIDER_CARD_LABELS_KEY, merged, auth.user_id
    )
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_setting",
        target_type="system_setting",
        target_id=AI_PROVIDER_CARD_LABELS_KEY,
        details={"patch": body.labels},
    )
    logger.info(f"[Admin] card labels updated by {auth.user_id}: {sorted(body.labels)}")
    return CardLabelsResponse(labels=merged)


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


@router.post("/sync-engine", response_model=NousEngineSyncResponse)
async def sync_nous_engine_models(auth: AdminAuthDep):
    """Create catalog rows for the services nous-engine lists and the
    catalog lacks, now.

    Every listed service without a row gets ``nous-<id>`` (credentials copied
    from an existing engine row, zero price row); existing rows take a newer
    ``context_window``. Nothing is disabled or re-statused: availability is
    read live from the engine (spec 2026-09-25 §3.4). This button is the only
    thing that creates engine rows.

    400 ``no_engine_row`` when no enabled ``actual_provider='nous'`` row exists
    to take the endpoint and key from. An engine that cannot be read is NOT a
    5xx: the report comes back with ``error`` set (admin-only text, same as
    the probe endpoints; a 5xx would be scrubbed by the error shell).
    """
    repo = get_nous_model_repository()
    rows = await repo.list_all()
    if not engine_endpoints(rows):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_engine_row",
                "message": (
                    "No enabled nous-engine model to take the endpoint and key "
                    "from. Add one model on the nous provider first."
                ),
            },
        )
    results = await sync_all_engines(rows)
    report = merge_reports([r for _, r in results])
    if report.created or report.updated:
        await refresh_catalog_windows()
    logger.info(
        f"[Admin] nous-engine sync: discovered={report.discovered} "
        f"created={len(report.created)} updated={len(report.updated)} "
        f"skipped={len(report.skipped)} error={report.error!r}"
    )
    return NousEngineSyncResponse(
        discovered=report.discovered,
        created=list(report.created),
        updated=list(report.updated),
        skipped=[
            NousEngineSyncSkipped(id=s.id, reason=s.reason) for s in report.skipped
        ],
        error=report.error,
    )


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
    """Create a new Nous model.

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
    logger.info(f"[Admin] Created Nous model: {body.name}")
    # This process's window cache (mig 500); other processes reload on TTL.
    await refresh_catalog_windows()
    return _to_response(row)


@router.put("/{model_id}", response_model=NousModelResponse)
async def update_nous_model(model_id: str, body: NousModelUpdate, auth: AdminAuthDep):
    """Update a Nous model."""
    repo = get_nous_model_repository()
    updates = body.model_dump(exclude_none=True, exclude={"clear_context_window"})
    # exclude_none can never write NULL; the explicit flag can (the schema
    # already rejected it arriving together with a value).
    if body.clear_context_window:
        updates["context_window_tokens"] = None
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if updates.get("name"):
        await _reject_name_collision(repo, updates["name"], own_id=model_id)
    row = await repo.update(model_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Updated Nous model: {model_id}")
    await refresh_catalog_windows()
    return _to_response(row)


@router.delete("/{model_id}", response_model=AdminNousModelDeleteResult)
async def delete_nous_model(model_id: str, auth: AdminAuthDep):
    """Delete a Nous model. A non-numeric id or one that matches no row is a
    typed 404 (it used to answer 200 "Deleted" for a missing row)."""
    if not (model_id.isascii() and model_id.isdigit() and int(model_id) < 2**63):
        require_row(None)
    repo = get_nous_model_repository()
    if not await repo.delete(model_id):
        require_row(None)
    logger.info(f"[Admin] Deleted Nous model: {model_id}")
    await refresh_catalog_windows()
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
