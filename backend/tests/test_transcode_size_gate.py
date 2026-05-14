"""Unit tests for the admin-configured transcode size gate.

Covers the 2026-05-14 fix. `transcode_min_size_mb` is an admin setting
(system_settings table) that means "don't waste a transcode on files
smaller than this". The PR-D7 workflow refactor dropped the gate from the
trigger path — the setting was still stored, shown in the admin UI, and
loaded into `settings` at startup, but nothing consumed it, so every file
got transcoded regardless. The gate is restored in
`TranscodeService.transcode_version`; these tests pin it.
"""

from __future__ import annotations

import pytest

from app.services.media.transcode.transcode_service import TranscodeService

pytestmark = pytest.mark.unit

_MB = 1024 * 1024


class _FakeRepo:
    """Records update_version calls so tests can assert on transcode_status."""

    def __init__(self, version: dict) -> None:
        self._version = version
        self.updates: list[tuple[str, dict]] = []

    async def get_version_by_id(self, version_id: str) -> dict:
        return self._version

    async def update_version(self, version_id: str, data: dict) -> dict:
        self.updates.append((version_id, data))
        return {}


def _settings_stub(min_size_mb: str):
    """Stub for TranscodeService._get_db_setting — only the keys
    transcode_version reads before the size gate."""
    table = {"transcode_enabled": "true", "transcode_min_size_mb": min_size_mb}
    return staticmethod(lambda key: table.get(key))


async def test_size_gate_skips_file_below_threshold(monkeypatch):
    svc = TranscodeService()
    svc.repo = _FakeRepo({"file_path": "x/v1/video.mp4", "file_size_bytes": 50 * _MB})
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub("100"))

    result = await svc.transcode_version("r1", "v1")

    assert result is None
    # Gate hit before "mark processing" — the only update is the skip.
    assert svc.repo.updates == [("v1", {"transcode_status": "skipped"})]


async def test_size_gate_passes_file_above_threshold(monkeypatch):
    # 200 MB > 100 MB threshold → gate must NOT skip. The file path does
    # not exist on disk, so transcode_version proceeds past the gate,
    # marks 'processing', then fails on the missing source — proving the
    # gate let it through rather than skipping it.
    svc = TranscodeService()
    svc.repo = _FakeRepo(
        {"file_path": "nonexistent/v1/video.mp4", "file_size_bytes": 200 * _MB}
    )
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub("100"))

    result = await svc.transcode_version("r1", "v1")

    assert result is None  # fails later (source missing), not gated
    statuses = [data.get("transcode_status") for _, data in svc.repo.updates]
    assert "skipped" not in statuses
    assert "processing" in statuses  # gate passed → proceeded


async def test_size_gate_disabled_when_min_size_zero(monkeypatch):
    # min_size_mb = 0 means "transcode everything" — even a 1 KB file must
    # not be skipped by the gate.
    svc = TranscodeService()
    svc.repo = _FakeRepo(
        {"file_path": "nonexistent/v1/video.mp4", "file_size_bytes": 1024}
    )
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub("0"))

    await svc.transcode_version("r1", "v1")

    statuses = [data.get("transcode_status") for _, data in svc.repo.updates]
    assert "skipped" not in statuses
