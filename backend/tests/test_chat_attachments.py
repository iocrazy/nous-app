"""Test suite for Task 2 — chat_attachments independent store.

Coverage:
  Service layer (save_chat_image):
    - non-image mime → ValueError
    - channel not found → ValueError
    - non-member caller → PermissionError
    - happy path: builds teams/{scope}/chat/... rel_path, calls repo.create

  Serve route (GET /chat/attachments/{id}/file):
    - row not found → 404
    - realpath traversal (../../etc/passwd) → 404
    - file missing on disk → 404
    - happy path → 200 + bytes + immutable cache header
    - no auth required (public endpoint)

Mocking idiom mirrors test_generated_media_router.py:
  - monkeypatch service methods on the module-level module object (sys.modules)
  - monkeypatch settings for DOWNLOAD_PATH
  - httpx AsyncClient + ASGITransport against the real FastAPI app
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

# The chat_router module — must use sys.modules because __init__.py may
# rebind the name to the APIRouter object.
cr = sys.modules.get("app.api.chat_router")

FAKE_USER_ID = "00000000-0000-0000-0000-000000000099"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Service unit tests ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_rejects_non_image_mime():
    """Non-image mime must raise ValueError before any I/O."""
    from app.services.chat.chat_attachment_service import save_chat_image

    with pytest.raises(ValueError, match="image/"):
        await save_chat_image(
            channel_id=1,
            user_id="user-1",
            file_bytes=b"data",
            filename="doc.pdf",
            mime="application/pdf",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_raises_for_missing_channel():
    """ValueError when channel is not found (get_channel returns None)."""
    from app.services.chat.chat_attachment_service import save_chat_image

    fake_chat_repo = AsyncMock()
    fake_chat_repo.get_channel.return_value = None

    with pytest.raises(ValueError, match="channel"):
        await save_chat_image(
            channel_id=999,
            user_id="user-1",
            file_bytes=b"\xff\xd8\xff",
            filename="photo.jpg",
            mime="image/jpeg",
            chat_repo=fake_chat_repo,
        )

    fake_chat_repo.get_channel.assert_awaited_once_with(channel_id=999)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_raises_for_non_member():
    """PermissionError when caller is not a channel member."""
    from app.services.chat.chat_attachment_service import save_chat_image

    fake_chat_repo = AsyncMock()
    fake_chat_repo.get_channel.return_value = {"id": 10, "team_id": 42}
    fake_chat_repo.is_member.return_value = False

    with pytest.raises(PermissionError, match="not a member"):
        await save_chat_image(
            channel_id=10,
            user_id="outsider",
            file_bytes=b"\xff\xd8\xff",
            filename="photo.jpg",
            mime="image/jpeg",
            chat_repo=fake_chat_repo,
        )

    fake_chat_repo.is_member.assert_awaited_once_with(channel_id=10, user_id="outsider")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_builds_correct_path_and_inserts_row(
    tmp_path, monkeypatch
):
    """Happy path: rel_path is teams/{scope}/chat/{uuid}/{name} and repo.create
    is called with the right arguments."""
    import app.services.chat.chat_attachment_service as svc_mod
    from app.services.chat.chat_attachment_service import save_chat_image

    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    fake_chat_repo = AsyncMock()
    fake_chat_repo.get_channel.return_value = {"id": 5, "team_id": 77}
    fake_chat_repo.is_member.return_value = True

    expected_row = {
        "id": 1234567890,
        "scope_id": 77,
        "channel_id": 5,
        "creator_id": "user-abc",
        "mime": "image/png",
        "file_path": "will-be-checked",
        "file_size_bytes": 3,
        "width": None,
        "height": None,
        "promoted_resource_id": None,
        "created_at": None,
    }
    fake_att_repo = AsyncMock()
    fake_att_repo.create.return_value = expected_row

    result = await save_chat_image(
        channel_id=5,
        user_id="user-abc",
        file_bytes=b"PNG",
        filename="shot.png",
        mime="image/png",
        chat_repo=fake_chat_repo,
        attachment_repo=fake_att_repo,
    )

    # Repo.create must be called once
    fake_att_repo.create.assert_awaited_once()
    call_kwargs = fake_att_repo.create.call_args.kwargs

    # Verify mandatory fields forwarded
    assert call_kwargs["scope_id"] == 77
    assert call_kwargs["channel_id"] == 5
    assert call_kwargs["creator_id"] == "user-abc"
    assert call_kwargs["mime"] == "image/png"
    assert call_kwargs["file_size_bytes"] == 3

    # Verify the rel_path structure: teams/{scope}/chat/{uuid}/{safe_name}
    rel = call_kwargs["file_path"]
    parts = rel.split("/")
    assert parts[0] == "teams"
    assert parts[1] == "77"
    assert parts[2] == "chat"
    assert len(parts[3]) == 32  # uuid4 hex
    assert parts[4] == "shot.png"

    # Verify the file was actually written to disk
    dest = tmp_path / rel
    assert dest.exists()
    assert dest.read_bytes() == b"PNG"

    # Service returns the repo row
    assert result == expected_row


# ── Serve route tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_attachment_404_when_row_missing(monkeypatch, client):
    """GET /chat/attachments/{id}/file → 404 when repo.get returns None."""
    from app.repositories.chat_attachment_repository import ChatAttachmentRepository

    async def _fake_get(self, att_id: int):
        return None

    monkeypatch.setattr(ChatAttachmentRepository, "get", _fake_get)

    resp = await client.get("/api/v1/chat/attachments/9999/file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_attachment_404_on_path_traversal(monkeypatch, tmp_path, client):
    """Realpath traversal (../../etc/passwd) is rejected with 404."""
    from app.repositories.chat_attachment_repository import ChatAttachmentRepository

    async def _fake_get(self, att_id: int):
        return {
            "id": att_id,
            "file_path": "../../etc/passwd",
            "mime": "text/plain",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(ChatAttachmentRepository, "get", _fake_get)
    monkeypatch.setattr(cr, "settings", fake_settings)

    resp = await client.get("/api/v1/chat/attachments/88/file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_attachment_404_when_file_missing_on_disk(
    monkeypatch, tmp_path, client
):
    """Row exists but the file is absent from disk → 404."""
    from app.repositories.chat_attachment_repository import ChatAttachmentRepository

    async def _fake_get(self, att_id: int):
        return {
            "id": att_id,
            "file_path": "teams/1/chat/abc/ghost.jpg",
            "mime": "image/jpeg",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(ChatAttachmentRepository, "get", _fake_get)
    monkeypatch.setattr(cr, "settings", fake_settings)

    resp = await client.get("/api/v1/chat/attachments/77/file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_attachment_happy_path(monkeypatch, tmp_path, client):
    """Happy path: returns 200, file bytes, image/jpeg content-type,
    and the immutable Cache-Control header."""
    from app.repositories.chat_attachment_repository import ChatAttachmentRepository

    file_rel = "teams/77/chat/deadbeef1234567890abcdef12345678/photo.jpg"
    file_bytes = b"\xff\xd8\xff\xe0JFIF fake jpeg"
    dest = tmp_path / file_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)

    async def _fake_get(self, att_id: int):
        return {
            "id": att_id,
            "file_path": file_rel,
            "mime": "image/jpeg",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(ChatAttachmentRepository, "get", _fake_get)
    monkeypatch.setattr(cr, "settings", fake_settings)

    resp = await client.get("/api/v1/chat/attachments/42/file")
    assert resp.status_code == 200, resp.text
    assert resp.content == file_bytes
    assert "image/jpeg" in resp.headers.get("content-type", "")
    cc = resp.headers.get("cache-control", "")
    assert "public" in cc
    assert "immutable" in cc


@pytest.mark.asyncio
async def test_serve_attachment_no_auth_required(monkeypatch, tmp_path):
    """Serve endpoint must be accessible with NO auth header (world-readable-by-id).

    This test deliberately skips the auth override fixture and sends no
    Authorization header — confirming the endpoint is genuinely public.
    """
    from app.repositories.chat_attachment_repository import ChatAttachmentRepository

    file_rel = "teams/5/chat/aaaa/img.png"
    content = b"\x89PNG\r\n\x1a\nfake png"
    dest = tmp_path / file_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    async def _fake_get(self, att_id: int):
        return {"id": att_id, "file_path": file_rel, "mime": "image/png"}

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))

    # Remove auth override so no bearer token is injected.
    app.dependency_overrides.pop(get_auth, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        monkeypatch.setattr(ChatAttachmentRepository, "get", _fake_get)
        monkeypatch.setattr(cr, "settings", fake_settings)
        resp = await ac.get("/api/v1/chat/attachments/55/file")

    assert resp.status_code == 200, resp.text
    assert resp.content == content
