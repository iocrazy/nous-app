"""Admin API routes for System Settings management."""

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.core.config import settings
from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.repositories.agent_memory_promotion_repository import (
    approve_proposal,
    demote_memory,
    list_proposals,
    reject_proposal,
)
from app.repositories.agent_memory_repository import get_memory_stats
from app.schemas.admin import (
    AIGovernanceResponse,
    AIGovernanceUpdate,
    ChatModuleGovernanceResponse,
    ConsolidateRequest,
    ConsolidateResponse,
    GraphMemorySettingsResponse,
    GraphMemorySettingsUpdate,
    HonchoConnectionResponse,
    HonchoConnectionUpdate,
    MemoryControlResponse,
    MemoryReloadResponse,
    MemorySlotStatus,
    MemorySlotUpdate,
    MemoryStatsResponse,
    PromotionItem,
    PromotionListResponse,
    SystemSettingResponse,
    SystemSettingUpdate,
    TaskModuleGovernanceResponse,
    TopicContentFetchConfigResponse,
    TopicModuleConfigResponse,
    TopicPrefilterConfigResponse,
    TopicScoringConfigResponse,
)
from app.services.ai.memory import registry as memory_registry
from app.services.ai.memory.graph_memory import STRUCTURED_OUTPUT_MODES
from app.services.ai.memory.honcho_memory import HonchoMemoryConfig
from app.services.topics.content_fetcher import (
    CONTENT_FETCH_CONFIG_KEY,
    content_fetch_payload,
    load_content_fetch_config,
    merge_content_fetch_config,
)
from app.services.topics.keyword_filter import (
    PREFILTER_CONFIG_KEY,
    load_prefilter_config,
    merge_prefilter_config,
    prefilter_payload,
)
from app.services.topics.module_config import (
    MODULE_CONFIG_KEY,
    is_module_enabled,
    parse_module_enabled,
)
from app.services.topics.scoring import (
    SCORING_CONFIG_KEY,
    config_payload,
    load_scoring_config,
    merge_scoring_config,
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
    "extractor_structured_output_mode": "graph_extractor_structured_output_mode",
    "embedder_base_url": "graph_embedder_base_url",
    "embedder_api_key": "graph_embedder_api_key",
    "embedder_model": "graph_embedder_model",
    "embedder_dimensions": "graph_embedder_dimensions",
}
_TRUTHY = {"1", "true", "yes", "on"}
_SECRET_FIELDS = {"extractor_api_key", "embedder_api_key"}
# Hard upper bound on the embedder dimension = Qwen3-Embedding-8B native width.
# Both consumers now index 4096: Graphiti on FalkorDB (no dim ceiling) and Honcho
# on its LanceDB backend. (The earlier 2000 cap was pgvector's HNSW limit, which
# no longer applies now that Honcho is migrated off pgvector — see the
# memory-embedder runbook.)
_EMBEDDER_DIM_MAX = 4096


def _to_response(row: dict) -> SystemSettingResponse:
    return SystemSettingResponse(
        key=row["key"],
        value=row["value"],
        description=row.get("description"),
        updated_at=row["updated_at"],
        updated_by=row.get("updated_by"),
    )


@router.get("/topics-scoring", response_model=TopicScoringConfigResponse)
async def get_topics_scoring_config(auth: AdminAuthDep):
    """Current hotspot scoring knobs (admin-tuned, merged over code defaults)."""
    cfg = await load_scoring_config()
    return TopicScoringConfigResponse(**config_payload(cfg))


@router.put("/topics-scoring", response_model=TopicScoringConfigResponse)
async def update_topics_scoring_config(
    body: TopicScoringConfigResponse,
    auth: AdminAuthDep,
):
    """Persist scoring knobs to ``system_settings['topics.scoring']``. The body
    is validated/clamped through the same merge as reads, so a malformed weight
    can't poison the scorer."""
    validated = merge_scoring_config(body.model_dump())
    payload = config_payload(validated)
    repo = get_system_settings_repository()
    await repo.upsert_setting(SCORING_CONFIG_KEY, payload, auth.user_id)
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_topics_scoring_config",
        target_type="system_setting",
        target_id=SCORING_CONFIG_KEY,
        details={"value": payload},
    )
    return TopicScoringConfigResponse(**payload)


@router.get("/topics-prefilter", response_model=TopicPrefilterConfigResponse)
async def get_topics_prefilter_config(auth: AdminAuthDep):
    """Current L0 pre-filter config (admin-tuned, merged over code defaults)."""
    cfg = await load_prefilter_config()
    return TopicPrefilterConfigResponse(**prefilter_payload(cfg))


@router.put("/topics-prefilter", response_model=TopicPrefilterConfigResponse)
async def update_topics_prefilter_config(
    body: TopicPrefilterConfigResponse,
    auth: AdminAuthDep,
):
    """Persist L0 pre-filter knobs to ``system_settings['topics.prefilter']``.
    Validated through the same merge as reads. Takes effect on the next fetch
    tick — no redeploy."""
    validated = merge_prefilter_config(body.model_dump())
    payload = prefilter_payload(validated)
    repo = get_system_settings_repository()
    await repo.upsert_setting(PREFILTER_CONFIG_KEY, payload, auth.user_id)
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_topics_prefilter_config",
        target_type="system_setting",
        target_id=PREFILTER_CONFIG_KEY,
        details={"value": payload},
    )
    return TopicPrefilterConfigResponse(**payload)


@router.get("/topics-module", response_model=TopicModuleConfigResponse)
async def get_topics_module_config(auth: AdminAuthDep):
    """Global Topic Inspiration master switch."""
    return TopicModuleConfigResponse(enabled=await is_module_enabled())


@router.put("/topics-module", response_model=TopicModuleConfigResponse)
async def update_topics_module_config(
    body: TopicModuleConfigResponse,
    auth: AdminAuthDep,
):
    """Persist the module master switch to ``system_settings['topics.module']``.
    Off = pause the whole feature (scheduled tick skipped) + the frontend hides
    the page. Instant, no redeploy."""
    payload = {"enabled": parse_module_enabled(body.model_dump())}
    repo = get_system_settings_repository()
    await repo.upsert_setting(MODULE_CONFIG_KEY, payload, auth.user_id)
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_topics_module_config",
        target_type="system_setting",
        target_id=MODULE_CONFIG_KEY,
        details={"value": payload},
    )
    return TopicModuleConfigResponse(**payload)


@router.get("/topics-content-fetch", response_model=TopicContentFetchConfigResponse)
async def get_topics_content_fetch_config(auth: AdminAuthDep):
    """Current L0.5 content-enrichment config (admin-tuned, over code defaults)."""
    cfg = await load_content_fetch_config()
    return TopicContentFetchConfigResponse(**content_fetch_payload(cfg))


@router.put("/topics-content-fetch", response_model=TopicContentFetchConfigResponse)
async def update_topics_content_fetch_config(
    body: TopicContentFetchConfigResponse,
    auth: AdminAuthDep,
):
    """Persist content-fetch knobs to ``system_settings['topics.content_fetch']``.
    Validated/clamped through the same merge as reads. Takes effect on the next
    fetch tick — no redeploy. ``enabled`` is the trafilatura kill switch."""
    validated = merge_content_fetch_config(body.model_dump())
    payload = content_fetch_payload(validated)
    repo = get_system_settings_repository()
    await repo.upsert_setting(CONTENT_FETCH_CONFIG_KEY, payload, auth.user_id)
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_topics_content_fetch_config",
        target_type="system_setting",
        target_id=CONTENT_FETCH_CONFIG_KEY,
        details={"value": payload},
    )
    return TopicContentFetchConfigResponse(**payload)


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
        embedder_dimensions=_parse_dim(s("embedder_dimensions")),
    )


def _parse_dim(raw: str) -> int:
    """Stored dimension → int, defaulting to 1536 on empty/garbage."""
    try:
        value = int(raw.strip())
        return value if value > 0 else 1536
    except (ValueError, AttributeError):
        return 1536


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

    # Reject an out-of-range dimension up front (422). The shared embedder feeds
    # Honcho's pgvector HNSW index, which is hard-capped at 2000 dimensions —
    # persisting a larger value would silently break the vector index.
    dimensions = data.get("embedder_dimensions")
    if dimensions is not None and not (1 <= dimensions <= _EMBEDDER_DIM_MAX):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "embedder_dimensions must be between 1 and "
                f"{_EMBEDDER_DIM_MAX} (pgvector HNSW index limit)"
            ),
        )

    written: list[str] = []
    for field, key in _GRAPH_FIELD_TO_KEY.items():
        if field not in data or data[field] is None:
            continue
        value = data[field]
        if field == "enabled":
            value = "true" if value else "false"
        elif field == "embedder_dimensions":
            # Store as a jsonb string to match the seed + the other graph_* keys.
            value = str(value)
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

# Governed module names (must match ai_governance.py TASK_MODULES)
_TASK_MODULE_NAMES = [
    "transcription",
    "translation",
    "visual_analysis",
    "caption",
    "classification",
    "summarization",
    "topic_scorer",
    "embedding",
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

    def get_nous(module: str) -> bool:
        v = data.get(f"ai_module.{module}.nous_allowed")
        if isinstance(v, bool):
            return v
        return True  # default-on (gated by the global switch)

    # nous.user_enabled is NOT ai_module.*-prefixed, so it is absent from `data`.
    # Read it directly from the full row set (default-off when absent).
    nous_global_on = any(
        r.get("key") == "nous.user_enabled" and r.get("value") is True for r in rows
    )

    return AIGovernanceResponse(
        nous_user_enabled=nous_global_on,
        chat=ChatModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.chat.user_allowed"),
            nous_allowed=get_nous("chat"),
        ),
        transcription=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.transcription.user_allowed"),
            nous_allowed=get_nous("transcription"),
            base_url=get_str("ai_module.transcription.base_url"),
            model=get_str("ai_module.transcription.model"),
            api_key_set=bool(get_str("ai_module.transcription.api_key")),
        ),
        translation=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.translation.user_allowed"),
            nous_allowed=get_nous("translation"),
            base_url=get_str("ai_module.translation.base_url"),
            model=get_str("ai_module.translation.model"),
            api_key_set=bool(get_str("ai_module.translation.api_key")),
        ),
        visual_analysis=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.visual_analysis.user_allowed"),
            nous_allowed=get_nous("visual_analysis"),
            base_url=get_str("ai_module.visual_analysis.base_url"),
            model=get_str("ai_module.visual_analysis.model"),
            api_key_set=bool(get_str("ai_module.visual_analysis.api_key")),
        ),
        caption=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.caption.user_allowed"),
            nous_allowed=get_nous("caption"),
            base_url=get_str("ai_module.caption.base_url"),
            model=get_str("ai_module.caption.model"),
            api_key_set=bool(get_str("ai_module.caption.api_key")),
        ),
        classification=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.classification.user_allowed"),
            nous_allowed=get_nous("classification"),
            base_url=get_str("ai_module.classification.base_url"),
            model=get_str("ai_module.classification.model"),
            api_key_set=bool(get_str("ai_module.classification.api_key")),
        ),
        summarization=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.summarization.user_allowed"),
            nous_allowed=get_nous("summarization"),
            base_url=get_str("ai_module.summarization.base_url"),
            model=get_str("ai_module.summarization.model"),
            api_key_set=bool(get_str("ai_module.summarization.api_key")),
        ),
        topic_scorer=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.topic_scorer.user_allowed"),
            nous_allowed=get_nous("topic_scorer"),
            base_url=get_str("ai_module.topic_scorer.base_url"),
            model=get_str("ai_module.topic_scorer.model"),
            api_key_set=bool(get_str("ai_module.topic_scorer.api_key")),
        ),
        embedding=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.embedding.user_allowed"),
            nous_allowed=get_nous("embedding"),
            base_url=get_str("ai_module.embedding.base_url"),
            model=get_str("ai_module.embedding.model"),
            api_key_set=bool(get_str("ai_module.embedding.api_key")),
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

    # Global Nous master switch (top-level, not under a module).
    if data.get("nous_user_enabled") is not None:
        await repo.upsert_setting(
            "nous.user_enabled", data["nous_user_enabled"], auth.user_id
        )
        written.append("nous.user_enabled")

    for module, module_data in data.items():
        if module == "nous_user_enabled":
            continue  # handled above; not a per-module dict
        if not module_data:
            continue
        # user_allowed (both chat and task modules)
        if "user_allowed" in module_data and module_data["user_allowed"] is not None:
            key = f"ai_module.{module}.user_allowed"
            # Write JSONB native bool so the gate can do ``isinstance(v, bool)``.
            await repo.upsert_setting(key, module_data["user_allowed"], auth.user_id)
            written.append(key)
        if "nous_allowed" in module_data and module_data["nous_allowed"] is not None:
            key = f"ai_module.{module}.nous_allowed"
            await repo.upsert_setting(key, module_data["nous_allowed"], auth.user_id)
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


# ── Memory Control-Plane (Phase 2a) ──────────────────────────────────────────

# Phase 1 providers only — Mem0/Hindsight (Phase 3) extend these sets.
_VALID_SLOT_PROVIDERS: dict[str, set[str]] = {
    "l2": {"honcho", "none"},
    "l3": {"graphiti", "none"},
}


async def _build_memory_control() -> MemoryControlResponse:
    l2 = await memory_registry.l2_provider()
    l3 = await memory_registry.l3_provider()
    slots = []
    for slot, provider in (("l2", l2), ("l3", l3)):
        slots.append(
            MemorySlotStatus(
                slot=slot,
                provider=provider.name if provider else "none",
                health=(await provider.health()) if provider else False,
            )
        )
    return MemoryControlResponse(slots=slots)


@router.get("/memory/control", response_model=MemoryControlResponse)
async def get_memory_control(auth: AdminAuthDep):
    """List each memory slot's active provider + liveness."""
    return await _build_memory_control()


@router.put("/memory/slot", response_model=MemoryControlResponse)
async def set_memory_slot(update: MemorySlotUpdate, auth: AdminAuthDep):
    """Switch a slot's provider (writes memory.<slot>_provider). Returns the
    refreshed control snapshot."""
    allowed = _VALID_SLOT_PROVIDERS.get(update.slot, set())
    if update.provider not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"provider '{update.provider}' not valid for slot '{update.slot}' "
            f"(allowed: {sorted(allowed)})",
        )
    repo = get_system_settings_repository()
    await repo.upsert_setting(
        f"memory.{update.slot}_provider", update.provider, auth.user_id
    )
    logger.info(f"[Admin] memory slot {update.slot} -> {update.provider}")
    return await _build_memory_control()


@router.post("/memory/{slot}/reload", response_model=MemoryReloadResponse)
async def reload_memory_slot(slot: str, auth: AdminAuthDep):
    """Drop the slot provider's cached client/config so the next call re-reads
    settings — applies a config edit without a backend restart."""
    if slot not in ("l2", "l3"):
        raise HTTPException(status_code=400, detail="slot must be 'l2' or 'l3'")
    provider = await (
        memory_registry.l2_provider() if slot == "l2" else memory_registry.l3_provider()
    )
    if provider is None:
        return MemoryReloadResponse(ok=False, reloaded=None)
    await provider.reload()
    logger.info(f"[Admin] reloaded memory slot {slot} ({provider.name})")
    return MemoryReloadResponse(ok=True, reloaded=provider.name)


# ── Honcho Connection Config (Phase 2b) ──────────────────────────────────────

# Maps HonchoConnectionUpdate fields → system_settings keys (mirrors
# _HONCHO_SETTINGS_MAP in honcho_memory.py; the DB-key half only).
_HONCHO_CONN_KEY = {
    "enabled": "honcho_memory_enabled",
    "base_url": "honcho_base_url",
    "workspace_id": "honcho_workspace_id",
}


@router.get("/memory/honcho-connection", response_model=HonchoConnectionResponse)
async def get_honcho_connection(auth: AdminAuthDep):
    """Effective Honcho connection config (settings with env fallback)."""
    cfg = await HonchoMemoryConfig.from_settings()
    return HonchoConnectionResponse(
        enabled=cfg.enabled, base_url=cfg.base_url, workspace_id=cfg.workspace_id
    )


@router.put("/memory/honcho-connection", response_model=HonchoConnectionResponse)
async def put_honcho_connection(update: HonchoConnectionUpdate, auth: AdminAuthDep):
    """Upsert the provided connection fields; returns the refreshed effective
    config. Click L2 Reload (Provider Slots) to apply to the live service."""
    repo = get_system_settings_repository()
    fields = update.model_dump(exclude_unset=True)
    for field_name, value in fields.items():
        key = _HONCHO_CONN_KEY[field_name]
        # store enabled as the string "true"/"false" (settings values are text)
        stored = (
            ("true" if value else "false") if field_name == "enabled" else str(value)
        )
        await repo.upsert_setting(key, stored, auth.user_id)
    logger.info(f"[Admin] honcho connection updated: {sorted(fields)}")
    cfg = await HonchoMemoryConfig.from_settings()
    return HonchoConnectionResponse(
        enabled=cfg.enabled, base_url=cfg.base_url, workspace_id=cfg.workspace_id
    )


# ── Agent Memory Consolidation (Phase B) ─────────────────────────────────────


@router.post("/memory/consolidate", response_model=ConsolidateResponse)
async def trigger_consolidation(body: ConsolidateRequest, auth: AdminAuthDep):
    """Manually run the Phase-B consolidation step for one (user, agent) pair.

    Useful for end-to-end validation without waiting for the weekly scheduled
    run. Delegates to the plain ``_consolidate_pair`` helper (not the DBOS
    step) to avoid entering a step context outside a workflow.
    """
    # Lazy import avoids pulling DBOS at module load time (DBOS decorators on
    # the step/workflow functions register side effects on import).
    from app.workflows.consolidate_agent_memory import (
        _consolidate_pair,
    )

    result = await _consolidate_pair(body.user_id, body.agent_id)
    logger.info(
        "[Admin] manual consolidation triggered by {} for user={} agent={}: {}",
        auth.user_id,
        body.user_id,
        body.agent_id,
        result,
    )
    return ConsolidateResponse(
        written=result.get("written", 0),
        skipped=result.get("skipped", 0),
        contexts=result.get("contexts", 0),
        proposed=result.get("proposed", 0),
    )


@router.get("/memory/promotions", response_model=PromotionListResponse)
async def list_memory_promotions(auth: AdminAuthDep, status: str = "pending"):
    """List agent-memory promotion proposals (Phase C1 review queue).

    Each row is a pending (or reviewed) proposal to flip a private team/project
    memory to shared, JOINed with its source agent_memory row for the title,
    owner, and original body so the admin can compare against the scrubbed text.
    """
    rows = await list_proposals(status=status, limit=100)
    logger.info(
        "[Admin] promotions listed by {} status={}: {} item(s)",
        auth.user_id,
        status,
        len(rows),
    )
    items = [
        PromotionItem(
            id=row["id"],
            memory_id=row["memory_id"],
            proposed_scope=row["proposed_scope"],
            target_team_id=row["target_team_id"],
            target_project_id=row.get("target_project_id"),
            title=row.get("title") or "",
            owner_user_id=str(row.get("owner_user_id") or ""),
            original_body_md=row.get("body_md") or "",
            scrubbed_body_md=row.get("scrubbed_body_md") or "",
            classification_kind=row.get("classification_kind") or "",
            confidence=row.get("confidence") or 0.0,
            justification=row.get("justification") or "",
            status=row["status"],
            created_at=str(row.get("created_at") or ""),
        )
        for row in rows
    ]
    return PromotionListResponse(items=items)


@router.post("/memory/promotions/{proposal_id}/approve")
async def approve_memory_promotion(proposal_id: int, auth: AdminAuthDep):
    """Approve a promotion proposal — flips the memory row to shared (Phase C1).

    Delegates to the service-role transaction in the repository, which sets
    visibility='shared' + team_id and writes the scrubbed body in one statement.
    """
    approved = await approve_proposal(proposal_id=proposal_id, reviewer_id=auth.user_id)
    logger.info(
        "[Admin] promotion {} approve by {}: {}",
        proposal_id,
        auth.user_id,
        approved,
    )
    return {"approved": approved}


@router.post("/memory/promotions/{proposal_id}/reject")
async def reject_memory_promotion(proposal_id: int, auth: AdminAuthDep):
    """Reject a promotion proposal — the memory row stays private (Phase C1)."""
    rejected = await reject_proposal(proposal_id=proposal_id, reviewer_id=auth.user_id)
    logger.info(
        "[Admin] promotion {} reject by {}: {}",
        proposal_id,
        auth.user_id,
        rejected,
    )
    return {"rejected": rejected}


@router.post("/memory/{memory_id}/demote")
async def demote_agent_memory(memory_id: int, auth: AdminAuthDep):
    """Revoke a shared memory — flip it back to private (Phase C1)."""
    demoted = await demote_memory(memory_id=memory_id)
    logger.info(
        "[Admin] memory {} demote by {}: {}",
        memory_id,
        auth.user_id,
        demoted,
    )
    return {"demoted": demoted}


# ── Agent Memory Stats (Phase C2 observability) ───────────────────────────────


@router.get("/memory/stats", response_model=MemoryStatsResponse)
async def memory_stats(auth: AdminAuthDep):
    """Aggregate counts over agent_memory + promotion queue depth + recall flag.

    Safe to call at any time — delegates to get_memory_stats which never raises
    (returns zeroed shape on DB error). The recall_enabled field reflects the
    live FEATURE_AGENT_MEMORY setting without a server restart.
    """
    stats = await get_memory_stats()
    return MemoryStatsResponse(
        recall_enabled=bool(settings.FEATURE_AGENT_MEMORY),
        total_active=stats.get("total_active", 0),
        by_visibility=stats.get("by_visibility", {}),
        by_scope=stats.get("by_scope", {}),
        by_status=stats.get("by_status", {}),
        created_24h=stats.get("created_24h", 0),
        created_7d=stats.get("created_7d", 0),
        last_created_at=stats.get("last_created_at"),
        promotions=stats.get("promotions", {}),
    )
