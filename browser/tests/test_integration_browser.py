"""Tests that need a real Chromium. Skipped wherever one is not installed.

Run with: uv run --extra dev pytest -m integration
"""

import pytest

from app.browser_runtime import probe_browser_ready
from app.platforms.douyin import validate_session
from app.schemas import SessionStatus

pytestmark = pytest.mark.integration


def _chromium_available() -> bool:
    """`executable_path` returns a path even when nothing was ever downloaded,
    so it has to be checked on disk - otherwise these tests 'run' and fail with
    'Executable doesn't exist' on every machine without browsers."""
    try:
        from pathlib import Path

        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


requires_browser = pytest.mark.skipif(
    not _chromium_available(), reason="no Chromium install available"
)


@requires_browser
async def test_probe_reports_a_launchable_browser():
    assert await probe_browser_ready(force=True) is True


@requires_browser
async def test_garbage_session_is_reported_invalid_not_crashed():
    """A fabricated cookie is the closest we get to 'expired session' without a
    real account: the platform must bounce us to login and we must say
    session_invalid rather than raising."""
    state = {
        "cookies": [
            {
                "name": "sessionid",
                "value": "definitely-not-a-real-session",
                "domain": ".douyin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    result = await validate_session(state)
    assert result.success is False
    # Network-restricted environments legitimately land on timeout/failed; the
    # contract we assert is that the status is always a typed member.
    assert result.status in {
        SessionStatus.SESSION_INVALID,
        SessionStatus.TIMEOUT,
        SessionStatus.FAILED,
        SessionStatus.PROXY_FAILED,
    }


@requires_browser
async def test_dead_proxy_is_reported_as_proxy_failed():
    from app.schemas import EnvironmentConfig

    state = {"cookies": [{"name": "sessionid", "value": "x", "domain": ".douyin.com", "path": "/"}]}
    # Port 1 has no listener, so Chromium fails at the proxy layer.
    env = EnvironmentConfig(proxy_url="http://127.0.0.1:1")
    result = await validate_session(state, env)
    assert result.status is SessionStatus.PROXY_FAILED


# --- QR login ---------------------------------------------------------------


@requires_browser
async def test_login_start_against_the_real_platform_yields_a_scannable_code():
    """The one thing no unit test can cover: do the QR selectors still match?

    Selector rot is silent and total - the judge, the registry and the TTL all
    keep working perfectly while every login fails at `read_qrcode`. Network
    restrictions make a hard assertion impossible here, so the test asserts the
    *typed* outcome and prints which way it went; run it inside the container to
    verify the selectors for real.
    """
    from app.login_sessions import LoginError, LoginSessionRegistry
    from app.platforms.douyin import LOGIN_SPEC

    registry = LoginSessionRegistry()
    try:
        session = await registry.start(LOGIN_SPEC, None)
    except LoginError as exc:
        # Reachable without network: what must not happen is an untyped crash.
        assert exc.status in {
            SessionStatus.FAILED,
            SessionStatus.TIMEOUT,
            SessionStatus.PROXY_FAILED,
        }
        pytest.skip(f"could not reach the login page: {exc.status.value} - {exc.message}")

    try:
        assert session.qrcode_data_url.startswith("data:image")
        snapshot = await session.poll_status()
        assert snapshot.status in {
            SessionStatus.WAITING_SCAN,
            SessionStatus.QRCODE_EXPIRED,
        }
    finally:
        await registry.shutdown()


@requires_browser
async def test_an_abandoned_login_really_closes_its_browser():
    """The leak this whole design exists to prevent, checked end to end against
    a real Chromium rather than a fake driver."""
    from datetime import timedelta

    from app.login import LoginDriver
    from app.login_sessions import LoginError, LoginSessionRegistry, SessionPhase
    from app.platforms.douyin import LOGIN_SPEC

    registry = LoginSessionRegistry()
    try:
        session = await registry.start(LOGIN_SPEC, None)
    except LoginError as exc:
        pytest.skip(f"could not reach the login page: {exc.status.value}")

    driver: LoginDriver = session._driver
    browser = driver._browser
    assert browser.is_connected()

    session.expires_at = session.created_at - timedelta(seconds=1)
    assert await registry.sweep() == 1

    assert session.phase is SessionPhase.RELEASED
    # Not "the registry entry is gone" - the browser process itself.
    assert browser.is_connected() is False
    await registry.shutdown()


# --- publish ----------------------------------------------------------------
#
# Only the pre-upload half is exercisable here: everything past the session
# check needs a real logged-in account, and there is none in CI. That half is
# still the one worth pinning against a real browser, because it is where the
# expensive mistakes are avoided.


def _publish_intent(media_url: str = "https://127.0.0.1:1/never-fetched.mp4"):
    from app.schemas import MediaItem, PublishIntent

    return PublishIntent(
        content_type="video",
        media=[MediaItem(kind="video", url=media_url, filename="clip.mp4")],
        title="Integration Probe",
    )


def _publish_job(**overrides):
    """A `PublishJob` for the form steps, which never look at the assets."""
    from app.assets import StagedAsset
    from app.publish import PublishJob
    from app.schemas import MediaItem, PublishIntent

    return PublishJob(
        platform="douyin",
        storage_state={"cookies": []},
        environment=overrides.pop("environment", None),
        intent=PublishIntent(
            content_type="video",
            media=[
                MediaItem(kind="video", url="https://127.0.0.1:1/x.mp4", filename="clip.mp4")
            ],
            title="Integration Probe",
            **overrides,
        ),
        assets={
            "video": StagedAsset(
                role="video", path="/tmp/none.mp4", filename="clip.mp4", size_bytes=1
            )
        },
    )


@requires_browser
async def test_a_publish_with_a_dead_session_stops_before_downloading_anything():
    """The asset URL here is unreachable on purpose.

    If the precheck ever moved after staging, this test would fail with a
    download error instead of a precheck verdict - which is exactly the
    regression worth catching, because in production the same reordering just
    looks like slower failures and wasted bandwidth on dead accounts.
    """
    from app.platforms import get_publisher
    from app.publish import run_publish

    state = {
        "cookies": [
            {
                "name": "sessionid",
                "value": "definitely-not-a-real-session",
                "domain": ".douyin.com",
                "path": "/",
            }
        ],
        "origins": [],
    }
    response = await run_publish(
        "douyin", get_publisher("douyin"), state, None, _publish_intent()
    )

    assert response.success is False
    assert response.detail["stage"] == "precheck"
    assert response.status in {
        SessionStatus.SESSION_INVALID,
        SessionStatus.TIMEOUT,
        SessionStatus.FAILED,
        SessionStatus.PROXY_FAILED,
    }


@requires_browser
async def test_a_publish_behind_a_dead_proxy_is_reported_as_a_proxy_failure():
    """`proxy_failed`, never `session_invalid`: a proxy outage that reads as an
    account problem sends every account behind that proxy off to re-scan a QR
    code for something the user cannot fix."""
    from app.platforms import get_publisher
    from app.publish import run_publish
    from app.schemas import EnvironmentConfig

    state = {"cookies": [{"name": "sessionid", "value": "x", "domain": ".douyin.com", "path": "/"}]}
    response = await run_publish(
        "douyin",
        get_publisher("douyin"),
        state,
        EnvironmentConfig(proxy_url="http://127.0.0.1:1"),
        _publish_intent(),
    )

    assert response.status is SessionStatus.PROXY_FAILED
    assert response.detail["stage"] == "precheck"


# --- the publish form's new fields ------------------------------------------
#
# These run against synthetic markup, not against Douyin. That is the point:
# the *selectors* cannot be verified without a logged-in account, but the
# *Playwright technique* can, and it is the half the fake page silently
# approves of. `FakePage` accepts any locator call that exists on it, so a wrong
# key name, an unsupported `exact=` on a role query, or a `wait_for(state=...)`
# the real API rejects all pass the unit suite and fail once, in production,
# three minutes into a publish.


SEMI_LIKE_FORM = """
<!doctype html><html><body>
  <div class="radio"><label class="semi-radio"><input type="radio" name="when">
    <span class="semi-radio-addon" style="pointer-events:none">立即发布</span>
  </label></div>
  <div class="radio"><label class="semi-radio"><input type="radio" name="when">
    <span class="semi-radio-addon" style="pointer-events:none">定时发布</span>
  </label></div>
  <input class="semi-input" placeholder="日期和时间" />
</body></html>
"""

SEMI_LIKE_DECLARATION = """
<!doctype html><html><body>
  <div>请选择自主声明</div>
  <div class="semi-modal-content">
    <div>对作品内容添加声明</div>
    <label class="semi-radio"><input type="radio" name="d">
      <span class="semi-radio-addon" style="pointer-events:none">内容由AI生成</span></label>
    <label class="semi-radio"><input type="radio" name="d">
      <span class="semi-radio-addon" style="pointer-events:none">虚构演绎，仅供娱乐</span></label>
    <label class="semi-radio"><input type="radio" name="d">
      <span class="semi-radio-addon" style="pointer-events:none">无需添加自主声明</span></label>
    <button>取消</button><button>确定</button>
  </div>
  <script>
    document.querySelectorAll('button')[1].addEventListener('click', () => {
      document.querySelector('.semi-modal-content').style.display = 'none';
    });
  </script>
</body></html>
"""


async def _page_with(content: str):
    """A real Chromium page holding `content`. Caller closes the browser."""
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    page = await (await browser.new_context()).new_page()
    await page.set_content(content)
    return playwright, browser, page


@requires_browser
async def test_the_scheduled_time_really_lands_in_a_semi_style_field():
    """Click, select-all, type, Enter - against a real keyboard implementation.

    `Control+KeyA` is the part no fake can check. Get that key name wrong and
    the select-all silently does nothing, so the typed timestamp is *appended*
    to the picker's own default rather than replacing it - which produces a
    valid-looking string, a schedule nobody chose, and no error anywhere.
    """
    from datetime import datetime, timezone

    from app.platforms import douyin_publish as dp
    from app.publish import Deadline

    playwright, browser, page = await _page_with(SEMI_LIKE_FORM)
    try:
        # Pre-seed the field the way the platform's picker does, so a
        # select-all that fails to select shows up as a concatenation.
        await page.locator('.semi-input[placeholder="日期和时间"]').fill("2026-01-01 00:00")

        job = _publish_job(scheduled_at=datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc))
        result = await dp._set_schedule(page, job, Deadline(30))

        assert result["scheduled_input"] == "2026-08-08 12:00"
        value = await page.locator('.semi-input[placeholder="日期和时间"]').input_value()
        assert value == "2026-08-08 12:00", f"select-all did not replace the seed: {value!r}"
        # And the radio the caption belongs to is the one that got selected,
        # despite `pointer-events: none` on that caption (§7.4).
        assert await page.locator(".semi-radio").nth(1).locator("input").is_checked()
    finally:
        await browser.close()
        await playwright.stop()


@requires_browser
async def test_the_declaration_dialog_chain_uses_a_real_playwright_api():
    """Every locator call in the declaration step, executed for real.

    Specifically: `.filter(has_text=...)` scoping to the modal, `get_by_text`
    with `exact=True`, `get_by_role("button", name="确定", exact=True)` reaching
    the right one of two buttons, and `wait_for(state="hidden")` actually
    resolving once the dialog closes. A publish that raises `AttributeError`
    here fails as an untyped crash, after the upload.
    """
    from app.platforms import douyin_publish as dp
    from app.publish import Deadline

    playwright, browser, page = await _page_with(SEMI_LIKE_DECLARATION)
    try:
        job = _publish_job(platform_options={"self_declaration": "虚构演绎,仅供娱乐"})
        result = await dp._set_self_declaration(page, job, Deadline(30))

        # The half-width comma the caller sent was folded onto the platform's
        # own copy, and that is what got clicked.
        assert result["self_declaration_value"] == "虚构演绎，仅供娱乐"
        assert await page.locator(".semi-radio").nth(1).locator("input").is_checked()
    finally:
        await browser.close()
        await playwright.stop()


@requires_browser
async def test_a_declaration_the_dialog_does_not_offer_fails_typed_not_crashed():
    """The failure path through the same real API. It must arrive as a
    `StepFailure` carrying what the dialog *did* offer - an untyped exception
    here reaches the endpoint as a generic 500-shaped answer and loses the one
    detail that makes it fixable."""
    from app.platforms import douyin_publish as dp
    from app.publish import Deadline

    playwright, browser, page = await _page_with(SEMI_LIKE_DECLARATION)
    try:
        job = _publish_job(platform_options={"self_declaration": "内容为转载信息"})
        with pytest.raises(dp.StepFailure) as excinfo:
            await dp._set_self_declaration(page, job, Deadline(30))

        assert excinfo.value.detail["reason"] == "self_declaration_option_missing"
        assert "内容由AI生成" in excinfo.value.detail["options_on_screen"]
    finally:
        await browser.close()
        await playwright.stop()
