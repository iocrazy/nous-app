"""Redis-backed ModelHealthRegistry — share cooldowns across replicas.

Phase K (K3). Sprint 3 ModelHealthRegistry is per-process. Multi-
replica fleet means replica A discovers a 429 on Qwen-Max, marks it
cooled-down, but replica B keeps trying — burns retries on a model
the whole fleet just learned is rate-limited.

Mirrors the I4 RedisBoundsRegistry pattern:
  - mark_cooled_down → also SET redis key with TTL = cooldown_seconds
  - mark_recovered   → also DEL redis key
  - is_available     → consults redis FIRST (multi-process truth) then
                       falls back to local

Optional dependency: redis_client=None falls back to pure in-process
behavior (same shape as Sprint 3 ModelHealthRegistry).
"""
from __future__ import annotations

from typing import Any, Optional

from app.agent_framework.model_health import (
    ModelHealth,
    ModelHealthRegistry,
)


KEY_PREFIX = "model_health:cooldown:"
DEFAULT_LOCAL_TTL_SECONDS = 60.0


class RedisModelHealthRegistry:
    """Wraps a local ModelHealthRegistry with Redis-backed cooldown sharing.

    Same interface as ModelHealthRegistry — drop in replacement.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        default_cooldown_seconds: float = DEFAULT_LOCAL_TTL_SECONDS,
    ) -> None:
        self.redis_client = redis_client
        self.local = ModelHealthRegistry(
            default_cooldown_seconds=default_cooldown_seconds
        )

    # ─── Read API ─────────────────────────────────────────────────

    async def is_available_async(self, model: str) -> bool:
        """Async variant — checks Redis first, then local. Use this in
        async contexts (most fallback chain calls)."""
        if self.redis_client is not None:
            try:
                cooled = await self.redis_client.exists(f"{KEY_PREFIX}{model}")
                if cooled:
                    return False
            except Exception:
                pass
        return self.local.is_available(model)

    def is_available(self, model: str) -> bool:
        """Sync variant — local only (Redis check is async).
        Callers in sync contexts (LLMFallbackChain.call's loop is async,
        so prefer is_available_async there) can use this for fast path."""
        return self.local.is_available(model)

    def health(self, model: str) -> ModelHealth:
        return self.local.health(model)

    def snapshot(self):
        return self.local.snapshot()

    # ─── Write API ────────────────────────────────────────────────

    def mark_cooled_down(
        self,
        model: str,
        *,
        seconds: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Local mark + Redis fan-out (best-effort)."""
        self.local.mark_cooled_down(model, seconds=seconds, reason=reason)
        if self.redis_client is not None:
            ttl = seconds if seconds is not None else DEFAULT_LOCAL_TTL_SECONDS
            if ttl > 0:
                import asyncio

                async def _push():
                    try:
                        await self.redis_client.set(
                            f"{KEY_PREFIX}{model}",
                            reason or "cooled",
                            ex=int(ttl),
                        )
                    except Exception:
                        pass

                try:
                    asyncio.create_task(_push())
                except RuntimeError:
                    # No event loop (sync context) — skip silently
                    pass

    def mark_recovered(self, model: str) -> None:
        self.local.mark_recovered(model)
        if self.redis_client is not None:
            import asyncio

            async def _push():
                try:
                    await self.redis_client.delete(f"{KEY_PREFIX}{model}")
                except Exception:
                    pass

            try:
                asyncio.create_task(_push())
            except RuntimeError:
                pass

    def report_status(self, model: str, status: int) -> None:
        cooldown = ModelHealthRegistry.cooldown_for_status(status)
        if cooldown > 0:
            self.mark_cooled_down(
                model, seconds=cooldown, reason=f"HTTP {status}"
            )

    @staticmethod
    def cooldown_for_status(status: int) -> float:
        return ModelHealthRegistry.cooldown_for_status(status)

    def pick_first_available(self, models: list[str]) -> Optional[str]:
        return self.local.pick_first_available(models)


__all__ = ["KEY_PREFIX", "RedisModelHealthRegistry"]
