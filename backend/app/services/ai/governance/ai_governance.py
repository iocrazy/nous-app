"""AI Config Governance — per-module admin control over user configurability.

Reads ``system_settings`` (service-role, via the SQLAlchemy engine) to
determine whether end-users may configure a given AI module, and provides
the admin-set fallback config when a module is locked.

Six governed modules
--------------------
chat            — chat agent turns (toggle only; platform keys used when locked)
transcription   — Whisper / volcengine ASR
translation     — LLM translate
visual_analysis — LLM visual/image analysis
caption         — image captioning
classification  — asset classification

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
from typing import Optional

logger = logging.getLogger(__name__)

TASK_MODULES = frozenset(
    {"transcription", "translation", "visual_analysis", "caption", "classification"}
)
CHAT_MODULE = "chat"
ALL_MODULES: frozenset[str] = TASK_MODULES | {CHAT_MODULE}


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
    (bool / str / int / None) or None when the key is absent or the DB
    is unavailable.  Never raises.

    Note: ``fetch_val`` returns the raw JSONB-deserialised value — a stored
    JSON ``true`` comes back as Python ``True``, NOT as the string ``"true"``.
    """
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        return await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k", {"k": key}
        )
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


__all__ = [
    "AIModuleGovernance",
    "ALL_MODULES",
    "CHAT_MODULE",
    "TASK_MODULES",
    "get_module_governance",
]
