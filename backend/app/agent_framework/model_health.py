"""ModelHealthRegistry — per-model availability tracking with TTL cooldown.

Mirrors part of OpenClaw agents/model-fallback.ts state machine
(cooldown phase). Probe + preserve-slot deferred to follow-up.

Today llm_fallback_chain walks primary → fallbacks linearly per call.
If primary failed 5s ago because of 429, the next call STILL tries
primary (waits N retries again) before falling to fallback. Wastes
5-30s of retry budget per call on a known-bad model.

This registry remembers which models are cooled-down and lets the chain
skip them until cooldown expires. Per-model state, per-process (not
shared across workers — each process discovers cooldowns independently
which is OK for provider rate limits that are global per key).

Usage from llm_fallback_chain:

    from app.agent_framework import ModelHealthRegistry

    health = ModelHealthRegistry()  # per-process singleton typically

    for model in [primary, *fallbacks]:
        if not health.is_available(model):
            continue  # skip — recently failed, still cooling down
        try:
            response = await call_with_retries(model)
            return response
        except LLMRetryExhausted as exc:
            status = _extract_status(exc)
            health.report_status(model, status)
            continue
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ModelHealth(str, Enum):
    """Per-model availability state. PROBING reserved for future
    (cheap test before full restoration); not used yet."""

    AVAILABLE = "available"
    COOLED_DOWN = "cooled_down"
    PROBING = "probing"  # reserved


# Per-status cooldown durations. Calibrated to provider recovery windows.
_COOLDOWN_BY_STATUS: dict[int, float] = {
    429: 60.0,      # rate limit — usually clears in seconds
    401: 3600.0,    # auth — operator likely needs to fix config
    403: 3600.0,    # forbidden — same as 401
    500: 30.0,      # transient server
    502: 30.0,
    503: 30.0,
    504: 30.0,
}


@dataclass
class _ModelState:
    health: ModelHealth = ModelHealth.AVAILABLE
    cooldown_until: float = 0.0
    reason: Optional[str] = None


class ModelHealthRegistry:
    """Per-process model health tracker.

    Not thread-safe; single asyncio event loop per registry instance.
    For multi-process deployments each process has its own — provider
    rate limits are global per key so each discovering 429 independently
    is fine.
    """

    def __init__(self, *, default_cooldown_seconds: float = 60.0) -> None:
        self._states: dict[str, _ModelState] = {}
        self._default_cooldown = default_cooldown_seconds

    def health(self, model: str) -> ModelHealth:
        """Current health of ``model``. AVAILABLE for unknown models
        (assume good until proven bad)."""
        state = self._states.get(model)
        if state is None:
            return ModelHealth.AVAILABLE
        # Lazy expire: if cooldown elapsed, flip back to AVAILABLE
        if state.health == ModelHealth.COOLED_DOWN and time.time() >= state.cooldown_until:
            state.health = ModelHealth.AVAILABLE
            state.reason = None
        return state.health

    def is_available(self, model: str) -> bool:
        return self.health(model) == ModelHealth.AVAILABLE

    def mark_cooled_down(
        self,
        model: str,
        *,
        seconds: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Put ``model`` in cooldown for ``seconds`` (or default)."""
        ttl = seconds if seconds is not None else self._default_cooldown
        if ttl <= 0:
            return
        state = self._states.setdefault(model, _ModelState())
        state.health = ModelHealth.COOLED_DOWN
        state.cooldown_until = time.time() + ttl
        state.reason = reason

    def mark_recovered(self, model: str) -> None:
        """Force-clear cooldown — successful call after cooldown expired,
        or operator manually marked recovered via admin."""
        state = self._states.get(model)
        if state is None:
            return
        state.health = ModelHealth.AVAILABLE
        state.cooldown_until = 0.0
        state.reason = None

    @staticmethod
    def cooldown_for_status(status: int) -> float:
        """Recommended cooldown duration for HTTP ``status``. 0 = no cooldown."""
        return _COOLDOWN_BY_STATUS.get(status, 0.0)

    def report_status(self, model: str, status: int) -> None:
        """Convenience: classify ``status`` and apply cooldown if needed."""
        cooldown = self.cooldown_for_status(status)
        if cooldown > 0:
            self.mark_cooled_down(
                model, seconds=cooldown, reason=f"HTTP {status}"
            )

    def pick_first_available(self, models: list[str]) -> Optional[str]:
        """First entry in ``models`` whose health is AVAILABLE, or None
        if everything is cooled down."""
        for m in models:
            if self.is_available(m):
                return m
        return None

    def snapshot(self) -> dict[str, dict]:
        """Admin UI / monitoring: per-model current state."""
        now = time.time()
        out: dict[str, dict] = {}
        for model, state in self._states.items():
            # Re-evaluate so expired cooldowns appear available
            current = self.health(model)
            remaining = max(0.0, state.cooldown_until - now) if current == ModelHealth.COOLED_DOWN else 0.0
            out[model] = {
                "health": current.value,
                "reason": state.reason,
                "cooldown_remaining_s": round(remaining, 1),
            }
        return out
