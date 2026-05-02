"""B1 retro-fit — media_fetch_router.fetch_video must reject SSRF URLs
at the boundary, before any service / points / DB call."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.boundary import url_guard
from app.core.exceptions import register_exception_handlers


@pytest.fixture(autouse=True)
def _reset_boundary_caches():
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()


@pytest.fixture
def app_with_routes() -> FastAPI:
    """Minimal app with the media fetch router and global handlers attached."""
    from app.api.media_fetch_router import router
    from app.core.deps import AuthContext, get_auth

    async def _fake_auth():
        return AuthContext(user_id="test-user", auth_type="jwt", scopes=None)

    app = FastAPI()
    register_exception_handlers(app)
    app.dependency_overrides[get_auth] = _fake_auth
    app.include_router(router)
    return app


@pytest.mark.unit
def test_fetch_video_rejects_internal_nas_url(app_with_routes: FastAPI):
    """Smoking gun: user posts NAS Supabase URL — must be rejected
    by SOME layer (extract_valid_url whitelist OR boundary), and the
    raw IP must NEVER leak to the response body."""
    client = TestClient(app_with_routes, raise_server_exceptions=False)
    r = client.post(
        "/fetch",
        json={"url": "http://192.168.50.9:9080/admin"},
    )
    assert r.status_code in (400, 422), f"got {r.status_code}: {r.text}"
    # Critical: no IP leak regardless of which layer rejected
    assert "192.168.50.9" not in r.text


@pytest.mark.unit
def test_fetch_video_rejects_decimal_ip_bypass(app_with_routes: FastAPI):
    """Non-canonical IPv4 (decimal) — must not bypass to a downstream
    fetcher. Either extract_valid_url or boundary catches it."""
    client = TestClient(app_with_routes, raise_server_exceptions=False)
    r = client.post("/fetch", json={"url": "http://2130706433/admin"})
    assert r.status_code in (400, 422), f"got {r.status_code}: {r.text}"
    assert "2130706433" not in r.text


@pytest.mark.unit
def test_fetch_video_rejects_localhost_suffix(app_with_routes: FastAPI):
    """mDNS suffix — must not be passed through to ytdlp."""
    client = TestClient(app_with_routes, raise_server_exceptions=False)
    r = client.post("/fetch", json={"url": "http://printer.localhost/api"})
    assert r.status_code in (400, 422), f"got {r.status_code}: {r.text}"
    assert "printer.localhost" not in r.text


@pytest.mark.unit
def test_fetch_video_boundary_fires_after_extract_passes(
    app_with_routes: FastAPI, monkeypatch
):
    """Verify boundary layer specifically catches a URL that passes
    extract_valid_url. Mock extract to allow + mock DNS to private."""
    from app.core.utils import Utils

    # Make extract_valid_url accept anything (returns the URL as-is)
    monkeypatch.setattr(
        Utils, "extract_valid_url", staticmethod(lambda url: [url])
    )
    # Mock DNS resolver to return private IP — boundary must reject
    monkeypatch.setattr(
        url_guard, "_resolve_host_async",
        AsyncMock(return_value=["192.168.50.9"]),
    )

    client = TestClient(app_with_routes, raise_server_exceptions=False)
    r = client.post(
        "/fetch",
        json={"url": "https://attacker-controlled.example.com/video"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body.get("code") == "url_blocked"
    # DNS-resolved IP must not leak to the response body
    assert "192.168.50.9" not in r.text
    assert "attacker-controlled" not in r.text
