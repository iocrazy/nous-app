import pytest
from fastapi.testclient import TestClient

from app import main

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    return TestClient(main.app)


def _patch(monkeypatch, *, browser_ready: bool, xvfb: bool):
    async def fake_probe(*_args, **_kwargs):
        return browser_ready

    monkeypatch.setattr(main, "probe_browser_ready", fake_probe)
    monkeypatch.setattr(main, "xvfb_ready", lambda *_a, **_k: xvfb)


def test_healthy(client, monkeypatch):
    _patch(monkeypatch, browser_ready=True, xvfb=True)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["browser_ready"] is True
    assert body["xvfb"] is True
    assert body["version"]


def test_dead_browser_is_reported_and_returns_503(client, monkeypatch):
    """browser_ready must reflect a real launch attempt. A green /healthz over a
    dead engine is the failure mode this project already paid for once."""
    _patch(monkeypatch, browser_ready=False, xvfb=True)
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "browser_ready": False,
        "xvfb": True,
        "version": resp.json()["version"],
    }


def test_dead_xvfb_is_reported_and_returns_503(client, monkeypatch):
    _patch(monkeypatch, browser_ready=True, xvfb=False)
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["xvfb"] is False


def test_healthz_needs_no_token(client, monkeypatch):
    """Container healthchecks must reach it, and it exposes no secrets."""
    _patch(monkeypatch, browser_ready=True, xvfb=True)
    monkeypatch.setenv("BROWSER_INTERNAL_TOKEN", "")
    assert client.get("/healthz").status_code == 200
