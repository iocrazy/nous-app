"""Tests for chat/issue upload scope resolution (Task 1) and temp resource
persistence (Task 2).

Task 1 tests pin the public contract of resolve_chat_scope:
- Returns ("team", str(team_id)) when the session has a team.
- Returns ("personal", str(personal_team_id)) when session has no team or
  session_id is None. scope_id is always a teams.id snowflake (PR-E 4c-3 made
  folders/resource_items.scope_id bigint), never the user UUID.

Task 2 tests pin save_chat_temp_upload and helpers:
- _kind_for_mime maps MIME types to kind strings correctly.
- save_chat_temp_upload calls upload_resource with correct scope/folder_id and
  returns a normalized dict.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.library import chat_upload as m
from app.services.library import resources_service as rs


def _patch_personal_team(monkeypatch, team_id="9001"):
    """resolve_chat_scope's personal branch resolves the user's personal team
    via resources_service._resolve_personal_team_id (deferred import). Patch it
    to a fixed snowflake so tests don't hit the DB."""
    monkeypatch.setattr(
        rs, "_resolve_personal_team_id", AsyncMock(return_value=team_id)
    )

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
    _patch_personal_team(monkeypatch)
    assert await m.resolve_chat_scope(session_id=None, user_id="u1") == (
        "personal",
        "9001",
    )


@pytest.mark.asyncio
async def test_resolve_scope_personal_when_session_has_no_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    _patch_personal_team(monkeypatch)
    assert await m.resolve_chat_scope(session_id="s1", user_id="u1") == (
        "personal",
        "9001",
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
    # Stub scope → personal (resolves to the personal-team snowflake)
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    _patch_personal_team(monkeypatch)

    # Stub ResourcesService
    fake_svc = MagicMock()
    fake_svc.upload_resource = AsyncMock(
        return_value={"id": "res-1", "file_path": "teams/u1/uploads/res-1/v1/x.png"}
    )
    monkeypatch.setattr(m, "_resources_service", lambda: fake_svc)

    # Stub _ensure_temp_folder
    fake_ensure = AsyncMock(return_value="folder-temp")
    monkeypatch.setattr(m, "_ensure_temp_folder", fake_ensure)

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

    # _ensure_temp_folder receives the resolved personal-team snowflake as
    # scope_id, plus user_id so it can set created_by
    assert fake_ensure.call_args.args == ("personal", "9001", "u1")

    # Verify upload_resource was called with the right keyword args
    kwargs = fake_svc.upload_resource.call_args.kwargs
    assert kwargs["scope_type"] == "personal"
    assert kwargs["scope_id"] == "9001"
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


# ------------------------------------------------------------------ #
# Task 2 — _ensure_temp_folder (real get/create logic)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_ensure_temp_folder_get_path_returns_existing(monkeypatch):
    """When a folder named 'temp' already exists, return its id; never create."""
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(
        return_value=[
            {"id": "other-1", "name": "inbox"},
            {"id": "temp-77", "name": m.TEMP_FOLDER_NAME},
        ]
    )
    fake_repo.create_folder = AsyncMock()
    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        lambda: fake_repo,
    )

    folder_id = await m._ensure_temp_folder("personal", "u1", "u1")

    assert folder_id == "temp-77"
    fake_repo.get_folders.assert_awaited_once_with("personal", "u1")
    fake_repo.create_folder.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_temp_folder_create_path_sets_created_by(monkeypatch):
    """When no 'temp' folder exists, create one with created_by set."""
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "other-1", "name": "inbox"}])
    fake_repo.create_folder = AsyncMock(return_value={"id": "new-temp-9"})
    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        lambda: fake_repo,
    )

    folder_id = await m._ensure_temp_folder("team", "42", "user-abc")

    assert folder_id == "new-temp-9"
    fake_repo.create_folder.assert_awaited_once()
    data = fake_repo.create_folder.call_args.args[0]
    assert data["name"] == m.TEMP_FOLDER_NAME
    # PR-E 4b: scope_type is no longer written (column nullable post mig 240).
    assert "scope_type" not in data
    assert data["scope_id"] == "42"
    assert data["created_by"] == "user-abc"


@pytest.mark.asyncio
async def test_ensure_temp_folder_raises_when_create_returns_no_id(monkeypatch):
    """create_folder returning {} AND no temp folder on re-fetch (real RLS
    denial / silent failure, not a concurrent winner) must fail fast."""
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[])  # initial + re-fetch both empty
    fake_repo.create_folder = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        lambda: fake_repo,
    )

    with pytest.raises(RuntimeError, match="create_folder returned no id"):
        await m._ensure_temp_folder("personal", "u1", "u1")
    # Confirm we did look twice before giving up.
    assert fake_repo.get_folders.await_count == 2


@pytest.mark.asyncio
async def test_ensure_temp_folder_reuses_concurrently_created_folder(monkeypatch):
    """If create_folder returns {} because a concurrent caller already
    created the temp folder, re-fetch and reuse it (don't raise)."""
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(
        side_effect=[
            [{"id": "other-1", "name": "inbox"}],  # initial — no temp yet
            [{"id": "concurrent-temp", "name": m.TEMP_FOLDER_NAME}],  # race winner
        ]
    )
    fake_repo.create_folder = AsyncMock(return_value={})  # our insert lost
    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        lambda: fake_repo,
    )

    folder_id = await m._ensure_temp_folder("team", "42", "user-abc")

    assert folder_id == "concurrent-temp"
    fake_repo.create_folder.assert_awaited_once()
    assert fake_repo.get_folders.await_count == 2


# ------------------------------------------------------------------ #
# Task 2 — save_chat_temp_upload fail-fast + error propagation
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_save_temp_upload_raises_on_incomplete_resource(monkeypatch):
    """upload_resource returning {} (no id/file_path) must fail fast."""
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    _patch_personal_team(monkeypatch)
    monkeypatch.setattr(m, "_ensure_temp_folder", AsyncMock(return_value="folder-temp"))

    fake_svc = MagicMock()
    fake_svc.upload_resource = AsyncMock(return_value={})
    monkeypatch.setattr(m, "_resources_service", lambda: fake_svc)

    with pytest.raises(RuntimeError, match="incomplete resource dict"):
        await m.save_chat_temp_upload(
            user_id="u1",
            session_id=None,
            file_bytes=b"\x89PNG\r\n\x1a\n",
            filename="x.png",
            mime="image/png",
        )


@pytest.mark.asyncio
async def test_save_temp_upload_propagates_upload_resource_error(monkeypatch):
    """Errors from upload_resource are re-raised, not swallowed."""
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    _patch_personal_team(monkeypatch)
    monkeypatch.setattr(m, "_ensure_temp_folder", AsyncMock(return_value="folder-temp"))

    fake_svc = MagicMock()
    fake_svc.upload_resource = AsyncMock(side_effect=RuntimeError("disk full"))
    monkeypatch.setattr(m, "_resources_service", lambda: fake_svc)

    with pytest.raises(RuntimeError, match="disk full"):
        await m.save_chat_temp_upload(
            user_id="u1",
            session_id=None,
            file_bytes=b"\x89PNG\r\n\x1a\n",
            filename="x.png",
            mime="image/png",
        )
