"""Regression: serve_audio_file must resolve audio for audio-only media that has
NO video download_path (qishui music) — previously _get_media_download_path 404'd
before music_download_path was ever checked."""

from app.api.media_slides_router import _resolve_audio_file


def test_resolves_music_download_path_without_video_download_path(tmp_path):
    f = tmp_path / "global/resources/web/qishui/9/audio.m4a"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    media = {
        "music_download_path": "global/resources/web/qishui/9/audio.m4a"
    }  # no download_path
    assert _resolve_audio_file(media, str(tmp_path)) == f


def test_falls_back_to_extract_audio_path(tmp_path):
    f = tmp_path / "ex/audio.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    media = {"music_download_path": None, "extract_audio_path": "ex/audio.mp3"}
    assert _resolve_audio_file(media, str(tmp_path)) == f


def test_video_download_path_audio_mp3_fallback(tmp_path):
    f = tmp_path / "dl/audio.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    media = {"download_path": "dl"}
    assert _resolve_audio_file(media, str(tmp_path)) == f


def test_none_when_nothing_on_disk(tmp_path):
    assert (
        _resolve_audio_file({"music_download_path": "nope.m4a"}, str(tmp_path)) is None
    )
    assert _resolve_audio_file({}, str(tmp_path)) is None
