"""_content_disposition — RFC 5987 safe Content-Disposition builder.

Regression: `download_video_file` / `download_music_file` (Task C2/C6) build
a bare ``attachment; filename="{safe_title}..."`` header string. Chinese
titles survive the ``safe_title`` alnum filter (``"视".isalnum()`` is
``True`` in Python), land in the header untouched, and blow up when
Starlette encodes the response headers as latin-1 — HTTP 500 on every
Chinese-titled video/music download since the storage migration shipped.

The fix builds a dual-parameter header: ``filename="<ascii fallback>";
filename*=UTF-8''<percent-encoded>`` (RFC 5987 / RFC 6266) so the whole
string is always latin-1 encodable regardless of what's in the title.
"""

from __future__ import annotations

from urllib.parse import unquote

import pytest

from app.api.media_download_router import _content_disposition

pytestmark = pytest.mark.unit


def test_chinese_filename_is_latin1_safe():
    """Core regression assertion: before the fix this raised
    UnicodeEncodeError, which is exactly what Starlette hit in production."""
    disposition = _content_disposition("我的视频.mp4", "7123456789.mp4")

    # Must not raise — this is what a bare header would have failed at.
    disposition.encode("latin-1")

    assert 'filename="' in disposition
    assert "filename*=UTF-8''" in disposition
    # The percent-encoded UTF-8 payload round-trips to the original name.
    encoded = disposition.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == "我的视频.mp4"


def test_ascii_filename_unaffected():
    """Pure-ASCII titles keep working as a normal attachment disposition."""
    disposition = _content_disposition("My Clip.mp4", "7123456789.mp4")

    disposition.encode("latin-1")
    assert 'filename="My Clip.mp4"' in disposition
    # ASCII-only names still get filename* for consistency, but the ascii
    # fallback must be the real name, not the platform_id fallback.
    assert "filename*=UTF-8''My%20Clip.mp4" in disposition


def test_symbol_only_filename_falls_back_to_platform_id():
    """A title that's pure symbols/emoji survives the caller's alnum
    filter as an empty string — the ascii fallback (filename=) must use
    the caller-supplied fallback (platform_id-based name), not go blank."""
    disposition = _content_disposition("🎬🎬🎬", "7123456789.mp4")

    disposition.encode("latin-1")
    assert 'filename="7123456789.mp4"' in disposition
    assert "filename*=UTF-8''" in disposition


def test_quote_in_filename_is_stripped_from_ascii_fallback():
    """Defensive: even if a caller forgets to strip quotes, the ascii
    fallback portion must not let a stray `"` break out of the quoted
    header value."""
    disposition = _content_disposition('evil".mp4', "7123456789.mp4")

    disposition.encode("latin-1")
    # exactly the two quotes wrapping the ascii filename= value survive —
    # any quote embedded in the source title must be stripped.
    filename_part = disposition.split(";")[1]
    assert filename_part.count('"') == 2
    assert "evil.mp4" in filename_part
