"""P0 regression tests — timestamp writes must bind datetime objects.

Incident (2026-07-05 → 2026-07-13): after #985 collapsed the media domain to
ORM-only, ``mark_media_as_downloaded`` kept passing
``datetime.now().isoformat()`` STRINGS for ``download_time``. asyncpg refuses
str for a timestamptz bind (``DataError: invalid input for query argument``),
so the whole UPDATE — including ``download_path`` — rolled back. Downloads
"succeeded" on disk but never registered in the DB; the UI showed every new
video as not-downloaded and Retry looped forever.

Three layers pinned here:
  1. The ``mark_*_as_downloaded`` call sites bind real datetimes.
  2. The shared coercion helper turns ISO strings into datetimes for any
     DateTime-typed model column (defense for future call sites).
  3. The downloader marks the video FAILED when the DB write fails, instead
     of reporting COMPLETED with nothing persisted.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict

import pytest


class _CaptureUpdate:
    """Stand-in for MediaRepository.update — records the data dict."""

    def __init__(self) -> None:
        self.captured: Dict[str, Any] | None = None

    async def __call__(self, platform_id: str, data: Dict[str, Any]):
        self.captured = data
        return dict(data)


async def test_mark_media_as_downloaded_binds_datetime(monkeypatch):
    from app.repositories.media_repository import MediaRepository

    repo = MediaRepository()
    capture = _CaptureUpdate()
    monkeypatch.setattr(repo, "update", capture)

    await repo.mark_media_as_downloaded(
        platform_id="pid-1",
        download_path="global/resources/web/douyin/1/video.mp4",
        duration=10,
        storage_size=123,
    )

    assert capture.captured is not None
    value = capture.captured["download_time"]
    assert isinstance(
        value, dt.datetime
    ), f"download_time must bind a datetime, got {type(value).__name__}: {value!r}"
    assert value.tzinfo is not None, "timestamptz column wants a tz-aware datetime"


async def test_mark_images_as_downloaded_binds_datetime(monkeypatch):
    from app.repositories.media_repository import MediaRepository

    repo = MediaRepository()
    capture = _CaptureUpdate()
    monkeypatch.setattr(repo, "update", capture)

    await repo.mark_images_as_downloaded(
        platform_id="pid-2",
        download_path="global/resources/web/douyin/2/slides",
        duration=5,
    )

    assert capture.captured is not None
    value = capture.captured["download_time"]
    assert isinstance(value, dt.datetime)
    assert value.tzinfo is not None


def test_coerce_datetime_strings_parses_iso_for_datetime_columns():
    """Defense layer: ISO strings destined for DateTime columns become
    datetimes; other values pass through untouched; input is not mutated."""
    from app.db.pg_coerce import coerce_datetime_strings
    from app.models.media import ParsedMedia

    original = {
        "download_time": "2026-07-13T13:15:32.864404",
        "download_path": "a/b/video.mp4",
        "storage_size": 42,
    }
    out = coerce_datetime_strings(ParsedMedia, original)

    assert isinstance(out["download_time"], dt.datetime)
    assert out["download_path"] == "a/b/video.mp4"
    assert out["storage_size"] == 42
    # immutability: caller's dict untouched
    assert isinstance(original["download_time"], str)


def test_coerce_datetime_strings_rejects_garbage():
    """A non-ISO string for a DateTime column must fail loudly, not reach
    asyncpg as a confusing bind error."""
    from app.db.pg_coerce import coerce_datetime_strings
    from app.models.media import ParsedMedia

    with pytest.raises(ValueError):
        coerce_datetime_strings(ParsedMedia, {"download_time": "not-a-date"})


def test_coerce_datetime_strings_handles_tz_aware_and_passthrough():
    """Offset/'Z'-suffixed ISO strings parse tz-aware; datetime objects
    pass through unchanged."""
    from app.db.pg_coerce import coerce_datetime_strings
    from app.models.media import ParsedMedia

    now = dt.datetime.now(dt.timezone.utc)
    out = coerce_datetime_strings(
        ParsedMedia,
        {
            "download_time": "2026-07-13T13:15:32+00:00",
            "created_at": now,
        },
    )
    assert out["download_time"].tzinfo is not None
    assert out["created_at"] is now


def test_media_normalize_for_pg_coerces_iso_datetime():
    """The media repo's write normalizer applies the coercion so any future
    string-passing call site degrades to a correct write, not a rollback."""
    from app.repositories.media_repository import _normalize_for_pg

    out = _normalize_for_pg({"download_time": "2026-07-13T13:15:32.864404"})
    assert isinstance(out["download_time"], dt.datetime)


async def test_downloader_marks_failed_when_db_write_fails(monkeypatch):
    """If the post-download DB write fails, the result must say FAILED —
    reporting COMPLETED while nothing persisted is how this incident hid
    for 8 days."""
    from app.services.media.downloader import downloader as dl_mod

    class _ExplodingRepo:
        failed_args: tuple | None = None

        async def mark_media_as_downloaded(self, **kwargs):
            raise RuntimeError("DataError: invalid input for query argument $4")

        async def mark_download_failed(self, platform_id, error_message, is_video):
            self.failed_args = (platform_id, error_message, is_video)
            return {}

    repo = _ExplodingRepo()
    ok = await dl_mod.persist_video_download(
        repo,
        platform_id="pid-3",
        download_path="global/resources/web/douyin/3/video.mp4",
        duration=10,
        storage_size=99,
    )
    assert ok is False
    assert repo.failed_args is not None
    pid, msg, is_video = repo.failed_args
    assert pid == "pid-3"
    assert "DataError" in msg
    assert is_video is True


async def test_persist_video_download_success_forwards_kwargs():
    from app.services.media.downloader import downloader as dl_mod

    class _RecordingRepo:
        captured: dict | None = None

        async def mark_media_as_downloaded(self, **kwargs):
            self.captured = kwargs
            return {}

        async def mark_download_failed(self, *a, **k):
            raise AssertionError("must not be called on success")

    repo = _RecordingRepo()
    ok = await dl_mod.persist_video_download(
        repo,
        platform_id="pid-4",
        download_path="global/resources/web/douyin/4/video.mp4",
        duration=12,
        storage_size=345,
    )
    assert ok is True
    assert repo.captured == {
        "platform_id": "pid-4",
        "download_path": "global/resources/web/douyin/4/video.mp4",
        "duration": 12,
        "storage_size": 345,
    }


async def test_persist_video_download_survives_double_failure():
    """Both the persist AND the failure-mark can hit the same broken DB —
    the helper must still return False without raising."""
    from app.services.media.downloader import downloader as dl_mod

    class _DoubleExplodingRepo:
        async def mark_media_as_downloaded(self, **kwargs):
            raise RuntimeError("primary write failed")

        async def mark_download_failed(self, *a, **k):
            raise RuntimeError("failure-mark also failed")

    ok = await dl_mod.persist_video_download(
        _DoubleExplodingRepo(),
        platform_id="pid-5",
        download_path="x/video.mp4",
        duration=1,
        storage_size=1,
    )
    assert ok is False
