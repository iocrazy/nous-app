"""Tests for chat/issue upload scope resolution (Task 1) and temp resource
persistence (Task 2).

Task 1 tests pin the public contract of resolve_chat_scope:
- Returns ("team", str(team_id)) when the session has a team.
- Returns ("personal", str(user_id)) when session has no team or session_id is None.

Task 2 tests pin save_chat_temp_upload and helpers:
- _kind_for_mime maps MIME types to kind strings correctly.
- save_chat_temp_upload calls upload_resource with correct scope/folder_id and
  returns a normalized dict.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.library import chat_upload as m

# ------------------------------------------------------------------ #
# Task 1 — resolve_chat_scope
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_resolve_scope_team_when_session_has_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=4242))
    assert await m.resolve_chat_scope(session_id="s1", user_id="u1") == ("team", "4242")


@pytest.mark.asyncio
async def test_resolve_scope_personal_when_no_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    assert await m.resolve_chat_scope(session_id=None, user_id="u1") == (
        "personal",
        "u1",
    )


@pytest.mark.asyncio
async def test_resolve_scope_personal_when_session_has_no_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    assert await m.resolve_chat_scope(session_id="s1", user_id="u1") == (
        "personal",
        "u1",
    )


# ------------------------------------------------------------------ #
# Task 2 — _kind_for_mime
# ------------------------------------------------------------------ #


def test_kind_for_mime_image():
    assert m._kind_for_mime("image/png", "photo.png") == "image"
    assert m._kind_for_mime("image/jpeg", "photo.jpg") == "image"
    assert m._kind_for_mime("image/gif", "anim.gif") == "image"


def test_kind_for_mime_video():
    assert m._kind_for_mime("video/mp4", "clip.mp4") == "video"
    assert m._kind_for_mime("video/webm", "clip.webm") == "video"


def test_kind_for_mime_pdf():
    assert m._kind_for_mime("application/pdf", "doc.pdf") == "pdf"


def test_kind_for_mime_fallback_uses_extension():
    # unknown MIME with a known extension falls back to extension-based detection
    assert m._kind_for_mime("application/octet-stream", "photo.png") == "image"
    assert m._kind_for_mime("application/octet-stream", "clip.mp4") == "video"
    assert m._kind_for_mime("application/octet-stream", "doc.pdf") == "pdf"


# ------------------------------------------------------------------ #
# Task 2 — save_chat_temp_upload (personal scope, no session)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_save_temp_upload_routes_to_resources_temp_folder(monkeypatch):
    # Stub scope → personal
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))

    # Stub ResourcesService
    fake_svc = MagicMock()
    fake_svc.upload_resource = AsyncMock(
        return_value={"id": "res-1", "file_path": "teams/u1/uploads/res-1/v1/x.png"}
    )
    monkeypatch.setattr(m, "_resources_service", lambda: fake_svc)

    # Stub _ensure_temp_folder
    monkeypatch.setattr(m, "_ensure_temp_folder", AsyncMock(return_value="folder-temp"))

    out = await m.save_chat_temp_upload(
        user_id="u1",
        session_id=None,
        file_bytes=b"\x89PNG\r\n\x1a\n",
        filename="x.png",
        mime="image/png",
    )

    assert out["resource_id"] == "res-1"
    assert out["file_path"] == "teams/u1/uploads/res-1/v1/x.png"
    assert out["kind"] == "image"
    assert out["mime"] == "image/png"
    assert out["filename"] == "x.png"
    assert out["size_bytes"] == len(b"\x89PNG\r\n\x1a\n")

    # Verify upload_resource was called with the right keyword args
    kwargs = fake_svc.upload_resource.call_args.kwargs
    assert kwargs["scope_type"] == "personal"
    assert kwargs["scope_id"] == "u1"
    assert kwargs["folder_id"] == "folder-temp"
    # file arg must expose .filename and .content_type
    file_arg = kwargs["file"]
    assert file_arg.filename == "x.png"
    assert file_arg.content_type == "image/png"


@pytest.mark.asyncio
async def test_save_temp_upload_team_scope(monkeypatch):
    """When session resolves to a team, scope_type='team' is forwarded."""
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=99))

    fake_svc = MagicMock()
    fake_svc.upload_resource = AsyncMock(
        return_value={"id": "res-2", "file_path": "teams/99/uploads/res-2/v1/clip.mp4"}
    )
    monkeypatch.setattr(m, "_resources_service", lambda: fake_svc)
    monkeypatch.setattr(
        m, "_ensure_temp_folder", AsyncMock(return_value="folder-team-temp")
    )

    out = await m.save_chat_temp_upload(
        user_id="u1",
        session_id="sess-x",
        file_bytes=b"\x00\x00\x00\x18ftyp",
        filename="clip.mp4",
        mime="video/mp4",
    )

    assert out["kind"] == "video"
    kwargs = fake_svc.upload_resource.call_args.kwargs
    assert kwargs["scope_type"] == "team"
    assert kwargs["scope_id"] == "99"
    assert kwargs["folder_id"] == "folder-team-temp"
