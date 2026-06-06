"""Soda login cookie provider — reads the per-user user_cookies table.

Replaces the Phase 2 env stopgap. Platform key is 'qishui' (matches URLRouter
+ the cookie API). Signature stays back-compatible: callers pass only user_id;
`repo` is injectable for tests.
"""

from __future__ import annotations

from typing import Any

SODA_COOKIE_PLATFORM = "qishui"


async def get_soda_cookie(user_id: str | None, *, repo: Any | None = None) -> str:
    """Return the Soda cookie for a user from user_cookies (empty if none)."""
    if not user_id:
        return ""
    if repo is None:
        from app.repositories.cookies_repository import (
            get_cookies_repository,
        )

        repo = get_cookies_repository()
    row = await repo.get_by_user_and_platform(user_id, SODA_COOKIE_PLATFORM)
    if not row:
        return ""
    return row.get("cookie_text") or row.get("cookie_file") or ""
