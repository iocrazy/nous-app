"""Phase 1b: object-store write path + reader ports.

These pin the flag-on behavior (sb:// writes, signed-URL vision, stream-proxy
serving, promote-from-object-store) with the storage SDK mocked — no live
storage-api. Flag-off / legacy-filesystem behavior is covered by the existing
suites (unchanged).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.services.library.generated_media_service as gm_svc
from app.services.library.media_storage import CHAT_MEDIA_BUCKET

# ── write path: register_uploaded_media → object store ───────────────────────


def _origin():
    return gm_svc.GenerationOrigin(kind="chat_upload", conversation_id=5)


@pytest.mark.asyncio
async def test_upload_routes_to_object_store_when_flag_on(monkeypatch):
    captured = {}

    async def fake_insert(**kw):
        captured.update(kw)
        return {"id": 1, **kw}

    store = AsyncMock()
    store.exists.return_value = False
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc, "_insert_uploaded_row", fake_insert)
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_uploaded_media(
            user_id="u",
            scope_id=42,
            file_bytes=b"PNGDATA",
            filename="shot.png",
            mime="image/png",
            origin=_origin(),
        )
    # Uploaded once (not present), then row written with sb:// path + sha.
    store.put_bytes.assert_awaited_once()
    assert captured["file_path"].startswith("sb://chat-media/t42/")
    assert captured["content_sha256"] and len(captured["content_sha256"]) == 64


@pytest.mark.asyncio
async def test_upload_dedup_skips_put_when_exists(monkeypatch):
    store = AsyncMock()
    store.exists.return_value = True  # identical bytes already uploaded
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(
        gm_svc, "_insert_uploaded_row", AsyncMock(return_value={"id": 1})
    )
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_uploaded_media(
            user_id="u",
            scope_id=42,
            file_bytes=b"X",
            filename="a.png",
            mime="image/png",
            origin=_origin(),
        )
    store.put_bytes.assert_not_awaited()  # dedup: row still inserts, no PUT


@pytest.mark.asyncio
async def test_upload_falls_back_to_filesystem_on_storage_error(tmp_path, monkeypatch):
    """storage-api down → upload must NOT hard-fail; write to filesystem."""
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    store = AsyncMock()
    store.exists.side_effect = RuntimeError("storage-api unreachable")
    inserted = {}

    async def fake_insert(**kw):
        inserted.update(kw)
        return {"id": 1, **kw}

    monkeypatch.setattr(gm_svc, "_insert_uploaded_row", fake_insert)
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_uploaded_media(
            user_id="u",
            scope_id=7,
            file_bytes=b"PNG",
            filename="s.png",
            mime="image/png",
            origin=_origin(),
        )
    # Fell through to filesystem: local rel path, no sb://, bytes on disk.
    assert inserted["file_path"].startswith("teams/7/chat/")
    assert "sb://" not in inserted["file_path"]
    assert (tmp_path / inserted["file_path"]).read_bytes() == b"PNG"


@pytest.mark.asyncio
async def test_video_never_routes_to_object_store(tmp_path, monkeypatch):
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    inserted = {}

    async def fake_insert(**kw):
        inserted.update(kw)
        return {"id": 1}

    monkeypatch.setattr(gm_svc, "_insert_uploaded_row", fake_insert)
    store = AsyncMock()
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_uploaded_media(
            user_id="u",
            scope_id=7,
            file_bytes=b"MP4",
            filename="v.mp4",
            mime="video/mp4",
            origin=_origin(),
        )
    store.put_bytes.assert_not_awaited()
    assert "sb://" not in inserted["file_path"]


# ── reader: agent vision → signed URL for object-store images ────────────────


@pytest.mark.asyncio
async def test_vision_uses_signed_url_for_object_store():
    import app.services.chat.conversation_agent_turn as turn

    recent = [
        {
            "sender_type": "user",
            "type": "image",
            "body": {"generated_media_id": 7, "alt": "photo.png"},
        }
    ]
    history = turn._build_history(recent)
    row = {
        "file_path": "sb://chat-media/t9/ab/cd/hash.png",
        "mime": "image/png",
        "file_size_bytes": 100,
        "conversation_id": 9,
    }
    store = AsyncMock()
    store.signed_url.return_value = "https://storage/signed?token=xyz"
    with (
        patch.object(turn, "model_supports_vision", new=AsyncMock(return_value=True)),
        patch.object(turn.db_engine, "fetch_one", new=AsyncMock(return_value=row)),
        patch.object(turn, "ObjectStore", return_value=store),
    ):
        n = await turn._inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=9,
        )
    assert n == 1
    block = history[0]["content"][1]
    assert block["image_url"]["url"] == "https://storage/signed?token=xyz"
    # signed URL, NOT base64 (the token-tax win)
    assert not block["image_url"]["url"].startswith("data:")
    store.signed_url.assert_awaited_once()
