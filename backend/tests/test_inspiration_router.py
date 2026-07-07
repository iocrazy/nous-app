"""Router-layer tests: guard clauses + service exception mapping."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.inspiration_router import (
    create_note,
    delete_note,
    list_notes,
    update_note,
)
from app.schemas.inspiration import NoteCreateIn, NoteUpdateIn
from app.services.inspiration.notes_service import NoteNotFound, NotePersistFailed

USER = {"id": "u1"}


@pytest.mark.asyncio
async def test_list_limit_out_of_range_400():
    for bad in (0, 201):
        with pytest.raises(HTTPException) as exc:
            await list_notes(
                date=None,
                tag=None,
                q=None,
                limit=bad,
                before_id=None,
                current_user=USER,
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_returns_service_row():
    svc = AsyncMock()
    svc.create_note.return_value = {
        "id": 123,
        "content_md": "x",
        "tags": [],
        "note_date": "2026-07-07",
        "pinned": False,
        "attachments": [],
        "ref_hotspot": None,
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:00:00Z",
    }
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await create_note(NoteCreateIn(content_md="x"), current_user=USER)
    assert out.id == "123"  # bigint → str


@pytest.mark.asyncio
async def test_update_maps_notfound_to_404():
    svc = AsyncMock()
    svc.update_note.side_effect = NoteNotFound()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await update_note("9", NoteUpdateIn(pinned=True), current_user=USER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_maps_notfound_to_404():
    svc = AsyncMock()
    svc.delete_note.side_effect = NoteNotFound()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await delete_note("9", current_user=USER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_maps_persist_failed_to_502():
    svc = AsyncMock()
    svc.delete_note.side_effect = NotePersistFailed()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await delete_note("9", current_user=USER)
    assert exc.value.status_code == 502
