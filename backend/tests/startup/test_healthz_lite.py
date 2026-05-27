"""Verify healthz_lite serves 200 from a background thread."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

import pytest

from app.startup import healthz_lite


@pytest.fixture
def lite_server():
    server = healthz_lite.start(port=0)  # port=0 = OS-assigned
    yield server
    healthz_lite.stop(server)


def test_lite_endpoint_returns_200(lite_server):
    port = lite_server.server_port
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/lite", timeout=2)
    assert resp.status == 200
    body = resp.read().decode()
    assert "ok" in body


def test_lite_endpoint_survives_main_loop_block(lite_server):
    """The lite endpoint runs on its own thread, so a sync sleep in the test
    thread (simulating a blocked main loop) must NOT make it timeout."""
    port = lite_server.server_port
    # Block the test thread (analog of a blocked event loop) but the
    # lite server thread should still answer.
    started = time.monotonic()
    time.sleep(0.5)
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/lite", timeout=2)
    assert resp.status == 200
    assert time.monotonic() - started < 2.0


def test_stop_kills_server(lite_server):
    """Calling stop must release the port within 1s."""
    port = lite_server.server_port
    healthz_lite.stop(lite_server)
    time.sleep(0.1)
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/lite", timeout=1)
