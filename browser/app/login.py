"""Generic QR-login machinery: page snapshots, the driver, the flow spec.

Same split as `validation.py`. A platform contributes a `LoginFlowSpec` - where
to navigate, which selectors hold the QR code, which on-page texts mean what,
and a **pure** judge - while this module owns everything platform-neutral:
driving Playwright, bounding every wait, turning failures into typed statuses.

The judge is pure on purpose. "What state is this page in?" is the part most
likely to be wrong and most expensive to test against a real platform, so it is
`LoginPageSnapshot -> LoginJudgement` with no Playwright types anywhere near it.
Reading the page (async, flaky, needs a browser) and interpreting the page
(sync, total, needs nothing) are separate jobs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from .browser_runtime import apply_stealth, build_launch_kwargs, viewport_kwargs
from .config import get_settings
from .dom import (
    click_element,
    describe_controls,
    first_text,
    first_visible_attribute,
    is_selector_visible,
    scroll_into_view,
    visible_marker_texts,
)
from .redaction import scrub_page_text
from .schemas import EnvironmentConfig, SessionStatus

logger = logging.getLogger("nous_browser.login")

# --- page evidence caps -----------------------------------------------------
#
# Everything captured for diagnostics is capped here rather than at the reader,
# because this blob does not stay in the process: it rides `detail` through the
# backend into `task_tracking.metadata`, which Supabase Realtime broadcasts to
# an open browser tab. A diagnostic that costs a megabyte per poll would be
# paid for by the user watching the modal.
EVIDENCE_CONTROL_LIMIT = 8
EVIDENCE_TARGET_LIMIT = 3

# Selector groups listed in `page_evidence`. `[role="button"]` is separate from
# `button` on purpose: the platform's cards are divs, and "there was no <button>
# on that screen at all" is itself the answer to one of the open questions.
_INPUT_ATTRIBUTES = ("placeholder", "type", "name")
_CONTROL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("button", ()),
    ('[role="button"]', ()),
)

# What the click target looks like in the DOM, answered in one round trip.
#
# The token in the comment is what the test fakes key on — the fakes cannot run
# JavaScript, so they recognise the probe by its source and answer from a
# scripted table. Anything this returns is *evidence about a failure*, so every
# branch degrades to null rather than throwing: a probe that can fail is a probe
# that leaves us with nothing on exactly the run we needed it for.
_CLICK_TARGET_PROBE_JS = """
(caption) => {
  /* __nous_click_target_probe__ */
  const describe = (el) => {
    if (!el || !el.tagName) return null;
    const role = el.getAttribute && el.getAttribute('role');
    return el.tagName.toLowerCase() + (role ? '[role=' + role + ']' : '');
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    if ((el.textContent || '').trim() !== caption) continue;
    let rect = {width: 0, height: 0, top: 0, left: 0, bottom: 0};
    try { rect = el.getBoundingClientRect(); } catch (e) {}
    const chain = [];
    let node = el;
    for (let i = 0; i < 5 && node; i += 1) { chain.push(describe(node)); node = node.parentElement; }
    let covered = null;
    if (rect.width > 0 && rect.height > 0) {
      const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) covered = describe(top);
    }
    let pointer = null;
    try { pointer = window.getComputedStyle(el).pointerEvents; } catch (e) {}
    out.push({
      tag: describe(el),
      chain: chain,
      size: [Math.round(rect.width), Math.round(rect.height)],
      in_viewport: rect.height > 0 && rect.top < (window.innerHeight || 0) && rect.bottom > 0,
      disabled: !!(el.closest && el.closest('[disabled],[aria-disabled="true"]')),
      pointer_events: pointer,
      covered_by: covered,
      button_ancestor: el.closest ? describe(el.closest('button,[role="button"]')) : null
    });
    if (out.length >= LIMIT) break;
  }
  return out;
}
""".replace("LIMIT", str(EVIDENCE_TARGET_LIMIT))

# The escalation target: the nearest ancestor the page itself declares
# clickable. The bare parent is deliberately **not** a candidate — a click lands
# at the element's centre, and a wrapper that spans both cards of this chooser
# would put that centre on 发送短信验证, the one option an unattended login can
# never complete. Narrowing to a declared button keeps the escalation from
# choosing for us.
BUTTON_ANCESTOR_XPATH = 'xpath=ancestor-or-self::*[@role="button" or self::button][1]'


@dataclass(frozen=True)
class LoginPageSnapshot:
    """Everything the judge is allowed to look at. Pure data."""

    url: str
    # Visible markers meaning "still sitting on the login screen".
    login_texts: tuple[str, ...] = ()
    # Visible markers meaning "the code was scanned, confirm on your phone".
    scanned_texts: tuple[str, ...] = ()
    # Visible markers meaning "this QR code is dead".
    expired_texts: tuple[str, ...] = ()
    # Visible captions of the platform's identity-verification chooser — the
    # step that asks *how* to verify before anything is sent anywhere. Carried
    # as the matched texts rather than a bool so the judge can say which options
    # were on offer: "only 发送短信验证 was there" is the difference between a
    # broken selector and a platform that never offered the path we drive.
    identity_challenge_texts: tuple[str, ...] = ()
    sms_input_visible: bool = False
    qrcode_visible: bool = False


@dataclass(frozen=True)
class LoginJudgement:
    status: SessionStatus
    reason: str


@dataclass(frozen=True)
class LoginProfile:
    """Who just logged in.

    Two classes of field, and the split is the whole point (2026-08-09):

    * ``platform_user_id`` is the **identity key**. The backend upserts
      ``social_accounts`` on ``(scope, platform, platform_user_id)``, so a value
      from a different namespace than last time does not degrade the account —
      it *forks* it into a second row and cuts the publish history in half.
      It is therefore resolved by this module from the spec's one declared
      cookie (``identity_cookie``) and **never** from a scraped field. A
      platform module cannot express a DOM identity path any more, because
      ``parse_profile`` is not handed the cookies and its return value's
      ``platform_user_id`` is overwritten below.
    * ``username`` / ``avatar_url`` / ``platform_handle`` are **display**.
      Best-effort as before: a console redesign that breaks a selector must
      leave a nameless account, never fail a login the user already completed.

    ``platform_handle`` is the human-facing account name (抖音号 / 小红书号).
    It used to be fed into ``platform_user_id`` whenever its selector happened
    to hit, which is exactly how one Douyin account ended up bound twice
    (``41cf16…`` from the cookie on 08-06, ``miopoo`` from the DOM on 08-09).
    Users rename it at will, so it is display material and nothing else.
    """

    platform_user_id: str = ""
    username: str = ""
    avatar_url: str | None = None
    platform_handle: str = ""


class IdentityUnresolved(Exception):
    """The identity cookie is not in the jar, so we do not know who this is.

    Deliberately **not** best-effort. Everything else about a profile degrades;
    this one fails the login. The alternative — carrying on with a blank or
    substituted identity — creates a duplicate account row whose publish history
    starts empty, and the user has no way to tell it apart from a fresh bind.
    """

    reason = "identity_unresolved"

    def __init__(self, platform: str, cookie: str) -> None:
        super().__init__(
            f"cannot identify the {platform} account: "
            f"cookie {cookie!r} was not present after login"
        )
        self.platform = platform
        self.cookie = cookie


def identity_from_cookies(
    identity_cookie: str, cookies: Sequence[Mapping[str, Any]]
) -> str:
    """The account's identity key, or "" when the cookie is absent.

    One cookie name, no fallback list, no DOM. A fallback would have to come
    from another namespace, and mixing namespaces under one unique key is the
    duplicate-row bug this function exists to make unrepresentable.
    """
    for cookie in cookies:
        if cookie.get("name") != identity_cookie:
            continue
        value = (cookie.get("value") or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class LoginFlowSpec:
    platform: str
    # THE identity source for this platform: the single cookie whose value is
    # the account's stable id. Required, and required to be exactly one — see
    # `LoginProfile`. Verified per platform against a live session; a guess here
    # is worse than a hard failure, because a value that changes between logins
    # silently forks the account.
    identity_cookie: str
    # Page that renders the QR code.
    login_url: str
    # Where to read profile fields after login. Empty = stay put.
    profile_url: str
    # Selector candidates for the QR <img>, priority order.
    qrcode_selectors: tuple[str, ...]
    login_markers: tuple[str, ...]
    scanned_markers: tuple[str, ...]
    expired_markers: tuple[str, ...]
    # Clicking this re-issues an expired code.
    refresh_selectors: tuple[str, ...]
    sms_input_selectors: tuple[str, ...]
    sms_submit_selectors: tuple[str, ...]
    # Pure: what state is this page in?
    judge: Callable[[LoginPageSnapshot], LoginJudgement]
    # Pure: scraped fields -> the DISPLAY half of the profile. Cookies are not
    # passed on purpose: identity comes from `identity_cookie` and nowhere else,
    # and any `platform_user_id` this returns is discarded.
    parse_profile: Callable[[Mapping[str, str]], LoginProfile]
    # --- identity challenge (2026-08-11) ---------------------------------
    #
    # Some accounts get an extra screen between "scanned" and "signed in": the
    # platform asks how it should verify the person. Three tuples, and the split
    # is the point — one says *we are on that screen*, one says *which option
    # gets us a text message*, one says *which button actually sends it*.
    #
    # All three are matched with `exact=True`. This repo has been burned twice
    # by generous matching voting on the wrong control (「允许」⊂「不允许」,
    # 「重新上传」⊂「清空并重新上传」), and here the wrong control is a
    # different verification flow entirely — 发送短信验证 makes the *user* text
    # the platform, which no automated login can complete.
    #
    # Empty tuples = this platform has no such screen modelled, and the flow
    # behaves exactly as it did before. Every platform but Douyin is in that
    # state today; none of them is worse off than before it existed.
    # Where the account's phone number goes, on platforms that sign in by text
    # message instead of by scan. Empty = this platform never asks us for one,
    # either because it is a QR platform or because its code screen is reached
    # some other way.
    #
    # Its presence is what makes `phone_required` a state at all. A page that
    # shows a phone field *and* a code field at the same time (Xiaohongshu shows
    # five inputs on one form) cannot be read to find out whether anybody has
    # entered a number, so that fact is tracked by the session, and this tuple
    # is how the session knows the question applies at all.
    phone_input_selectors: tuple[str, ...] = ()
    identity_challenge_markers: tuple[str, ...] = ()
    # Priority order. Clicked at most once per login.
    sms_challenge_option_texts: tuple[str, ...] = ()
    # The "send me the code" button on the screen *after* the chooser (and on
    # any code screen reached without one). Listed as complete captions rather
    # than a prefix so that 重新获取验证码 is covered without a substring match
    # that could also land on an unrelated control.
    sms_request_texts: tuple[str, ...] = ()
    # Captions that mean the platform is asking for something no unattended
    # login can do — a slider, a jigsaw, a rotate-the-image puzzle. Matched as
    # substrings against the *captured page text*, and used for one thing only:
    # choosing the wording of a failure that has already happened. Nothing here
    # can block a login or change a status, so a stale entry costs a slightly
    # wrong sentence, never a working sign-in.
    blocking_challenge_markers: tuple[str, ...] = ()
    # field name -> selector candidates, read as text.
    profile_text_selectors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # field name -> (selector candidates, attribute name).
    profile_attr_selectors: Mapping[str, tuple[tuple[str, ...], str]] = field(
        default_factory=dict
    )

    @property
    def login_method(self) -> str:
        """``"qrcode"`` or ``"sms"`` — how a human signs this platform in.

        **Derived, never declared.** A separate field would be a second place to
        state the same fact, and the two would eventually disagree: the failure
        mode is a platform whose QR selectors were emptied (because the platform
        turned out not to have a QR login at all) while a stale
        ``login_method="qrcode"`` kept a whole UI promising a code. That is the
        exact shape of the Xiaohongshu bug, so it is made unrepresentable rather
        than documented.

        ``capabilities.PLATFORM_LOGIN_METHODS`` mirrors this for consumers that
        cannot import Playwright-dependent modules, and a test pins the two
        together.
        """
        return "qrcode" if self.qrcode_selectors else "sms"

    @property
    def requires_phone_number(self) -> bool:
        """Does signing in start by us typing the account's phone number?"""
        return bool(self.phone_input_selectors)


# --- driver -----------------------------------------------------------------


class LoginDriver:
    """Owns one live browser + context + page for the length of a login.

    Deliberately *not* an async context manager: its whole reason to exist is
    outliving the request that created it. Release is explicit, and the session
    registry is the only thing allowed to call it.
    """

    def __init__(self, spec: LoginFlowSpec, playwright: Any, browser: Any, context: Any, page: Any):
        self._spec = spec
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page

    @property
    def spec(self) -> LoginFlowSpec:
        return self._spec

    async def snapshot(self) -> LoginPageSnapshot:
        spec = self._spec
        page = self._page
        return LoginPageSnapshot(
            url=page.url,
            login_texts=tuple(await visible_marker_texts(page, spec.login_markers)),
            scanned_texts=tuple(await visible_marker_texts(page, spec.scanned_markers)),
            expired_texts=tuple(await visible_marker_texts(page, spec.expired_markers)),
            # `exact=True`, unlike the three above. Those decide "are we still
            # logged out", where a miss is the dangerous direction; this one
            # decides which of two verification flows to *click*, where a
            # generous match is the dangerous direction (see the spec fields).
            identity_challenge_texts=tuple(
                await visible_marker_texts(
                    page, spec.identity_challenge_markers, exact=True
                )
            ),
            sms_input_visible=await _any_selector_visible(page, spec.sms_input_selectors),
            qrcode_visible=await _any_selector_visible(page, spec.qrcode_selectors),
        )

    async def read_qrcode(self) -> str | None:
        """Current QR code as a `data:image/...` URL, or None.

        Bounded wait for the node to appear, because the login card is injected
        by client-side JS after `domcontentloaded` and reading immediately gets
        nothing on a cold page.
        """
        settings = get_settings()
        attempts = settings.login_qrcode_attempts
        found: str | None = None
        # Bounded poll - a fixed number of attempts, never `while True`
        # (spec 7.2). An unbounded wait here would hold a browser forever on a
        # page that simply never renders a code.
        for attempt in range(attempts):
            src = await first_visible_attribute(
                self._page, self._spec.qrcode_selectors, "src"
            )
            if src and src.startswith("data:image"):
                return src
            # A non-data src (remote image, placeholder) is still worth
            # reporting over nothing, but does not end the poll.
            found = src or found
            if attempt + 1 < attempts:
                await asyncio.sleep(settings.login_qrcode_poll_s)
        return found

    async def refresh_qrcode(self) -> str | None:
        """Click the platform's own "refresh" affordance and re-read the code.

        Reloading the whole page would also work but throws away any 2FA state
        the page is holding; the in-page refresh is what a human would click.
        """
        clicked = False
        for selector in self._spec.refresh_selectors:
            try:
                locator = self._page.locator(selector).first
                if not await locator.count() or not await locator.is_visible():
                    continue
                await locator.click(timeout=get_settings().login_click_timeout_ms)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            return None
        return await self.read_qrcode()

    async def choose_sms_challenge(self) -> str | None:
        """Pick "receive an SMS" on the identity-verification chooser.

        Returns the caption that was clicked, or None when nothing matched.
        None is a **finding**, not a shrug: the caller turns a run of them into
        a typed failure, because the alternative — sitting on the chooser
        without answering it — is the exact shape of the bug this exists to
        fix (nobody clicks, so the platform never sends, so the user waits out
        the TTL for a code that was never requested).

        Only the platform's `sms_challenge_option_texts` are clicked, matched
        exactly. The other card on that screen (`发送短信验证`) reverses the
        direction — the *user* texts the platform from their own phone — and
        an automated login has no way to complete it, so clicking it by
        accident would be worse than clicking nothing.
        """
        return await self._click_exact_text(self._spec.sms_challenge_option_texts)

    async def escalate_challenge_click(self, caption: str) -> str | None:
        """Re-click `caption` on the nearest ancestor declared clickable.

        The second attempt, and only ever a second one. It exists because
        `_click_exact_text` reports success from *the click landing*, which is
        not the same as the handler running: `get_by_text` resolves to the leaf
        text node, and a card whose handler sits on a wrapper can swallow a
        click on a child that is styled `pointer-events: none`.

        That is a **hypothesis, not a diagnosis** — the same poll that
        escalates also records what the page looked like (`page_evidence`), and
        that record is what should settle it. Escalating anyway is cheap and
        bounded: the target is a control the page itself marks up as a button
        (see `BUTTON_ANCESTOR_XPATH` for why the bare parent is not allowed),
        and clicking the same card twice cannot pick the other one.

        Returns the selector that was clicked, or None when there is no such
        ancestor (which is itself a finding worth having in the failure).
        """
        timeout = get_settings().login_click_timeout_ms
        try:
            node = self._page.get_by_text(caption, exact=True).first
            target = node.locator(BUTTON_ANCESTOR_XPATH).first
            if not await target.count() or not await target.is_visible():
                return None
        except Exception:
            return None
        if await click_element(target, timeout):
            return BUTTON_ANCESTOR_XPATH
        return None

    async def wait_for_challenge_progress(self) -> list[str]:
        """Bounded wait for the page to answer the click. `[]` = it never did.

        Polling once and hoping is what the caller used to do, via a fixed
        `sleep`: whether the click worked was then decided by whichever frame
        the *next* status request happened to land on. This asks the page
        directly, and returns **which** signal moved so a failure can say what
        was and was not seen.

        Two signals, and the one that is missing matters:

        * `chooser_gone` — none of the challenge captions are on screen.
        * `sms_request_visible` — the platform's own "send me the code" button
          has appeared, i.e. we are on the screen after the chooser.

        A visible code *input* is deliberately not a signal. Douyin renders one
        on the chooser itself, which is the whole reason the judge used to call
        that screen `sms_required` and tell the user a code had been sent when
        nothing had (2026-08-11). Reusing it here would rebuild that bug inside
        the fix for it.
        """
        settings = get_settings()
        spec = self._spec
        page = self._page
        attempts = settings.login_challenge_progress_attempts
        for attempt in range(attempts):
            signals: list[str] = []
            remaining = await visible_marker_texts(
                page, spec.identity_challenge_markers, exact=True
            )
            if not remaining:
                signals.append("chooser_gone")
            if await visible_marker_texts(page, spec.sms_request_texts, exact=True):
                signals.append("sms_request_visible")
            if signals:
                return signals
            if attempt + 1 < attempts:
                await asyncio.sleep(settings.login_challenge_progress_poll_s)
        return []

    async def page_evidence(self, captions: Sequence[str] = ()) -> dict[str, Any]:
        """What the page looked like, in a shape that fits in a JSON column.

        Called on the failure paths, where the only thing worth having is an
        answer to "what was the platform actually showing". Every field is
        gathered defensively and independently: this runs *while a login is
        already failing*, so a reader that raises would trade the one useful
        artefact for a second, less informative failure.

        Nothing here is a credential, and nothing may become one: page text
        goes through `scrub_page_text`, which masks the two numbers a
        verification screen puts on display (the code, the phone).
        """
        page = self._page
        evidence: dict[str, Any] = {}

        try:
            evidence["url"] = scrub_page_text(page.url or "", max_len=200)
        except Exception:
            evidence["url"] = None

        # The whole visible page rather than a guessed region selector. A
        # region selector that misses returns nothing, and diagnostics that can
        # return nothing are the thing being fixed here; truncation loses the
        # tail of a long page, which is the cheaper failure.
        try:
            evidence["visible_text"] = scrub_page_text(
                await first_text(page, ("body",)) or ""
            )
        except Exception:
            evidence["visible_text"] = None

        total, items = await describe_controls(
            page,
            "input",
            attributes=_INPUT_ATTRIBUTES,
            limit=EVIDENCE_CONTROL_LIMIT,
        )
        evidence["inputs"] = {"total": total, "items": items}

        buttons: list[dict[str, Any]] = []
        button_total = 0
        for selector, attributes in _CONTROL_GROUPS:
            group_total, group_items = await describe_controls(
                page, selector, attributes=attributes, limit=EVIDENCE_CONTROL_LIMIT
            )
            button_total += group_total
            buttons.extend(group_items)
        evidence["buttons"] = {"total": button_total, "items": buttons}

        evidence["click_targets"] = [
            await self._describe_click_target(caption) for caption in captions
        ]
        return evidence

    async def _describe_click_target(self, caption: str) -> dict[str, Any]:
        """One caption we click: is it there, and what shape is it in?

        Split in two on purpose. The locator half (does it resolve, is it
        visible) works everywhere; the DOM-shape half needs real JavaScript and
        is allowed to be absent — `shape_error` says which, so "we never looked"
        is never mistaken for "nothing was covering it".
        """
        target: dict[str, Any] = {"caption": caption}
        try:
            locator = self._page.get_by_text(caption, exact=True).first
            target["matches"] = int(await locator.count())
            target["visible"] = (
                bool(await locator.is_visible()) if target["matches"] else False
            )
        except Exception as exc:  # noqa: BLE001
            target["matches"] = None
            target["locator_error"] = type(exc).__name__
            return target

        evaluate = getattr(self._page, "evaluate", None)
        if evaluate is None:
            target["shape_error"] = "evaluate_unavailable"
            return target
        try:
            shapes = await evaluate(_CLICK_TARGET_PROBE_JS, caption)
        except Exception as exc:  # noqa: BLE001
            target["shape_error"] = type(exc).__name__
            return target
        if isinstance(shapes, list):
            target["shapes"] = shapes[:EVIDENCE_TARGET_LIMIT]
        else:
            target["shape_error"] = "unexpected_probe_result"
        return target

    async def request_sms_code(self) -> str | None:
        """Click the platform's own "send me the code" button, if it is there.

        Separate from `choose_sms_challenge` because it is a *second* screen,
        not a second selector for the same one: the chooser hands over to a
        form that (on the reference implementation's evidence, and on this
        platform) sends nothing until 获取验证码 is pressed. Whichever of the
        two anchors is on screen gets clicked; neither being present is a
        legitimate state (some flows send on their own), so this returns None
        rather than failing.
        """
        return await self._click_exact_text(self._spec.sms_request_texts)

    async def _click_exact_text(self, texts: Sequence[str]) -> str | None:
        """First visible exact-text match, clicked. Returns which caption.

        `exact=True` is not optional here and not a style choice: these
        captions are clicked, and Playwright's substring mode also matches
        every *ancestor* containing the text — `.first` on a generous match
        can be the page body.

        The scroll is not decoration either. A card below the fold is visible
        to `is_visible()` but its centre is off screen, and both of the things
        we record about a click that did not work — whether it was in the
        viewport, what was covering it — describe a different element than the
        one the click found if the page moves in between.
        """
        if not texts:
            return None
        timeout = get_settings().login_click_timeout_ms
        for text in texts:
            try:
                locator = self._page.get_by_text(text, exact=True).first
                if not await locator.count() or not await locator.is_visible():
                    continue
            except Exception:
                # A locator racing a re-render is not evidence of absence; the
                # next poll looks again.
                continue
            await scroll_into_view(locator, timeout)
            if await click_element(locator, timeout):
                return text
        return None

    async def fill_phone_number(self, phone: str) -> bool:
        """Type the account's phone number. False = no field to type it into.

        False is a **finding**, not a shrug, and the caller turns it into a typed
        failure the user can see. On this platform every selector below is one
        nobody has ever watched work on a live bind (no Xiaohongshu account has
        ever been bound), and the whole point of failing loudly is that a wrong
        guess must not read as "waiting" — that is how a user ends up staring at
        a code field for a message nothing ever asked the platform to send.

        Deliberately does **not** press anything: requesting the code is a
        separate, separately-latched action (`request_sms_code`), because it is
        the one that costs a real text message.
        """
        settings = get_settings()
        for selector in self._spec.phone_input_selectors:
            try:
                locator = self._page.locator(selector).first
                if not await locator.count() or not await locator.is_visible():
                    continue
            except Exception:
                # A locator racing a re-render is not evidence of absence.
                continue
            await locator.fill(phone, timeout=settings.login_click_timeout_ms)
            return True
        return False

    async def submit_sms_code(self, code: str) -> bool:
        """Type the code and submit. False = no input to type into."""
        settings = get_settings()
        target = None
        for selector in self._spec.sms_input_selectors:
            try:
                locator = self._page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    target = locator
                    break
            except Exception:
                continue
        if target is None:
            return False

        await target.fill(code, timeout=settings.login_click_timeout_ms)
        for selector in self._spec.sms_submit_selectors:
            try:
                button = self._page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=settings.login_click_timeout_ms)
                    return True
            except Exception:
                continue
        # No explicit submit control found - platforms commonly auto-submit on
        # the last digit, and Enter is the fallback a human would use.
        await target.press("Enter", timeout=settings.login_click_timeout_ms)
        return True

    async def read_profile(self) -> LoginProfile:
        """Display fields best effort; the identity key is not negotiable.

        A missing display name must never fail a good login — but a missing
        *identity* must, and this is the boundary where that difference is
        enforced. The login path is the one caller that turns a profile into a
        durable account row, so it is the one caller that cannot accept
        "whoever this is". Session *validation* calls
        ``read_profile_from_page`` directly and keeps its old best-effort
        contract: a refresh that cannot identify the account simply refreshes
        nothing.

        Raises ``IdentityUnresolved`` — typed, so the caller can say *why* the
        login failed instead of reporting a generic scrape error.
        """
        cookies = await self._safe_cookies()
        try:
            profile = await read_profile_from_page(self._page, self._spec, cookies)
        except Exception as exc:  # noqa: BLE001 - display scrape is best-effort
            logger.warning(
                "profile scrape failed for platform=%s: %s",
                self._spec.platform,
                type(exc).__name__,
            )
            # The cookies are already in hand and owe nothing to the DOM, so a
            # broken page still yields a correctly-identified account.
            profile = LoginProfile(
                platform_user_id=identity_from_cookies(
                    self._spec.identity_cookie, cookies
                )
            )
        if not profile.platform_user_id:
            raise IdentityUnresolved(self._spec.platform, self._spec.identity_cookie)
        return profile

    async def _safe_cookies(self) -> list[dict[str, Any]]:
        try:
            return list(await self._context.cookies())
        except Exception:
            return []

    async def storage_state(self) -> dict[str, Any]:
        """Plaintext session material. Returned as a dict, never a path.

        `storage_state(path=...)` would write credentials to disk, which spec
        7.6 forbids outright; the overload must not be used here.
        """
        return await self._context.storage_state()

    async def close(self) -> None:
        """Release every resource, best effort, in dependency order.

        Each step is guarded separately: a page that already crashed must not
        stop the browser process from being reaped, or a failed login leaks a
        Chromium for the container's lifetime.
        """
        for closer in (
            getattr(self._page, "close", None),
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                continue


async def read_profile_from_page(
    page: Any, spec: LoginFlowSpec, cookies: Sequence[Mapping[str, Any]]
) -> LoginProfile:
    """Navigate to the profile page and scrape the account identity. Best effort.

    Module-level rather than a `LoginDriver` method because the session
    *validation* path needs the same scrape and has no driver: it holds its own
    page from a storage_state-seeded context. Duplicating the logic there is how
    the two copies drift, and this scrape is already fragile enough — the
    selectors are CSS-Modules hashes that move on every console deploy.

    Every step swallows its failure by design: a console redesign that breaks a
    display-name selector must degrade to a nameless account, never fail a login
    the user already completed or condemn a session that is actually alive.

    **The identity key is not part of that bargain.** It is resolved here, from
    the spec's single declared cookie, and stamped over whatever
    ``parse_profile`` returned — so no platform can accidentally (or
    deliberately) source it from the DOM. "" means the cookie was absent;
    turning that into a failure is the *caller's* call, because the login path
    and the validation path want opposite things from it (see
    ``LoginDriver.read_profile``).
    """
    if spec.profile_url:
        try:
            await page.goto(
                spec.profile_url,
                wait_until="domcontentloaded",
                timeout=get_settings().nav_timeout_ms,
            )
            await page.wait_for_timeout(get_settings().settle_ms)
        except Exception:
            # Stay on whatever page we are on and read what we can.
            pass

    fields: dict[str, str] = {}
    for name, selectors in spec.profile_text_selectors.items():
        value = await first_text(page, selectors)
        if value:
            fields[name] = value
    for name, (selectors, attribute) in spec.profile_attr_selectors.items():
        value = await first_visible_attribute(page, selectors, attribute)
        if value:
            fields[name] = value

    return replace(
        spec.parse_profile(fields),
        platform_user_id=identity_from_cookies(spec.identity_cookie, cookies),
    )


async def _any_selector_visible(page: Any, selectors: Sequence[str]) -> bool:
    for selector in selectors:
        if await is_selector_visible(page, selector):
            return True
    return False


async def open_login_driver(
    spec: LoginFlowSpec, environment: EnvironmentConfig | None
) -> LoginDriver:
    """Launch a browser, open the login page, hand back a live driver.

    Raises on failure - the caller classifies. A partially-built driver is torn
    down here rather than leaked, since there is no handle to release it with.

    Unlike validation, the context gets **no** storage_state: a QR login starts
    from a clean profile by definition, and seeding it with a dead session is
    how you land on a logged-in-looking shell with no QR code on it.
    """
    # patchright，不是 playwright：drop-in fork，补 CDP 层泄露。
    # 四个 import 点必须一致 —— test_patchright_everywhere 会失败。
    from patchright.async_api import async_playwright

    settings = get_settings()
    playwright = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await playwright.chromium.launch(**build_launch_kwargs(environment))
        context = await browser.new_context(**_login_context_kwargs(environment))
        await apply_stealth(context)
        page = await context.new_page()
        await page.goto(
            spec.login_url,
            wait_until="domcontentloaded",
            timeout=settings.nav_timeout_ms,
        )
        return LoginDriver(spec, playwright, browser, context, page)
    except Exception:
        for closer in (
            getattr(context, "close", None),
            getattr(browser, "close", None),
            playwright.stop,
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                continue
        raise


def _login_context_kwargs(environment: EnvironmentConfig | None) -> dict[str, Any]:
    """Same environment isolation as validation, minus storage_state.

    The account's environment is pinned at bind time on purpose: the fingerprint
    the platform sees while the account is created should be the one it sees
    forever after (design doc 3.2).
    """
    kwargs: dict[str, Any] = {}
    if environment is None:
        return kwargs
    if environment.user_agent:
        kwargs["user_agent"] = environment.user_agent
    if environment.locale:
        kwargs["locale"] = environment.locale
    if environment.timezone_id:
        kwargs["timezone_id"] = environment.timezone_id
    if environment.geo_lat is not None and environment.geo_lng is not None:
        kwargs["geolocation"] = {
            "latitude": environment.geo_lat,
            "longitude": environment.geo_lng,
        }
        kwargs["permissions"] = ["geolocation"]
    kwargs.update(viewport_kwargs(environment))
    return kwargs
