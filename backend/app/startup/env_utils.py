"""Defensive env parsing for daemon startup paths.

Daemon loops (`long_running=True` bg tasks) parse their tuning knobs from
env vars BEFORE the per-iteration try/except — a malformed value used to
kill the loop at boot with only a log line to show for it. Parse with a
logged fallback instead: a mistyped `DBOS_REAP_INTERVAL_SECONDS=120s`
should degrade to the default, not silently disable the reaper.
"""

from __future__ import annotations

import os

from loguru import logger


def env_int(name: str, default: int) -> int:
    """``int(os.environ[name])`` with a logged fallback on missing/garbage."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            f"env {name}={raw!r} is not an integer — falling back to {default}"
        )
        return default
