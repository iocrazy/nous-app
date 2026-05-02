"""B3 — DownloaderService.download_file defensive SSRF guard.

6 internal call sites pass URLs from parsed metadata fields. Defensive
validation inside download_file catches anything that bypassed boundary."""
from __future__ import annotations

import pytest

from app.boundary import url_guard


@pytest.fixture(autouse=True)
def _reset_caches():
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()


@pytest.mark.unit
async def test_download_file_blocks_internal_ip(tmp_path):
    """NAS IP must be rejected before any HTTP request — no file written."""
    from app.services.downloader import DownloaderService

    out = tmp_path / "subdir" / "file.bin"
    ok = await DownloaderService.download_file(
        url="http://192.168.50.9:9080/file.bin",
        file_path=str(out),
    )
    assert ok is False
    # File MUST NOT have been written (the bytes never came down)
    assert not out.exists()


@pytest.mark.unit
async def test_download_file_blocks_decimal_ipv4(tmp_path):
    from app.services.downloader import DownloaderService

    out = tmp_path / "x" / "file.bin"
    ok = await DownloaderService.download_file(
        url="http://2130706433/file.bin",  # decimal == 127.0.0.1
        file_path=str(out),
    )
    assert ok is False


@pytest.mark.unit
async def test_download_file_blocks_localhost_suffix(tmp_path):
    from app.services.downloader import DownloaderService

    out = tmp_path / "y" / "file.bin"
    ok = await DownloaderService.download_file(
        url="http://printer.localhost/file.bin",
        file_path=str(out),
    )
    assert ok is False


@pytest.mark.unit
async def test_download_file_progress_tracker_marked_failed(tmp_path):
    """When boundary blocks, progress_tracker.failed() must be called
    so the unified task manager sees the failure."""
    from app.services.downloader import DownloaderService

    class _StubTracker:
        def __init__(self):
            self.failed_called = False
            self.failed_reason = None
            self.complete_called = False
        def failed(self, reason: str):
            self.failed_called = True
            self.failed_reason = reason
        def complete(self):
            self.complete_called = True
        def update(self, *a, **kw):
            pass

    tracker = _StubTracker()
    out = tmp_path / "z" / "file.bin"
    ok = await DownloaderService.download_file(
        url="http://10.0.0.1/file.bin",
        file_path=str(out),
        progress_tracker=tracker,
    )
    assert ok is False
    assert tracker.failed_called is True
    assert tracker.complete_called is False
