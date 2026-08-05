"""Douyin session validation - the only implementation in this codebase.

Do not add a second Douyin session check anywhere (backend, scripts, tests).
The reference project kept two copies; only the CLI one ever received the
"headless causes false expiry" fix, so its web path kept killing healthy
accounts intermittently. Everything that needs to know whether a Douyin session
is alive calls `validate_session` here.
"""

from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlsplit

from ..schemas import EnvironmentConfig, SessionResult
from ..validation import DomValidationSpec, Judgement, run_dom_session_validation
from . import register

PLATFORM = "douyin"

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"

# Only these hosts count. Without a host check, an interstitial on some other
# domain that happens to carry the path in a query string would read as valid.
CREATOR_HOSTS = ("creator.douyin.com",)

UPLOAD_PATH_FRAGMENT = "/content/upload"

# Texts that only render when the user is logged out. "二维码失效" is included
# because an expired QR code is still the login screen - the session is dead
# either way, and the caller re-scans via the login flow, not via validate.
LOGIN_TEXT_MARKERS: tuple[str, ...] = ("手机号登录", "扫码登录", "二维码失效")


def judge_douyin_session(url: str, visible_login_texts: Sequence[str]) -> Judgement:
    """Decide validity from the settled page state. Pure.

    Lenient by design: the *path* has to still be the upload page and no login
    text may be visible. It deliberately does not require an exact URL match -
    Douyin appends query/hash and runs two URL variants of the publish page in
    parallel gray releases.

    The one place it is strict is parsing: the logged-out redirect keeps the
    upload path inside a `redirect_url` query parameter, so a naive
    `"content/upload" in url` substring test - what the reference implementation
    does - calls the login page a valid session.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()

    if host not in CREATOR_HOSTS:
        return Judgement(
            valid=False,
            reason=f"navigated away from the creator host (host={host or 'unknown'})",
        )

    if UPLOAD_PATH_FRAGMENT not in parts.path:
        return Judgement(
            valid=False,
            reason=f"redirected off the upload page (path={parts.path or '/'})",
        )

    if visible_login_texts:
        return Judgement(
            valid=False,
            reason="login prompt visible on the upload page: "
            + ", ".join(visible_login_texts),
        )

    return Judgement(valid=True, reason="upload page reached with no login prompt")


SPEC = DomValidationSpec(
    platform=PLATFORM,
    target_url=UPLOAD_URL,
    login_text_markers=LOGIN_TEXT_MARKERS,
    judge=judge_douyin_session,
)


async def validate_session(
    storage_state: dict[str, Any], environment: EnvironmentConfig | None = None
) -> SessionResult:
    return await run_dom_session_validation(SPEC, storage_state, environment)


register(PLATFORM, validate_session)
