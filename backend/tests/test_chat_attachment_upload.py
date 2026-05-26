"""B — chat attachment upload endpoint tests.

After the media-asset-foundation change, the endpoint no longer writes to
gateway-local ``/tmp``; it validates the upload (extension allow-list,
content-length pre-check, magic-byte sniff, 50MB cap) and then persists the
bytes as a temp resource on the shared library via ``save_chat_temp_upload``.
These tests mock ``save_chat_temp_upload`` so no disk/db is touched and pin:
  - the validation gates still fire (415 / 413 / 400) before persistence,
  - the validated bytes + scope are forwarded to ``save_chat_temp_upload``,
  - the response exposes ``resource_id`` / ``file_path`` (+ ``url`` alias).
"""

from __future__ import annotations

import sys
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import app.api.ai_library_router  # noqa: F401

router_module = sys.modules["app.api.ai_library_router"]

_SAVE_TARGET = "app.services.library.chat_upload.save_chat_temp_upload"


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


def _make_request(upload=None, content_length=None, session_id=None, query=None):
    """Build a fake Request whose .form() returns a single file (and an
    optional session_id form field)."""
    req = MagicMock()
    req.headers = {"content-length": str(content_length)} if content_length else {}
    req.query_params = query or {}

    form_data: dict = {}
    if upload is not None:
        form_data["file"] = upload
    if session_id is not None:
        form_data["session_id"] = session_id

    async def _form():
        return form_data

    req.form = _form
    return req


def _auth(user_id=None):
    auth = MagicMock()
    auth.user_id = str(user_id or uuid4())
    return auth


def _patch_save(monkeypatch, *, return_value=None, side_effect=None):
    fake = AsyncMock(return_value=return_value, side_effect=side_effect)
    monkeypatch.setattr(_SAVE_TARGET, fake)
    return fake


# ------------------------------------------------------------------ #
# Success paths — persist as a temp resource
# ------------------------------------------------------------------ #


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_image_persists_temp_resource(monkeypatch):
    """A valid JPEG is forwarded to save_chat_temp_upload and the response
    exposes resource_id / file_path (+ url alias)."""
    user_id = uuid4()
    # Real JPEG magic bytes (FF D8 FF) at offset 0
    payload = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"x" * 100
    upload = _FakeUpload("photo.jpg", payload, content_type="image/jpeg")
    req = _make_request(upload=upload, content_length=len(payload))

    fake_save = _patch_save(
        monkeypatch,
        return_value={
            "resource_id": "res-1",
            "file_path": "personal/u1/temp/photo.jpg",
            "kind": "image",
            "mime": "image/jpeg",
            "filename": "photo.jpg",
            "size_bytes": len(payload),
        },
    )

    result = await router_module.upload_chat_attachment(_auth(user_id), req)

    assert result["kind"] == "image"
    assert result["resource_id"] == "res-1"
    assert result["file_path"] == "personal/u1/temp/photo.jpg"
    assert result["url"] == result["file_path"]  # back-compat alias
    assert result["size_bytes"] == len(payload)
    assert result["mime"] == "image/jpeg"
    assert result["filename"] == "photo.jpg"

    # The validated bytes + scope inputs are forwarded verbatim.
    fake_save.assert_awaited_once()
    kwargs = fake_save.call_args.kwargs
    assert kwargs["user_id"] == str(user_id)
    assert kwargs["session_id"] is None
    assert kwargs["file_bytes"] == payload
    assert kwargs["filename"] == "photo.jpg"
    assert kwargs["mime"] == "image/jpeg"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_pdf_persists_temp_resource(monkeypatch):
    payload = b"%PDF-1.4\nfake pdf content"  # real PDF magic bytes
    upload = _FakeUpload("doc.pdf", payload, content_type="application/pdf")
    req = _make_request(upload=upload, content_length=len(payload))

    fake_save = _patch_save(
        monkeypatch,
        return_value={
            "resource_id": "res-2",
            "file_path": "personal/u1/temp/doc.pdf",
            "kind": "pdf",
            "mime": "application/pdf",
            "filename": "doc.pdf",
            "size_bytes": len(payload),
        },
    )

    result = await router_module.upload_chat_attachment(_auth(), req)
    assert result["kind"] == "pdf"
    assert result["resource_id"] == "res-2"
    assert fake_save.call_args.kwargs["file_bytes"] == payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_video_persists_temp_resource(monkeypatch):
    # Real MP4 ftyp box: 4 size bytes then 'ftyp' at offset 4
    payload = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00mp42mp41"
    upload = _FakeUpload("clip.mp4", payload, content_type="video/mp4")
    req = _make_request(upload=upload, content_length=len(payload))

    fake_save = _patch_save(
        monkeypatch,
        return_value={
            "resource_id": "res-3",
            "file_path": "personal/u1/temp/clip.mp4",
            "kind": "video",
            "mime": "video/mp4",
            "filename": "clip.mp4",
            "size_bytes": len(payload),
        },
    )

    result = await router_module.upload_chat_attachment(_auth(), req)
    assert result["kind"] == "video"
    assert fake_save.call_args.kwargs["file_bytes"] == payload


# ------------------------------------------------------------------ #
# Scope resolution — session_id forwarding
# ------------------------------------------------------------------ #


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_forwards_session_id_from_form(monkeypatch):
    payload = b"\xff\xd8\xff\xe0fresh"
    upload = _FakeUpload("photo.jpg", payload, content_type="image/jpeg")
    req = _make_request(
        upload=upload, content_length=len(payload), session_id="sess-123"
    )

    fake_save = _patch_save(
        monkeypatch,
        return_value={
            "resource_id": "r",
            "file_path": "team/9/temp/photo.jpg",
            "kind": "image",
            "mime": "image/jpeg",
            "filename": "photo.jpg",
            "size_bytes": len(payload),
        },
    )

    await router_module.upload_chat_attachment(_auth(), req)
    assert fake_save.call_args.kwargs["session_id"] == "sess-123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_forwards_session_id_from_query(monkeypatch):
    payload = b"\xff\xd8\xff\xe0fresh"
    upload = _FakeUpload("photo.jpg", payload, content_type="image/jpeg")
    req = _make_request(
        upload=upload, content_length=len(payload), query={"session_id": "sess-q"}
    )

    fake_save = _patch_save(
        monkeypatch,
        return_value={
            "resource_id": "r",
            "file_path": "team/9/temp/photo.jpg",
            "kind": "image",
            "mime": "image/jpeg",
            "filename": "photo.jpg",
            "size_bytes": len(payload),
        },
    )

    await router_module.upload_chat_attachment(_auth(), req)
    assert fake_save.call_args.kwargs["session_id"] == "sess-q"


# ------------------------------------------------------------------ #
# Validation gates fire BEFORE persistence (save is never called)
# ------------------------------------------------------------------ #


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_extension_spoofing_via_magic_bytes(monkeypatch):
    """C2: .jpg with non-JPEG magic bytes is rejected (415) and never
    reaches save_chat_temp_upload."""
    from fastapi import HTTPException

    # Looks like an executable header, named .jpg
    payload = b"MZ\x90\x00\x03\x00\x00\x00fake exe payload"
    upload = _FakeUpload("malware.jpg", payload, content_type="image/jpeg")
    req = _make_request(upload=upload, content_length=len(payload))
    fake_save = _patch_save(monkeypatch, return_value={})

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(_auth(), req)
    assert exc_info.value.status_code == 415
    assert "does not match" in str(exc_info.value.detail).lower()
    fake_save.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_disallowed_extension(monkeypatch):
    """Executable extensions etc. are rejected with 415 before any read."""
    from fastapi import HTTPException

    upload = _FakeUpload("hax.exe", b"MZ\x00\x00")
    req = _make_request(upload=upload)
    fake_save = _patch_save(monkeypatch, return_value={})

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(_auth(), req)
    assert exc_info.value.status_code == 415
    fake_save.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_oversize_via_content_length(monkeypatch):
    """Content-Length > 50MB * 1.1 → 413 before reading body."""
    from fastapi import HTTPException

    upload = _FakeUpload("huge.mp4", b"")  # body irrelevant; CL trips first
    req = _make_request(upload=upload, content_length=100 * 1024 * 1024)  # 100 MB
    fake_save = _patch_save(monkeypatch, return_value={})

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(_auth(), req)
    assert exc_info.value.status_code == 413
    fake_save.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_missing_file_field_returns_400(monkeypatch):
    from fastapi import HTTPException

    req = _make_request(upload=None)  # no file in form
    fake_save = _patch_save(monkeypatch, return_value={})

    with pytest.raises(HTTPException) as exc_info:
        await router_module.upload_chat_attachment(_auth(), req)
    assert exc_info.value.status_code == 400
    fake_save.assert_not_awaited()


# ------------------------------------------------------------------ #
# Magic-byte helper (pure function) — unchanged by this task
# ------------------------------------------------------------------ #


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
    assert (
        router_module._check_magic_bytes(".webp", b"RIFF\x00\x00\x00\x00FAKE") is False
    )
