"""Test suite for chat uploads via the generated_media staged store.

Coverage:
  Service layer (save_chat_image):
    1. non-image mime → ValueError (fast-path, no I/O)
    2. non-member caller → PermissionError
    3. member + image → file written atomically under
       tmp_path/teams/{scope}/chat/... AND INSERT called with
       origin_kind='chat_upload' + conversation_id set
  Utility:
    4. _safe_filename strips directory traversal and replaces unsafe chars

Mocking idiom (mirrors test_generated_media_register.py):
  - monkeypatch module-level attributes (settings, write_scope) on the
    service module so the real import graph is exercised
  - AsyncMock for conv_repo (get_conversation / is_member)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

# ── Service unit tests ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_rejects_non_image_mime():
    """Non-image mime must raise ValueError before any I/O."""
    from app.services.chat.chat_attachment_service import save_chat_image

    with pytest.raises(ValueError, match="image/"):
        await save_chat_image(
            conversation_id=1,
            user_id="user-1",
            file_bytes=b"data",
            filename="doc.pdf",
            mime="application/pdf",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_raises_for_non_member():
    """PermissionError when caller is not a conversation member."""
    from app.services.chat.chat_attachment_service import save_chat_image

    fake_conv_repo = AsyncMock()
    fake_conv_repo.get_conversation.return_value = {"id": 10, "scope_id": 42}
    fake_conv_repo.is_member.return_value = False

    with pytest.raises(PermissionError, match="not a member"):
        await save_chat_image(
            conversation_id=10,
            user_id="outsider",
            file_bytes=b"\xff\xd8\xff",
            filename="photo.jpg",
            mime="image/jpeg",
            conv_repo=fake_conv_repo,
        )

    fake_conv_repo.is_member.assert_awaited_once_with(
        conversation_id=10, user_id="outsider"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_chat_image_writes_file_and_calls_insert(tmp_path, monkeypatch):
    """Happy path: file written atomically + INSERT called with correct params."""
    import app.services.library.generated_media_service as gm_svc
    from app.services.chat.chat_attachment_service import save_chat_image

    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))

    fake_conv_repo = AsyncMock()
    fake_conv_repo.get_conversation.return_value = {"id": 5, "scope_id": 77}
    fake_conv_repo.is_member.return_value = True

    expected_row = {
        "id": 9876543210,
        "scope_id": 77,
        "conversation_id": 5,
        "creator_id": "user-abc",
        "mime": "image/png",
        "file_path": "teams/77/chat/placeholder/shot.png",
        "file_size_bytes": 3,
        "origin_kind": "chat_upload",
        "promoted_resource_id": None,
        "created_at": None,
    }
    captured: dict = {}

    class _FakeResult:
        def mappings(self):
            return self

        def first(self):
            return expected_row

    class _FakeSession:
        async def execute(self, stmt):
            captured["params"] = dict(stmt.compile(dialect=postgresql.dialect()).params)
            return _FakeResult()

    @asynccontextmanager
    async def _fake_write_scope():
        yield _FakeSession()

    monkeypatch.setattr(gm_svc, "write_scope", _fake_write_scope)

    result = await save_chat_image(
        conversation_id=5,
        user_id="user-abc",
        file_bytes=b"PNG",
        filename="shot.png",
        mime="image/png",
        conv_repo=fake_conv_repo,
    )

    # File must be written to disk under the right path structure:
    # teams/{scope}/chat/{yyyy}/{mm}/{dd}/{uuid}/{filename} — the date
    # bucket keeps any single directory from accumulating unbounded entries.
    rel = captured["params"]["file_path"]
    assert rel.startswith("teams/77/chat/")
    assert rel.endswith("/shot.png")
    parts = rel.split("/")
    assert len(parts) == 8, f"expected date-bucketed path, got {rel!r}"
    yyyy, mm, dd = parts[3], parts[4], parts[5]
    assert len(yyyy) == 4 and yyyy.isdigit(), "year bucket"
    assert len(mm) == 2 and mm.isdigit(), "month bucket"
    assert len(dd) == 2 and dd.isdigit(), "day bucket"
    assert len(parts[6]) == 32, "per-file segment must be a uuid4 hex"
    dest = tmp_path / rel
    assert dest.exists(), "file must exist on disk"
    assert dest.read_bytes() == b"PNG"

    p = captured["params"]
    assert p["origin_kind"] == "chat_upload"
    assert p["conversation_id"] == 5
    assert p["scope_id"] == 77
    assert p["creator_id"] == "user-abc"
    assert p["mime"] == "image/png"
    assert p["file_size_bytes"] == 3

    assert result == expected_row


# ── Utility tests ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_safe_filename_strips_traversal_and_unsafe():
    """_safe_filename removes path components and replaces unsafe chars."""
    from app.services.library.generated_media_service import _safe_filename

    # Directory traversal is stripped to just the basename
    assert _safe_filename("../etc/passwd") == "passwd"
    assert _safe_filename("../../secret.txt") == "secret.txt"

    # Spaces and special chars replaced with underscore
    assert _safe_filename("hello world.jpg") == "hello_world.jpg"

    # Empty string falls back to "attachment"
    assert _safe_filename("") == "attachment"

    # Normal name passes through unchanged
    assert _safe_filename("normal.png") == "normal.png"
