# backend/app/api/ai_settings_router.py

"""
AI Settings API

Endpoints for managing AI provider settings and testing connections.
Settings are stored per-user in the user_settings table (settings_json field).
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.secure_settings import (
    MARKER,
    conceal_byok_providers,
    reveal_byok_providers,
)
from app.repositories.user_settings_repository import UserSettingsRepository
from app.schemas.ai import (
    AISettingsResponse,
    AISettingsUpdate,
    ProviderHealthUpdate,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.services.ai.provider_health import (
    InvalidProviderKey,
    persist_provider_health,
    validate_provider_key,
)
from app.services.ai.providers.ai_provider import AIProviderFactory
from app.services.codex import provider_card as codex_card

router = APIRouter(prefix="/ai", tags=["AI"])

# Key used inside user_settings.settings_json to store AI config
_AI_SETTINGS_KEY = "ai_settings"

# Provider config fields that hold secrets: a blank value in the payload
# means "unchanged", never "delete" — the form may save before AuthContext
# has hydrated it, and a wholesale replace would wipe stored keys.
_SECRET_FIELDS = ("api_key", "app_id")

# GET/PUT response masking: how many trailing characters of a real (revealed)
# api_key to expose as a hint, e.g. "...ab12".
_HINT_LEN = 4


def _api_key_hint(key: str) -> str:
    key = key.strip()
    if not key:
        return ""
    return key[-_HINT_LEN:] if len(key) > _HINT_LEN else key


def _mask_provider_entry(entry: Any) -> Any:
    """Replace one provider entry's ``api_key`` with presence metadata.

    Product decision (secret-at-rest Phase 2, owner-ratified): the API never
    returns a raw ``api_key`` to the client again — encrypted or not. ``entry``
    must already be PLAINTEXT (call ``reveal_byok_providers`` first) so the
    hint reflects real key material, not ciphertext. Every other field
    (``base_url``, ``model``, ``enabled``, ``app_id``, …) is returned as-is.
    """
    if not isinstance(entry, dict):
        return entry
    out = {k: v for k, v in entry.items() if k != "api_key"}
    raw_key = entry.get("api_key")
    if isinstance(raw_key, list):
        keys = [k.strip() for k in raw_key if isinstance(k, str) and k.strip()]
        out["api_key_set"] = bool(keys)
        out["api_key_count"] = len(keys)
        out["api_key_hint"] = _api_key_hint(keys[0]) if keys else ""
    else:
        key = raw_key.strip() if isinstance(raw_key, str) else ""
        out["api_key_set"] = bool(key)
        out["api_key_count"] = 1 if key else 0
        out["api_key_hint"] = _api_key_hint(key)
    return out


def mask_ai_providers(plaintext_providers: dict) -> dict:
    """GET/PUT response masking chokepoint — replaces every provider's
    ``api_key`` with ``api_key_set`` / ``api_key_hint`` / ``api_key_count``.
    Input must already be revealed (plaintext); non-dict input passes
    through untouched."""
    if not isinstance(plaintext_providers, dict):
        return plaintext_providers
    return {
        name: _mask_provider_entry(cfg) for name, cfg in plaintext_providers.items()
    }


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def reject_client_ciphertext(incoming: dict | None) -> None:
    """API-boundary guard (security review of PR #1004, layer 1): reject any
    CLIENT-submitted secret value that already carries the ``enc:v1:``
    marker with 422.

    A legitimate request NEVER contains ciphertext: the blank-means-keep
    semantics of ``merge_ai_providers`` source the previous (possibly
    encrypted) value from the DB row SERVER-side — the client sends either
    a new plaintext key or a blank. A marker-prefixed value in the payload
    is therefore always either a client bug or an attempted ciphertext
    replay (planting a stolen ciphertext from another surface/user so the
    reveal path decrypts it — the decryption-oracle attack). The ownership
    binding inside ``conceal/reveal_byok_providers`` is the second,
    defense-in-depth layer.
    """
    if not isinstance(incoming, dict):
        return
    for provider, entry in incoming.items():
        if not isinstance(entry, dict):
            continue
        for field_name in _SECRET_FIELDS:
            value = entry.get(field_name)
            candidates = value if isinstance(value, list) else [value]
            for item in candidates:
                if isinstance(item, str) and item.startswith(MARKER):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"{provider}.{field_name} must be a plaintext "
                            "credential — encrypted (enc:v1:) values are not "
                            "accepted from the client. Leave the field blank "
                            "to keep the stored key unchanged."
                        ),
                    )


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
        provider_health = {}
        if settings and settings.get("settings_json"):
            settings_json = settings["settings_json"]
            ai_settings = settings_json.get(_AI_SETTINGS_KEY, {})
            # Provider health lives at the settings_json TOP LEVEL (not under
            # ai_settings) so the shallow || merge can't clobber it — see
            # app.services.ai.provider_health.
            provider_health = settings_json.get("ai_provider_health", {})

        # Stored ai_providers may carry enc:v1: ciphertext api_keys (secret-at-
        # rest Phase 2) or, for legacy rows, plaintext — reveal_byok_providers
        # handles both (no-op on unmarked strings) and verifies the ownership
        # binding against the requesting user (a replayed foreign ciphertext
        # resolves to "" here, so the hint below can never leak it). The
        # client never sees either form: mask_ai_providers replaces api_key
        # with presence metadata computed from the real (revealed) material.
        plaintext_providers = reveal_byok_providers(
            ai_settings.get("ai_providers", {}), user_id=str(auth.user_id)
        )
        return AISettingsResponse(
            ai_providers=mask_ai_providers(plaintext_providers),
            whisper_provider=ai_settings.get("whisper_provider", "openai_api"),
            default_summary_model=ai_settings.get(
                "default_summary_model", "gpt-4o-mini"
            ),
            default_analysis_model=ai_settings.get("default_analysis_model", "gpt-4o"),
            ai_enabled=ai_settings.get("ai_enabled", True),
            preferred_language=ai_settings.get("preferred_language", "auto"),
            transcription_hotwords=ai_settings.get("transcription_hotwords", ""),
            task_assignment=ai_settings.get("task_assignment", {}),
            provider_health=provider_health,
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
            # Layer-1 anti-replay guard: the CLIENT payload must never carry
            # enc:v1: ciphertext (422). Runs BEFORE the merge so a planted
            # ciphertext never even reaches the stored map.
            reject_client_ciphertext(body.ai_providers)
            # merge_ai_providers runs against the STORED (possibly enc:v1:
            # ciphertext) previous value: a blank incoming secret field falls
            # back to the previous value byte-for-byte, so an unchanged key
            # is carried forward as ciphertext without ever being decrypted.
            # conceal_byok_providers is idempotent (marker check) — encrypts
            # only the fields the caller actually supplied plaintext for,
            # BOUND to the requesting user (layer-2 anti-replay).
            merged = merge_ai_providers(
                ai_settings.get("ai_providers"), body.ai_providers
            )
            ai_settings["ai_providers"] = conceal_byok_providers(
                merged, user_id=str(auth.user_id)
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
        if body.transcription_hotwords is not None:
            ai_settings["transcription_hotwords"] = body.transcription_hotwords
        if body.task_assignment is not None:
            ai_settings["task_assignment"] = body.task_assignment

        # Patch only the ai_settings subtree — repo merges it into the shared
        # blob, leaving every other top-level key (parse_mode, General settings)
        # untouched. The nested merge above preserves sibling ai_settings fields.
        await repo.patch_settings_json(auth.user_id, {_AI_SETTINGS_KEY: ai_settings})

        # Same masking contract as GET — the response never carries a raw
        # api_key (plaintext OR ciphertext). reveal_byok_providers decrypts
        # only the ciphertext fields (verifying the ownership binding);
        # fields the caller just typed in this request are already plaintext
        # and pass through unchanged.
        plaintext_providers = reveal_byok_providers(
            ai_settings.get("ai_providers", {}), user_id=str(auth.user_id)
        )
        return AISettingsResponse(
            ai_providers=mask_ai_providers(plaintext_providers),
            whisper_provider=ai_settings.get("whisper_provider", "openai_api"),
            default_summary_model=ai_settings.get(
                "default_summary_model", "gpt-4o-mini"
            ),
            default_analysis_model=ai_settings.get("default_analysis_model", "gpt-4o"),
            ai_enabled=ai_settings.get("ai_enabled", True),
            preferred_language=ai_settings.get("preferred_language", "auto"),
            transcription_hotwords=ai_settings.get("transcription_hotwords", ""),
            task_assignment=ai_settings.get("task_assignment", {}),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save AI settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to save AI settings")


async def _stored_provider_config(user_id: str, provider_key: str) -> dict | None:
    """Decrypted stored config for one provider, or None when absent."""
    try:
        repo = UserSettingsRepository()
        settings = await repo.get_by_user_id(user_id)
        blob = (settings or {}).get("settings_json") or {}
        providers = (blob.get(_AI_SETTINGS_KEY) or {}).get("ai_providers") or {}
        revealed = reveal_byok_providers(providers, user_id=str(user_id))
        cfg = revealed.get(provider_key)
        return dict(cfg) if isinstance(cfg, dict) else None
    except Exception as exc:
        logger.warning("stored provider config load failed: {}", exc)
        return None


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_ai_connection(body: TestConnectionRequest, auth: AuthDep):
    """Test AI provider connection.

    Validates that the provider is reachable and lists available models.
    """
    # Secret-at-rest fallback (2026-08-26): the frontend cannot re-send a
    # stored key (GET masks it), which used to leave Test Connection
    # permanently disabled for saved providers. A blank key now means
    # "test with what the server already holds".
    api_key = body.api_key
    app_id = body.app_id
    base_url = body.base_url
    if not api_key:
        stored = await _stored_provider_config(auth.user_id, body.provider_key)
        if stored:
            api_key = stored.get("api_key") or api_key
            app_id = app_id or stored.get("app_id")
            base_url = base_url or stored.get("base_url")

    if body.provider_key == codex_card.PROVIDER_KEY:
        # No credential to test: the user's paired daemon IS the credential.
        # Asks presence + env_report instead of dialing a URL; same result
        # shape, so persistence and the card need no special case.
        result = await codex_card.test_codex_local_connection(str(auth.user_id))
    else:
        result = await AIProviderFactory.test_connection(
            provider_key=body.provider_key,
            config={
                "api_key": api_key,
                "app_id": app_id,
                "base_url": base_url,
                "model": body.model,
            },
        )

    # Best-effort: persist the probe outcome so the Settings UI can show
    # "Last tested ..." after a reload (mirrors the admin mediahub_models probe
    # board). Wrapped so telemetry can never fail the connection test.
    try:
        success = bool(result.get("success"))
        if success:
            n_models = len(result.get("models") or [])
            detail = f"{n_models} models available" if n_models else "Connection OK"
        else:
            detail = result.get("error") or "Connection failed"
        await persist_provider_health(
            auth.user_id,
            body.provider_key,
            "ok" if success else "fail",
            detail,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort telemetry
        logger.warning("provider-health persist (test-connection) failed: {}", exc)

    return TestConnectionResponse(**result)


@router.post("/provider-health")
async def report_provider_health(body: ProviderHealthUpdate, auth: AuthDep):
    """Record a browser-direct local-provider test outcome.

    The frontend probes local providers (Ollama / LM Studio) directly from
    the browser — the backend can't reach ``localhost`` on the user's machine
    — then reports the result here so it persists across reloads. Cloud
    providers are persisted server-side by ``/ai/test-connection`` and need no
    client report.
    """
    try:
        validate_provider_key(body.provider_key)
    except InvalidProviderKey as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    detail = body.detail or ("Connection OK" if body.status == "ok" else "")
    await persist_provider_health(
        auth.user_id,
        body.provider_key,
        "ok" if body.status == "ok" else "fail",
        detail,
    )
    return {"ok": True}


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
    """Return per-module ``user_allowed`` booleans plus the Nous master-control
    state for all governed AI modules.

    The frontend uses this to hide locked modules' BYOK config and to gate the
    Nous provider card + per-module Nous options.  Only booleans are returned —
    no keys, no admin config.  Absent BYOK settings ⇒ True (default-open); the
    Nous global switch is default-OFF.
    """
    from app.services.ai.governance.ai_governance import (
        ALL_MODULES,
        get_module_governance,
        is_nous_allowed,
        is_nous_globally_enabled,
    )

    result: dict = {}
    for module in sorted(ALL_MODULES):
        g = await get_module_governance(module)
        result[module] = g.allowed

    result["nous_enabled"] = await is_nous_globally_enabled()
    result["nous_modules"] = {
        module: await is_nous_allowed(module) for module in sorted(ALL_MODULES)
    }
    return result


@router.get("/mediahub-models")
async def list_mediahub_models(auth: AuthDep, type: str | None = None):
    """List enabled Nous models (public, no API keys), optionally filtered by
    model type (``llm`` / ``embedding`` / ``tts`` / ``asr``).

    Returns models available for users to select. If none are configured,
    returns an empty list. Requires auth (added with owner scoping, migration
    431): the list is scoped to the caller so owner-private rows never leak.
    """
    from app.repositories.mediahub_model_repository import get_mediahub_model_repository

    repo = get_mediahub_model_repository()
    models = await repo.list_enabled(type, viewer_user_id=auth.user_id)
    return {"models": models}
