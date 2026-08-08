"""Bilibili (B站) session validation + QR login.

Same contract as `douyin.py` / `xiaohongshu.py`: platform facts + two pure
judges, nothing platform-neutral.

**Bilibili differs from the other two in ways that matter here:**

1. **Two hosts, not one.** The QR code is rendered on `passport.bilibili.com`
   (or the `www.bilibili.com` login modal), while the creator console lives on
   `member.bilibili.com`. A login therefore *changes host* on success — the
   judge below treats reaching the member host as the success signal, and must
   NOT treat "left the passport host" as a failure.
2. **The console is the studio**, `member.bilibili.com/platform/upload/video`.
3. Its QR flow exposes a real polling API, but we stay at the DOM level like
   the other platforms — one mechanism, one place to debug.

**Verification status.** Every constant is tagged the same way as the
Xiaohongshu module:

- `[URL]`   — documented entry points, low risk.
- `[COPY]`  — Chinese UI copy. **UNVERIFIED against a live page.**
- `[GUESS]` — DOM selectors. **UNVERIFIED.**

Calibrate by binding one account, then counting `get_by_text(..., exact=True)`
matches on the live pages. Anything not exactly 1 is wrong. Douyin's history
(three inferred profile selectors, all wrong, green tests throughout) is the
reason this warning is repeated in every platform module.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from ..login import LoginFlowSpec, LoginJudgement, LoginPageSnapshot, LoginProfile
from ..schemas import EnvironmentConfig, SessionResult, SessionStatus
from ..validation import DomValidationSpec, Judgement, run_dom_session_validation
from . import register, register_login

PLATFORM = "bilibili"

# --- session validation -----------------------------------------------------

# [URL] The creator studio's video upload page.
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"
MEMBER_HOSTS = ("member.bilibili.com",)
UPLOAD_PATH_FRAGMENT = "/platform/upload"

# [COPY] UNVERIFIED. Broad on purpose — missing a marker means reading a login
# page as a live session, which is the expensive direction.
LOGIN_TEXT_MARKERS = (
    "登录",
    "扫码登录",
    "短信登录",
    "密码登录",
    "请使用哔哩哔哩客户端扫码登录",
)


def judge_bilibili_session(url: str, visible_login_texts: Sequence[str]) -> Judgement:
    """Decide validity from the settled page. Pure.

    Parses the URL rather than substring-matching it: bilibili's logged-out
    redirect carries the original destination in a `returnUrl`/`gourl` query
    parameter, so `"/platform/upload" in url` would read the *login* page as a
    valid session — the same trap documented in the Douyin judge.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()

    if host not in MEMBER_HOSTS:
        return Judgement(
            valid=False,
            reason=f"navigated away from the member host (host={host or 'unknown'})",
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
    judge=judge_bilibili_session,
)


async def validate_session(
    storage_state: dict[str, Any], environment: EnvironmentConfig | None = None
) -> SessionResult:
    return await run_dom_session_validation(SPEC, storage_state, environment)


register(PLATFORM, validate_session)

# --- QR login ---------------------------------------------------------------

# [URL] Passport login page (renders the QR code).
LOGIN_URL = "https://passport.bilibili.com/login"
# [URL] Creator studio home — where the identity is read after login.
PROFILE_URL = "https://member.bilibili.com/platform/home"

# ⚠️ Login STARTS on passport.* and SUCCEEDS on member.* — the host changes.
# `judge_bilibili_login` must therefore accept both, and must not read "no
# longer on the passport host" as a failure. This is the one structural
# difference from Douyin / Xiaohongshu, where login begins and ends on the same
# host.
PASSPORT_HOSTS = ("passport.bilibili.com", "www.bilibili.com")
CONSOLE_PATH_FRAGMENT = "/platform"

# [GUESS] UNVERIFIED. QR image candidates, priority order.
QRCODE_SELECTORS = (
    'div[class*="qrcode"] img',
    'img[class*="qrcode"]',
    'div[class*="login-scan"] img',
    ".login-scan-box img",
)

# [COPY] UNVERIFIED — the login sub-states.
LOGIN_MARKERS = ("扫码登录", "请使用哔哩哔哩客户端扫码登录", "短信登录", "密码登录")
SCANNED_MARKERS = ("扫码成功", "请在手机端确认", "已扫描")
EXPIRED_MARKERS = ("二维码已失效", "点击刷新", "已过期")

# [GUESS] UNVERIFIED. Re-issue an expired code.
REFRESH_SELECTORS = (
    'div[class*="qrcode"]',
    'div[class*="refresh"]',
    ".login-scan-box",
)

# [GUESS] UNVERIFIED. SMS fallback.
SMS_INPUT_SELECTORS = (
    'input[placeholder*="验证码"]',
    'input[placeholder*="短信"]',
)
SMS_SUBMIT_SELECTORS = (
    'button:has-text("登录")',
    '.btn-login',
)

# [GUESS] UNVERIFIED. Identity on the studio home page.
PROFILE_TEXT_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "username": (
        '[class*="nickname"]',
        '[class*="user-name"]',
        '[class*="userName"]',
    ),
    "platform_user_id": (
        '[class*="uid"]',
        '[class*="mid"]',
    ),
}
PROFILE_ATTR_SELECTORS: Mapping[str, tuple[tuple[str, ...], str]] = {
    "avatar_url": (
        (
            'img[src*="hdslb.com"]',   # bilibili's CDN — the most stable handle
            '[class*="avatar"] img',
            'img[class*="avatar"]',
        ),
        "src",
    ),
}

# `DedeUserID` is bilibili's numeric user id cookie and is the reliable
# fallback identity when every profile selector misses.
USER_ID_COOKIES = ("DedeUserID",)

# [COPY] Prefixes shown in front of the public id.
_ID_PREFIXES = ("UID：", "UID:", "UID", "ID：", "ID:")


def judge_bilibili_login(snapshot: LoginPageSnapshot) -> LoginJudgement:
    """What state is the login page in? Pure.

    Specific → general, same as the other platforms: an expired code and a
    scanned code both still render login copy, so testing `login_texts` first
    would swallow both.
    """
    if snapshot.expired_texts:
        return LoginJudgement(
            SessionStatus.QRCODE_EXPIRED,
            "the QR code expired: " + ", ".join(snapshot.expired_texts),
        )

    if snapshot.scanned_texts:
        return LoginJudgement(
            SessionStatus.WAITING_CONFIRM,
            "scanned; waiting for confirmation on the phone",
        )

    if snapshot.sms_input_visible:
        return LoginJudgement(
            SessionStatus.WAITING_SMS,
            "the platform is asking for an SMS code",
        )

    parts = urlsplit(snapshot.url or "")
    host = (parts.hostname or "").lower()

    # Success = arrived on the MEMBER host. Unlike the other platforms this is a
    # different host from where login started, which is why it is checked before
    # anything else host-related.
    if host in MEMBER_HOSTS:
        return LoginJudgement(SessionStatus.SESSION_VALID, "reached the creator studio")

    if snapshot.login_texts or snapshot.qrcode_visible:
        return LoginJudgement(
            SessionStatus.WAITING_SCAN, "waiting for the code to be scanned"
        )

    # Still on passport/www with nothing recognisable yet — the login card is
    # injected by client-side JS, so an early read legitimately sees nothing.
    if host in PASSPORT_HOSTS:
        return LoginJudgement(
            SessionStatus.WAITING_SCAN,
            f"no recognisable login state on the page yet (path={parts.path or '/'})",
        )

    return LoginJudgement(
        SessionStatus.FAILED,
        f"navigated to an unexpected host (host={host or 'unknown'})",
    )


def parse_bilibili_profile(
    fields: Mapping[str, str], cookies: Sequence[Mapping[str, Any]]
) -> LoginProfile:
    """Scraped strings -> identity. Pure, best-effort throughout.

    `DedeUserID` makes the fallback unusually reliable here: even with every
    display selector broken, a bound account still gets its real numeric id
    rather than an opaque session token.
    """
    username = (fields.get("username") or "").strip()

    raw_id = (fields.get("platform_user_id") or "").strip()
    for prefix in _ID_PREFIXES:
        if raw_id.startswith(prefix):
            raw_id = raw_id[len(prefix) :].strip()
            break

    if not raw_id:
        by_name = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
        for name in USER_ID_COOKIES:
            value = (by_name.get(name) or "").strip()
            if value:
                raw_id = value
                break

    avatar = (fields.get("avatar_url") or "").strip()
    if not avatar.startswith(("http://", "https://", "data:image")):
        avatar = ""

    return LoginProfile(
        platform_user_id=raw_id,
        username=username,
        avatar_url=avatar or None,
    )


LOGIN_SPEC = LoginFlowSpec(
    platform=PLATFORM,
    login_url=LOGIN_URL,
    profile_url=PROFILE_URL,
    qrcode_selectors=QRCODE_SELECTORS,
    login_markers=LOGIN_MARKERS,
    scanned_markers=SCANNED_MARKERS,
    expired_markers=EXPIRED_MARKERS,
    refresh_selectors=REFRESH_SELECTORS,
    sms_input_selectors=SMS_INPUT_SELECTORS,
    sms_submit_selectors=SMS_SUBMIT_SELECTORS,
    judge=judge_bilibili_login,
    parse_profile=parse_bilibili_profile,
    profile_text_selectors=PROFILE_TEXT_SELECTORS,
    profile_attr_selectors=PROFILE_ATTR_SELECTORS,
)

register_login(PLATFORM, LOGIN_SPEC)
