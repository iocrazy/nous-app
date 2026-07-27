"""Task 1 of the generated-media object-store-only refactor.

register_generated_media routes ALL flag-on generations through the object
store — no filesystem fallback. Oversized images (> _OBJECT_STORE_IMAGE_MAX_BYTES)
stream via put_file (the same disk-backed path videos already use) instead of
degrading to the filesystem, and any object-store write failure propagates
instead of being swallowed. Flag-off keeps the pure filesystem path (dev
environments without object storage configured) as a regression anchor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.services.library.generated_media_service as gm_svc


def _origin() -> gm_svc.GenerationOrigin:
    return gm_svc.GenerationOrigin(kind="canvas_run")


@pytest.mark.asyncio
async def test_small_local_image_routes_to_object_store(monkeypatch, tmp_path):
    """flag-on + small image local source → put_bytes, sb:// path, no DOWNLOAD_PATH write."""
    captured = {}

    async def fake_returning_one(sql, params):
        captured.update(params)
        return {"id": 1, **params}

    src = tmp_path / "small.png"
    src.write_bytes(b"PNGDATA")
    download_path = tmp_path / "unused_download_path"

    store = AsyncMock()
    store.exists.return_value = False
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(download_path))
    monkeypatch.setattr(gm_svc.db_engine, "execute_returning_one", fake_returning_one)

    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_generated_media(
            user_id="u",
            scope_id=9,
            source_path=str(src),
            mime="image/png",
            origin=_origin(),
        )

    store.put_bytes.assert_awaited_once()
    store.put_file.assert_not_awaited()
    assert captured["file_path"].startswith("sb://chat-media/t9/")
    assert not download_path.exists()


@pytest.mark.asyncio
async def test_large_local_image_streams_via_put_file(monkeypatch, tmp_path):
    """flag-on + LARGE image local source → put_file (streaming path), still sb://, no fallback."""
    captured = {}

    async def fake_returning_one(sql, params):
        captured.update(params)
        return {"id": 1, **params}

    src = tmp_path / "big.png"
    src.write_bytes(b"X" * (gm_svc._OBJECT_STORE_IMAGE_MAX_BYTES + 1))
    download_path = tmp_path / "unused_download_path"

    store = AsyncMock()
    store.exists.return_value = False
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(download_path))
    monkeypatch.setattr(gm_svc.db_engine, "execute_returning_one", fake_returning_one)

    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_generated_media(
            user_id="u",
            scope_id=9,
            source_path=str(src),
            mime="image/png",
            origin=_origin(),
        )

    store.put_file.assert_awaited_once()
    store.put_bytes.assert_not_awaited()
    assert captured["file_path"].startswith("sb://chat-media/t9/")
    assert captured["file_size_bytes"] == gm_svc._OBJECT_STORE_IMAGE_MAX_BYTES + 1
    assert not download_path.exists()


@pytest.mark.asyncio
async def test_object_store_write_failure_raises_and_skips_db_insert(
    monkeypatch, tmp_path
):
    """flag-on + store.put_bytes raise → register_generated_media raises, DB insert not called."""
    src = tmp_path / "small.png"
    src.write_bytes(b"PNGDATA")

    store = AsyncMock()
    store.exists.return_value = False
    store.put_bytes.side_effect = RuntimeError("storage-api unreachable")
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    insert_mock = AsyncMock()
    monkeypatch.setattr(gm_svc.db_engine, "execute_returning_one", insert_mock)

    with patch.object(gm_svc, "chat_media_store", return_value=store):
        with pytest.raises(RuntimeError, match="storage-api unreachable"):
            await gm_svc.register_generated_media(
                user_id="u",
                scope_id=9,
                source_path=str(src),
                mime="image/png",
                origin=_origin(),
            )

    insert_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_off_stays_on_filesystem(monkeypatch, tmp_path):
    """flag-off → filesystem path, file_path is a relative path (current-behavior anchor)."""
    captured = {}

    async def fake_returning_one(sql, params):
        captured.update(params)
        return {"id": 1, **params}

    src = tmp_path / "small.png"
    src.write_bytes(b"PNGDATA")
    download_path = tmp_path / "downloads"

    store = AsyncMock()
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(download_path))
    monkeypatch.setattr(gm_svc.db_engine, "execute_returning_one", fake_returning_one)

    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_generated_media(
            user_id="u",
            scope_id=9,
            source_path=str(src),
            mime="image/png",
            origin=_origin(),
        )

    store.put_bytes.assert_not_awaited()
    store.put_file.assert_not_awaited()
    assert "sb://" not in captured["file_path"]
    assert captured["file_path"].startswith("teams/9/generations/")
    assert (download_path / captured["file_path"]).read_bytes() == b"PNGDATA"
