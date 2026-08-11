"""HTTP contract for the five login endpoints.

Two rules get asserted repeatedly because callers depend on them more than on
any individual field: every non-2xx body is a `SessionResult`, and the polling
routes answer 200 with their own shape even when the outcome is a failure - a
poller must never read the HTTP code to find out what happened to the login.
"""

import pytest
from fastapi.testclient import TestClient

from app import login_sessions, main
from app.config import get_settings
from app.login_sessions import LoginSessionRegistry
from app.schemas import SessionStatus
from tests.conftest import TEST_TOKEN
from tests.fakes import FakeDriver, always, driver_factory, make_spec

pytestmark = pytest.mark.unit

AUTH = {"X-Internal-Token": TEST_TOKEN}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def wired(monkeypatch):
    """Register a fake platform and point the app's registry at a fake driver.

    Returns a helper that installs a given judge/driver, so each test scripts
    the page state it cares about without a browser anywhere in the picture.
    """
    installed: dict = {}

    def install(judge=None, driver=None, platform="testplatform"):
        spec = make_spec(judge=judge, platform=platform)
        drv = driver or FakeDriver(spec)
        registry = LoginSessionRegistry(open_driver=driver_factory(drv))
        monkeypatch.setattr(login_sessions, "_registry", registry)
        monkeypatch.setattr(main, "get_login_flow", lambda p: spec if p == platform else None)
        monkeypatch.setattr(main, "login_platforms", lambda: [platform])
        installed.update(spec=spec, driver=drv, registry=registry)
        return installed

    yield install
    login_sessions.reset_registry()


def _start(client, platform="testplatform", **body):
    payload = {"platform": platform}
    payload.update(body)
    return client.post("/session/login/start", json=payload, headers=AUTH)


# --- auth ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/session/login/start"),
        ("get", "/session/login/abc/status"),
        ("post", "/session/login/abc/sms"),
        ("get", "/session/login/abc/state"),
        ("post", "/session/login/abc/close"),
    ],
)
def test_every_login_endpoint_requires_the_internal_token(client, method, path):
    """This service holds decrypted session material and hands out plaintext
    storage_state; none of it may be reachable unauthenticated, even on the
    internal network."""
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 401


def test_an_unset_token_fails_closed_rather_than_disabling_auth(client, monkeypatch):
    monkeypatch.setenv("BROWSER_INTERNAL_TOKEN", "")
    get_settings.cache_clear()
    resp = client.post("/session/login/start", json={"platform": "douyin"}, headers={"X-Internal-Token": ""})
    assert resp.status_code == 503


# --- start -----------------------------------------------------------------


def test_start_returns_the_handle_and_the_qr_code(client, wired):
    wired()
    resp = _start(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.WAITING_SCAN.value
    assert body["qrcode_data_url"].startswith("data:image")
    assert body["login_session_id"]
    assert body["expires_at"]


def test_unsupported_platform_is_a_400_in_the_result_shape(client, wired):
    wired()
    resp = _start(client, platform="kuaishou")

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == SessionStatus.FAILED.value
    assert body["success"] is False
    assert "testplatform" in body["detail"]["supported"]


def test_capacity_exhaustion_is_back_pressure_not_an_account_verdict(
    client, wired, monkeypatch
):
    monkeypatch.setenv("BROWSER_LOGIN_MAX_SESSIONS", "1")
    get_settings.cache_clear()
    wired()

    assert _start(client).status_code == 200
    resp = _start(client)

    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "login_capacity"
    # Tells the caller to queue rather than to give up on the account.
    assert resp.headers["Retry-After"]


def test_a_proxy_failure_at_launch_keeps_its_own_status(client, wired, monkeypatch):
    wired()

    async def factory(_spec, _env):
        raise RuntimeError("net::ERR_PROXY_CONNECTION_FAILED")

    monkeypatch.setattr(login_sessions.get_registry(), "_open_driver", factory)
    resp = _start(client)

    assert resp.status_code == 502
    # Flattening this into `failed` would have the UI tell the user to re-scan
    # a QR code in order to fix a proxy outage.
    assert resp.json()["status"] == SessionStatus.PROXY_FAILED.value


def test_a_malformed_body_is_rejected_before_any_browser_work(client, wired):
    wired()
    resp = client.post("/session/login/start", json={}, headers=AUTH)
    assert resp.status_code == 422


# --- status ----------------------------------------------------------------


def test_status_returns_the_current_page_state(client, wired):
    wired()
    session_id = _start(client).json()["login_session_id"]

    resp = client.get(f"/session/login/{session_id}/status", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.WAITING_SCAN.value
    assert body["detail"]["terminal"] is False
    assert body["detail"]["expires_at"]


def test_expired_qrcode_arrives_already_refreshed(client, wired):
    wired(judge=always(SessionStatus.QRCODE_EXPIRED, "二维码失效"))
    session_id = _start(client).json()["login_session_id"]

    body = client.get(f"/session/login/{session_id}/status", headers=AUTH).json()

    assert body["status"] == SessionStatus.QRCODE_EXPIRED.value
    assert body["qrcode_data_url"] == "data:image/png;base64,QR1"


def test_unknown_session_is_404_in_the_result_shape(client, wired):
    wired()
    resp = client.get("/session/login/does-not-exist/status", headers=AUTH)

    assert resp.status_code == 404
    assert resp.json()["detail"]["reason"] == "unknown_login_session"


def test_polling_an_expired_session_gets_timeout_not_a_404(client, wired):
    """The tombstone exists for exactly this: a poller that arrives a second
    late must be told the login timed out, not handed an ambiguous 404."""
    from datetime import timedelta

    wired()
    session_id = _start(client).json()["login_session_id"]
    session = login_sessions.get_registry().get(session_id)
    session.expires_at = session.created_at - timedelta(seconds=1)

    resp = client.get(f"/session/login/{session_id}/status", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.TIMEOUT.value
    assert body["detail"]["terminal"] is True
    assert body["detail"]["released"] is True


def test_a_driver_crash_is_a_200_with_a_typed_status(client, wired):
    from tests.fakes import ExplodingDriver

    spec = make_spec()
    wired(driver=ExplodingDriver(spec))
    session_id = _start(client).json()["login_session_id"]

    resp = client.get(f"/session/login/{session_id}/status", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["status"] == SessionStatus.FAILED.value


# --- sms -------------------------------------------------------------------


def test_submitting_a_code_returns_the_resulting_status(client, wired):
    installed = wired(judge=always(SessionStatus.SUCCESS, "logged in"))
    session_id = _start(client).json()["login_session_id"]

    resp = client.post(f"/session/login/{session_id}/sms", json={"code": "123456"}, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["status"] == SessionStatus.SUCCESS.value
    assert installed["driver"].submitted_codes == ["123456"]


def test_a_rejected_code_says_so_on_the_wire_not_just_in_the_session(client, wired):
    """The marker has to make it out of the process, or it protects nothing.

    `SmsCodeResponse` used to be `{status, message}` only, which meant a
    verdict that existed in the session object was dropped at the HTTP
    boundary — and the backend, seeing a pending `sms_required`, called the
    submission a success.
    """
    wired(judge=always(SessionStatus.SMS_REQUIRED, "asking for a verification code"))
    session_id = _start(client).json()["login_session_id"]

    resp = client.post(
        f"/session/login/{session_id}/sms", json={"code": "000000"}, headers=AUTH
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.SMS_REQUIRED.value
    assert body["detail"]["code_rejected"] is True
    # The submitted code is never echoed back, on any path.
    assert "000000" not in resp.text


@pytest.mark.parametrize("code", ["", "12", "abcdef", "12345678901", "12 34"])
def test_a_nonsense_code_is_rejected_before_it_reaches_the_page(client, wired, code):
    installed = wired()
    session_id = _start(client).json()["login_session_id"]

    resp = client.post(f"/session/login/{session_id}/sms", json={"code": code}, headers=AUTH)

    assert resp.status_code == 422
    assert installed["driver"].submitted_codes == []


def test_sms_on_an_unknown_session_is_404(client, wired):
    wired()
    resp = client.post("/session/login/nope/sms", json={"code": "123456"}, headers=AUTH)
    assert resp.status_code == 404


# --- state -----------------------------------------------------------------


def test_state_returns_the_session_material_and_the_profile(client, wired):
    wired(judge=always(SessionStatus.SUCCESS, "logged in"))
    session_id = _start(client).json()["login_session_id"]

    resp = client.get(f"/session/login/{session_id}/state", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["storage_state"]["cookies"][0]["name"] == "sessionid"
    assert body["username"] == "Test Creator"
    assert body["platform_user_id"] == "uid"


def test_state_before_login_completes_is_409_and_says_what_to_wait_for(client, wired):
    wired()
    session_id = _start(client).json()["login_session_id"]

    resp = client.get(f"/session/login/{session_id}/state", headers=AUTH)

    assert resp.status_code == 409
    body = resp.json()
    # The status says whether waiting longer would help; the caller does not
    # have to parse the message to find out.
    assert body["status"] == SessionStatus.WAITING_SCAN.value
    assert body["success"] is False


def test_state_on_an_unknown_session_is_404(client, wired):
    wired()
    assert client.get("/session/login/nope/state", headers=AUTH).status_code == 404


# --- close -----------------------------------------------------------------


def test_close_releases_the_browser(client, wired):
    installed = wired()
    session_id = _start(client).json()["login_session_id"]

    resp = client.post(f"/session/login/{session_id}/close", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"closed": True}
    assert installed["driver"].closed is True


def test_close_is_idempotent(client, wired):
    wired()
    session_id = _start(client).json()["login_session_id"]

    assert client.post(f"/session/login/{session_id}/close", headers=AUTH).status_code == 200
    assert client.post(f"/session/login/{session_id}/close", headers=AUTH).status_code == 200


def test_closing_an_unknown_session_is_404(client, wired):
    wired()
    assert client.post("/session/login/nope/close", headers=AUTH).status_code == 404


# --- credential hygiene ----------------------------------------------------


def test_storage_state_never_appears_in_a_status_or_sms_response(client, wired):
    """Plaintext session material leaves this service through exactly one
    route: /state (spec 7.6). Any other response carrying it is a leak."""
    spec = make_spec(judge=always(SessionStatus.SUCCESS, "logged in"))
    driver = FakeDriver(spec, storage={"cookies": [{"name": "sessionid", "value": "TOP-SECRET"}]})
    wired(judge=always(SessionStatus.SUCCESS, "logged in"), driver=driver)
    session_id = _start(client).json()["login_session_id"]

    status_body = client.get(f"/session/login/{session_id}/status", headers=AUTH).text
    sms_body = client.post(
        f"/session/login/{session_id}/sms", json={"code": "123456"}, headers=AUTH
    ).text

    assert "TOP-SECRET" not in status_body
    assert "TOP-SECRET" not in sms_body
