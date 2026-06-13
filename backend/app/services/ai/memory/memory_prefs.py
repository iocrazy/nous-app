"""Per-user memory preferences (Claude-style toggles).

Two switches stored in ``user_settings.settings_json.ai_settings``:

    memory_learn_enabled   — write side: may chats feed the Honcho
                             user model? (write_memory step 4)
    memory_inject_enabled  — read side: may the user model be injected
                             into chat prompts? (<user_context>, L2)

Both default True (memory on unless explicitly disabled) and FAIL OPEN
on lookup errors — a settings-table hiccup must not silently change
memory behaviour mid-conversation. The global FEATURE_HONCHO_MEMORY
flag still gates everything above these.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.repositories.user_settings_repository import UserSettingsRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryPrefs:
    learn: bool = True
    inject: bool = True


async def get_memory_prefs(user_id: str) -> MemoryPrefs:
    """Read the user's memory toggles; defaults/fail-open to enabled."""
    try:
        repo = UserSettingsRepository()
        settings = await repo.get_by_user_id(user_id)
        ai_settings = ((settings or {}).get("settings_json") or {}).get(
            "ai_settings"
        ) or {}
        return MemoryPrefs(
            learn=ai_settings.get("memory_learn_enabled", True) is not False,
            inject=ai_settings.get("memory_inject_enabled", True) is not False,
        )
    except Exception:  # noqa: BLE001 — fail open, never block chat paths
        logger.warning("[memory_prefs] lookup failed for %s; defaulting on", user_id)
        return MemoryPrefs()


__all__ = ["MemoryPrefs", "get_memory_prefs"]
