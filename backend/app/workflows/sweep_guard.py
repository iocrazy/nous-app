"""sweep_guard — shared restart-grace gate for the health sweeper + the
stuck-task reaper (G2).

A deploy/restart leaves `started_at` unchanged and heartbeat stale across
the restart gap. The sweeper (every 2 min) and the reaper (every 15 min)
would then immediately mark in-flight work LOST/stuck the moment the new
process comes up — even though DBOS is about to recover those workflows.

We record the process boot time once at import (a tiny, cycle-free module
both sweeper and reaper can import) and have both gate all LOST/stuck
marking until a grace window has elapsed since boot.

Grace defaults to 300s and is overridable via the
`DBOS_SWEEP_BOOT_GRACE_SECONDS` env var.
"""

from __future__ import annotations

import os
import time

# Captured once, at first import in the process. Monotonic so it is immune
# to wall-clock adjustments (NTP steps, manual clock changes).
_BOOT_MONOTONIC: float = time.monotonic()

_DEFAULT_GRACE_SECONDS = 300


def boot_grace_seconds() -> int:
    """Grace window length in seconds. Reads DBOS_SWEEP_BOOT_GRACE_SECONDS,
    falling back to 300 when unset, non-numeric, or negative."""
    raw = os.environ.get("DBOS_SWEEP_BOOT_GRACE_SECONDS")
    if raw is None:
        return _DEFAULT_GRACE_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_GRACE_SECONDS
    return value if value >= 0 else _DEFAULT_GRACE_SECONDS


def within_boot_grace(now_monotonic: "float | None" = None) -> bool:
    """True while the process is still inside its post-boot grace window —
    callers must SKIP all LOST/stuck marking. `now_monotonic` is injectable
    for tests; defaults to the real monotonic clock."""
    now = time.monotonic() if now_monotonic is None else now_monotonic
    return (now - _BOOT_MONOTONIC) < boot_grace_seconds()
