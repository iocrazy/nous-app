"""Process-local media-path cache: put/get/TTL/invalidate.

The cache backs /media/{id} resolution. Its invalidate() is what lets a
version-content write (overwrite / new version / set-current) drop the stale
entry so the detail page shows the edit immediately instead of after the TTL.
"""

from __future__ import annotations

import pytest

from app.services.media import media_path_cache as mpc

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean():
    mpc.clear()
    yield
    mpc.clear()


def test_put_then_get_roundtrip():
    mpc.put("10", "file", "sb://library/x.md", "u1", ("t1",))
    e = mpc.get("10", "file")
    assert e is not None
    assert e.file_path == "sb://library/x.md"
    assert e.creator_id == "u1"
    assert e.team_ids == ("t1",)


def test_get_missing_returns_none():
    assert mpc.get("nope", "file") is None


def test_get_expired_returns_none(monkeypatch):
    mpc.put("10", "file", "sb://library/x.md", "u1", ())
    # Jump past the TTL.
    real = mpc.time.time()
    monkeypatch.setattr(mpc.time, "time", lambda: real + mpc.TTL_SECONDS + 1)
    assert mpc.get("10", "file") is None
    # Expired entry is also evicted.
    assert ("10", "file") not in mpc._cache


def test_invalidate_clears_every_file_type_for_id():
    mpc.put("10", "file", "sb://a", "u1", ())
    mpc.put("10", "cover", "sb://b", "u1", ())
    mpc.put("11", "file", "sb://c", "u1", ())
    removed = mpc.invalidate("10")
    assert removed == 2
    assert mpc.get("10", "file") is None
    assert mpc.get("10", "cover") is None
    # A different id is untouched.
    assert mpc.get("11", "file") is not None


def test_invalidate_coerces_int_id():
    mpc.put("10", "file", "sb://a", "u1", ())
    assert mpc.invalidate(10) == 1  # int id matches the str key
    assert mpc.get("10", "file") is None
