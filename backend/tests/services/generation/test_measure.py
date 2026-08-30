"""Measuring what actually came back.

The contract's whole point is that "the provider returned a file" never
again gets read as "the provider did what was asked". These functions are
the only place that reads real pixels, so they must be honest about the
cases where they cannot: a missing file, a corrupt file and a degenerate
size are all `None`, never a guess and never an exception that would fail
an already-paid-for generation.
"""

import os
import shutil
import struct
import subprocess
import zlib

import pytest

from app.services.generation.measure import (
    Measured,
    compare_aspect,
    measure_image,
    measure_video,
)


def _png(path, width: int, height: int) -> str:
    """A real, minimal PNG of the requested size (Pillow reads it)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    body += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.write_bytes(body)
    return str(path)


def test_measure_image_reads_real_pixels(tmp_path):
    got = measure_image(_png(tmp_path / "a.png", 1536, 1024))
    assert got == Measured(width=1536, height=1024, duration_s=None)


def test_measure_image_returns_none_for_a_missing_file(tmp_path):
    assert measure_image(str(tmp_path / "nope.png")) is None


def test_measure_image_returns_none_for_a_corrupt_file_rather_than_raising(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png at all")
    assert measure_image(str(bad)) is None


def test_compare_aspect_accepts_the_requested_shape():
    assert compare_aspect("16:9", 1536, 864) is True


def test_compare_aspect_rejects_a_different_shape():
    # 1199x1312 = 0.914 — the real codex-local output for a 16:9 request.
    assert compare_aspect("16:9", 1199, 1312) is False


def test_compare_aspect_honours_the_shared_six_percent_tolerance():
    # 1536x1024 is 1.5, which is 16 % off 1.778 — outside tolerance.
    assert compare_aspect("16:9", 1536, 1024) is False
    # 1520x864 is 1.759, ~1 % off — inside.
    assert compare_aspect("16:9", 1520, 864) is True


def test_compare_aspect_is_none_when_there_was_no_request():
    # "let the model choose" is a real request; inventing a verdict for it
    # would put a false `honored` in the record.
    assert compare_aspect(None, 100, 100) is None
    assert compare_aspect("", 100, 100) is None


def test_compare_aspect_is_none_for_an_unknown_ratio_or_degenerate_size():
    assert compare_aspect("7:5", 100, 100) is None
    assert compare_aspect("16:9", 0, 100) is None


# ── measure_video ────────────────────────────────────────────────────────
# The brief specifies the function but ships no test for it. An async
# helper that shells out is exactly where "it degrades to None" quietly
# stops being true, so the real-ffprobe cases are covered here rather than
# with a mocked subprocess: a stub would pass even if the args, the
# safe_popen_kwargs splat or the JSON shape were wrong.

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def test_measure_video_returns_none_for_a_missing_file(tmp_path):
    assert await measure_video(str(tmp_path / "nope.mp4")) is None


@pytest.mark.skipif(not _HAS_FFMPEG, reason="needs a real ffmpeg/ffprobe")
async def test_measure_video_reads_a_real_file(tmp_path):
    dst = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(dst),
        ],
        check=True,
    )
    got = await measure_video(str(dst))
    assert got is not None
    assert (got.width, got.height) == (320, 240)
    assert got.duration_s is not None and 1.5 < got.duration_s < 2.5


@pytest.mark.skipif(not _HAS_FFMPEG, reason="needs a real ffprobe")
async def test_measure_video_returns_none_for_a_non_video_rather_than_raising(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    assert await measure_video(str(bad)) is None


# ── the duration is orthogonal to the shape ──────────────────────────────
# ffprobe's literal answer for "I could not determine a duration" is the
# string "N/A". Whether it reaches us depends on the JSON writer's
# optional-field mode, which is a build default and not something our args
# pin: ffprobe 8.0.1 (this host) and 7.1.5 (the backend image) both print
# `"duration":"N/A"` for a duration-less stream when optional fields are
# shown, and omit the key when they are not. So a build that shows them
# hands us a string float() rejects — and a video whose width and height
# read perfectly must not be recorded as unmeasurable because of it. That
# would put `honored=None` ("we never asked") on a product that could have
# been judged in violation.

# Byte-for-byte what ffprobe 7.1.5 in the backend image prints for a raw
# h264 stream with `-show_optional_fields always`.
_FFPROBE_NA_DURATION = (
    '{"programs":[],"stream_groups":[],'
    '"streams":[{"width":64,"height":48}],'
    '"format":{"duration":"N/A"}}'
)


def _ffprobe_answering(tmp_path, monkeypatch, body: str) -> None:
    """Put a stand-in `ffprobe` first on PATH, answering with `body`.

    Everything else stays real — create_subprocess_exec, the
    safe_popen_kwargs splat, the decode and the JSON parse. Only the
    binary's answer is chosen, because no file we can build locally makes
    either ffprobe build print an unparseable duration under the args this
    module actually passes.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    probe = bindir / "ffprobe"
    probe.write_text("#!/bin/sh\ncat <<'JSON'\n" + body + "\nJSON\n")
    probe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


async def test_measure_video_keeps_dimensions_when_the_duration_is_unreadable(
    tmp_path, monkeypatch
):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"stand-in for a real clip; the probe is faked, not the file")
    _ffprobe_answering(tmp_path, monkeypatch, _FFPROBE_NA_DURATION)

    got = await measure_video(str(clip))

    assert got is not None, "a readable width/height must survive a bad duration"
    assert (got.width, got.height) == (64, 48)
    assert got.duration_s is None


async def test_measure_video_gives_up_on_a_hung_probe_and_leaves_no_child(
    tmp_path, monkeypatch
):
    """The timeout branch had no coverage, and it is the one that reaps.

    A probe that never answers must end as `None` — and the child must be
    gone when we return, not left holding the file. The real D-state case
    (a probe that outlives SIGKILL on a stuck mount) cannot be staged from
    userspace; what is pinned here is that we kill and then actually wait
    for the exit, rather than firing a kill and walking away.
    """
    from app.services.generation import measure as measure_mod

    pidfile = tmp_path / "probe.pid"
    _ffprobe_answering(tmp_path, monkeypatch, "unused")
    (tmp_path / "bin" / "ffprobe").write_text(
        f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 30\n"
    )
    (tmp_path / "bin" / "ffprobe").chmod(0o755)
    monkeypatch.setattr(measure_mod, "_FFPROBE_TIMEOUT_S", 0.5)

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"stand-in for a real clip; the probe is faked, not the file")

    assert await measure_video(str(clip)) is None

    pid = int(pidfile.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
