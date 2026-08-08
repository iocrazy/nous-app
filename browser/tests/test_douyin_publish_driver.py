"""The publisher's own teardown contract, with a fake Playwright.

`publish()` is mostly a browser lifecycle: launch, drive, collect, close. The
driving is DOM work that only a real account can exercise, but the lifecycle
around it carries the one guarantee that is silent when broken - **the refreshed
session is collected on every path out**, including the ones where the publish
failed.

A missing renewal produces no error, no log line and no failed test. It shows up
months later as accounts that need re-scanning every couple of weeks instead of
every couple of months (design doc 4.2 step 6). That is exactly the kind of
invariant that has to be pinned by a test rather than by a comment.
"""

from typing import Any

import pytest

from app.platforms import douyin_publish
from app.publish import Deadline, PublishJob, PublishOutcome
from app.schemas import EnvironmentConfig, MediaItem, PublishIntent, SessionStatus

pytestmark = pytest.mark.unit

REFRESHED_STATE = {"cookies": [{"name": "sessionid", "value": "renewed"}]}


class FakeContext:
    def __init__(self, state: dict[str, Any] | None = None, explode_on_state: bool = False):
        self._state = state if state is not None else REFRESHED_STATE
        self._explode_on_state = explode_on_state
        self.closed = False
        self.state_calls = 0
        # 记录注入过的脚本 —— 反检测是否真的加到了发布这条路径上,
        # 由此可断言,而不是只看代码里写没写。
        self.init_scripts: list[str] = []

    async def new_page(self) -> Any:
        return object()

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def storage_state(self) -> dict[str, Any]:
        self.state_calls += 1
        if self._explode_on_state:
            raise RuntimeError("Target page, context or browser has been closed")
        return dict(self._state)

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext, fail_context: bool = False):
        self._context = context
        self._fail_context = fail_context
        self.closed = False

    async def new_context(self, **_kwargs) -> FakeContext:
        if self._fail_context:
            raise RuntimeError("net::ERR_PROXY_CONNECTION_FAILED at new_context")
        return self._context

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self, browser: FakeBrowser):
        self.chromium = self
        self._browser = browser

    async def launch(self, **_kwargs) -> FakeBrowser:
        return self._browser

    async def __aenter__(self) -> "FakePlaywright":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False


@pytest.fixture
def wire(monkeypatch):
    """Install a fake Playwright and a scripted `_drive`."""

    def install(drive_result: PublishOutcome | Exception, **context_kwargs):
        context = FakeContext(**context_kwargs)
        browser = FakeBrowser(context)
        # 打在 patchright 上 —— 生产代码 import 的是它。此前这里写死
        # "playwright.async_api",换 driver 后桩没打中,测试真的去启浏览器,
        # 报 "Executable doesn't exist at …chromium-1208"。测试替身跟着
        # 生产代码的 import 走,不要各写各的。
        monkeypatch.setattr(
            "patchright.async_api.async_playwright", lambda: FakePlaywright(browser)
        )

        async def fake_drive(_page, _job, _deadline):
            if isinstance(drive_result, Exception):
                raise drive_result
            return drive_result

        monkeypatch.setattr(douyin_publish, "_drive", fake_drive)
        return context, browser

    return install


def job(environment: EnvironmentConfig | None = None) -> PublishJob:
    return PublishJob(
        platform="douyin",
        storage_state={"cookies": [{"name": "sessionid", "value": "original"}]},
        environment=environment,
        intent=PublishIntent(
            content_type="video",
            media=[
                MediaItem(kind="video", url="https://host/clip.mp4", filename="clip.mp4")
            ],
            title="Launch Day Recap",
        ),
        assets={},
    )


def published() -> PublishOutcome:
    return PublishOutcome(
        status=SessionStatus.PUBLISHED, message="video published", detail={}
    )


async def _publish(environment: EnvironmentConfig | None = None) -> PublishOutcome:
    return await douyin_publish.publish(job(environment), Deadline(60))


# --- the renewal is collected on every path --------------------------------


async def test_refreshed_cookies_are_collected_after_a_successful_publish(wire):
    context, browser = wire(published())

    outcome = await _publish()

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.updated_storage_state == REFRESHED_STATE
    assert context.state_calls == 1
    assert browser.closed is True


async def test_refreshed_cookies_are_collected_even_when_the_publish_fails(wire):
    """The publish failed; the *session* did not. The platform slid it forward
    the moment the authenticated page loaded, and that renewal is worth exactly
    as much as it would have been on the happy path."""
    context, browser = wire(RuntimeError("the publish button was never found"))

    outcome = await _publish()

    assert outcome.status is SessionStatus.FAILED
    assert outcome.updated_storage_state == REFRESHED_STATE
    assert context.state_calls == 1
    assert browser.closed is True


async def test_a_typed_step_failure_keeps_its_status_and_still_returns_the_state(wire):
    context, _browser = wire(
        douyin_publish.StepFailure(
            SessionStatus.SESSION_INVALID,
            "the session dropped mid-publish",
            reason="session_lost_during_publish",
        )
    )

    outcome = await _publish()

    assert outcome.status is SessionStatus.SESSION_INVALID
    assert outcome.detail["reason"] == "session_lost_during_publish"
    assert outcome.updated_storage_state == REFRESHED_STATE
    assert context.state_calls == 1


async def test_a_timeout_is_classified_from_the_error_rather_than_flattened(wire):
    """`classify_playwright_error` is shared with validation, so a Playwright
    timeout reports `timeout` here too - a caller retrying on timeout must not
    have to distinguish which endpoint produced it."""
    _context, _browser = wire(RuntimeError("Timeout 30000ms exceeded"))

    outcome = await _publish()

    assert outcome.status is SessionStatus.TIMEOUT


async def test_a_failure_to_read_the_state_is_survivable(wire):
    """Losing the renewal is bad; turning a finished publish into a failure
    because we could not read cookies off a dying context is worse. The post is
    already live either way."""
    context, _browser = wire(published(), explode_on_state=True)

    outcome = await _publish()

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.updated_storage_state is None
    assert context.state_calls == 1


# --- failures before a context exists --------------------------------------


async def test_a_bad_proxy_url_never_launches_a_browser(wire):
    """Fail fast (spec 7.7), and as `proxy_failed` rather than `failed` - the
    status is what stops a proxy misconfiguration from being reported to the
    user as an account that needs re-scanning (spec 7.8)."""
    _context, browser = wire(published())

    outcome = await _publish(EnvironmentConfig(proxy_url="not-a-proxy-url"))

    assert outcome.status is SessionStatus.PROXY_FAILED
    assert outcome.detail["stage"] == "proxy_config"
    # The browser was never reached, so nothing needed closing.
    assert browser.closed is False


async def test_the_browser_is_closed_even_when_the_context_cannot_be_created(
    monkeypatch,
):
    """A leaked headed Chromium per failed publish is how a container that looks
    idle runs out of memory hours later."""
    browser = FakeBrowser(FakeContext(), fail_context=True)
    monkeypatch.setattr(
        "patchright.async_api.async_playwright", lambda: FakePlaywright(browser)
    )

    outcome = await douyin_publish.publish(job(), Deadline(60))

    assert outcome.status is SessionStatus.PROXY_FAILED
    assert outcome.updated_storage_state is None
    assert browser.closed is True


async def test_the_publish_path_actually_injects_the_evasions(wire):
    """发布这条路径在运行时真的注入了反检测脚本。

    源码守卫(test_stealth_injection)只能证明"代码里写了 apply_stealth",
    证明不了它在这条路径上真的被执行到 —— 一个提前 return、一个异常分支,
    都能让那行代码永远不运行。这里跑一遍真实驱动,断言 context 上确实落了
    脚本。
    """
    from app.browser_runtime import STEALTH_SCRIPT

    context, _browser = wire(published())

    outcome = await _publish()

    assert outcome.status is SessionStatus.PUBLISHED
    assert context.init_scripts, "发布路径没有注入任何 init script"
    assert STEALTH_SCRIPT in context.init_scripts
