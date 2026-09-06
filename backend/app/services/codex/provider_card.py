"""Codex as a standard provider card (Settings → AI → Providers), 2026-09-05.

The card is stored like every BYOK provider — ``ai_settings.ai_providers
["codex-local"]`` with ``enabled`` / ``enabled_models`` / ``selected_model`` —
but it has no credential of its own: the user's paired daemon and the ChatGPT
login on that machine are the credential (spec 2026-08-23). So:

* **Test Connection** asks the daemon, not a URL: is a paired device online,
  and does its env_report say ``codex`` is logged in? Success fills the
  catalog with the orchestrator models known to be accepted for a ChatGPT
  account; the user may still type another name.
* **Model ids carry a ``codex:`` prefix.** Agent ``model`` values are bare
  names routed by prefix (``factory.provider_key_for_model``), and Codex's
  models are ``gpt-*`` like the OpenAI card's — without the prefix the two
  cards would collide. Same convention as ``nous:<model>`` and the ASR
  picker's ``provider:model``. Stripped exactly once, at the protocol
  boundary (``CodexLocalProtocol.build_chat_adapter``) and here for images.
* **Image generation** reads its orchestrator model from this same card
  (``selected_model``, else the first enabled model), so one place answers
  "which Codex model" for text and images alike. The catalog row's
  ``actual_model`` stays the platform default when the card offers nothing.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

PROVIDER_KEY = "codex-local"
MODEL_PREFIX = "codex:"

# Orchestrator models seen accepted for a ChatGPT-account Codex session on
# 2026-09-05 (request create → "Input must be a list", i.e. past the model
# gate). A hint list for the picker, not a gate: the user may type another.
KNOWN_CODEX_MODELS: tuple[str, ...] = ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.5")

# It becomes one argv token on the user's machine. Letters, digits, dot, dash,
# underscore; nothing a shell or a path would read.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def strip_model_prefix(model: str) -> str:
    m = (model or "").strip()
    return m[len(MODEL_PREFIX) :] if m.lower().startswith(MODEL_PREFIX) else m


def is_valid_model_name(value: str) -> bool:
    return bool(_MODEL_RE.match(value or ""))


def catalog_models() -> list[str]:
    """What Test Connection hands the picker — prefixed, ready to enable."""
    return [f"{MODEL_PREFIX}{m}" for m in KNOWN_CODEX_MODELS]


async def _load_ai_settings(user_id: str) -> dict:
    from app.services.ai.providers.ai_provider_helpers import get_ai_settings

    return await get_ai_settings(user_id)


async def codex_orchestrator_model(user_id: str) -> Optional[str]:
    """The Codex model the user's card names, bare (no prefix), or None.

    None means "the card offers nothing usable" — disabled, absent, empty, or
    a name that would not survive as an argv token — and the caller keeps the
    catalog row's default. Never raises: a settings lookup must not break an
    image dispatch.
    """
    try:
        settings = await _load_ai_settings(user_id)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(
            "[codex-card] settings lookup failed, using catalog default: {}", exc
        )
        return None
    cfg = ((settings or {}).get("ai_providers") or {}).get(PROVIDER_KEY) or {}
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return None
    enabled_raw = cfg.get("enabled_models")
    enabled = (
        [m for m in enabled_raw if isinstance(m, str)]
        if isinstance(enabled_raw, list)
        else []
    )
    # ``selected_model`` counts only when it is one of the chips. The card
    # pre-seeds it with the catalog's first entry on Test Connection (before
    # any chip exists) and never overwrites a present value, so on its own it
    # can name a model the user never enabled. The chips are what the user
    # sees; they win.
    candidates: list[str] = []
    selected = cfg.get("selected_model")
    if isinstance(selected, str) and selected.strip() and selected in enabled:
        candidates.append(selected)
    candidates.extend(enabled)
    for raw in candidates:
        bare = strip_model_prefix(raw)
        if is_valid_model_name(bare):
            return bare
    return None


async def test_codex_local_connection(
    user_id: str,
    *,
    online_device_id: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
    list_devices: Optional[Callable[[str], Awaitable[list[dict[str, Any]]]]] = None,
) -> dict[str, Any]:
    """Same result shape as ``AIProviderFactory.test_connection``:
    ``{success, models, error}`` — the router persists and the card renders
    it without knowing this provider is special.

    Error strings are English literals on purpose: the router stores them in
    ``provider_health`` and the health page stitches operator hints from
    them (see AISettings.tsx ``reportDetail``).
    """
    if online_device_id is None:
        from app.services.codex.daemon_presence import online_device_id as _odi

        online_device_id = _odi
    if list_devices is None:
        from app.repositories.codex_daemon_repository import CodexDaemonRepository

        list_devices = CodexDaemonRepository().list_for_user

    device_id = await online_device_id(user_id)
    if not device_id:
        return {
            "success": False,
            "models": None,
            "error": "No paired device online — pair one under Settings → AI → Local CLI",
        }
    name = str(device_id)
    report: dict[str, Any] = {}
    for d in await list_devices(user_id):
        if str(d.get("id")) == str(device_id):
            report = dict(d.get("env_report") or {})
            name = str(d.get("device_name") or name)
            break
    if not report.get("codex_ok", True):
        return {
            "success": False,
            "models": None,
            "error": f"codex CLI not installed on {name}",
        }
    if not report.get("auth_ok"):
        return {
            "success": False,
            "models": None,
            "error": f"codex is not logged in on {name} — run `codex login` there",
        }
    return {"success": True, "models": catalog_models(), "error": None}
