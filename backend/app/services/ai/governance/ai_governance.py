"""AI Config Governance — per-module admin control over user configurability.

Reads ``system_settings`` (service-role, via the SQLAlchemy engine) to
determine whether end-users may configure a given AI module, and provides
the admin-set fallback config when a module is locked.

Governed modules
----------------
chat            — chat agent turns (toggle only; platform keys used when locked)
transcription   — Whisper / volcengine ASR
translation     — LLM translate
visual_analysis — LLM visual/image analysis
caption         — image captioning
classification  — asset classification
summarization   — LLM summary/rewrite of transcripts (summarize agent)
topic_scorer    — background hotspot relevance scorer (no user-facing config)
embedding       — text/multimodal embedding provider for topic clustering; a
                  background system capability, admin-configured like topic_scorer
                  (no user-facing agent-select)

Data model (``system_settings`` keys, value = JSONB)
----------------------------------------------------
    ai_module.<m>.user_allowed  — JSONB native bool (absent = True = allowed)
    ai_module.<m>.base_url      — JSONB string  (task modules only)
    ai_module.<m>.model         — JSONB string  (task modules only)
    ai_module.<m>.api_key       — JSONB string  (task modules only; NEVER returned
                                   to clients — admin GET returns api_key_present bool)

Key invariant: absent settings ⇒ allowed=True.  Zero behaviour change until an
admin explicitly flips a switch.

CRITICAL — JSONB bool: ``system_settings.value`` is JSONB; a stored boolean
deserialises to a Python ``bool`` (NOT the string "true"/"false").  The gate
tests ``if not governance.allowed``, never ``== "true"`` or ``== "false"``.
DB down ⇒ degrade to allowed/empty — governance must never block AI when the
settings table is unreachable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TASK_MODULES = frozenset(
    {
        "transcription",
        "translation",
        "visual_analysis",
        "caption",
        "classification",
        "summarization",
        "topic_scorer",
        "embedding",
    }
)
CHAT_MODULE = "chat"
ALL_MODULES: frozenset[str] = TASK_MODULES | {CHAT_MODULE}
NOUS_GLOBAL_KEY = "nous.user_enabled"


@dataclass(frozen=True)
class AIModuleGovernance:
    """Resolved governance config for one AI module.

    ``allowed=True``  — user can configure the module freely (default).
    ``allowed=False`` — backend ignores user BYOK/task_assignment; uses
                        admin-set ``base_url``/``model``/``api_key`` instead.

    For the ``chat`` module only ``allowed`` is meaningful; the agent owns
    its model and platform settings keys serve as the adapter fallback when
    locked (``get_adapter_for_user`` receives an empty user_cfg).
    """

    allowed: bool = True
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    # True when api_key is non-empty — surfaced to admin UI, never the key itself.
    api_key_present: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        # frozen dataclass: object.__setattr__ to set derived field.
        object.__setattr__(self, "api_key_present", bool(self.api_key.strip()))


async def _read_raw(key: str) -> Optional[object]:
    """Read one ``system_settings`` JSONB value via the SQLAlchemy engine
    (service-role, bypasses RLS).  Returns the native Python value
    (bool / str / int / dict / None) or None when the key is absent or the
    DB is unavailable.  Never raises.

    Note: the ORM read returns the raw JSONB-deserialised value — a stored
    JSON ``true`` comes back as Python ``True``, NOT as the string ``"true"``.

    SECRETS: the value is passed through ``secure_settings.reveal`` before
    returning — a no-op for non-secret keys (bool/int/plain str/absent
    marker), but for a registered secret key (``ai_module.*.api_key``,
    ``platform.ai_providers``) it transparently decrypts the ``enc:v1:``
    ciphertext written by ``SystemSettingsRepository``'s conceal chokepoint.
    Fail-soft: a marked value that can't be decrypted logs an ERROR inside
    ``reveal`` and resolves to ``""`` rather than raising here.
    """
    try:
        from sqlalchemy import select

        from app.core.secure_settings import reveal
        from app.db import engine as db_engine
        from app.db.session import read_scope
        from app.models import SystemSettings

        if not db_engine.is_configured():
            return None
        async with read_scope() as session:
            value = await session.scalar(
                select(SystemSettings.value).where(SystemSettings.key == key)
            )
        return reveal(value) if value is not None else None
    except Exception:  # noqa: BLE001
        logger.warning("[governance] system_settings read failed for key: %s", key)
        return None


async def get_module_governance(module: str) -> AIModuleGovernance:
    """Read and return the governance config for ``module``.

    Always returns an ``AIModuleGovernance`` — never raises.  When the DB is
    unreachable or the key is absent the result defaults to ``allowed=True``
    (default-open, zero behaviour change).

    CRITICAL: the ``user_allowed`` value is a JSONB-native Python ``bool``.
    Gate code MUST do ``if not governance.allowed``, never string-compare.
    """
    try:
        return await _get_module_governance_inner(module)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[governance] unexpected error reading governance for %s — degrading to allowed",
            module,
        )
        return AIModuleGovernance()


async def _get_module_governance_inner(module: str) -> AIModuleGovernance:
    """Inner implementation — callers should use get_module_governance which wraps this."""
    # ── user_allowed ──────────────────────────────────────────────────────
    raw_allowed = await _read_raw(f"ai_module.{module}.user_allowed")

    if raw_allowed is None:
        allowed = True  # absent → default-open
    elif isinstance(raw_allowed, bool):
        # JSONB bool → native Python bool.  This is the expected path.
        allowed = raw_allowed
    else:
        # Unexpected type (e.g. accidentally stored as a string). Degrade to
        # allowed so a misconfiguration never permanently breaks the module.
        logger.warning(
            "[governance] ai_module.%s.user_allowed has unexpected type %s "
            "— treating as allowed (degrade-safe)",
            module,
            type(raw_allowed).__name__,
        )
        allowed = True

    if module not in TASK_MODULES:
        # Chat module: only the toggle is meaningful.
        return AIModuleGovernance(allowed=allowed)

    # ── task-module admin config (base_url / model / api_key) ─────────────
    raw_base_url = await _read_raw(f"ai_module.{module}.base_url")
    raw_model = await _read_raw(f"ai_module.{module}.model")
    raw_api_key = await _read_raw(f"ai_module.{module}.api_key")

    return AIModuleGovernance(
        allowed=allowed,
        base_url=str(raw_base_url).strip() if raw_base_url is not None else "",
        model=str(raw_model).strip() if raw_model is not None else "",
        api_key=str(raw_api_key).strip() if raw_api_key is not None else "",
    )


PLATFORM_PROVIDERS_KEY = "platform.ai_providers"


async def get_platform_ai_providers() -> dict:
    """Platform-level provider credentials, stored in the DATABASE like every
    other AI key in this system (user BYOK in ``user_settings``, module admin
    keys in ``ai_module.*`` — env vars are legacy bootstrap only).

    ``system_settings.platform.ai_providers`` holds a JSONB dict in the SAME
    shape as a user's BYOK ``ai_providers``::

        {"doubao": {"api_key": "...", "base_url": ""}, "qwen": {...}}

    Chat resolution layers it beneath user BYOK (user wins per provider) and
    above env fallbacks — see ``resolve_chat_config``. A single flat key per
    module can't serve chat because the AGENT owns the model there: one turn
    may be doubao, the next deepseek, so credentials must be per-provider.

    Returns ``{}`` when absent, malformed, or the DB is unreachable — never
    raises; degrading to env-only preserves pre-existing behaviour.
    """
    raw = await _read_raw(PLATFORM_PROVIDERS_KEY)
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(
                "[governance] %s has unexpected type %s — ignoring",
                PLATFORM_PROVIDERS_KEY,
                type(raw).__name__,
            )
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


async def is_nous_globally_enabled() -> bool:
    """Master switch for the user-side Nous platform-provider feature.

    Reads ``nous.user_enabled`` (system_settings). DEFAULT-OFF: absent or any
    non-``True`` value → False, so platform-cost exposure is opt-in only.
    DB unreachable (``_read_raw`` returns None) ⇒ False (fail-closed for cost).
    """
    return (await _read_raw(NOUS_GLOBAL_KEY)) is True


async def is_nous_allowed(module: str) -> bool:
    """Whether users may use platform Nous models for ``module``.

    Two layers (AND): the global switch must be on, AND the per-module
    ``ai_module.<module>.nous_allowed`` must not be explicitly false.
    Per-module DEFAULT-ON once the global switch is on (absent → allowed).
    Unexpected per-module type degrades to allowed (degrade-safe), but the
    global switch alone still fully gates the feature.
    """
    if not await is_nous_globally_enabled():
        return False
    raw = await _read_raw(f"ai_module.{module}.nous_allowed")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    logger.warning(
        "[governance] ai_module.%s.nous_allowed has unexpected type %s "
        "— treating as allowed (degrade-safe)",
        module,
        type(raw).__name__,
    )
    return True


@dataclass(frozen=True)
class LockedModuleConfig:
    """Resolved provider config for an admin-locked AI module.

    Returned by :func:`resolve_locked_module_config` when a module is locked
    (``governance.allowed is False``); ``None`` from that function means the
    module is NOT locked and the caller should proceed down its own user path.

    ``provider_config`` always carries the four keys the adapter factory reads
    — ``api_key`` / ``base_url`` / ``model`` / ``app_id`` — so consumers can
    ``.get`` uniformly regardless of whether the config came from the platform
    catalog or the admin's manual fields.
    """

    provider_key: str
    provider_config: Dict[str, Any]
    model: str


async def resolve_locked_module_config(
    module: str, *, default_provider_key: str = ""
) -> Optional[LockedModuleConfig]:
    """Resolve the admin config for a governed AI ``module`` — the shared gate.

    Single source of truth for the "module is admin-locked" branch that used to
    live (triplicated + subtly divergent) in ``resolve_task_provider_config``,
    ``ai_transcription.load_transcribe_inputs`` and
    ``ai_summary.load_summary_inputs``.

    Resolution order:

    1. ``get_module_governance(module)``; if ``governance.allowed`` → return
       ``None`` (module not locked — the caller proceeds down its user path).
    2. **Platform-catalog model FIRST** (#857 order). If ``governance.model`` is
       set, look it up in the platform catalog via ``resolve_platform_model``
       (lazily imported to avoid the ai_provider_helpers ↔ governance cycle):
         - a catalog hit returns ``(provider, config, model)`` → wrap in a
           ``LockedModuleConfig`` and return it (catalog models carry their own
           credentials, so the admin's manual api_key may be blank).
         - not a catalog name (``None``) → fall through to the manual path.
         - found-but-disabled (``RuntimeError``): if the admin ALSO configured a
           manual api_key, warn and fall through to the manual path (the manual
           key still works); otherwise re-raise the informative "no longer
           available" error instead of the generic no-key one below.
    3. Manual path — the admin's ``base_url`` / ``model`` / ``api_key`` fields.
       If no admin api_key is configured → fail closed (RuntimeError): the
       whisper / analysis / summarize services call the provider factory
       directly with no env fallback, so a missing key would fail late and
       cryptically; surface it early.
    4. Otherwise derive the provider key from the model prefix (falling back to
       ``default_provider_key`` when the model is absent or its prefix is
       unknown) and return the manual admin config.

    Deliberate behaviour change (#857 generalised): the shared task resolver
    previously checked ``api_key_present`` fail-closed BEFORE consulting the
    platform catalog, so a module locked to a catalog model with a blank manual
    key failed closed even though catalog models carry their own credentials.
    That is the exact bug #857 fixed for transcription + summarization; folding
    all three call sites onto this platform-first helper fixes the same latent
    bug for the five shared-resolver modules (visual_analysis / classification /
    caption / translation / script_generation).
    """
    governance = await get_module_governance(module)
    if governance.allowed:
        return None  # not locked — caller proceeds down its own user path.

    # ── Platform-catalog model FIRST (#857 order) ─────────────────────────────
    # Admin may lock a module directly TO a platform-catalog model (picked from
    # the governance dropdown). Use the UNGATED lookup — this is admin config,
    # not a user pick, so the user-facing nous switches do not apply. Lazy import
    # avoids the ai_provider_helpers → ai_governance import cycle.
    if governance.model:
        from app.services.ai.providers.ai_provider_helpers import (
            resolve_platform_model,
        )

        try:
            platform = await resolve_platform_model(governance.model)
        except RuntimeError:
            # Found-but-disabled catalog model. If the admin also set a manual
            # api_key, fall through and use it; otherwise surface the informative
            # "no longer available" error rather than the generic no-key one.
            if not governance.api_key_present:
                raise
            logger.warning(
                "[governance] %s locked to platform model %r which is no longer "
                "available; falling back to admin manual config",
                module,
                governance.model,
            )
            platform = None
        if platform is not None:
            p_key, p_cfg, p_model = platform
            logger.info(
                "[governance] %s locked to platform model %r → provider %r",
                module,
                governance.model,
                p_key,
            )
            return LockedModuleConfig(
                provider_key=p_key, provider_config=p_cfg, model=p_model
            )
        # Not a catalog name → fall through to the manual path below.

    # ── Manual admin config (base_url / model / api_key) ──────────────────────
    if not governance.api_key_present:
        logger.error(
            "[governance] %s is admin-locked but no admin api_key is configured; "
            "failing closed — the AI service has no env fallback",
            module,
        )
        raise RuntimeError(
            f"AI module '{module}' is admin-locked but no admin API key is "
            "configured. Contact your platform administrator."
        )

    # Derive provider_key from the admin-set model prefix; unknown/absent prefix
    # falls back to default_provider_key (e.g. "openai" for the whisper path, ""
    # for the generic OpenAI-compatible adapter).
    from app.services.ai.adapters.factory import provider_key_for_model

    if governance.model:
        try:
            derived_key = provider_key_for_model(governance.model)
        except ValueError:
            derived_key = default_provider_key
    else:
        derived_key = default_provider_key

    provider_config: Dict[str, Any] = {
        "api_key": governance.api_key,
        "base_url": governance.base_url,
        "model": governance.model,
        "app_id": "",
    }
    logger.info(
        "[governance] %s locked by admin; using admin config "
        "(provider_key=%r model=%r)",
        module,
        derived_key,
        governance.model,
    )
    return LockedModuleConfig(
        provider_key=derived_key,
        provider_config=provider_config,
        model=governance.model,
    )


__all__ = [
    "AIModuleGovernance",
    "ALL_MODULES",
    "CHAT_MODULE",
    "LockedModuleConfig",
    "NOUS_GLOBAL_KEY",
    "TASK_MODULES",
    "get_module_governance",
    "resolve_locked_module_config",
    "is_nous_allowed",
    "is_nous_globally_enabled",
]
