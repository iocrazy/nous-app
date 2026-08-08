"""Xiaohongshu (小红书) session validation + QR login.

Same shape as `douyin.py`: this module contributes only **platform facts** —
where to navigate, which on-page texts mean what — plus two **pure judge
functions**. Everything platform-neutral (driving the browser, bounding waits,
turning failures into typed statuses) lives in `validation.py` / `login.py`.

**Verification status — read this before trusting a selector.**

Douyin's module carries the scars of getting this wrong: a generation of
profile selectors was written by *inference* from the framework's markup, the
unit tests were written against the same guess, everything was green, and the
first real run found all three selectors wrong (2026-08-07). The lesson written
into that file applies double here:

> an inferred selector is not a weak fact, it is a *placeholder* for a fact.

So every constant below is tagged:

- `[URL]`     — the platform's documented/stable entry points. Low risk.
- `[COPY]`    — Chinese UI copy. **UNVERIFIED against a live page.** Chosen to
                be the phrases a logged-out / scanning state actually shows,
                but nobody has counted matches on a real session yet.
- `[GUESS]`   — DOM selectors. **UNVERIFIED.** Xiaohongshu, like Douyin, ships
                hashed CSS-Modules class names, so these are written as
                attribute/prefix matches and text is preferred wherever the
                shared helpers allow it.

**Calibration procedure** (do this before trusting publishing on this
platform): bind one account through the QR flow, then use the live session to
open the pages below and count matches for each string with
`get_by_text(..., exact=True)`. Anything that is not exactly 1 is wrong. That
is how the Douyin selectors were fixed, and it is the only method that has ever
produced a correct selector in this codebase.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from ..login import LoginFlowSpec, LoginJudgement, LoginPageSnapshot, LoginProfile
from ..schemas import EnvironmentConfig, SessionResult, SessionStatus
from ..validation import DomValidationSpec, Judgement, run_dom_session_validation
from . import register, register_login

PLATFORM = "xiaohongshu"

# --- session validation -----------------------------------------------------

# [URL] The creator platform's publish entry. Validation navigates here because
# a live session lands on it while a dead one is bounced to login.
UPLOAD_URL = "https://creator.xiaohongshu.com/publish/publish"
CREATOR_HOSTS = ("creator.xiaohongshu.com",)
UPLOAD_PATH_FRAGMENT = "/publish"

# [COPY] UNVERIFIED. Texts that mean "you are looking at a login screen".
# Kept broad on purpose: a missed marker reads a login page as a live session,
# which is the expensive direction of this error — the caller would go on to
# "publish" into a logged-out page.
LOGIN_TEXT_MARKERS = (
    "扫码登录",
    "手机号登录",
    "登录",
    "新用户注册",
)


def judge_xiaohongshu_session(
    url: str, visible_login_texts: Sequence[str]
) -> Judgement:
    """Decide validity from the settled page. Pure — mirrors Douyin's judge.

    Strict about *parsing* the URL rather than substring-matching it: a
    logged-out redirect commonly carries the original path inside a
    `redirect`/`redirectUrl` query parameter, so `"/publish" in url` would call
    the login page a valid session. `urlsplit(...).path` cannot be fooled that
    way.
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
            reason=f"redirected off the publish page (path={parts.path or '/'})",
        )

    if visible_login_texts:
        return Judgement(
            valid=False,
            reason="login prompt visible on the publish page: "
            + ", ".join(visible_login_texts),
        )

    return Judgement(valid=True, reason="publish page reached with no login prompt")


SPEC = DomValidationSpec(
    platform=PLATFORM,
    target_url=UPLOAD_URL,
    login_text_markers=LOGIN_TEXT_MARKERS,
    judge=judge_xiaohongshu_session,
)


async def validate_session(
    storage_state: dict[str, Any], environment: EnvironmentConfig | None = None
) -> SessionResult:
    return await run_dom_session_validation(SPEC, storage_state, environment)


register(PLATFORM, validate_session)

# --- QR login ---------------------------------------------------------------

# [URL] Login page that renders the QR code.
LOGIN_URL = "https://creator.xiaohongshu.com/login"
# [URL] Where to read the account identity once logged in.
PROFILE_URL = "https://creator.xiaohongshu.com/new/home"
CONSOLE_PATH_FRAGMENT = "/new/"

# ⚠️ 小红书创作平台**没有扫码登录**,只有短信/密码。
#
# 2026-08-08 实测 https://creator.xiaohongshu.com/login:
#   - 大图(>80px)      : 0 个
#   - canvas            : 0 个
#   - 含"扫码/二维码/QR"的可点元素: 0 个
#   - 页面文案          : 短信登录 / 发送验证码 / 收不到验证码? / 忘记密码?
#
# 我最初照抖音的模式假设它默认扫码,那是**把一个平台的形态套到另一个平台**
# —— 用户第一次点绑定就拿到 "login page rendered no QR code"。
#
# 所以这里走**短信通道**:登录 flow 的 `sms_input_selectors` /
# `submit_sms_code()` / 后端 `/login/{task_id}/sms` 端点本来就都在,只是此前
# 只当抖音的降级路径用。二维码选择器留空 —— 留空是事实陈述,填几个猜的
# 反而会让 `qrcode_visible` 永远为 False 却看起来"配了"。
QRCODE_SELECTORS: tuple[str, ...] = ()

# [VERIFIED 2026-08-08] 精确匹配数各为 1。
# ⚠️「登 录」中间**有一个空格**,`登录` 的精确匹配是 0 —— 又一个"猜的字符串
# 一定会错"的例子。
LOGIN_MARKERS = ("短信登录", "发送验证码", "登 录")
# 短信通道没有"已扫描"这个状态。留空而不是编几个词。
SCANNED_MARKERS: tuple[str, ...] = ()
EXPIRED_MARKERS: tuple[str, ...] = ()
REFRESH_SELECTORS: tuple[str, ...] = ()

# [VERIFIED 2026-08-08] placeholder 实测值。页面上有 5 个 input
# (请选择选项 / 手机号 / 验证码 / 邮箱 / 密码),验证码那个是我们要的。
SMS_INPUT_SELECTORS = (
    'input[placeholder="验证码"]',
    'input[placeholder*="验证码"]',
)
SMS_SUBMIT_SELECTORS = (
    'button:has-text("登 录")',
    ".beer-login-bt",
)

# [GUESS] UNVERIFIED — 待绑定成功后用活会话校准。
PROFILE_TEXT_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "username": (
        '[class*="nickname"]',
        '[class*="user-name"]',
        '[class*="userName"]',
    ),
    "platform_user_id": (
        '[class*="red-id"]',
        '[class*="redId"]',
        '[class*="account-id"]',
    ),
}
PROFILE_ATTR_SELECTORS: Mapping[str, tuple[tuple[str, ...], str]] = {
    "avatar_url": (
        (
            'img[src*="avatar"]',
            '[class*="avatar"] img',
            'img[class*="avatar"]',
        ),
        "src",
    ),
}

USER_ID_COOKIES = ("customer-sso-sid", "customerClientId", "userId")
_ID_PREFIXES = ("小红书号：", "小红书号:", "小红书号", "ID：", "ID:")


def judge_xiaohongshu_login(snapshot: LoginPageSnapshot) -> LoginJudgement:
    """What state is the login page in? Pure.

    Order matters and is not arbitrary — it goes from most specific to least:
    an expired code and a scanned code both still render login copy, so testing
    `login_texts` first would swallow both.
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

    # Landed inside the console = logged in. Checked before the login-copy test
    # because the console briefly renders shared chrome that can match.
    if host in CREATOR_HOSTS and CONSOLE_PATH_FRAGMENT in parts.path:
        return LoginJudgement(SessionStatus.SESSION_VALID, "reached the creator console")

    # 这个平台没有扫码,登录页 == 等待用户输入手机号并提交验证码。
    # 报 WAITING_SCAN 会让 UI 去等一张永远不出现的二维码。
    if snapshot.login_texts:
        return LoginJudgement(
            SessionStatus.WAITING_SMS,
            "SMS login page: enter the phone number and submit the code",
        )

    if host in CREATOR_HOSTS:
        return LoginJudgement(
            SessionStatus.WAITING_SMS,
            f"no recognisable login state on the page yet (path={parts.path or '/'})",
        )

    return LoginJudgement(
        SessionStatus.FAILED,
        f"navigated to an unexpected host (host={host or 'unknown'})",
    )


def parse_xiaohongshu_profile(
    fields: Mapping[str, str], cookies: Sequence[Mapping[str, Any]]
) -> LoginProfile:
    """Scraped strings -> account identity. Pure, and best-effort throughout.

    Everything degrades rather than fails: a console redesign that breaks the
    display-name selector must leave a nameless-but-usable account, never abort
    a login the user already completed by scanning.
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
        # Relative / protocol-less values render as a broken image, which is
        # worse than showing no avatar at all.
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
    judge=judge_xiaohongshu_login,
    parse_profile=parse_xiaohongshu_profile,
    profile_text_selectors=PROFILE_TEXT_SELECTORS,
    profile_attr_selectors=PROFILE_ATTR_SELECTORS,
)

register_login(PLATFORM, LOGIN_SPEC)
