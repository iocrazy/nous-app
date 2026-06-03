"""lyrics_payload_from_track: shared lyric extraction for the parse-time
formatter and the on-demand lyrics top-up endpoint (must produce the identical
``metadata.lyrics`` shape so a topped-up track looks like a freshly-parsed one)."""

from __future__ import annotations

from app.services.media.parsers.soda_music.lyrics import (
    extract_lyric_text,
    lyrics_payload_from_track,
)

_TIMED = "[0,2000]<0,500,0>Hello<500,500,0> world"


def test_extract_from_dict_content():
    assert extract_lyric_text({"lyric": {"content": _TIMED}}) == _TIMED


def test_extract_from_bare_string():
    assert extract_lyric_text({"lyric": _TIMED}) == _TIMED


def test_extract_missing_or_null():
    assert extract_lyric_text({}) == ""
    assert extract_lyric_text({"lyric": None}) == ""
    assert extract_lyric_text({"lyric": {"content": None}}) == ""


def test_payload_parses_lines_and_lrc():
    payload = lyrics_payload_from_track({"lyric": {"content": _TIMED}})
    assert payload["lines"], "expected parsed lines"
    assert payload["lines"][0]["text"] == "Hello world"
    assert payload["lrc"].startswith("[00:00")


def test_payload_empty_when_instrumental():
    # No lyric → empty payload (same shape the formatter writes for instrumentals).
    assert lyrics_payload_from_track({}) == {"lrc": "", "lines": []}
    assert lyrics_payload_from_track({"lyric": None}) == {"lrc": "", "lines": []}
