# backend/app/services/media/parsers/soda_music/cookie_source.py
"""Soda login cookie provider.

PHASE 2 STOPGAP: reads the SODA_COOKIE env var. PHASE 3 will replace the body
with a read from the user_cookies table (platform='qishui') keyed by user_id —
the signature stays the same so no caller changes.
"""

from __future__ import annotations

import os


async def get_soda_cookie(user_id: str | None) -> str:
    """Return the Soda cookie string for a user (empty if none configured)."""
    return os.environ.get("SODA_COOKIE", "")
