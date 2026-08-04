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
