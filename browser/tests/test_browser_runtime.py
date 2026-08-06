import pytest

from app import browser_runtime

from app.browser_runtime import (
    HEADLESS,
    ProxyConfigError,
    build_context_kwargs,
    build_launch_kwargs,
    parse_proxy_url,
    xvfb_socket_path,
)
from app.schemas import EnvironmentConfig

pytestmark = pytest.mark.unit


def test_launch_is_always_headed():
    """Design doc 2.4 is a decision, not a tunable. A regression to headless
    reintroduces intermittent false 'session expired' verdicts."""
    assert HEADLESS is False
    assert build_launch_kwargs(None)["headless"] is False


def test_launch_disables_the_automation_flag():
    assert "--disable-blink-features=AutomationControlled" in build_launch_kwargs(None)["args"]


def test_shared_memory_is_never_redirected_to_disk():
    """`--disable-dev-shm-usage` moves Chromium's shared memory from /dev/shm
    (tmpfs, in memory) to /tmp, which in this volume-less container is the
    overlay write layer - a real disk. This process renders logged-in pages, so
    that flag would put session-derived content on disk, against spec 7.6.

    The 64MB-default problem it exists to solve is handled by `shm_size: 1gb` on
    the compose service. Re-adding the flag here must be a conscious act.
    """
    assert "--disable-dev-shm-usage" not in build_launch_kwargs(None)["args"]


def test_proxy_credentials_are_split_out_of_the_server_url():
    options = parse_proxy_url("http://alice:s3cr3t@proxy.example.com:8080")
    assert options == {
        "server": "http://proxy.example.com:8080",
        "username": "alice",
        "password": "s3cr3t",
    }


def test_percent_encoded_proxy_credentials_are_decoded():
    options = parse_proxy_url("http://user%40corp:p%40ss@proxy.example.com:8080")
    assert options["username"] == "user@corp"
    assert options["password"] == "p@ss"


def test_proxy_without_credentials():
    assert parse_proxy_url("socks5://proxy.example.com:1080") == {
        "server": "socks5://proxy.example.com:1080"
    }


@pytest.mark.parametrize(
    "value", ["", "   ", "ftp://proxy.example.com:21", "proxy.example.com:8080", "http://"]
)
def test_bad_proxy_urls_raise_a_typed_error(value):
    with pytest.raises(ProxyConfigError):
        parse_proxy_url(value)


def test_launch_kwargs_carry_the_proxy():
    env = EnvironmentConfig(proxy_url="http://alice:s3cr3t@proxy.example.com:8080")
    assert build_launch_kwargs(env)["proxy"]["server"] == "http://proxy.example.com:8080"


def test_no_proxy_key_when_none_configured():
    assert "proxy" not in build_launch_kwargs(EnvironmentConfig())


def test_storage_state_is_passed_as_a_dict_never_a_path():
    """Spec 7.6: plaintext session material must not touch disk. Playwright
    accepts a path here, which is exactly the API we must not use."""
    state = {"cookies": [{"name": "sessionid", "value": "x"}]}
    kwargs = build_context_kwargs(None, state)
    assert kwargs["storage_state"] is state
    assert not isinstance(kwargs["storage_state"], str)


def test_environment_reaches_the_context():
    env = EnvironmentConfig(
        user_agent="Mozilla/5.0 (Test)",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        geo_lat=39.9042,
        geo_lng=116.4074,
    )
    kwargs = build_context_kwargs(env, {"cookies": []})
    assert kwargs["user_agent"] == "Mozilla/5.0 (Test)"
    assert kwargs["locale"] == "zh-CN"
    assert kwargs["timezone_id"] == "Asia/Shanghai"
    assert kwargs["geolocation"] == {"latitude": 39.9042, "longitude": 116.4074}
    # Without the grant the page gets a prompt instead of coordinates.
    assert kwargs["permissions"] == ["geolocation"]


def test_partial_geolocation_is_ignored():
    kwargs = build_context_kwargs(EnvironmentConfig(geo_lat=39.9), {"cookies": []})
    assert "geolocation" not in kwargs
    assert "permissions" not in kwargs


def test_null_environment_fields_do_not_leak_none_into_context_kwargs():
    env = EnvironmentConfig(locale=None, timezone_id=None)
    kwargs = build_context_kwargs(env, {"cookies": []})
    assert set(kwargs) == {"storage_state"}


@pytest.mark.parametrize(
    "display,expected",
    [
        (":99", "/tmp/.X11-unix/X99"),
        (":0", "/tmp/.X11-unix/X0"),
        (":99.0", "/tmp/.X11-unix/X99"),
        ("host:0", None),
        ("", None),
        (":abc", None),
    ],
)
def test_xvfb_socket_path(display, expected):
    assert xvfb_socket_path(display) == expected


# --- xvfb_ready: the probe that was not a probe -----------------------------
#
# Shipped 2026-08-06 as `os.path.exists(socket)`. Xvfb died in production, left
# its socket and lock file behind, and /healthz kept answering `xvfb: true`
# while every headed launch failed. The user was shown "the platform refused
# the sign-in" and went looking at proxies.


def test_xvfb_ready_is_false_when_no_socket_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browser_runtime, "xvfb_socket_path", lambda _d: str(tmp_path / "X99")
    )
    assert browser_runtime.xvfb_ready(":99") is False


def test_xvfb_ready_is_false_for_a_socket_nobody_is_listening_on(
    tmp_path, monkeypatch
):
    """The regression itself: the file outlives the process.

    A bound-then-closed socket leaves exactly what a crashed Xvfb leaves — a
    path that stats fine and refuses connections. An existence check calls this
    healthy; that is how a dead display reported ready for hours.
    """
    import socket as _socket

    path = tmp_path / "X99"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.close()  # file stays, listener does not

    monkeypatch.setattr(browser_runtime, "xvfb_socket_path", lambda _d: str(path))
    assert path.exists(), "fixture must leave the socket file behind"
    assert browser_runtime.xvfb_ready(":99") is False


def test_xvfb_ready_is_true_when_something_is_actually_listening(
    tmp_path, monkeypatch
):
    import socket as _socket

    path = tmp_path / "X99"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    try:
        monkeypatch.setattr(browser_runtime, "xvfb_socket_path", lambda _d: str(path))
        assert browser_runtime.xvfb_ready(":99") is True
    finally:
        srv.close()
