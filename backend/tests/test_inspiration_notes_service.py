"""NotesService orchestration tests — repos mocked."""

from unittest.mock import AsyncMock

import pytest

import app.services.inspiration.notes_service as svc_mod
from app.services.inspiration.notes_service import (
    NoteNotFound,
    NotePersistFailed,
    NotesService,
)


def _service():
    svc = NotesService()
    svc._notes = AsyncMock()
    svc._attachments = AsyncMock()
    svc._attachments.list_for_notes.return_value = []
    return svc


@pytest.fixture(autouse=True)
def _stub_pool_tags(monkeypatch):
    """Default no-op tag-pool sync so orchestration tests don't hit the real
    tags / note_tags repos. Tests that assert on the sync override these."""

    class _NoopTags:
        async def resolve_note_tags(self, user_id, names):
            return []

    class _NoopNoteTags:
        async def sync_for_note(self, note_id, tag_ids):
            return None

    monkeypatch.setattr(
        svc_mod, "get_tags_repository", lambda: _NoopTags(), raising=False
    )
    monkeypatch.setattr(
        svc_mod, "get_note_tags_repository", lambda: _NoopNoteTags(), raising=False
    )


@pytest.mark.asyncio
async def test_create_parses_tags_and_stamps_shanghai_date():
    svc = _service()
    svc._notes.create.return_value = {"id": 1, "tags": ["hooks"]}
    await svc.create_note("u1", "idea #hooks")
    kwargs = svc._notes.create.call_args.kwargs
    assert kwargs["tags"] == ["hooks"]
    assert len(kwargs["note_date"]) == 10  # YYYY-MM-DD


@pytest.mark.asyncio
async def test_update_rejects_non_owner_as_not_found():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "someone-else"}
    with pytest.raises(NoteNotFound):
        await svc.update_note("u1", "1", content_md="new")
    svc._notes.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_content_reparses_tags():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "u1"}
    svc._notes.update.return_value = {"id": 1}
    await svc.update_note("u1", "1", content_md="now #fresh")
    assert svc._notes.update.call_args.kwargs["tags"] == ["fresh"]


@pytest.mark.asyncio
async def test_update_pinned_only_does_not_touch_tags():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "u1"}
    svc._notes.update.return_value = {"id": 1}
    await svc.update_note("u1", "1", pinned=True)
    assert svc._notes.update.call_args.kwargs["tags"] is None


@pytest.mark.asyncio
async def test_update_folds_attachments_into_response():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "u1"}
    svc._notes.update.return_value = {"id": 1}
    svc._attachments.list_for_notes.return_value = [
        {"id": 9, "note_id": 1, "mime": "image/png"}
    ]
    row = await svc.update_note("u1", "1", content_md="now #fresh")
    svc._attachments.list_for_notes.assert_awaited_once_with([1])
    assert row["attachments"][0]["id"] == 9


@pytest.mark.asyncio
async def test_list_folds_attachments_per_note():
    svc = _service()
    svc._notes.list.return_value = [{"id": 1}, {"id": 2}]
    svc._attachments.list_for_notes.return_value = [
        {"id": 9, "note_id": 1, "mime": "image/png"}
    ]
    rows = await svc.list_notes(
        "u1", date=None, tag=None, q=None, limit=50, before_id=None
    )
    assert rows[0]["attachments"][0]["id"] == 9
    assert rows[1]["attachments"] == []


@pytest.mark.asyncio
async def test_delete_missing_raises_not_found():
    svc = _service()
    svc._notes.get_by_id.return_value = None
    with pytest.raises(NoteNotFound):
        await svc.delete_note("u1", "404")


@pytest.mark.asyncio
async def test_delete_raises_persist_failed_when_repo_reports_false():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "u1"}
    svc._notes.soft_delete.return_value = False
    with pytest.raises(NotePersistFailed):
        await svc.delete_note("u1", "1")


@pytest.mark.asyncio
async def test_assert_owned_raises_for_missing():
    svc = _service()
    svc._notes.get_by_id.return_value = None
    with pytest.raises(NoteNotFound):
        await svc.assert_owned("u1", "404")


@pytest.mark.asyncio
async def test_create_note_syncs_note_tags(monkeypatch):
    svc = _service()

    resolve_calls, sync_calls = [], []

    class _FakeTagsRepo:
        async def resolve_note_tags(self, user_id, names):
            resolve_calls.append((user_id, names))
            return [101, 102]

    class _FakeNoteTagsRepo:
        async def sync_for_note(self, note_id, tag_ids):
            sync_calls.append((note_id, tag_ids))

    monkeypatch.setattr(
        svc_mod, "get_tags_repository", lambda: _FakeTagsRepo(), raising=False
    )
    monkeypatch.setattr(
        svc_mod, "get_note_tags_repository", lambda: _FakeNoteTagsRepo(), raising=False
    )
    svc._notes.create.return_value = {
        "id": 7,
        "content_md": "x #ai #ml",
        "tags": ["ai", "ml"],
    }

    await svc.create_note("user-1", "x #ai #ml")

    assert resolve_calls == [("user-1", ["ai", "ml"])]
    assert sync_calls == [(7, [101, 102])]


@pytest.mark.asyncio
async def test_update_content_change_syncs_note_tags(monkeypatch):
    svc = _service()

    resolve_calls, sync_calls = [], []

    class _FakeTagsRepo:
        async def resolve_note_tags(self, user_id, names):
            resolve_calls.append((user_id, names))
            return [201]

    class _FakeNoteTagsRepo:
        async def sync_for_note(self, note_id, tag_ids):
            sync_calls.append((note_id, tag_ids))

    monkeypatch.setattr(
        svc_mod, "get_tags_repository", lambda: _FakeTagsRepo(), raising=False
    )
    monkeypatch.setattr(
        svc_mod, "get_note_tags_repository", lambda: _FakeNoteTagsRepo(), raising=False
    )
    svc._notes.get_by_id.return_value = {"id": 7, "user_id": "user-1"}
    svc._notes.update.return_value = {"id": 7, "tags": ["fresh"]}

    await svc.update_note("user-1", "7", content_md="now #fresh")

    assert resolve_calls == [("user-1", ["fresh"])]
    assert sync_calls == [(7, [201])]


@pytest.mark.asyncio
async def test_update_note_without_content_change_skips_tag_sync(monkeypatch):
    svc = _service()

    called = []

    class _RecordingTagsRepo:
        async def resolve_note_tags(self, user_id, names):
            called.append((user_id, names))
            return []

    monkeypatch.setattr(
        svc_mod, "get_tags_repository", lambda: _RecordingTagsRepo(), raising=False
    )
    svc._notes.get_by_id.return_value = {"id": 7, "user_id": "user-1"}
    svc._notes.update.return_value = {"id": 7}

    await svc.update_note("user-1", "7", pinned=True)  # content_md=None

    assert called == []
