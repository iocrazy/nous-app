"""Unit tests for shared upload utilities (app/core/file_utils.py).

Covers the 2026-05-14 upload-hardening work:
- sanitize_filename: path-component stripping, control-char scrubbing,
  leading/trailing dot+space removal, empty-input fallback, length cap.
- stream_upload_to_disk: chunked write, real-size accounting, server-side
  size enforcement with partial-file cleanup.
- sniff_mime: content-based type detection vs the (untrusted) declared type.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException

from app.core.file_utils import (
    sanitize_filename,
    sniff_mime,
    stream_upload_to_disk,
)

pytestmark = pytest.mark.unit


# ─── sanitize_filename ────────────────────────────────────────────────


def test_sanitize_plain_name_unchanged():
    assert sanitize_filename("report.pdf") == "report.pdf"


def test_sanitize_strips_posix_path_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("/abs/path/x.mp4") == "x.mp4"


def test_sanitize_neutralizes_windows_path_separators():
    # On POSIX, Path(...).name does not split on backslash, so the
    # backslashes and colon are scrubbed to underscores instead. The
    # security property that matters: the result carries no path
    # separator and cannot escape the upload directory.
    out = sanitize_filename("C:\\Windows\\evil.exe")
    assert "/" not in out and "\\" not in out
    assert out == "C__Windows_evil.exe"


def test_sanitize_strips_leading_dot():
    assert sanitize_filename(".htaccess") == "htaccess"


def test_sanitize_dotdot_becomes_untitled():
    assert sanitize_filename("..") == "untitled"
    assert sanitize_filename(".") == "untitled"


def test_sanitize_empty_becomes_untitled():
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename("   .  ") == "untitled"


def test_sanitize_scrubs_control_chars():
    assert sanitize_filename("a\x00b\x1fc.txt") == "a_b_c.txt"


def test_sanitize_scrubs_windows_reserved_chars():
    assert sanitize_filename('a<>:"|?*b.txt') == "a_______b.txt"


def test_sanitize_caps_length_at_255():
    out = sanitize_filename("a" * 300 + ".txt")
    assert len(out) == 255


# ─── stream_upload_to_disk ────────────────────────────────────────────


class _FakeUpload:
    """Minimal UploadFile stand-in — only `.read(size)` is exercised."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._data[self._pos :]
        else:
            chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


async def test_stream_writes_file_and_returns_size_and_hash(tmp_path):
    data = b"hello world " * 1000
    target = tmp_path / "out.bin"

    size, sha = await stream_upload_to_disk(
        _FakeUpload(data), target, max_size=10 * 1024 * 1024
    )

    assert size == len(data)
    assert sha == hashlib.sha256(data).hexdigest()
    assert target.read_bytes() == data


async def test_stream_rejects_oversize_and_removes_partial(tmp_path):
    data = b"x" * 5000
    target = tmp_path / "big.bin"

    with pytest.raises(HTTPException) as ei:
        await stream_upload_to_disk(_FakeUpload(data), target, max_size=1024)

    assert ei.value.status_code == 413
    # Partial file must not be left behind.
    assert not target.exists()


async def test_stream_size_counted_from_bytes_not_declared(tmp_path):
    # The helper must never trust a caller-supplied size — it counts the
    # bytes it actually writes. An empty body produces (0, sha256 of "").
    target = tmp_path / "empty.bin"
    size, sha = await stream_upload_to_disk(_FakeUpload(b""), target, max_size=1024)
    assert size == 0
    assert sha == hashlib.sha256(b"").hexdigest()
    assert target.read_bytes() == b""


# ─── sniff_mime ───────────────────────────────────────────────────────


def test_sniff_detects_png_from_content(tmp_path):
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert sniff_mime(png) == "image/png"


def test_sniff_returns_none_for_plain_text(tmp_path):
    txt = tmp_path / "x.txt"
    txt.write_bytes(b"just some plain text content, no magic bytes")
    assert sniff_mime(txt) is None


def test_sniff_returns_none_for_missing_file(tmp_path):
    assert sniff_mime(tmp_path / "does-not-exist.bin") is None
