"""KeyRotator — same-provider multi-key rotation with cooldown.

Defends against single-key rate-limit lock-out: when a user fills 3
doubao keys, hitting 429 on key #1 silently swaps to key #2 instead of
failing the whole request.

This is the primitive. Wiring into the adapter factory + per-user
``ai_providers`` schema (allow ``api_key`` to be ``str | List[str]``)
is a separate follow-up commit — schema migration touches frontend
forms too.

Mirrors OpenClaw ``agents/api-key-rotation.ts``.
"""
from __future__ import annotations

import time
from typing import List, Optional

# Cooldown durations per HTTP status — calibrated to provider recovery
# windows. Short enough that legitimate users don't notice; long enough
# that the rotator doesn't immediately ping a freshly-limited key.
_COOLDOWN_BY_STATUS: dict[int, float] = {
    # Rate limits — usually clear within seconds, give the bucket time
    429: 60.0,
    # Auth failures — bad/expired key, give it 1 hour before retry
    # (operator likely needs to fix the config; quicker retry just
    # spams the provider)
    401: 3600.0,
    403: 3600.0,
    # Transient server errors — short cooldown, recovery is fast
    500: 30.0,
    502: 30.0,
    503: 30.0,
    504: 30.0,
}


class AllKeysCooledDown(Exception):
    """Every key in the rotator is currently in cooldown. Caller should
    surface a "rate limited, try later" error to the user."""


class KeyRotator:
    """Round-robin rotator with per-key cooldown.

    Not thread-safe — each rotator instance is owned by one async caller.
    For multi-process scenarios use one rotator per process; cooldowns
    don't replicate across processes (provider rate limits are usually
    per-key globally so each process discovering 429 independently is OK).
    """

    def __init__(self, keys: List[str]) -> None:
        # Dedupe while preserving order — user may have pasted the same
        # key twice by accident, no point treating them as separate.
        seen: set[str] = set()
        deduped: list[str] = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                deduped.append(k)
        if not deduped:
            raise ValueError("KeyRotator requires at least one non-empty key")

        self._keys: list[str] = deduped
        self._cooldown_until: dict[str, float] = {}
        self._cursor: int = 0  # round-robin starting point

    def next_key(self) -> str:
        """Return the next available key. Skip any in cooldown.

        Raises:
            AllKeysCooledDown: every key is in cooldown.
        """
        now = time.time()
        # Walk all keys exactly once starting from cursor
        for i in range(len(self._keys)):
            idx = (self._cursor + i) % len(self._keys)
            key = self._keys[idx]
            cooled_until = self._cooldown_until.get(key, 0.0)
            if cooled_until <= now:
                self._cursor = (idx + 1) % len(self._keys)  # advance cursor
                return key

        raise AllKeysCooledDown(
            f"all {len(self._keys)} keys in cooldown"
        )

    def mark_cooldown(self, key: str, seconds: float) -> None:
        """Put ``key`` in cooldown for ``seconds`` from now."""
        if seconds <= 0:
            return
        self._cooldown_until[key] = time.time() + seconds

    @staticmethod
    def cooldown_for_status(status: int) -> float:
        """Return the recommended cooldown duration for an HTTP status.

        0 means "don't mark cooldown" (success or non-key fault)."""
        return _COOLDOWN_BY_STATUS.get(status, 0.0)

    def report_status(self, key: str, status: int) -> None:
        """Convenience: classify status + mark cooldown if applicable.

        Combines ``cooldown_for_status`` + ``mark_cooldown`` so callers
        in the retry middleware can do one call per response."""
        cooldown = self.cooldown_for_status(status)
        if cooldown > 0:
            self.mark_cooldown(key, cooldown)

    def __len__(self) -> int:
        return len(self._keys)

    def usable_count(self) -> int:
        """How many keys are NOT in cooldown right now."""
        now = time.time()
        return sum(
            1
            for k in self._keys
            if self._cooldown_until.get(k, 0.0) <= now
        )
