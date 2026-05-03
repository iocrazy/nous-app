"""B — chat attachment upload endpoint smoke tests."""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

import app.api.ai_library_router  # noqa: F401
router_module = sys.modules["app.api.ai_library_router"]


def _fake_request(content_length=None):
    """Construct a fake Request that supports .form() with a single
    UploadFile-like entry."""
    req = MagicMock()
    req.headers = {"content-length": str(content_length)} if content_length else {}
    return req


class _FakeUpload:
    """Mimics enough of starlette.UploadFile for the endpoint."""

    def __init__(self, filename, content, content_type=None):
        self.filename = filename
        self.content_type = content_type
        self._buf = BytesIO(content)

    async def read(self, n=-1):
        return self._buf.read(n)

    async def close(self):
        self._buf.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_image_routes_to_image_kind(tmp_path, monkeypatch):
    """JPEG upload returns kind=image, server-local path, correct size."""
    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)

    # Real JPEG magic bytes (FF D8 FF) at offset 0
    payload = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"x" * 100
    upload = _FakeUpload("photo.jpg", payload, content_type="image/jpeg")
    req = _fake_request(content_length=len(payload))

    async def _form():
        return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)

    result = await router_module.upload_chat_attachment(auth, req)
    assert result["kind"] == "image"
    assert result["filename"] == "photo.jpg"
    assert result["size_bytes"] == len(payload)
    out_path = Path(result["url"])
    assert out_path.exists()
    assert out_path.read_bytes() == payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_pdf_routes_to_pdf_kind(tmp_path, monkeypatch):
    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)

    payload = b"%PDF-1.4\nfake pdf content"  # real PDF magic bytes
    upload = _FakeUpload("doc.pdf", payload, content_type="application/pdf")
    req = _fake_request(content_length=len(payload))
    async def _form(): return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)

    result = await router_module.upload_chat_attachment(auth, req)
    assert result["kind"] == "pdf"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_video_routes_to_video_kind(tmp_path, monkeypatch):
    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)

    # Real MP4 ftyp box: 4 size bytes then 'ftyp' at offset 4
    payload = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00mp42mp41"
    upload = _FakeUpload("clip.mp4", payload, content_type="video/mp4")
    req = _fake_request(content_length=len(payload))
    async def _form(): return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)

    result = await router_module.upload_chat_attachment(auth, req)
    assert result["kind"] == "video"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_extension_spoofing_via_magic_bytes(tmp_path, monkeypatch):
    """C2: .jpg with non-JPEG magic bytes is rejected even though
    extension is in allow-list."""
    from fastapi import HTTPException

    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)

    # Looks like an executable header, named .jpg
    payload = b"MZ\x90\x00\x03\x00\x00\x00fake exe payload"
    upload = _FakeUpload("malware.jpg", payload, content_type="image/jpeg")
    req = _fake_request(content_length=len(payload))
    async def _form(): return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(auth, req)
    assert exc_info.value.status_code == 415
    assert "does not match" in str(exc_info.value.detail).lower()


@pytest.mark.unit
def test_check_magic_bytes_unknown_extension_accepts():
    """Unknown extensions accept (caller already filtered via _ALLOWED_EXTS)."""
    assert router_module._check_magic_bytes(".unknown", b"any") is True


@pytest.mark.unit
def test_check_magic_bytes_too_short_rejects():
    """Buffer shorter than the signature length → reject."""
    assert router_module._check_magic_bytes(".png", b"\x89PN") is False


@pytest.mark.unit
def test_check_magic_bytes_webp_offset_8_signature():
    """WEBP signature is at offset 8 — verify offset matching works."""
    head = b"RIFF\x00\x00\x00\x00WEBPVP8 fake"
    assert router_module._check_magic_bytes(".webp", head) is True
    # Wrong offset payload
    assert router_module._check_magic_bytes(".webp", b"RIFF\x00\x00\x00\x00FAKE") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_disallowed_extension(tmp_path, monkeypatch):
    """Executable extensions etc. are rejected with 415."""
    from fastapi import HTTPException

    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)
    upload = _FakeUpload("hax.exe", b"MZ\x00\x00")
    req = _fake_request()
    async def _form(): return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(auth, req)
    assert exc_info.value.status_code == 415


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_oversize_via_content_length(tmp_path, monkeypatch):
    """Content-Length > 50MB * 1.1 → 413 before reading body."""
    from fastapi import HTTPException

    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)
    upload = _FakeUpload("huge.mp4", b"")  # body irrelevant; CL trips first
    req = _fake_request(content_length=100 * 1024 * 1024)  # 100 MB
    async def _form(): return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(auth, req)
    assert exc_info.value.status_code == 413


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_missing_file_field_returns_400(tmp_path, monkeypatch):
    from fastapi import HTTPException

    user_id = uuid4()
    auth = MagicMock()
    auth.user_id = str(user_id)
    req = _fake_request()
    async def _form(): return {}  # no file
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(auth, req)
    assert exc_info.value.status_code == 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_reaps_old_files(tmp_path, monkeypatch):
    """Files older than TTL get unlinked when a new upload triggers reap."""
    import os
    user_id = uuid4()
    user_dir = tmp_path / str(user_id)
    user_dir.mkdir()
    old_file = user_dir / "old.jpg"
    old_file.write_bytes(b"x")
    old_mtime = router_module._time.time() - (router_module._CHAT_ATTACHMENT_TTL_SECONDS + 100)
    os.utime(old_file, (old_mtime, old_mtime))

    fresh_file = user_dir / "fresh.jpg"
    fresh_file.write_bytes(b"y")

    auth = MagicMock()
    auth.user_id = str(user_id)
    new_payload = b"\xff\xd8\xff\xe0fresh"
    upload = _FakeUpload("new.jpg", new_payload, content_type="image/jpeg")
    req = _fake_request(content_length=len(new_payload))
    async def _form(): return {"file": upload}
    req.form = _form

    monkeypatch.setattr(router_module, "_CHAT_ATTACHMENTS_BASE", tmp_path)
    result = await router_module.upload_chat_attachment(auth, req)

    # Old file gone; fresh file kept; new file present
    assert not old_file.exists()
    assert fresh_file.exists()
    assert Path(result["url"]).exists()
