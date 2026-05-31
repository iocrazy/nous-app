from app.workflows.soda_download import build_audio_dest, build_resource_row


def test_build_audio_dest_uses_platform_and_ext():
    full, rel = build_audio_dest(media_id="999", ext="flac", base_dir="/tmp/dl")
    assert rel == "global/resources/web/qishui/999/audio.flac"
    assert str(full).endswith("global/resources/web/qishui/999/audio.flac")


def test_build_resource_row_required_fields():
    row = build_resource_row(
        creator_id="user-1",
        media_id="999",
        file_path="global/x/audio.flac",
        ext="flac",
        size_bytes=12345,
        title="Song",
    )
    assert row["creator_id"] == "user-1"
    assert row["media_id"] == "999"
    assert row["source_type"] == "web"
    assert row["file_type"] == "audio"
    assert row["mime_type"] == "audio/flac"
    assert row["filename"] == "Song.flac"
    assert row["file_path"] == "global/x/audio.flac"
    assert row["file_size_bytes"] == 12345
    assert row["music_download_status"] == "completed"
    assert "scope_type" not in row
