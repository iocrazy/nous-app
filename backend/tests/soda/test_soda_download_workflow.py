from app.workflows.soda_download import (
    build_audio_dest,
    build_cover_dest,
    build_resource_fields,
)


def test_build_audio_dest_uses_platform_and_ext():
    full, rel = build_audio_dest(media_id="999", ext="flac", base_dir="/tmp/dl")
    assert rel == "global/resources/web/qishui/999/audio.flac"
    assert str(full).endswith("global/resources/web/qishui/999/audio.flac")


def test_build_cover_dest_mirrors_audio_dest():
    full, rel = build_cover_dest(media_id="999", base_dir="/tmp/dl")
    assert rel == "global/resources/web/qishui/999/cover.jpg"
    assert str(full).endswith("global/resources/web/qishui/999/cover.jpg")
    assert str(full).startswith("/tmp/dl")


def test_build_resource_fields_are_valid_resources_columns():
    fields = build_resource_fields(
        file_path="global/x/audio.flac", ext="flac", size_bytes=12345, title="Song"
    )
    assert fields["file_type"] == "audio"
    assert fields["mime_type"] == "audio/flac"
    assert fields["filename"] == "Song.flac"
    assert fields["file_path"] == "global/x/audio.flac"
    assert fields["file_size_bytes"] == 12345


def test_build_resource_fields_omit_nonexistent_columns():
    # Regression: `resources` has NO music_download_status column (PGRST204).
    # The completion fields must only contain real resources columns, and must
    # not carry creator_id/media_id/source_type (those belong to the create
    # fallback, not the update path).
    fields = build_resource_fields(file_path="p", ext="m4a", size_bytes=1, title="t")
    assert "music_download_status" not in fields
    assert "scope_type" not in fields
    assert "creator_id" not in fields
    assert "media_id" not in fields


def test_build_resource_fields_mime_by_ext():
    assert (
        build_resource_fields(file_path="p", ext="m4a", size_bytes=1, title="t")[
            "mime_type"
        ]
        == "audio/mp4"
    )
    assert (
        build_resource_fields(file_path="p", ext="mp3", size_bytes=1, title="t")[
            "mime_type"
        ]
        == "audio/mpeg"
    )


def test_already_downloaded_true(tmp_path):
    from app.workflows.soda_download import already_downloaded

    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    assert already_downloaded({"music_download_path": "a.flac"}, str(tmp_path)) is True


def test_already_downloaded_false_no_path():
    from app.workflows.soda_download import already_downloaded

    assert already_downloaded({"music_download_path": None}, "/tmp") is False
    assert already_downloaded({}, "/tmp") is False


def test_already_downloaded_false_missing_file(tmp_path):
    from app.workflows.soda_download import already_downloaded

    assert (
        already_downloaded({"music_download_path": "nope.flac"}, str(tmp_path)) is False
    )


def test_download_cover_returns_false_when_no_url_cover():
    # Regression: a skipped cover (no album.url_cover) must return False so the
    # caller marks cover_download_status='failed' (terminal). Leaving it at
    # 'pending' is what made the UI spin "Cover Downloading..." forever.
    import asyncio

    from app.workflows.soda_download import _download_cover

    result = asyncio.run(
        _download_cover(
            parsed={"metadata": {"album": {}}},  # no url_cover
            media_id="1",
            platform_id="1",
            user_id="u",
            base_dir="/tmp",
            res_repo=None,  # never touched on the skip path
            existing=None,
        )
    )
    assert result is False
