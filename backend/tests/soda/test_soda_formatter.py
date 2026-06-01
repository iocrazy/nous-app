# backend/tests/soda/test_soda_formatter.py
from app.services.media.parsers.soda_music.formatter import format_track

TRACK = {
    "id": "7123",
    "name": "Test Song",
    "duration": 200000,  # ms
    "artists": [
        {"name": "Artist A", "url_avatar": {"urls": ["https://p/"], "uri": "av"}}
    ],
    "album": {
        "name": "Album X",
        "release_date": "2024-01-01",
        "url_cover": {"urls": ["https://p/"], "uri": "cov"},
    },
    "stats": {"count_collected": 10, "count_comment": 2, "count_shared": 3},
    "colors": {"cover_gradient_effect_color": "#fff"},
    "tags": ["pop"],
}
CHOSEN = {
    "Quality": "lossless",
    "Format": "flac",
    "Bitrate": 729,
    "MainPlayUrl": "https://cdn/flac",
    "PlayAuth": "auth",
    "Duration": 200,
}


def test_format_track_core_fields():
    pd = format_track(TRACK, CHOSEN, original_url="https://qishui.douyin.com/s/x/")
    assert pd["platform_id"] == "7123"
    assert pd["source_platform"] == "qishui"
    assert pd["media_type"] == "audio"
    assert pd["title"] == "Test Song"
    assert pd["author"] == "Artist A"
    assert pd["original_url"] == "https://qishui.douyin.com/s/x/"


def test_format_track_cover_url_assembled():
    pd = format_track(TRACK, CHOSEN, original_url="u")
    assert pd["cover_urls"] == ["https://p/cov~c5_375x375.jpg"]


def test_format_track_packs_metadata():
    pd = format_track(TRACK, CHOSEN, original_url="u")
    meta = pd["metadata"]
    assert meta["album"]["name"] == "Album X"
    assert meta["stats"]["count_collected"] == 10
    assert meta["quality"]["Quality"] == "lossless"
    assert meta["colors"]["cover_gradient_effect_color"] == "#fff"
    assert pd["favorite_count"] == 10
    assert pd["comment_count"] == 2
    assert pd["share_count"] == 3


def test_format_track_ext_from_format():
    pd = format_track(TRACK, CHOSEN, original_url="u")
    assert pd["metadata"]["ext"] == "flac"


def test_format_track_sets_top_level_duration():
    from app.core.utils import Utils

    track = {**TRACK, "duration": 227808}
    pd = format_track(track, CHOSEN, original_url="u")
    expected = Utils.format_duration(227808)
    assert pd["duration"] == expected
    assert pd["duration"]  # non-empty
    assert pd["duration"] != "0s"
    # still keeps the raw ms in metadata
    assert pd["metadata"]["duration_ms"] == 227808


def test_format_track_captures_lyrics_from_content_dict():
    from app.services.media.parsers.soda_music.formatter import format_track

    track = {
        "id": "7",
        "name": "S",
        "artists": [],
        "album": {},
        "lyric": {"content": "[1000,1000]<0,1000,0>Hi"},
    }
    chosen = {"Quality": "lossless", "Format": "flac", "Bitrate": 729}
    pd = format_track(track, chosen, original_url="https://q/x")
    assert pd["metadata"]["lyrics"]["lrc"].startswith("[00:01.00]Hi")
    assert pd["metadata"]["lyrics"]["lines"][0]["text"] == "Hi"


def test_format_track_captures_lyrics_from_plain_string():
    from app.services.media.parsers.soda_music.formatter import format_track

    track = {
        "id": "7",
        "name": "S",
        "artists": [],
        "album": {},
        "lyric": "[2000,1000]<0,1000,0>Yo",
    }
    chosen = {"Quality": "lossless", "Format": "flac", "Bitrate": 729}
    pd = format_track(track, chosen, original_url="https://q/x")
    assert pd["metadata"]["lyrics"]["lrc"].startswith("[00:02.00]Yo")


def test_format_track_no_lyrics_is_empty():
    from app.services.media.parsers.soda_music.formatter import format_track

    track = {"id": "7", "name": "S", "artists": [], "album": {}}
    chosen = {"Quality": "lossless", "Format": "flac", "Bitrate": 729}
    pd = format_track(track, chosen, original_url="https://q/x")
    assert pd["metadata"]["lyrics"] == {"lrc": "", "lines": []}
