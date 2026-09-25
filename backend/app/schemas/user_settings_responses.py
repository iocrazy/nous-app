"""Response shapes for ``/api/v1/settings/*`` (per-platform cookies and headers).

Every body here is the exact dict the handler built before it had a model;
``tests/api/test_user_settings_wire.py`` pins that. None of them carries cookie
content: the cookie routes answer with the platform only, and the cookie list
(``CookieListResponse`` in the router) reports presence and validity.
"""

from __future__ import annotations

from pydantic import BaseModel


class SettingsPlatformWriteResult(BaseModel):
    """``{"success": true, "platform": ...}`` from the cookie/header writes.

    ``success`` is always true on a 2xx: every failure is an HTTP error.
    """

    success: bool
    platform: str


class SettingsPlatformHeaders(BaseModel):
    """``GET /settings/headers/{platform}``.

    ``headers_text`` is ``""`` when the caller has no row for the platform and
    ``null`` when the row exists (a saved cookie) but headers were never set.
    """

    platform: str
    headers_text: str | None
