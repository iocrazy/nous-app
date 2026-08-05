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
    ):
        self.spec = spec
        self._snapshots = snapshots or [LoginPageSnapshot(url="https://x/", qrcode_visible=True)]
        self._index = 0
        self._qrcode = qrcode
        self._refreshed = refreshed_qrcode
        self._storage = storage or {"cookies": [{"name": "sessionid", "value": "s"}]}
        self._profile = profile or LoginProfile(platform_user_id="uid", username="Test Creator")
        self._sms_input_present = sms_input_present
        self.closed = False
        self.submitted_codes: list[str] = []
        self.refresh_calls = 0
        self.profile_calls = 0

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


def make_spec(judge=None, platform: str = "testplatform") -> LoginFlowSpec:
    return LoginFlowSpec(
        platform=platform,
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
        parse_profile=lambda _fields, _cookies: LoginProfile(),
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

    def get_by_text(self, text: str, exact: bool = False) -> "FakeLocator":
        return FakeLocator(self.page, f"text={text}")

    def get_by_role(self, _role: str, name: str = "", exact: bool = False) -> "FakeLocator":
        return FakeLocator(self.page, f"text={name}")

    async def count(self) -> int:
        return self.page.count_of(self.selector)

    async def is_visible(self) -> bool:
        return self.selector in self.page.visible_now()

    async def wait_for(self, state: str = "visible", timeout: Any = None) -> None:
        self.page.waits.append((self.selector, state, timeout))
        if not self.page.count_of(self.selector):
            raise TimeoutError(f"Timeout waiting for {self.selector}")

    async def click(self, timeout: Any = None, force: bool = False) -> None:
        self.page.clicks.append(self.selector)

    async def evaluate(self, _expression: str) -> None:
        self.page.clicks.append(self.selector)

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
    ):
        self._url = url
        self._visible = visible
        self.counts = dict(counts or {})
        self.attributes = attributes or {}
        self.clicks: list[str] = []
        self.file_inputs: list[tuple[str, str, int]] = []
        self.waits: list[tuple[str, str, Any]] = []
        self.navigations: list[str] = []
        self.removed_overlays = 0
        self.keyboard = FakeKeyboard()

    @property
    def url(self) -> str:
        return self._url(self) if callable(self._url) else self._url

    def visible_now(self) -> set[str]:
        value = self._visible(self) if callable(self._visible) else self._visible
        return set(value)

    def count_of(self, selector: str) -> int:
        if selector in self.counts:
            return self.counts[selector]
        return 1 if selector in self.visible_now() else 0

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"text={text}")

    def get_by_role(self, _role: str, name: str = "", exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"text={name}")

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.navigations.append(url)

    async def evaluate(self, _script: str, _arg: Any = None) -> int:
        self.removed_overlays += 1
        return 0

    async def wait_for_timeout(self, _ms: Any) -> None:
        return None
