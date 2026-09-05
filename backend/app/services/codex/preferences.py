"""Per-user preferences for the local codex daemon.

Today one knob: which ORCHESTRATOR model ``gpt-image-2-skill`` is told to use
(``--model``). The catalog row's ``actual_model`` is the platform default; a
user who wants a different one — because OpenAI stopped accepting the default
for their account, as on 2026-09-05 — must be able to say so without an admin.

Stored under ``user_settings.settings_json["local_cli"]`` through
``patch_settings_json`` (merge, never clobber — the settings_json lesson).
"""

from __future__ import annotations

import re
from typing import Any, Optional

SETTINGS_KEY = "local_cli"

# Orchestrator models seen accepted for a ChatGPT-account Codex session on
# 2026-09-05 (request create → "Input must be a list", i.e. past the model
# gate). A hint list for the picker, not a gate: the user may type another.
KNOWN_CODEX_MODELS: tuple[str, ...] = ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.5")

# It becomes one argv token on the user's machine. Letters, digits, dot, dash,
# underscore; nothing a shell or a path would read.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_valid_model_name(value: str) -> bool:
    return bool(_MODEL_RE.match(value or ""))


def _repo():
    from app.repositories.user_settings_repository import UserSettingsRepository

    return UserSettingsRepository()


async def get_codex_model(user_id: str) -> Optional[str]:
    """The user's chosen orchestrator model, or None for "use the catalog"."""
    try:
        row = await _repo().get_by_user_id(user_id)
    except Exception:  # noqa: BLE001 — a preference lookup must not break dispatch
        return None
    block = ((row or {}).get("settings_json") or {}).get(SETTINGS_KEY) or {}
    value = block.get("codex_model")
    return value if isinstance(value, str) and is_valid_model_name(value) else None


async def set_codex_model(user_id: str, value: Optional[str]) -> Optional[str]:
    """Persist (or clear, with None). Caller validates; this only writes."""
    await _repo().patch_settings_json(user_id, {SETTINGS_KEY: {"codex_model": value}})
    return value


def preferences_payload(codex_model: Optional[str]) -> dict[str, Any]:
    return {"codex_model": codex_model, "options": list(KNOWN_CODEX_MODELS)}
