"""Lifecycle of a keep-alive login session.

This is where the real risk of S2 lives. Everything else in the service is
request-scoped and cleans itself up by returning; a login session deliberately
outlives its request, so every test here is ultimately asking one question:
**does the browser actually get closed?**
"""

import asyncio
from datetime import timedelta

import pytest

from app.config import get_settings
from app.login import IdentityUnresolved
from app.login_sessions import (
    LoginCapacityError,
    LoginError,
    LoginSessionRegistry,
    SessionPhase,
)
from app.schemas import SessionStatus
from tests.fakes import (
    ExplodingDriver,
    FakeDriver,
    SlowDriver,
    always,
    driver_factory,
    make_spec,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def spec():
    return make_spec()


async def _registry_with(driver, spec):
    return LoginSessionRegistry(open_driver=driver_factory(driver))


# --- start -----------------------------------------------------------------


async def test_start_returns_a_live_session_holding_the_qr_code(spec):
    driver = FakeDriver(spec)
    registry = await _registry_with(driver, spec)

    session = await registry.start(spec, None)

    assert session.status is SessionStatus.WAITING_SCAN
    assert session.qrcode_data_url == "data:image/png;base64,QR0"
    assert session.is_active
    assert driver.closed is False
    assert registry.get(session.id) is session


async def test_a_page_with_no_qr_code_does_not_leave_a_browser_running(spec):
    """The failure that would leak most quietly: start fails, nobody holds a
    handle, and the Chromium stays up until the container dies."""
    driver = FakeDriver(spec, qrcode=None)
    registry = await _registry_with(driver, spec)

    with pytest.raises(LoginError) as exc:
        await registry.start(spec, None)

    assert exc.value.status is SessionStatus.FAILED
    assert driver.closed is True
    assert registry.active_count() == 0


async def test_capacity_is_refused_with_a_typed_error_not_by_opening_one_more(
    spec, monkeypatch
):
    monkeypatch.setenv("BROWSER_LOGIN_MAX_SESSIONS", "2")
    get_settings.cache_clear()

    opened = []

    async def factory(_spec, _env):
        driver = FakeDriver(_spec)
        opened.append(driver)
        return driver

    registry = LoginSessionRegistry(open_driver=factory)
    await registry.start(spec, None)
    await registry.start(spec, None)

    with pytest.raises(LoginCapacityError) as exc:
        await registry.start(spec, None)

    assert exc.value.detail["reason"] == "login_capacity"
    assert exc.value.detail["limit"] == 2
    # The refusal must happen *before* a browser is launched, or the ceiling
    # protects nothing.
    assert len(opened) == 2


async def test_a_closed_session_frees_its_capacity_slot(spec, monkeypatch):
    monkeypatch.setenv("BROWSER_LOGIN_MAX_SESSIONS", "1")
    get_settings.cache_clear()

    async def factory(_spec, _env):
        return FakeDriver(_spec)

    registry = LoginSessionRegistry(open_driver=factory)
    first = await registry.start(spec, None)
    await registry.close(first.id)

    second = await registry.start(spec, None)
    assert second.is_active


async def test_a_failing_launch_becomes_a_typed_status(spec):
    async def factory(_spec, _env):
        raise RuntimeError("net::ERR_PROXY_CONNECTION_FAILED at http://p:1")

    registry = LoginSessionRegistry(open_driver=factory)
    with pytest.raises(LoginError) as exc:
        await registry.start(spec, None)

    # Not session_invalid, not failed: a dead proxy must not send the user off
    # to re-scan a QR code that was never the problem.
    assert exc.value.status is SessionStatus.PROXY_FAILED


async def test_launch_credentials_never_reach_the_error_message(spec):
    async def factory(_spec, _env):
        raise RuntimeError("proxy http://alice:s3cr3t@proxy.example.com:8080 refused")

    registry = LoginSessionRegistry(open_driver=factory)
    with pytest.raises(LoginError) as exc:
        await registry.start(spec, None)

    assert "s3cr3t" not in exc.value.message
    assert "alice" not in exc.value.message


# --- status polling --------------------------------------------------------


async def test_status_is_a_single_snapshot_and_does_not_wait(spec):
    registry = await _registry_with(FakeDriver(spec), spec)
    session = await registry.start(spec, None)

    snapshot = await asyncio.wait_for(session.poll_status(), timeout=1)
    assert snapshot.status is SessionStatus.WAITING_SCAN
    assert snapshot.detail["terminal"] is False


async def test_expired_qrcode_comes_back_refreshed_in_the_same_response(spec):
    """Contract: the caller must never receive `qrcode_expired` without the
    replacement code, or the UI shows a dead image until the next poll."""
    driver = FakeDriver(spec)
    spec_expired = make_spec(judge=always(SessionStatus.QRCODE_EXPIRED, "二维码失效"))
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec_expired, None)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.QRCODE_EXPIRED
    assert snapshot.qrcode_data_url == "data:image/png;base64,QR1"
    assert snapshot.detail["refreshed"] is True
    assert driver.refresh_calls == 1


async def test_a_refresh_that_fails_reports_no_code_rather_than_the_dead_one(spec):
    driver = FakeDriver(spec, refreshed_qrcode=None)
    spec_expired = make_spec(judge=always(SessionStatus.QRCODE_EXPIRED, "二维码失效"))
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec_expired, None)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.QRCODE_EXPIRED
    # Null, not stale: serving the consumed code would have the user scan an
    # image that cannot work.
    assert snapshot.qrcode_data_url is None
    assert snapshot.detail["refreshed"] is False


async def test_success_drops_the_qrcode_and_marks_the_poll_terminal(spec):
    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(spec_ok)))
    session = await registry.start(spec_ok, None)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.SUCCESS
    assert snapshot.qrcode_data_url is None
    assert snapshot.detail["terminal"] is True


async def test_a_crashed_page_becomes_a_typed_failure_and_releases_the_browser(spec):
    driver = ExplodingDriver(spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)

    with pytest.raises(LoginError) as exc:
        await session.poll_status()

    assert exc.value.status is SessionStatus.FAILED
    # A page in an unknown state is not worth keeping - but the tombstone stays
    # so the caller still learns why.
    assert driver.closed is True
    assert session.phase is SessionPhase.RELEASED
    assert registry.get(session.id) is not None


async def test_concurrent_operations_are_serialised_within_their_wait_budget(
    spec, monkeypatch
):
    monkeypatch.setenv("BROWSER_LOGIN_LOCK_WAIT_S", "1")
    get_settings.cache_clear()

    registry = LoginSessionRegistry(open_driver=driver_factory(SlowDriver(spec, delay=5)))
    session = await registry.start(spec, None)

    slow = asyncio.create_task(session.poll_status())
    await asyncio.sleep(0.1)

    # The second caller must not queue forever behind a stuck page.
    with pytest.raises(LoginError) as exc:
        await session.poll_status()
    assert exc.value.status is SessionStatus.TIMEOUT
    assert exc.value.detail["stage"] == "lock"

    slow.cancel()


# --- TTL and reaping -------------------------------------------------------


async def test_an_expired_session_is_released_without_the_reaper(spec):
    """Inline expiry is the load-bearing one. If cleanup only worked when a
    background task was alive, a dead reaper would mean unbounded leakage with
    every probe still green."""
    driver = FakeDriver(spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)

    session.expires_at = session.created_at - timedelta(seconds=1)
    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.TIMEOUT
    assert snapshot.detail["released"] is True
    assert driver.closed is True


async def test_the_sweep_closes_browsers_rather_than_just_forgetting_them(spec):
    driver = FakeDriver(spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)
    session.expires_at = session.created_at - timedelta(seconds=1)

    assert await registry.sweep() == 1
    assert driver.closed is True
    assert session.phase is SessionPhase.RELEASED


async def test_a_released_session_stays_queryable_as_a_tombstone_then_is_purged(spec):
    driver = FakeDriver(spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)
    session.expires_at = session.created_at - timedelta(seconds=1)
    await registry.sweep()

    # Still answerable: the poller gets `timeout`, not an unexplained 404.
    assert registry.get(session.id) is session
    assert session.status is SessionStatus.TIMEOUT

    session.purge_at = session.created_at - timedelta(seconds=1)
    assert registry.get(session.id) is None


async def test_a_successful_login_gets_a_bounded_extension_to_be_collected(
    spec, monkeypatch
):
    """A scan that lands at 4:58 of a 5:00 TTL must still leave time to fetch
    storage_state, or the user scanned for nothing."""
    monkeypatch.setenv("BROWSER_LOGIN_STATE_GRACE_S", "60")
    get_settings.cache_clear()

    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(spec_ok)))
    session = await registry.start(spec_ok, None)
    session.expires_at = session.created_at + timedelta(seconds=1)

    await session.poll_status()
    extended = session.expires_at

    assert extended > session.created_at + timedelta(seconds=30)

    # One-shot: staying successful must not keep pushing the deadline out.
    await session.poll_status()
    assert session.expires_at == extended


async def test_reaper_releases_expired_sessions_in_the_background(spec, monkeypatch):
    monkeypatch.setenv("BROWSER_LOGIN_REAPER_INTERVAL_S", "1")
    get_settings.cache_clear()

    driver = FakeDriver(spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)
    session.expires_at = session.created_at - timedelta(seconds=1)

    registry.start_reaper()
    try:
        for _ in range(30):
            if driver.closed:
                break
            await asyncio.sleep(0.1)
    finally:
        await registry.shutdown()

    assert driver.closed is True


async def test_shutdown_closes_every_live_browser(spec):
    drivers = []

    async def factory(_spec, _env):
        driver = FakeDriver(_spec)
        drivers.append(driver)
        return driver

    registry = LoginSessionRegistry(open_driver=factory)
    await registry.start(spec, None)
    await registry.start(spec, None)

    await registry.shutdown()

    assert all(d.closed for d in drivers)
    assert registry.active_count() == 0


# --- sms -------------------------------------------------------------------


async def test_submitting_a_code_reports_the_resulting_page_state(spec):
    driver = FakeDriver(spec)
    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec_ok, None)

    snapshot = await session.submit_sms("123456")

    assert driver.submitted_codes == ["123456"]
    assert snapshot.status is SessionStatus.SUCCESS


async def test_submitting_when_no_input_is_present_says_so_instead_of_failing(spec):
    driver = FakeDriver(spec, sms_input_present=False)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)

    snapshot = await session.submit_sms("123456")

    assert "no verification code input" in snapshot.message
    assert snapshot.status is SessionStatus.WAITING_SCAN
    # Nothing was typed, so nothing was refused. Marking this a rejection would
    # tell the user their code is wrong when the page never took one.
    assert "code_rejected" not in snapshot.detail


async def test_a_code_the_page_did_not_accept_comes_back_marked_as_rejected(spec):
    """**The tombstone of "wrong code, zero feedback".**

    The page still showing a code field *after* we typed one into it is the
    only evidence available, and it has to survive as something a caller can
    branch on. Delete the marker and the whole chain silently reverts: the
    backend's `success` goes back to True (`sms_required` is a pending state),
    the modal takes the success path, clears the field and says nothing.
    """
    sms_spec = make_spec(
        judge=always(SessionStatus.SMS_REQUIRED, "the platform is asking for a code")
    )
    driver = FakeDriver(sms_spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(sms_spec, None)

    snapshot = await session.submit_sms("000000")

    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert snapshot.detail["code_rejected"] is True
    assert snapshot.detail["submitted"] is True
    # The judge's reason reads as a first-time prompt; a rejection must not
    # report it as its message, because that message is shown to people.
    assert "was not accepted" in snapshot.message
    # Never the code itself, on any path.
    assert "000000" not in snapshot.message


async def test_the_first_request_for_a_code_is_not_reported_as_a_rejection(spec):
    """The other half of the same contract: polling has submitted nothing."""
    sms_spec = make_spec(
        judge=always(SessionStatus.SMS_REQUIRED, "the platform is asking for a code")
    )
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(sms_spec)))
    session = await registry.start(sms_spec, None)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert "code_rejected" not in snapshot.detail


async def test_an_accepted_code_carries_no_rejection_marker(spec):
    sms_spec = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(sms_spec)))
    session = await registry.start(sms_spec, None)

    snapshot = await session.submit_sms("123456")

    assert snapshot.status is SessionStatus.SUCCESS
    assert "code_rejected" not in snapshot.detail


# --- state collection ------------------------------------------------------


async def test_state_is_refused_until_the_login_actually_completed(spec):
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(spec)))
    session = await registry.start(spec, None)

    with pytest.raises(LoginError) as exc:
        await session.collect_state()

    assert exc.value.status is SessionStatus.WAITING_SCAN
    assert "has not completed" in exc.value.message


async def test_state_is_returned_once_the_page_says_logged_in(spec):
    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    driver = FakeDriver(spec_ok)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec_ok, None)

    state, profile = await session.collect_state()

    assert state["cookies"][0]["name"] == "sessionid"
    assert profile.username == "Test Creator"


async def test_a_failed_profile_scrape_still_yields_the_session_state(spec):
    """Cookies are the thing we came for. A display name that could not be read
    must not throw away a login the user already completed."""

    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    driver = FakeDriver(spec_ok)

    async def boom():
        raise RuntimeError("navigation failed")

    driver.read_profile = boom
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec_ok, None)

    state, profile = await session.collect_state()

    assert state["cookies"]
    assert profile.username == ""


async def test_an_unresolved_identity_fails_the_login_instead_of_degrading(spec):
    """The one profile failure that must NOT degrade.

    A nameless account is a cosmetic loss. An account with no identity key is a
    *different* account: the backend upserts on
    `(scope, platform, platform_user_id)`, so binding without one either fails
    downstream with a generic message or, worse, keys on whatever substitute is
    at hand and forks the row. `identity_unresolved` says which, so the user is
    told to rescan rather than left owning two half-accounts.
    """
    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    driver = FakeDriver(spec_ok)

    async def unresolved():
        raise IdentityUnresolved("testplatform", "test_uid")

    driver.read_profile = unresolved
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec_ok, None)

    with pytest.raises(LoginError) as exc:
        await session.collect_state()

    assert exc.value.status is SessionStatus.FAILED
    assert exc.value.detail["reason"] == "identity_unresolved"
    assert exc.value.detail["identity_cookie"] == "test_uid"
    assert exc.value.detail["terminal"] is True


async def test_state_after_release_is_a_typed_error_not_a_crash(spec):
    """The browser is gone, so the cookies are gone. Say that, with the status
    the session ended on, rather than raising out of the handler."""
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(spec)))
    session = await registry.start(spec, None)
    await registry.close(session.id)

    with pytest.raises(LoginError) as exc:
        await session.collect_state()

    assert exc.value.status is SessionStatus.FAILED
    assert "no longer active" in exc.value.message


async def test_closing_a_completed_login_is_not_recorded_as_a_failure(spec):
    """Closing after a successful bind is the happy path's last step."""
    spec_ok = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(spec_ok)))
    session = await registry.start(spec_ok, None)
    await session.poll_status()

    await registry.close(session.id)
    assert session.status is SessionStatus.SUCCESS


async def test_close_is_idempotent(spec):
    driver = FakeDriver(spec)
    registry = LoginSessionRegistry(open_driver=driver_factory(driver))
    session = await registry.start(spec, None)

    assert await registry.close(session.id) is True
    assert await registry.close(session.id) is True
    assert driver.closed is True


async def test_closing_an_unknown_session_reports_it(spec):
    registry = LoginSessionRegistry(open_driver=driver_factory(FakeDriver(spec)))
    assert await registry.close("nope") is False
