"""Unit tests for transcode encoder probing (transcode_service._probe_encoder).

Covers the 2026-05-14 fix. The probe must reflect what actually works at
*runtime* on this machine, not what ffmpeg was compiled with. An ffmpeg
build with nvenc support lists `h264_nvenc` in `ffmpeg -encoders` even on a
GPU-less box — the old probe trusted that list, picked h264_nvenc, and then
failed every tier with "No device available" before falling back to
libx264. The new probe runs a 1-frame encode and trusts the return code.
"""

from __future__ import annotations

import pytest

from app.services.media.transcode.transcode_service import TranscodeService

pytestmark = pytest.mark.unit


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def communicate(self):
        return b"", b""


@pytest.fixture
def patch_subprocess(monkeypatch):
    def _install(returncode: int | None = None, raises: Exception | None = None):
        async def _fake_exec(*args, **kwargs):
            if raises is not None:
                raise raises
            return _FakeProc(returncode)

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    return _install


async def test_probe_returns_true_when_encode_succeeds(patch_subprocess):
    patch_subprocess(returncode=0)
    assert await TranscodeService._probe_encoder("h264_nvenc") is True


async def test_probe_returns_false_when_encode_fails(patch_subprocess):
    # NAS case: the ffmpeg build lists h264_nvenc, but there is no GPU, so
    # a real 1-frame encode exits non-zero ("No device available").
    patch_subprocess(returncode=8)
    assert await TranscodeService._probe_encoder("h264_nvenc") is False


async def test_probe_returns_false_on_subprocess_error(patch_subprocess):
    patch_subprocess(raises=FileNotFoundError("ffmpeg not found"))
    assert await TranscodeService._probe_encoder("libx264") is False
