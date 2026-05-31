"""Validate a Soda cookie via a lightweight authenticated call.

Uses /luna/pc/me (get_me): a logged-in cookie returns my_info.id. VIP /
full-stream capability is NOT checked here — that surfaces at download time as
SodaPreviewError (§A.4). Returns (is_valid, error_message)."""

from __future__ import annotations

from typing import Any

from app.services.media.parsers.soda_music.soda_api import SodaApiClient


async def validate_soda_cookie(
    cookie: str, *, api: Any | None = None
) -> tuple[bool, str | None]:
    if not cookie:
        return False, "empty cookie"
    client = api or SodaApiClient(cookie=cookie)
    try:
        me = await client.get_me()
    except Exception as exc:  # noqa: BLE001 — any failure means the cookie is unusable
        return False, str(exc)[:200]
    uid = (me.get("my_info", {}) or {}).get("id")
    if not uid:
        return False, "not logged in (no my_info.id)"
    return True, None
