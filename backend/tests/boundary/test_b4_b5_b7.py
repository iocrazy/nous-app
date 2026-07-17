"""B5 + B7 retro-fits — verify boundary fires at each entry point.

B5: visual_analysis_service._encode_image_from_url
B7: download_progress.download_file_with_progress
(B4 covered sb_ai_router; removed with the retired storyboard workbench.)
"""

from __future__ import annotations

import pytest

from app.boundary import url_guard


@pytest.fixture(autouse=True)
def _reset_boundary_caches():
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()


# ============================================================================
# B5 — visual_analysis._encode_image_from_url
# ============================================================================


@pytest.mark.unit
async def test_b5_image_url_blocked_returns_none(monkeypatch):
    """When URL fails boundary, _encode_image_from_url returns None
    without making any httpx call (no SSRF leak)."""
    from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

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
    from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

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
    from app.services.media.downloader.download_progress import (
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
    from app.services.media.downloader.download_progress import (
        download_file_with_progress,
    )

    class _StubTracker:
        def __init__(self):
            self.failed_called = False

        def complete(self):
            pass

        def failed(self, reason):
            self.failed_called = True

        def update(self, *a):
            pass

    tracker = _StubTracker()
    out = tmp_path / "x" / "y.bin"
    ok = await download_file_with_progress(
        url="http://printer.localhost/file.bin",
        file_path=str(out),
        tracker=tracker,
    )
    assert ok is False
    assert tracker.failed_called is True
