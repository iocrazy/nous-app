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
