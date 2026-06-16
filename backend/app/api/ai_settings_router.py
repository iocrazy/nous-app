# backend/app/api/ai_settings_router.py

"""
AI Settings API

Endpoints for managing AI provider settings and testing connections.
Settings are stored per-user in the user_settings table (settings_json field).
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.user_settings_repository import UserSettingsRepository
from app.schemas.ai import (
    AISettingsResponse,
    AISettingsUpdate,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.services.ai.providers.ai_provider import AIProviderFactory

router = APIRouter(prefix="/ai", tags=["AI"])

# Key used inside user_settings.settings_json to store AI config
_AI_SETTINGS_KEY = "ai_settings"

# Provider config fields that hold secrets: a blank value in the payload
# means "unchanged", never "delete" — the form may save before AuthContext
# has hydrated it, and a wholesale replace would wipe stored keys.
_SECRET_FIELDS = ("api_key", "app_id")


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def merge_ai_providers(existing: dict | None, incoming: dict | None) -> dict:
    """Merge the ai_providers payload into the stored map, per provider.

    Providers absent from the payload are preserved; within an incoming
    provider config, blank secret fields fall back to the stored value.
    Returns a new dict — neither input is mutated.
    """
    merged = {
        key: dict(cfg) if isinstance(cfg, dict) else cfg
        for key, cfg in (existing or {}).items()
    }
    if incoming is None:
        return merged
    for key, config in incoming.items():
        previous = merged.get(key)
        if not isinstance(config, dict) or not isinstance(previous, dict):
            merged[key] = dict(config) if isinstance(config, dict) else config
            continue
        next_config = dict(config)
        for secret in _SECRET_FIELDS:
            if _is_blank(next_config.get(secret)) and not _is_blank(
                previous.get(secret)
            ):
                next_config[secret] = previous[secret]
        merged[key] = next_config
    return merged


@router.get("/settings", response_model=AISettingsResponse)
async def get_ai_settings(auth: AuthDep):
    """Get current user's AI settings."""
    try:
        repo = UserSettingsRepository()
        settings = await repo.get_by_user_id(auth.user_id)

        ai_settings = {}
        if settings and settings.get("settings_json"):
            ai_settings = settings["settings_json"].get(_AI_SETTINGS_KEY, {})

        return AISettingsResponse(
            ai_providers=ai_settings.get("ai_providers", {}),
            whisper_provider=ai_settings.get("whisper_provider", "openai_api"),
            default_summary_model=ai_settings.get(
                "default_summary_model", "gpt-4o-mini"
            ),
            default_analysis_model=ai_settings.get("default_analysis_model", "gpt-4o"),
            ai_enabled=ai_settings.get("ai_enabled", True),
            preferred_language=ai_settings.get("preferred_language", "auto"),
            task_assignment=ai_settings.get("task_assignment", {}),
        )

    except Exception as e:
        logger.error(f"Failed to get AI settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to get AI settings")


@router.put("/settings", response_model=AISettingsResponse)
async def save_ai_settings(body: AISettingsUpdate, auth: AuthDep):
    """Save AI settings for current user."""
    try:
        repo = UserSettingsRepository()

        # Get existing settings
        existing = await repo.get_by_user_id(auth.user_id)
        settings_json = existing.get("settings_json", {}) if existing else {}

        # Merge AI settings
        ai_settings = settings_json.get(_AI_SETTINGS_KEY, {})

        if body.ai_providers is not None:
            ai_settings["ai_providers"] = merge_ai_providers(
                ai_settings.get("ai_providers"), body.ai_providers
            )
        if body.whisper_provider is not None:
            ai_settings["whisper_provider"] = body.whisper_provider
        if body.default_summary_model is not None:
            ai_settings["default_summary_model"] = body.default_summary_model
        if body.default_analysis_model is not None:
            ai_settings["default_analysis_model"] = body.default_analysis_model
        if body.ai_enabled is not None:
            ai_settings["ai_enabled"] = body.ai_enabled
        if body.preferred_language is not None:
            ai_settings["preferred_language"] = body.preferred_language
        if body.task_assignment is not None:
            ai_settings["task_assignment"] = body.task_assignment

        # Patch only the ai_settings subtree — repo merges it into the shared
        # blob, leaving every other top-level key (parse_mode, General settings)
        # untouched. The nested merge above preserves sibling ai_settings fields.
        await repo.patch_settings_json(auth.user_id, {_AI_SETTINGS_KEY: ai_settings})

        return AISettingsResponse(
            ai_providers=ai_settings.get("ai_providers", {}),
            whisper_provider=ai_settings.get("whisper_provider", "openai_api"),
            default_summary_model=ai_settings.get(
                "default_summary_model", "gpt-4o-mini"
            ),
            default_analysis_model=ai_settings.get("default_analysis_model", "gpt-4o"),
            ai_enabled=ai_settings.get("ai_enabled", True),
            preferred_language=ai_settings.get("preferred_language", "auto"),
            task_assignment=ai_settings.get("task_assignment", {}),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save AI settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to save AI settings")


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_ai_connection(body: TestConnectionRequest, auth: AuthDep):
    """Test AI provider connection.

    Validates that the provider is reachable and lists available models.
    """
    result = await AIProviderFactory.test_connection(
        provider_key=body.provider_key,
        config={
            "api_key": body.api_key,
            "app_id": body.app_id,
            "base_url": body.base_url,
            "model": body.model,
        },
    )
    return TestConnectionResponse(**result)


@router.get("/health")
async def get_ai_health(auth: AuthDep):
    """Capability health board: what model+provider+key each AI feature
    actually resolves to, with an actionable status per capability.

    Surfaces silent misconfigurations (missing key, text model on a
    vision task) that otherwise only show up as a failed task."""
    from app.services.ai.ai_health import get_capability_health

    return {"capabilities": await get_capability_health(str(auth.user_id))}


@router.get("/providers")
async def list_providers():
    """List available AI provider keys."""
    return {"providers": AIProviderFactory.available_providers()}


@router.get("/governance")
async def get_ai_governance(auth: AuthDep):
    """Return per-module ``user_allowed`` booleans for all governed AI modules.

    The frontend uses this to hide locked modules' config sections in
    ``AISettings.tsx``.  Only boolean values are returned — no keys, no
    admin config.  Absent settings ⇒ True (default-open).
    """
    from app.services.ai.governance.ai_governance import (
        ALL_MODULES,
        get_module_governance,
    )

    result: dict[str, bool] = {}
    for module in sorted(ALL_MODULES):
        g = await get_module_governance(module)
        result[module] = g.allowed
    return result


@router.get("/nous-models")
async def list_nous_models(category: str = None):
    """List enabled Nous models (public, no API keys).

    Returns models available for users to select in Task Assignment.
    If no models are configured, returns empty list.
    """
    from app.repositories.nous_repository import get_nous_repository

    repo = get_nous_repository()
    models = await repo.list_enabled(category)
    return {"models": models}
