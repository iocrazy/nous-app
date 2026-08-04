"""Runtime settings, sourced from environment variables only.

This service holds decrypted session material in memory and must not read or
write any config file that could end up on disk alongside credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Shared secret for X-Internal-Token. Empty means "not configured" and the
    # service fails closed (503) rather than accepting unauthenticated calls.
    internal_token: str
    # X display the entrypoint's Xvfb owns. Headed Chromium needs it.
    display: str
    # Per-navigation budget. Douyin's creator centre is a slow SPA; sau's 5s
    # wait_for_url is exactly the false-negative source we are avoiding.
    nav_timeout_ms: int
    # Idle time after domcontentloaded before reading the URL, so a transient
    # client-side redirect does not get sampled as the final state.
    settle_ms: int
    # Retry count for session validation (spec 7.1 "three-piece set").
    validate_attempts: int
    # Wall-clock budget for one /session/validate call across all attempts.
    validate_total_timeout_s: int
    # Per-attempt ceiling; also the minimum slack required to start a retry.
    validate_attempt_timeout_s: int
    # Concurrent headed Chromium instances. Headed contexts are memory hogs.
    max_concurrent_browsers: int
    # How long to wait for a free browser slot before giving up.
    browser_slot_wait_s: int
    # TTL for the cached /healthz browser probe, so healthchecks do not launch
    # a Chromium per hit.
    health_probe_ttl_s: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        internal_token=os.environ.get("BROWSER_INTERNAL_TOKEN", "").strip(),
        display=os.environ.get("DISPLAY", ":99").strip() or ":99",
        nav_timeout_ms=_int_env("BROWSER_NAV_TIMEOUT_MS", 90_000),
        settle_ms=_int_env("BROWSER_SETTLE_MS", 2_500),
        validate_attempts=max(1, _int_env("BROWSER_VALIDATE_ATTEMPTS", 3)),
        validate_total_timeout_s=_int_env("BROWSER_VALIDATE_TOTAL_TIMEOUT_S", 180),
        validate_attempt_timeout_s=_int_env("BROWSER_VALIDATE_ATTEMPT_TIMEOUT_S", 120),
        max_concurrent_browsers=max(1, _int_env("BROWSER_MAX_CONCURRENT", 4)),
        browser_slot_wait_s=_int_env("BROWSER_SLOT_WAIT_S", 30),
        health_probe_ttl_s=_int_env("BROWSER_HEALTH_PROBE_TTL_S", 60),
    )
