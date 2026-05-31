from app.api.media_download_router import audio_content_type


def test_flac_content_type():
    assert audio_content_type(".flac") == "audio/flac"


def test_existing_types_preserved():
    assert audio_content_type(".mp3") == "audio/mpeg"
    assert audio_content_type(".m4a") == "audio/mp4"


def test_unknown_defaults_to_mpeg():
    assert audio_content_type(".xyz") == "audio/mpeg"
