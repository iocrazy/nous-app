"""A stand-in for `LoginDriver`, so session lifecycle is testable without one.

The registry talks to the driver through a narrow surface on purpose - that
seam is what lets TTL, capacity, locking and teardown be tested exhaustively
while the only thing needing a real Chromium stays the driver itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.login import LoginFlowSpec, LoginJudgement, LoginPageSnapshot, LoginProfile
from app.schemas import SessionStatus


class FakeDriver:
    """Scripted page states. `closed` is what the leak tests assert on."""

    def __init__(
        self,
        spec: LoginFlowSpec,
        snapshots: list[LoginPageSnapshot] | None = None,
        qrcode: str | None = "data:image/png;base64,QR0",
        refreshed_qrcode: str | None = "data:image/png;base64,QR1",
        storage: dict[str, Any] | None = None,
        profile: LoginProfile | None = None,
        sms_input_present: bool = True,
        # What the two identity-challenge clicks resolve to. `None` = "that
        # anchor is not on this page", which is a legitimate state for
        # `request_sms_code` (some flows send on their own) and a finding for
        # `choose_sms_challenge` (nobody answered the chooser).
        challenge_option: str | None = None,
        code_request_text: str | None = None,
        # What the page does after the chooser is answered. The default says
        # "it moved on", which is the healthy path; `[]` is the stall the
        # escalation and the typed failure exist for.
        challenge_progress: list[str] | None = None,
        escalated_click: str | None = None,
        evidence: dict[str, Any] | None = None,
    ):
        self.spec = spec
        self._snapshots = snapshots or [LoginPageSnapshot(url="https://x/", qrcode_visible=True)]
        self._index = 0
        self._qrcode = qrcode
        self._refreshed = refreshed_qrcode
        self._storage = storage or {"cookies": [{"name": "sessionid", "value": "s"}]}
        self._profile = profile or LoginProfile(platform_user_id="uid", username="Test Creator")
        self._sms_input_present = sms_input_present
        self._challenge_option = challenge_option
        self._code_request_text = code_request_text
        self._challenge_progress = (
            ["chooser_gone"] if challenge_progress is None else challenge_progress
        )
        self._escalated_click = escalated_click
        self._evidence = evidence if evidence is not None else {"url": "https://x/"}
        self.closed = False
        self.submitted_codes: list[str] = []
        self.refresh_calls = 0
        self.profile_calls = 0
        self.challenge_clicks = 0
        self.code_request_clicks = 0
        self.escalation_clicks = 0
        self.evidence_calls = 0

    async def snapshot(self) -> LoginPageSnapshot:
        current = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return current

    async def read_qrcode(self) -> str | None:
        return self._qrcode

    async def refresh_qrcode(self) -> str | None:
        self.refresh_calls += 1
        self._qrcode = self._refreshed
        return self._refreshed

    async def choose_sms_challenge(self) -> str | None:
        self.challenge_clicks += 1
        return self._challenge_option

    async def request_sms_code(self) -> str | None:
        self.code_request_clicks += 1
        return self._code_request_text

    async def escalate_challenge_click(self, _caption: str) -> str | None:
        self.escalation_clicks += 1
        return self._escalated_click

    async def wait_for_challenge_progress(self) -> list[str]:
        return list(self._challenge_progress)

    async def page_evidence(self, _captions: Any = ()) -> dict[str, Any]:
        self.evidence_calls += 1
        return dict(self._evidence)

    async def submit_sms_code(self, code: str) -> bool:
        self.submitted_codes.append(code)
        return self._sms_input_present

    async def read_profile(self) -> LoginProfile:
        self.profile_calls += 1
        return self._profile

    async def storage_state(self) -> dict[str, Any]:
        return dict(self._storage)

    async def close(self) -> None:
        self.closed = True


class ExplodingDriver(FakeDriver):
    """Raises on the first page read, the way a crashed renderer would."""

    def __init__(self, spec: LoginFlowSpec, error: Exception | None = None):
        super().__init__(spec)
        self._error = error or RuntimeError("Target page, context or browser has been closed")

    async def snapshot(self) -> LoginPageSnapshot:
        raise self._error


class SlowDriver(FakeDriver):
    """Holds the session lock long enough to exercise the bounded wait."""

    def __init__(self, spec: LoginFlowSpec, delay: float = 5.0):
        super().__init__(spec)
        self._delay = delay

    async def snapshot(self) -> LoginPageSnapshot:
        await asyncio.sleep(self._delay)
        return await super().snapshot()


def always(status: SessionStatus, reason: str = "scripted"):
    return lambda _snapshot: LoginJudgement(status, reason)


def make_spec(
    judge=None, platform: str = "testplatform", identity_cookie: str = "test_uid"
) -> LoginFlowSpec:
    return LoginFlowSpec(
        platform=platform,
        identity_cookie=identity_cookie,
        login_url="https://example.test/login",
        profile_url="https://example.test/home",
        qrcode_selectors=("img",),
        login_markers=(),
        scanned_markers=(),
        expired_markers=(),
        refresh_selectors=(),
        sms_input_selectors=(),
        sms_submit_selectors=(),
        judge=judge or always(SessionStatus.WAITING_SCAN),
        parse_profile=lambda _fields: LoginProfile(),
    )


def driver_factory(driver: Any):
    """A `DriverFactory` that always hands back the same fake."""

    async def factory(_spec, _environment):
        return driver

    return factory


# --- publish-side fakes -----------------------------------------------------
#
# The publish driver reads a page through a small, fixed slice of Playwright's
# locator surface. Faking that slice is what lets the upload/editor/confirm
# loops - where the deadline and the retry budget live - be tested without a
# Chromium and without a Douyin account.
#
# Page *state* is scripted, not individual locators: a test says "these
# selectors are on screen right now", optionally as a function of what the
# driver has already done, which is how a self-healing retry is expressed
# without inventing a call-order protocol.


def text_selector(text: Any) -> str:
    """The fake's key for a `get_by_text` call.

    Playwright takes either a string or a compiled pattern, and the gallery
    upload judge passes a pattern (it reads a *number* off the page, which no
    fixed string can do). Both collapse to one key here so a test can name the
    node the same way the production code finds it.
    """
    return f"text={getattr(text, 'pattern', text)}"


class FakeLocator:
    """The locator methods the publish driver calls, and nothing else."""

    def __init__(self, page: "FakePage", selector: str, *, index: int = 0):
        self.page = page
        self.selector = selector
        self.index = index

    @property
    def first(self) -> "FakeLocator":
        return self

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator(self.page, self.selector, index=index)

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(self.page, selector)

    def filter(self, **_kwargs: Any) -> "FakeLocator":
        return self

    def get_by_text(self, text: Any, exact: bool = False) -> "FakeLocator":
        self.page.text_queries.append((text, exact))
        return FakeLocator(self.page, text_selector(text))

    def get_by_role(self, _role: str, name: str = "", exact: bool = False) -> "FakeLocator":
        return FakeLocator(self.page, f"text={name}")

    async def count(self) -> int:
        return self.page.count_of(self.selector)

    async def inner_text(self) -> str:
        return self.page.texts.get(self.selector, "")

    async def is_visible(self) -> bool:
        return self.selector in self.page.visible_now()

    async def wait_for(self, state: str = "visible", timeout: Any = None) -> None:
        """Honours the direction of the wait.

        `hidden` / `detached` are not "visible with extra steps": a dialog that
        must *close* before the driver may trust the choice it just made is a
        real assertion, and a fake that satisfied it by having the dialog still
        on screen would pass exactly the case the driver exists to catch.
        """
        self.page.waits.append((self.selector, state, timeout))
        present = bool(self.page.count_of(self.selector))
        wants_gone = state in ("hidden", "detached")
        if wants_gone == present:
            raise TimeoutError(f"Timeout waiting for {self.selector} to be {state}")

    async def click(self, timeout: Any = None, force: bool = False) -> None:
        self.page.clicks.append(self.selector)
        # The index too, separately. `clicks` cannot carry it without breaking
        # every existing `selector in page.clicks` assertion, and *which* of
        # several identically-captioned nodes was clicked is the whole question
        # for 「选择音乐」 (exact=2 on the live page).
        self.page.click_targets.append((self.selector, self.index))

    async def evaluate(self, _expression: str) -> None:
        self.page.clicks.append(self.selector)
        self.page.click_targets.append((self.selector, self.index))

    async def fill(self, value: str, timeout: Any = None) -> None:
        self.page.fills.append((self.selector, value))

    async def set_input_files(self, path: str, timeout: Any = None) -> None:
        self.page.file_inputs.append((self.selector, path, self.index))

    async def get_attribute(self, name: str) -> str | None:
        return self.page.attributes.get(self.selector, {}).get(name)


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []

    async def type(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    """A scripted page.

    `url` and `visible` accept either a value or a callable taking the page, so
    a test can describe a page whose state depends on what the driver has done
    to it so far (a retried upload, a click that landed) without scripting a
    call sequence.
    """

    def __init__(
        self,
        url: Any = "https://creator.douyin.com/creator-micro/content/upload",
        visible: Any = (),
        counts: dict[str, int] | None = None,
        attributes: dict[str, dict[str, str]] | None = None,
        texts: dict[str, Any] | None = None,
    ):
        self._url = url
        self._visible = visible
        self.counts = dict(counts or {})
        self.attributes = attributes or {}
        # selector -> what `inner_text()` returns. Same value-or-callable
        # convention as `url` / `visible`, so "the composer's count climbs as
        # files land" is expressible without scripting a call sequence.
        self._texts = dict(texts or {})
        self.clicks: list[str] = []
        # (selector, nth index) per click. `clicks` keeps its old shape so the
        # existing membership assertions still read the way they did.
        self.click_targets: list[tuple[str, int]] = []
        self.fills: list[tuple[str, str]] = []
        self.file_inputs: list[tuple[str, str, int]] = []
        self.waits: list[tuple[str, str, Any]] = []
        self.text_queries: list[tuple[Any, bool]] = []
        self.navigations: list[str] = []
        self.removed_overlays = 0
        # caption -> data-checked。缺键 = 读不出来（None），不是 False。
        self.data_checked: dict[str, bool] = {}
        # caption -> what the click-target probe reports about that caption's
        # nodes. The fake cannot run JavaScript, so it recognises the probe by
        # the token in its source and answers from here; a caption with no
        # entry answers `[]`, i.e. "that text is not on this page as a leaf".
        self.dom_probe: dict[str, list[dict[str, Any]]] = {}
        # The music dialog's result rows, in the order the probe would return
        # them. A list of titles, or a callable taking the page — the same
        # value-or-callable convention as `visible`, so "the rows only exist
        # once something was typed into the search box" is expressible.
        self.music_rows: Any = ()
        # needle -> how many places on the page show it. Callable form takes
        # (page, needle), which is how a test says "the preview starts showing
        # the track only after its row was clicked".
        self.music_mentions: Any = {}
        self.keyboard = FakeKeyboard()

    @property
    def url(self) -> str:
        return self._url(self) if callable(self._url) else self._url

    def visible_now(self) -> set[str]:
        value = self._visible(self) if callable(self._visible) else self._visible
        return set(value)

    @property
    def texts(self) -> dict[str, str]:
        return {
            selector: (value(self) if callable(value) else value)
            for selector, value in self._texts.items()
        }

    def count_of(self, selector: str) -> int:
        # A node the test gave text to exists, without also having to list it in
        # `visible` — reading its text is the only thing the driver does with it.
        if selector not in self.counts and selector in self._texts:
            return 1 if self.texts.get(selector) else 0
        if selector in self.counts:
            value = self.counts[selector]
            # 与 `url` / `visible` 同样的约定:值或"接收 page 的函数"。此前
            # 只有那两个支持,counts 不支持,于是"某次点击之后这个节点就消失了"
            # 这类状态变化无法表达 —— 而弹窗关闭正是这种。
            return value(self) if callable(value) else value
        return 1 if selector in self.visible_now() else 0

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def get_by_text(self, text: Any, exact: bool = False) -> FakeLocator:
        # Recorded with the flag, because `exact` is load-bearing on this page
        # and invisible in the resulting key: 「允许」⊂「不允许」 is the house
        # example, and the identity chooser adds another (a generous match
        # resolves `.first` to an ancestor, i.e. the whole card stack).
        self.text_queries.append((text, exact))
        return FakeLocator(self, text_selector(text))

    def get_by_role(self, _role: str, name: str = "", exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"text={name}")

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.navigations.append(url)

    async def evaluate(self, _script: str, _arg: Any = None) -> Any:
        # `_radio_is_checked` asks the page whether the radio captioned `_arg`
        # carries data-checked="true". Answer from `data_checked`, where a
        # missing key means "could not determine" (None) — NOT False. The
        # production code treats those two very differently: False retries the
        # next caption, None refuses the publish outright.
        if "data-checked" in (_script or ""):
            return self.data_checked.get(_arg)
        if "__nous_click_target_probe__" in (_script or ""):
            return self.dom_probe.get(_arg, [])
        if "__nous_music_rows_probe__" in (_script or ""):
            rows = self.music_rows(self) if callable(self.music_rows) else self.music_rows
            # Shaped like the real probe's return value, indices included: the
            # driver clicks `[data-nous-music-row="<index>"]`, so a fake that
            # only handed back names would not exercise the addressing at all.
            return [{"index": i, "name": name} for i, name in enumerate(rows)]
        if "__nous_music_readback_probe__" in (_script or ""):
            mentions = self.music_mentions
            if callable(mentions):
                return int(mentions(self, _arg))
            return int(mentions.get(_arg, 0))
        self.removed_overlays += 1
        return 0

    async def wait_for_timeout(self, _ms: Any) -> None:
        return None
