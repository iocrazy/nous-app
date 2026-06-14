"""Admin API routes for System Settings management."""

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.schemas.admin import (
    GraphMemorySettingsResponse,
    GraphMemorySettingsUpdate,
    SystemSettingResponse,
    SystemSettingUpdate,
)
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
