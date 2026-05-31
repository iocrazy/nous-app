"""Unit tests for the lyrics extraction helper used by GET /media/{id}/lyrics."""

from app.api.media_slides_router import extract_lyrics


def test_extract_lyrics_from_metadata():
    row = {"metadata": {"lyrics": {"lrc": "[00:01.00]Hi", "lines": [{"text": "Hi"}]}}}
    assert extract_lyrics(row) == {"lrc": "[00:01.00]Hi", "lines": [{"text": "Hi"}]}


def test_extract_lyrics_missing_metadata():
    assert extract_lyrics({"metadata": {}}) == {"lrc": "", "lines": []}
    assert extract_lyrics({}) == {"lrc": "", "lines": []}
    assert extract_lyrics({"metadata": None}) == {"lrc": "", "lines": []}
