"""Settings-driven memory slot selection (Phase 1).

Two independent slots:
  memory.l2_provider — user model   (default 'honcho')
  memory.l3_provider — knowledge graph (default 'graphiti')

Phase 1 ships only the current providers; an unknown value falls back to the
current default so a stray setting can never silently disable memory. 'none'
explicitly disables a slot. Mem0/Hindsight arrive in Phase 3.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.services.ai.memory.provider import MemoryProvider
from app.services.ai.memory.providers.graphiti_provider import GraphitiProvider
from app.services.ai.memory.providers.honcho_provider import HonchoProvider


async def _read_provider_setting(key: str, default: str) -> str:
    """Read a memory.* provider setting; ``default`` on any miss/error."""
    try:
        from app.db import engine as db_engine

        value = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": key},
        )
        if value is None:
            return default
        # system_settings.value is JSONB — a quoted string here. asyncpg may
        # hand back the raw JSON text ('"honcho"') or the decoded str; strip
        # surrounding quotes/whitespace defensively.
        text = str(value).strip().strip('"').strip()
        return text or default
    except Exception:  # noqa: BLE001
        logger.warning(
            f"[memory_registry] read {key} failed; using default {default!r}"
        )
        return default


async def l2_provider() -> Optional[MemoryProvider]:
    choice = await _read_provider_setting("memory.l2_provider", "honcho")
    if choice == "none":
        return None
    if choice != "honcho":
        logger.info(
            f"[memory_registry] l2 '{choice}' not available in Phase 1; using honcho"
        )
    return HonchoProvider()


async def l3_provider() -> Optional[MemoryProvider]:
    choice = await _read_provider_setting("memory.l3_provider", "graphiti")
    if choice == "none":
        return None
    if choice != "graphiti":
        logger.info(
            f"[memory_registry] l3 '{choice}' not available in Phase 1; using graphiti"
        )
    return GraphitiProvider()


__all__ = ["l2_provider", "l3_provider"]
