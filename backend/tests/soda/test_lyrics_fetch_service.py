"""fetch_and_persist_lyrics: platform dispatch + metadata merge for the
on-demand lyrics top-up endpoint."""

from __future__ import annotations

import pytest

import app.services.media.lyrics_fetch_service as svc
from app.services.media.lyrics_fetch_service import (
    LyricsFetchUnsupported,
    fetch_and_persist_lyrics,
)

_PAYLOAD = {"lrc": "[00:00.00]Hi", "lines": [{"text": "Hi"}]}


class _FakeRepo:
    """Captures the update call so the test can assert the merged metadata."""

    last_update: tuple | None = None

    def __init__(self, row):
        self._row = row

    async def get_by_id(self, media_id):
        return self._row

    async def update(self, platform_id, data):
        _FakeRepo.last_update = (platform_id, data)
        return {**(self._row or {}), **data}


def _patch_repo(monkeypatch, row):
    _FakeRepo.last_update = None
    monkeypatch.setattr(svc, "MediaRepository", lambda: _FakeRepo(row))


async def test_unsupported_platform_raises(monkeypatch):
    _patch_repo(monkeypatch, {"platform_id": "p1", "source_platform": "douyin"})
    with pytest.raises(LyricsFetchUnsupported):
        await fetch_and_persist_lyrics("m1", "u1")


async def test_missing_media_raises_lookup(monkeypatch):
    _patch_repo(monkeypatch, None)
    with pytest.raises(LookupError):
        await fetch_and_persist_lyrics("m1", "u1")


async def test_qishui_merges_and_persists(monkeypatch):
    row = {
        "platform_id": "p1",
        "source_platform": "qishui",
        "metadata": {"album": {"name": "X"}, "lyrics": {"lrc": "", "lines": []}},
    }
    _patch_repo(monkeypatch, row)

    async def fake_fetch(platform_id, user_id):
        assert platform_id == "p1" and user_id == "u1"
        return _PAYLOAD

    monkeypatch.setattr(svc, "_fetch_qishui_lyrics", fake_fetch)

    result = await fetch_and_persist_lyrics("m1", "u1")
    assert result == _PAYLOAD

    platform_id, data = _FakeRepo.last_update
    assert platform_id == "p1"
    # lyrics swapped, other metadata keys preserved.
    assert data["metadata"]["lyrics"] == _PAYLOAD
    assert data["metadata"]["album"] == {"name": "X"}


async def test_qishui_case_insensitive_platform(monkeypatch):
    row = {"platform_id": "p1", "source_platform": "QISHUI", "metadata": {}}
    _patch_repo(monkeypatch, row)
    monkeypatch.setattr(svc, "_fetch_qishui_lyrics", lambda p, u: _async(_PAYLOAD))
    result = await fetch_and_persist_lyrics("m1", "u1")
    assert result == _PAYLOAD


async def _async(value):
    return value
