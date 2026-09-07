"""Phase 1b: object-store write path + reader ports.

These pin the flag-on behavior (sb:// writes, signed-URL vision, stream-proxy
serving, promote-from-object-store) with the storage SDK mocked — no live
storage-api. Flag-off / legacy-filesystem behavior is covered by the existing
suites (unchanged).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import app.services.library.generated_media_service as gm_svc
from app.services.library.media_storage import CHAT_MEDIA_BUCKET

# ── write path: register_uploaded_media → object store ───────────────────────


def _origin():
    return gm_svc.GenerationOrigin(kind="chat_upload", conversation_id=5)


def _bind_params(stmt) -> dict:
    """Compile an insert(...).returning(...) statement (postgresql dialect)
    and return its literal bind values keyed by column name — the ORM
    equivalent of the old ``fake_returning_one(sql, params)``'s ``params``."""
    return dict(stmt.compile(dialect=postgresql.dialect()).params)


class _FakeResult:
    def __init__(self, row: dict):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


def _fake_write_scope(row_id, captured: dict):
    @asynccontextmanager
    async def _scope():
        class _Session:
            async def execute(self, stmt):
                params = _bind_params(stmt)
                captured.update(params)
                return _FakeResult({"id": row_id, **params})

        yield _Session()

    return _scope


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
async def test_upload_storage_error_is_a_hard_failure(tmp_path, monkeypatch):
    """storage-api down → typed ObjectStoreWriteFailed: no filesystem row,
    nothing on disk (2026-09-07: the transit dir is not a durable store)."""
    from app.services.library.storage_errors import ObjectStoreWriteFailed

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
        with pytest.raises(ObjectStoreWriteFailed) as excinfo:
            await gm_svc.register_uploaded_media(
                user_id="u",
                scope_id=7,
                file_bytes=b"PNG",
                filename="s.png",
                mime="image/png",
                origin=_origin(),
            )
    assert excinfo.value.details["where"] == "register_uploaded_media"
    assert excinfo.value.details["scope_id"] == 7
    assert inserted == {}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


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


# ── reader: agent vision for object-store images ─────────────────────────────


def _vision_fixture():
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
    return turn, recent, history, row


def _fake_read_scope_returning(row: dict):
    """ORM equivalent of the old ``db_engine.fetch_one`` mock — patches
    ``turn.read_scope`` so ``_inject_image_blocks``'s column-level select
    resolves to ``row`` via ``.mappings().first()``."""

    class _FakeResult:
        def mappings(self):
            return self

        def first(self):
            return row

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult()

    @asynccontextmanager
    async def _scope():
        yield _FakeSession()

    return _scope


async def test_vision_object_store_defaults_to_base64(monkeypatch):
    """No public base configured → object-store images inline as base64.

    A signed URL here would be built on the LAN SUPABASE_URL, which a CLOUD
    provider cannot fetch — that silently broke vision post-go-live. Base64
    always works, so it is the default.
    """
    turn, recent, history, row = _vision_fixture()
    monkeypatch.setattr(turn.settings, "STORAGE_SIGNED_URL_PUBLIC_BASE", "")
    store = AsyncMock()
    store.get_bytes.return_value = b"PNGBYTES"
    with (
        patch.object(turn, "model_supports_vision", new=AsyncMock(return_value=True)),
        patch.object(turn, "read_scope", new=_fake_read_scope_returning(row)),
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
    url = history[0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    store.get_bytes.assert_awaited_once()
    store.signed_url.assert_not_awaited()


async def test_vision_object_store_signed_url_when_public_base_set(monkeypatch):
    """Public base configured → signed URL with the LAN host swapped out."""
    turn, recent, history, row = _vision_fixture()
    monkeypatch.setattr(
        turn.settings,
        "STORAGE_SIGNED_URL_PUBLIC_BASE",
        "https://sb-mediahub.example.com:88",
    )
    store = AsyncMock()
    store.signed_url.return_value = (
        "http://192.168.50.9:9082/storage/v1/object/sign/chat-media/k?token=xyz"
    )
    with (
        patch.object(turn, "model_supports_vision", new=AsyncMock(return_value=True)),
        patch.object(turn, "read_scope", new=_fake_read_scope_returning(row)),
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
    url = history[0]["content"][1]["image_url"]["url"]
    # LAN origin swapped for the public one; path + token preserved.
    assert url == (
        "https://sb-mediahub.example.com:88"
        "/storage/v1/object/sign/chat-media/k?token=xyz"
    )
    store.get_bytes.assert_not_awaited()


# ── AI-generation write path → object store (Phase 1c) ───────────────────────


@pytest.mark.asyncio
async def test_generated_image_routes_to_object_store(monkeypatch):
    captured = {}

    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc, "_download_to_bytes", AsyncMock(return_value=b"GENIMG"))
    monkeypatch.setattr(gm_svc, "write_scope", _fake_write_scope(5, captured))
    store = AsyncMock()
    store.exists.return_value = False
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_generated_media(
            user_id="u",
            scope_id=9,
            source_url="http://x/y.png",
            mime="image/png",
            origin=gm_svc.GenerationOrigin(kind="canvas_run"),
        )
    store.put_bytes.assert_awaited_once()
    assert captured["file_path"].startswith("sb://chat-media/t9/")
    assert captured["content_sha256"] and len(captured["content_sha256"]) == 64


@pytest.mark.asyncio
async def test_generated_video_streams_to_object_store(monkeypatch):
    """AI-generated SHORT video now goes to the bucket too — streamed via a
    temp file (put_file, not put_bytes) to avoid buffering it in memory."""
    captured = {}

    async def fake_download(dest_path, source_url, **k):
        from pathlib import Path

        Path(dest_path).write_bytes(b"MP4DATA")
        return 7

    store = AsyncMock()
    store.exists.return_value = False
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc, "_download_to", fake_download)
    monkeypatch.setattr(gm_svc, "write_scope", _fake_write_scope(5, captured))
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        await gm_svc.register_generated_media(
            user_id="u",
            scope_id=9,
            source_url="http://x/y.mp4",
            mime="video/mp4",
            origin=gm_svc.GenerationOrigin(kind="canvas_run"),
        )
    # streamed upload from a temp file, NOT an in-memory put_bytes
    store.put_file.assert_awaited_once()
    store.put_bytes.assert_not_awaited()
    assert captured["file_path"].startswith("sb://chat-media/t9/")
    assert captured["media_kind"] == "video"
    assert captured["content_sha256"] and len(captured["content_sha256"]) == 64


@pytest.mark.asyncio
async def test_generated_video_raises_on_storage_error(tmp_path, monkeypatch):
    """Storage failure on a generated video RAISES — object store is the only
    write path while the flag is on (Task 1: no filesystem fallback)."""
    session_execute = AsyncMock()

    @asynccontextmanager
    async def _scope():
        class _Session:
            execute = session_execute

        yield _Session()

    async def fake_download(dest_path, source_url, **k):
        from pathlib import Path

        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"MP4")
        return 3

    store = AsyncMock()
    store.exists.side_effect = RuntimeError("storage down")
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(gm_svc, "_download_to", fake_download)
    monkeypatch.setattr(gm_svc, "write_scope", _scope)
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        with pytest.raises(RuntimeError, match="storage down"):
            await gm_svc.register_generated_media(
                user_id="u",
                scope_id=9,
                source_url="http://x/y.mp4",
                mime="video/mp4",
                origin=gm_svc.GenerationOrigin(kind="canvas_run"),
            )
    session_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_image_raises_on_storage_error(tmp_path, monkeypatch):
    """Storage failure on a generated image RAISES — object store is the only
    write path while the flag is on (Task 1: no filesystem fallback)."""
    session_execute = AsyncMock()

    @asynccontextmanager
    async def _scope():
        class _Session:
            execute = session_execute

        yield _Session()

    store = AsyncMock()
    store.exists.side_effect = RuntimeError("storage down")
    monkeypatch.setattr(gm_svc.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(gm_svc, "_download_to_bytes", AsyncMock(return_value=b"IMG"))
    monkeypatch.setattr(gm_svc, "write_scope", _scope)
    with patch.object(gm_svc, "chat_media_store", return_value=store):
        with pytest.raises(RuntimeError, match="storage down"):
            await gm_svc.register_generated_media(
                user_id="u",
                scope_id=9,
                source_url="http://x/y.png",
                mime="image/png",
                origin=gm_svc.GenerationOrigin(kind="canvas_run"),
            )
    session_execute.assert_not_awaited()
