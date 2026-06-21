from app.repositories.generated_media_repository import _decode_cursor, _encode_cursor


def test_cursor_roundtrip():
    c = _encode_cursor("2026-06-21T00:00:00+00:00", 123)
    ts, gid = _decode_cursor(c)
    assert ts == "2026-06-21T00:00:00+00:00" and gid == 123


def test_decode_bad_cursor_returns_none():
    assert _decode_cursor("garbage") is None
    assert _decode_cursor(None) is None
