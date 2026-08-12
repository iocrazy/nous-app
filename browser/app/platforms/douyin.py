"""Douyin adapter: session validation and QR login. The only one in this tree.

Do not add a second Douyin session check anywhere (backend, scripts, tests).
The reference project kept two copies; only the CLI one ever received the
"headless causes false expiry" fix, so its web path kept killing healthy
accounts intermittently. Everything that needs to know whether a Douyin session
is alive calls `validate_session` here.

Both halves follow the same shape: this module contributes selectors, marker
texts and **pure** judgement functions; all the driving, retrying and bounding
lives in the platform-neutral `validation` / `login` modules.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from ..inspect import InspectSpec
from ..login import LoginFlowSpec, LoginJudgement, LoginPageSnapshot, LoginProfile
from ..schemas import EnvironmentConfig, SessionResult, SessionStatus
from ..validation import DomValidationSpec, Judgement, run_dom_session_validation
from . import register, register_inspect_spec, register_login

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


# --- read-only recon (T0) ---------------------------------------------------
#
# The allow-list `/session/inspect` enforces. It reuses `CREATOR_HOSTS` and
# `LOGIN_TEXT_MARKERS` above rather than restating them: a second copy of "our
# hosts" is a second thing to miss when the platform moves one.

INSPECT_SPEC = InspectSpec(
    platform=PLATFORM,
    allowed_hosts=CREATOR_HOSTS,
    login_text_markers=LOGIN_TEXT_MARKERS,
)

register_inspect_spec(PLATFORM, INSPECT_SPEC)


# --- QR login ---------------------------------------------------------------

LOGIN_URL = "https://creator.douyin.com/"

# Read after login. The creator home is where the display name and 抖音号 live;
# the upload page shows neither.
PROFILE_URL = "https://creator.douyin.com/creator-micro/home"

# Any path under here means we are inside the authenticated console. The login
# screen itself sits at the site root, which is what makes this discriminating.
CONSOLE_PATH_FRAGMENT = "/creator-micro"

# Priority-ordered, and that ordering is the point. The reference implementation
# hangs its extraction on `aria-label="二维码"`, which the current creator
# centre no longer emits - it moved to a `single_tab` layout with an
# `animate_qrcode_container` wrapper. A single selector turns each redesign into
# a hard outage, so specific hooks come first and historical ones stay as
# fallbacks rather than being deleted.
QRCODE_SELECTORS: tuple[str, ...] = (
    'div#animate_qrcode_container img[src^="data:image"]',
    'div[class*="animate_qrcode_container"] img[src^="data:image"]',
    'div[class*="scan_qrcode_login_content"] img[src^="data:image"]',
    'img[aria-label="二维码"]',
    'div[class*="qrcode"] img[src^="data:image"]',
)

# Visible ⇒ still on the login screen. Reused from validation so the two halves
# cannot drift apart on what "logged out" looks like.
LOGIN_MARKERS: tuple[str, ...] = LOGIN_TEXT_MARKERS

# Visible ⇒ the phone scanned the code and the user has to confirm there. This
# state has no counterpart in the reference implementation, which jumps straight
# from "waiting" to "logged in" and therefore leaves the user staring at an
# unchanged QR code through the most confusing part of the flow.
SCANNED_MARKERS: tuple[str, ...] = (
    "扫码成功",
    "扫描成功",
    "请在手机上确认",
    "手机端确认",
    "在手机上确认登录",
)

EXPIRED_MARKERS: tuple[str, ...] = ("二维码失效", "二维码已失效", "点击刷新")

# The refresh affordance is the QR container itself (or an overlay inside it) -
# the "二维码失效" caption is a text node with no click handler of its own.
# Container-first because `div#animate_qrcode_container` is confirmed present on
# the live page, whereas the refresh-classed child is not.
REFRESH_SELECTORS: tuple[str, ...] = (
    'div#animate_qrcode_container div[class*="refresh"]',
    'div#animate_qrcode_container',
    'div[class*="qrcode"] div[class*="refresh"]',
    'div[class*="animate_qrcode_container"]',
    'img[aria-label="二维码"]',
)

# `input[type="tel"]` is deliberately absent. Douyin uses it for *both* the
# phone number and the code field, so it distinguishes nothing (see the note on
# `judge_douyin_login` about why the presence of a code field means very little
# on this page in the first place).
SMS_INPUT_SELECTORS: tuple[str, ...] = (
    'input[placeholder*="验证码"]',
    'input[placeholder*="短信"]',
    'input[name*="verify"]',
)

# --- identity challenge (read off the live page 2026-08-11) ----------------
#
# Some accounts get an extra screen between "scanned" and "signed in":
#
#     身份验证
#     为保障账号安全，请先完成身份验证，以确保为本人操作
#     [ 接收短信验证码  > ]      ← the platform texts the account's phone
#     [ 发送短信验证    > ]      ← the USER texts the platform from that phone
#
# **Nothing is sent until one of those cards is clicked**, and the flow never
# clicked either. Meanwhile `judge_douyin_login` saw a code field with no QR
# panel and answered `sms_required`, so the UI told the user a code had been
# texted to them. It had not. Two accounts bound fine before this surfaced
# simply because the platform did not challenge them.
#
# Only the two card captions are markers, and 身份验证 deliberately is not.
# The title is a plausible caption elsewhere in the creator console (a settings
# row, a menu item), and a false positive here does not degrade quietly — it
# would block a completed login from ever reading as `success`. The captions
# are specific to this screen, and they are also what we click, so detection
# and action cannot drift apart.
IDENTITY_CHALLENGE_MARKERS: tuple[str, ...] = (
    "接收短信验证码",
    "发送短信验证",
)

# The one option an unattended login can actually complete. `发送短信验证` is
# deliberately absent: it requires the human to send an SMS from the bound
# handset to a number the page shows, which nothing in this service can do, and
# picking it would strand the login on a screen with no way forward.
SMS_CHALLENGE_OPTION_TEXTS: tuple[str, ...] = ("接收短信验证码",)

# The button on the screen *after* the chooser. The reference project
# (`social-auto-upload`, publish-side) shows the same shape on Douyin's other
# SMS gate: an input plus 获取验证码, and **the code is sent by the click, not
# by the screen appearing**. Listed as whole captions instead of a
# `获取验证码` substring so the resend wording is covered without a generous
# match that could land on some other control — the failure mode this repo
# already owns twice (「允许」⊂「不允许」).
SMS_REQUEST_TEXTS: tuple[str, ...] = (
    "获取验证码",
    "重新获取验证码",
    "发送验证码",
)

SMS_SUBMIT_SELECTORS: tuple[str, ...] = (
    'button:has-text("确认")',
    'button:has-text("提交")',
    'button:has-text("登录")',
    'div[class*="submit"][role="button"]',
)

# Read off the live console on 2026-08-06, after a bound account came back
# named `41cf16775ee3e9fdf5e021f9c1ddfc12` — every selector below missed and the
# code fell back to the cookie id.
#
# The console builds class names as `<role>-<hash>` (CSS Modules): `name-_lSSDc`,
# `unique_id-EuH8eA`. The hash changes between deploys, the role prefix does not,
# so these match on the prefix. Two consequences worth stating:
#
#   * `[class*="nickname"]` never had a chance — the role is `name`, not
#     `nickname`, and the id one is `unique_id` with an UNDERSCORE where the old
#     selector guessed a hyphen.
#   * `name-` and `img-` are generic enough to appear elsewhere on the page
#     (`img-pos9sN` is a post thumbnail), so both are scoped under the profile
#     header — `name-_lSSDc < left-zEzdJX < header-_F2uzl` is the observed chain.
#
# Historical selectors stay as fallbacks rather than being deleted: a redesign
# should degrade to the next candidate, not to a hard outage.
PROFILE_TEXT_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "username": (
        '[class^="header-"] [class^="name-"]',
        '[class^="name-"]',
        'div[class*="nickname"]',
        'span[class*="nickname"]',
        'div[class*="user-name"]',
    ),
    # 抖音号. DISPLAY ONLY — it is not, and must never again be, the identity
    # key. See `IDENTITY_COOKIE` below.
    "platform_handle": (
        '[class^="unique_id-"]',
        '[class*="unique_id"]',
        'div[class*="unique-id"]',
        'span[class*="unique-id"]',
        'div[class*="douyin-id"]',
    ),
}

PROFILE_ATTR_SELECTORS: Mapping[str, tuple[tuple[str, ...], str]] = {
    "avatar_url": (
        (
            # Scoped to the header for the reason above: bare `img[class^="img-"]`
            # also matches post thumbnails, and picking the newest video's cover
            # as someone's avatar is worse than having no avatar at all.
            '[class^="header-"] img[class^="img-"]',
            # The avatar CDN path is stable across layouts, so this survives a
            # header rename that the scoped selector would not.
            'img[src*="aweme-avatar"]',
            'img[class*="avatar"]',
            'div[class*="avatar"] img',
        ),
        "src",
    ),
}

# THE identity source. One cookie, no fallback (2026-08-09).
#
# It used to be the *second* choice, behind the scraped 抖音号 — and that cost
# us a duplicated account. `MioPoo` bound on 08-06 while the `unique_id-` hash
# selector missed, so it keyed on this cookie (`41cf16…`); it bound again on
# 08-09 after the selector was fixed, keyed on `miopoo`, and the backend's
# unique key `(scope, platform, platform_user_id)` saw two different accounts.
# Ten publish records stayed with the first row while the UI showed the second.
#
# `uid_tt_ss` is gone from here on purpose. It is the same id under the secure
# cookie, so "try uid_tt, else uid_tt_ss" reads as harmless — but a second
# source is a second answer, and there is no way to be sure they agree on a
# page we did not write. Verified present alongside `uid_tt` in the live jar
# (2026-08-09 dry-run: 46 cookies, both present, `uid_tt` equal to the id
# already stored for the bound account).
IDENTITY_COOKIE = "uid_tt"

# Labels the console prints in front of the 抖音号. Display-side only now.
_ID_PREFIXES = ("抖音号：", "抖音号:", "抖音号", "ID：", "ID:")


def judge_douyin_login(snapshot: LoginPageSnapshot) -> LoginJudgement:
    """Which login state is this page in? Pure.

    **A visible code field is not evidence of 2FA on this platform.** Douyin's
    login screen renders the phone-login form next to the QR panel, and its
    "请输入验证码" input is visible the entire time the QR code is on display
    (confirmed against the live page: three visible inputs, two of them
    `type="tel"`, while `扫码登录` is the active tab). Judging on the input
    alone reports `sms_required` on *every* poll of a perfectly normal scan -
    which is what an early draft of this function did. So the code field only
    counts once the QR code is gone: the platform swaps the login card out for
    the verification step, it does not show both. The reference implementation
    dodges the same trap from a different angle, by only looking for the input
    after the URL has changed.

    Order is the rest of the design, and two placements are load-bearing:

    * **Success is checked first**, but only accepts a page with no login
      markers *and* no live code prompt. Checking SMS first would let a
      false-positive input selector wedge a completed login forever; checking
      success naively would call a half-finished 2FA page a finished login and
      hand the backend cookies that do not work.
    * **SMS outranks "expired"**, because a QR code visibly dies the moment it
      is consumed. A page that is asking for a text message may *also* be
      showing the expired caption, and the other order would click "refresh"
      right out of the 2FA flow the user is halfway through.
    * **The identity chooser outranks SMS**, and that ordering is the 2026-08-11
      fix. That screen renders a code field of its own, so the older order
      answered `sms_required` on it — a sentence ("enter the code we texted
      you") that was false, because the platform sends nothing until a card is
      clicked. Reading the chooser first is what lets the flow click, and what
      keeps the UI from promising a message nobody asked for.
    """
    parts = urlsplit(snapshot.url or "")
    host = (parts.hostname or "").lower()
    on_creator_host = host in CREATOR_HOSTS
    asking_for_code = snapshot.sms_input_visible and not snapshot.qrcode_visible
    identity_challenge = bool(snapshot.identity_challenge_texts)

    if (
        on_creator_host
        and CONSOLE_PATH_FRAGMENT in parts.path
        and not snapshot.login_texts
        and not asking_for_code
        # Same reasoning as `asking_for_code`: a half-finished verification
        # step sitting on a console URL is not a finished login, and treating
        # it as one hands the backend cookies that do not work.
        and not identity_challenge
    ):
        return LoginJudgement(
            SessionStatus.SUCCESS,
            "reached the creator console with no login prompt",
        )

    if identity_challenge:
        return LoginJudgement(
            SessionStatus.IDENTITY_CHALLENGE,
            "the platform is asking to verify your identity: "
            + ", ".join(snapshot.identity_challenge_texts),
        )

    if asking_for_code:
        return LoginJudgement(
            SessionStatus.SMS_REQUIRED,
            "the platform is asking for a verification code",
        )

    if snapshot.expired_texts:
        return LoginJudgement(
            SessionStatus.QRCODE_EXPIRED,
            "QR code expired: " + ", ".join(snapshot.expired_texts),
        )

    if snapshot.scanned_texts:
        return LoginJudgement(
            SessionStatus.SCANNED,
            "scanned; waiting for confirmation on the phone: "
            + ", ".join(snapshot.scanned_texts),
        )

    if snapshot.qrcode_visible:
        return LoginJudgement(SessionStatus.WAITING_SCAN, "QR code displayed")

    if on_creator_host:
        # Still on the platform, just not showing anything we recognise - most
        # often a mid-render frame. Report it as "keep waiting" but say so in
        # the reason, because a silently generic answer here is what makes a
        # broken selector list look like a patient user (CLAUDE.md: silent
        # no-op is not acceptable).
        return LoginJudgement(
            SessionStatus.WAITING_SCAN,
            "no recognisable login state on the page yet "
            f"(path={parts.path or '/'})",
        )

    return LoginJudgement(
        SessionStatus.FAILED,
        f"navigated to an unexpected host (host={host or 'unknown'})",
    )


def parse_douyin_profile(fields: Mapping[str, str]) -> LoginProfile:
    """Turn scraped strings into the DISPLAY half of a profile. Pure.

    Everything here is best-effort by design: a console redesign that breaks a
    display-name selector must degrade to a nameless account, never fail a login
    the user already completed.

    It has no say in `platform_user_id` — no cookies are passed in, and the
    caller stamps the identity over whatever this returns. That is deliberate:
    "best-effort" and "identity" are the combination that duplicated an account
    (see `IDENTITY_COOKIE`), and the cleanest way not to repeat it is to make
    the mistake unrepresentable rather than to remember not to make it.
    """
    username = (fields.get("username") or "").strip()

    handle = (fields.get("platform_handle") or "").strip()
    for prefix in _ID_PREFIXES:
        if handle.startswith(prefix):
            handle = handle[len(prefix) :].strip()
            break

    avatar = (fields.get("avatar_url") or "").strip()
    if not avatar.startswith(("http://", "https://", "data:image")):
        # Relative or protocol-less values are useless to the frontend and
        # worse than absent, since they render as a broken image.
        avatar = ""

    return LoginProfile(
        username=username,
        avatar_url=avatar or None,
        platform_handle=handle,
    )


LOGIN_SPEC = LoginFlowSpec(
    platform=PLATFORM,
    identity_cookie=IDENTITY_COOKIE,
    login_url=LOGIN_URL,
    profile_url=PROFILE_URL,
    qrcode_selectors=QRCODE_SELECTORS,
    login_markers=LOGIN_MARKERS,
    scanned_markers=SCANNED_MARKERS,
    expired_markers=EXPIRED_MARKERS,
    refresh_selectors=REFRESH_SELECTORS,
    sms_input_selectors=SMS_INPUT_SELECTORS,
    sms_submit_selectors=SMS_SUBMIT_SELECTORS,
    judge=judge_douyin_login,
    parse_profile=parse_douyin_profile,
    identity_challenge_markers=IDENTITY_CHALLENGE_MARKERS,
    sms_challenge_option_texts=SMS_CHALLENGE_OPTION_TEXTS,
    sms_request_texts=SMS_REQUEST_TEXTS,
    profile_text_selectors=PROFILE_TEXT_SELECTORS,
    profile_attr_selectors=PROFILE_ATTR_SELECTORS,
)

register_login(PLATFORM, LOGIN_SPEC)
