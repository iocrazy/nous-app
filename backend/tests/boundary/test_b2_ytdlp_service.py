"""B2 — YtdlpService runtime asserts. Three public methods now require
ValidatedURL; passing raw str must crash at function entry, not run yt-dlp
with unvalidated input."""
from __future__ import annotations

import pytest

from app.boundary import ValidatedURL


@pytest.mark.unit
async def test_fetch_metadata_rejects_raw_str():
    from app.services.media.parsers.ytdlp_service import YtdlpService

    with pytest.raises(AssertionError, match="ValidatedURL"):
        await YtdlpService.fetch_metadata("https://example.com/v")


@pytest.mark.unit
async def test_download_video_rejects_raw_str(tmp_path):
    from app.services.media.parsers.ytdlp_service import YtdlpService

    with pytest.raises(AssertionError, match="ValidatedURL"):
        await YtdlpService.download_video(
            "https://example.com/v", str(tmp_path), "platform-123"
        )


@pytest.mark.unit
async def test_download_audio_rejects_raw_str(tmp_path):
    from app.services.media.parsers.ytdlp_service import YtdlpService

    with pytest.raises(AssertionError, match="ValidatedURL"):
        await YtdlpService.download_audio(
            "https://example.com/v", str(tmp_path), "platform-123"
        )


@pytest.mark.unit
async def test_fetch_metadata_accepts_validated_url(monkeypatch):
    """ValidatedURL passes the assert. We don't actually want to spawn
    yt-dlp, so we mock the subprocess path. The test only proves the
    assert allows ValidatedURL."""
    import asyncio

    from app.services.media.parsers.ytdlp_service import YtdlpService

    async def fake_subprocess(*args, **kwargs):
        # Return an object whose communicate() returns valid JSON
        class _Proc:
            returncode = 0
            async def communicate(self):
                return (b'{"title": "stub"}', b"")
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(asyncio, "wait_for", lambda awaitable, **kw: awaitable)

    validated = ValidatedURL("https://example.com/v")
    try:
        result = await YtdlpService.fetch_metadata(validated)
        assert result.get("title") == "stub"
    except AssertionError:
        pytest.fail("ValidatedURL should pass the runtime assert")
