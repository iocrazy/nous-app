"""Admin API routes for System Settings management."""

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.schemas.admin import (
    AIGovernanceResponse,
    AIGovernanceUpdate,
    ChatModuleGovernanceResponse,
    GraphMemorySettingsResponse,
    GraphMemorySettingsUpdate,
    SystemSettingResponse,
    SystemSettingUpdate,
    TaskModuleGovernanceResponse,
)
from app.services.ai.memory.graph_memory import STRUCTURED_OUTPUT_MODES
from app.utils.admin_helpers import create_audit_log

router = APIRouter()

# Admin Memory-panel field → system_settings key. Mirrors the keys
# GraphMemoryConfig.from_settings reads (graph_memory.py::_SETTINGS_MAP); the
# 291 migration seeds them so repo.update() (which requires an existing row)
# works without an upsert.
_GRAPH_FIELD_TO_KEY = {
    "enabled": "graph_memory_enabled",
    "falkordb_host": "graph_falkordb_host",
    "falkordb_port": "graph_falkordb_port",
    "falkordb_database": "graph_falkordb_database",
    "extractor_base_url": "graph_extractor_base_url",
    "extractor_api_key": "graph_extractor_api_key",
    "extractor_model": "graph_extractor_model",
    "extractor_structured_output_mode": "graph_extractor_structured_output_mode",
    "embedder_base_url": "graph_embedder_base_url",
    "embedder_api_key": "graph_embedder_api_key",
    "embedder_model": "graph_embedder_model",
}
_TRUTHY = {"1", "true", "yes", "on"}
_SECRET_FIELDS = {"extractor_api_key", "embedder_api_key"}


def _to_response(row: dict) -> SystemSettingResponse:
    return SystemSettingResponse(
        key=row["key"],
        value=row["value"],
        description=row.get("description"),
        updated_at=row["updated_at"],
        updated_by=row.get("updated_by"),
    )


@router.get("", response_model=list[SystemSettingResponse])
async def list_settings(auth: AdminAuthDep):
    """List all system settings (excluding transcode_*, managed elsewhere)."""
    repo = get_system_settings_repository()
    rows = await repo.list_non_transcode()
    return [_to_response(r) for r in rows]


async def _read_graph_settings() -> GraphMemorySettingsResponse:
    """Build the masked graph-memory bundle from the seeded graph_* keys."""
    repo = get_system_settings_repository()
    rows = await repo.list_non_transcode()
    values = {r["key"]: r.get("value") for r in rows}

    def s(field: str) -> str:
        raw = values.get(_GRAPH_FIELD_TO_KEY[field])
        return "" if raw is None else str(raw)

    return GraphMemorySettingsResponse(
        enabled=s("enabled").strip().lower() in _TRUTHY,
        falkordb_host=s("falkordb_host"),
        falkordb_port=s("falkordb_port") or "6379",
        falkordb_database=s("falkordb_database") or "mediahub_memory",
        extractor_base_url=s("extractor_base_url"),
        extractor_model=s("extractor_model"),
        extractor_structured_output_mode=(
            s("extractor_structured_output_mode") or "json_object"
        ),
        extractor_api_key_set=bool(s("extractor_api_key").strip()),
        embedder_base_url=s("embedder_base_url"),
        embedder_model=s("embedder_model"),
        embedder_api_key_set=bool(s("embedder_api_key").strip()),
    )


@router.get("/graph-memory", response_model=GraphMemorySettingsResponse)
async def get_graph_memory_settings(auth: AdminAuthDep):
    """Graphiti graph-memory config for the admin Memory panel (api keys masked)."""
    return await _read_graph_settings()


@router.put("/graph-memory", response_model=GraphMemorySettingsResponse)
async def update_graph_memory_settings(
    update: GraphMemorySettingsUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Write only the provided graph_* keys. API-key fields are written only
    when a value is sent (omit to keep the stored key). Returns the masked
    bundle — the raw keys never leave the server."""
    repo = get_system_settings_repository()
    data = update.model_dump(exclude_unset=True)

    # Reject an unknown structured-output mode up front (422) — a bad value would
    # otherwise persist and silently break the extractor LLM client at run time.
    mode = data.get("extractor_structured_output_mode")
    if mode is not None and mode not in STRUCTURED_OUTPUT_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "extractor_structured_output_mode must be one of "
                f"{list(STRUCTURED_OUTPUT_MODES)}"
            ),
        )

    written: list[str] = []
    for field, key in _GRAPH_FIELD_TO_KEY.items():
        if field not in data or data[field] is None:
            continue
        value = data[field]
        if field == "enabled":
            value = "true" if value else "false"
        await repo.update(key, value, auth.user_id)
        written.append(field)

    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_graph_memory_settings",
        target_type="system_setting",
        target_id="graph_memory",
        # never log raw secret values — only which fields changed
        details={"fields": [f for f in written if f not in _SECRET_FIELDS]},
        ip_address=client_ip,
    )
    logger.info(f"Graph-memory settings updated by admin {auth.user_id}: {written}")
    return await _read_graph_settings()


# ── AI Governance ────────────────────────────────────────────────────────────

# Governed module names (must match ai_governance.py)
_TASK_MODULE_NAMES = [
    "transcription",
    "translation",
    "visual_analysis",
    "caption",
    "classification",
]
_ALL_MODULE_NAMES = ["chat"] + _TASK_MODULE_NAMES

# system_settings keys that hold api_key material — never logged in audit.
_GOVERNANCE_SECRET_KEYS = {f"ai_module.{m}.api_key" for m in _TASK_MODULE_NAMES}


async def _read_governance_settings() -> AIGovernanceResponse:
    """Build the masked governance bundle from system_settings rows."""
    repo = get_system_settings_repository()
    rows = await repo.list_non_transcode()
    # Build a lookup of all ai_module.* values (raw JSONB).
    data: dict = {
        r["key"]: r.get("value")
        for r in rows
        if isinstance(r.get("key"), str) and r["key"].startswith("ai_module.")
    }

    def get_bool(key: str) -> bool:
        v = data.get(key)
        if isinstance(v, bool):
            return v
        # Absent (None) or unexpected type → True (default-open).
        return True

    def get_str(key: str) -> str:
        v = data.get(key)
        return str(v).strip() if v is not None else ""

    return AIGovernanceResponse(
        chat=ChatModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.chat.user_allowed"),
        ),
        transcription=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.transcription.user_allowed"),
            base_url=get_str("ai_module.transcription.base_url"),
            model=get_str("ai_module.transcription.model"),
            api_key_set=bool(get_str("ai_module.transcription.api_key")),
        ),
        translation=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.translation.user_allowed"),
            base_url=get_str("ai_module.translation.base_url"),
            model=get_str("ai_module.translation.model"),
            api_key_set=bool(get_str("ai_module.translation.api_key")),
        ),
        visual_analysis=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.visual_analysis.user_allowed"),
            base_url=get_str("ai_module.visual_analysis.base_url"),
            model=get_str("ai_module.visual_analysis.model"),
            api_key_set=bool(get_str("ai_module.visual_analysis.api_key")),
        ),
        caption=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.caption.user_allowed"),
            base_url=get_str("ai_module.caption.base_url"),
            model=get_str("ai_module.caption.model"),
            api_key_set=bool(get_str("ai_module.caption.api_key")),
        ),
        classification=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.classification.user_allowed"),
            base_url=get_str("ai_module.classification.base_url"),
            model=get_str("ai_module.classification.model"),
            api_key_set=bool(get_str("ai_module.classification.api_key")),
        ),
    )


@router.get("/ai-governance", response_model=AIGovernanceResponse)
async def get_ai_governance_settings(auth: AdminAuthDep):
    """AI config governance config for the admin panel.  API keys are MASKED —
    only ``*_api_key_set`` booleans are returned, never the raw keys."""
    return await _read_governance_settings()


@router.put("/ai-governance", response_model=AIGovernanceResponse)
async def update_ai_governance_settings(
    update: AIGovernanceUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Write AI governance settings.

    Only the modules/fields explicitly sent in the payload are written.
    For task modules, ``api_key`` is only written when the value is
    non-blank (omit or send blank to keep the stored key unchanged).
    Returns the masked bundle — raw api_key values never leave the server.
    """
    repo = get_system_settings_repository()
    data = update.model_dump(exclude_unset=True)

    written: list[str] = []
    for module, module_data in data.items():
        if not module_data:
            continue
        # user_allowed (both chat and task modules)
        if "user_allowed" in module_data and module_data["user_allowed"] is not None:
            key = f"ai_module.{module}.user_allowed"
            # Write JSONB native bool so the gate can do ``isinstance(v, bool)``.
            await repo.upsert_setting(key, module_data["user_allowed"], auth.user_id)
            written.append(key)
        if module not in _TASK_MODULE_NAMES:
            continue
        # base_url / model (task modules only)
        for field_name in ("base_url", "model"):
            if field_name in module_data and module_data[field_name] is not None:
                key = f"ai_module.{module}.{field_name}"
                await repo.upsert_setting(key, module_data[field_name], auth.user_id)
                written.append(key)
        # api_key: write-only, only when non-blank.
        api_key_val = module_data.get("api_key")
        if isinstance(api_key_val, str) and api_key_val.strip():
            key = f"ai_module.{module}.api_key"
            await repo.upsert_setting(key, api_key_val, auth.user_id)
            written.append(key)

    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_ai_governance_settings",
        target_type="system_setting",
        target_id="ai_governance",
        # Never log raw api_key material.
        details={"keys": [k for k in written if k not in _GOVERNANCE_SECRET_KEYS]},
        ip_address=client_ip,
    )
    logger.info(f"AI governance settings updated by admin {auth.user_id}: {written}")
    return await _read_governance_settings()


@router.patch("/{key}", response_model=SystemSettingResponse)
async def update_setting(
    key: str,
    update: SystemSettingUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Update a system setting by key."""
    repo = get_system_settings_repository()

    if not await repo.exists(key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found",
        )

    updated = await repo.update(key, update.value, auth.user_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update setting",
        )

    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_setting",
        target_type="system_setting",
        target_id=key,
        details={"value": update.value},
        ip_address=client_ip,
    )

    logger.info(f"Setting '{key}' updated by admin {auth.user_id}")
    return _to_response(updated)
