# backend/app/services/douyin_parse/ua_pool.py

"""
Centralised Douyin User-Agent pool.

Rule: a single UA is chosen per parse task and reused for every outbound
request in that task — ABogus signatures are bound to the UA string,
so mixing UAs between the sign step and the API fetch causes Douyin to
silently reject the request. The download phase (yt-dlp) also reuses
the same UA so the CDN sees a consistent client across the whole chain.

Callers:
  - parse_tasks.parse_media_task:  pick_ua() once at task start,
    passes the chosen UA to every fallback parser.
  - download_tasks.download_unified_task: reads the UA from task
    metadata and passes it to yt-dlp via http_headers.

Keep the pool Chrome-family only. The pure-Python ABogus engine in
_f2_abogus.py is calibrated for Blink / Chrome fingerprints — Safari /
Firefox / Android UAs produce signatures that Douyin sometimes rejects.
"""

from __future__ import annotations

import random

from app.core.config import settings


def pick_ua() -> str:
    """Pick a Douyin UA from the configured pool.

    Called exactly once per parse task — never per request. This is the
    **only** place a Douyin UA string is produced; every other module
    must receive it as a parameter. If the pool is empty, we raise
    loudly instead of silently using a stale hardcoded default — a
    silent fallback would break ABogus signature consistency.
    """
    pool = settings.DOUYIN_USER_AGENTS or []
    if not pool:
        raise RuntimeError(
            "DOUYIN_USER_AGENTS pool is empty — configure config.yml "
            "or settings.DOUYIN_USER_AGENTS."
        )
    return random.choice(pool)
