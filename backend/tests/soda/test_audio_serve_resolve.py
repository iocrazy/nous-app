"""Regression: serve_audio_file must resolve audio for audio-only media that has
NO video download_path (qishui music) — previously _get_media_download_path 404'd
before music_download_path was ever checked.

_resolve_audio_file was renamed to _resolve_audio_source (PR-1 终审 fix #2) and
made async + object-storage-aware: it now returns the selected candidate's raw
string value (rel path or sb:// value) instead of a materialized Path, so it
can be handed straight to serve_stored_file. The filesystem-only scenarios
below are unchanged in behavior — only the return type differs (str, not
Path)."""

import pytest

from app.api.media_slides_router import _resolve_audio_source

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_resolves_music_download_path_without_video_download_path(tmp_path):
    rel = "global/resources/web/qishui/9/audio.m4a"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    media = {"music_download_path": rel}  # no download_path
    assert await _resolve_audio_source(media, str(tmp_path)) == rel


@pytest.mark.asyncio
async def test_falls_back_to_extract_audio_path(tmp_path):
    rel = "ex/audio.mp3"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    media = {"music_download_path": None, "extract_audio_path": rel}
    assert await _resolve_audio_source(media, str(tmp_path)) == rel


@pytest.mark.asyncio
async def test_video_download_path_audio_mp3_fallback(tmp_path):
    f = tmp_path / "dl/audio.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    media = {"download_path": "dl"}
    assert await _resolve_audio_source(media, str(tmp_path)) == "dl/audio.mp3"


@pytest.mark.asyncio
async def test_none_when_nothing_on_disk(tmp_path):
    assert (
        await _resolve_audio_source({"music_download_path": "nope.m4a"}, str(tmp_path))
        is None
    )
    assert await _resolve_audio_source({}, str(tmp_path)) is None


@pytest.mark.asyncio
async def test_sb_download_path_not_used_for_derived_candidate(tmp_path):
    """An sb:// download_path has no meaningful "/audio.mp3" sibling —
    the derived candidate must not be constructed for it (mirrors C3's
    dropped .parent fallback)."""
    media = {"download_path": "sb://library/1/ab/cd/video.mp4"}
    assert await _resolve_audio_source(media, str(tmp_path)) is None
