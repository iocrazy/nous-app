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
            ai_settings["ai_providers"] = body.ai_providers
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


@router.get("/providers")
async def list_providers():
    """List available AI provider keys."""
    return {"providers": AIProviderFactory.available_providers()}


@router.get("/nous-models")
async def list_nous_models(category: str = None):
    """List enabled Nous models (public, no API keys).

    Returns models available for users to select in Task Assignment.
    If no models are configured, returns empty list.
    """
    from app.repositories.nous_repository import NousRepository

    repo = NousRepository()
    models = await repo.list_enabled(category)
    return {"models": models}
