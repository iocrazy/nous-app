"""B4 + B5 + B7 retro-fits — verify boundary fires at each entry point.

B4: sb_ai_router /storyboard/analyze-video + /storyboard/detect-scenes
B5: visual_analysis_service._encode_image_from_url
B7: download_progress.download_file_with_progress
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.boundary import url_guard
from app.core.exceptions import register_exception_handlers


@pytest.fixture(autouse=True)
def _reset_boundary_caches():
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()


# ============================================================================
# B4 — sb_ai_router (analyze-video + detect-scenes)
# ============================================================================

@pytest.fixture
def sb_app() -> FastAPI:
    from app.api.sb_ai_router import router
    from app.core.deps import AuthContext, get_auth

    async def _fake_auth():
        return AuthContext(user_id="test-user", auth_type="jwt", scopes=None)

    app = FastAPI()
    register_exception_handlers(app)
    app.dependency_overrides[get_auth] = _fake_auth
    app.include_router(router)
    return app


@pytest.mark.unit
def test_b4_analyze_video_rejects_internal_ip(sb_app: FastAPI):
    """analyze-video: NAS IP must be blocked before workflow dispatch."""
    client = TestClient(sb_app, raise_server_exceptions=False)
    r = client.post(
        "/storyboard/analyze-video",
        json={"project_id": "p1", "video_url": "http://192.168.50.9:9080/video.mp4"},
    )
    assert r.status_code == 400, f"got {r.status_code}: {r.text}"
    assert r.json()["code"] == "url_blocked"
    assert "192.168.50.9" not in r.text


@pytest.mark.unit
def test_b4_detect_scenes_rejects_localhost(sb_app: FastAPI):
    """detect-scenes: .localhost suffix must be blocked."""
    client = TestClient(sb_app, raise_server_exceptions=False)
    r = client.post(
        "/storyboard/detect-scenes",
        json={
            "project_id": "p1",
            "video_url": "http://printer.localhost/v.mp4",
            "threshold": 0.3,
        },
    )
    assert r.status_code == 400, f"got {r.status_code}: {r.text}"
    assert r.json()["code"] == "url_blocked"
    assert "printer.localhost" not in r.text


@pytest.mark.unit
def test_b4_analyze_video_rejects_decimal_ipv4(sb_app: FastAPI):
    """analyze-video: decimal IPv4 (http://2130706433/) bypass blocked."""
    client = TestClient(sb_app, raise_server_exceptions=False)
    r = client.post(
        "/storyboard/analyze-video",
        json={"project_id": "p1", "video_url": "http://2130706433/v.mp4"},
    )
    assert r.status_code == 400
    assert "2130706433" not in r.text


# ============================================================================
# B5 — visual_analysis._encode_image_from_url
# ============================================================================

@pytest.mark.unit
async def test_b5_image_url_blocked_returns_none(monkeypatch):
    """When URL fails boundary, _encode_image_from_url returns None
    without making any httpx call (no SSRF leak)."""
    from app.services.visual_analysis_service import VisualAnalysisService

    # Construct minimal instance (skip __init__ that needs DB)
    svc = VisualAnalysisService.__new__(VisualAnalysisService)

    # Mock httpx so a leak would be detectable
    httpx_called = {"n": 0}
    import httpx as _httpx

    class FakeClient:
        async def __aenter__(self):
            httpx_called["n"] += 1
            return self
        async def __aexit__(self, *a):
            return None
        async def get(self, *a, **kw):
            httpx_called["n"] += 100
            raise AssertionError("httpx.get must not be called for blocked URL")

    monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    result = await svc._encode_image_from_url("http://192.168.50.9:9080/img.jpg")
    assert result is None
    assert httpx_called["n"] == 0  # boundary blocked before httpx context manager


@pytest.mark.unit
async def test_b5_image_url_decimal_ipv4_blocked(monkeypatch):
    from app.services.visual_analysis_service import VisualAnalysisService

    svc = VisualAnalysisService.__new__(VisualAnalysisService)
    result = await svc._encode_image_from_url("http://2130706433/img.jpg")
    assert result is None


# ============================================================================
# B7 — download_progress.download_file_with_progress
# ============================================================================

@pytest.mark.unit
async def test_b7_download_url_blocked_returns_false(tmp_path):
    """Blocked URL must return False AND mark tracker failed,
    without any httpx call."""
    from app.services.download_progress import (
        DownloadProgressTracker,
        download_file_with_progress,
    )

    # Minimal tracker stub
    class _StubTracker:
        def __init__(self):
            self.failed_called = False
            self.failed_reason: str | None = None
            self.complete_called = False
            self.update_called = 0

        def complete(self):
            self.complete_called = True

        def failed(self, reason: str):
            self.failed_called = True
            self.failed_reason = reason

        def update(self, downloaded: int, total: int):
            self.update_called += 1

    tracker = _StubTracker()
    out_path = tmp_path / "subdir" / "out.bin"

    ok = await download_file_with_progress(
        url="http://192.168.50.9:9080/file.bin",
        file_path=str(out_path),
        tracker=tracker,
    )
    assert ok is False
    assert tracker.failed_called is True
    assert tracker.complete_called is False
    # File path must NOT have been created
    assert not out_path.exists()


@pytest.mark.unit
async def test_b7_download_localhost_suffix_blocked(tmp_path):
    from app.services.download_progress import download_file_with_progress

    class _StubTracker:
        def __init__(self):
            self.failed_called = False
        def complete(self): pass
        def failed(self, reason): self.failed_called = True
        def update(self, *a): pass

    tracker = _StubTracker()
    out = tmp_path / "x" / "y.bin"
    ok = await download_file_with_progress(
        url="http://printer.localhost/file.bin",
        file_path=str(out),
        tracker=tracker,
    )
    assert ok is False
    assert tracker.failed_called is True
