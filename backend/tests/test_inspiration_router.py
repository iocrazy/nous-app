"""Router-layer tests: guard clauses + service exception mapping."""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.api.inspiration_router import (
    create_note,
    delete_attachment,
    delete_note,
    get_attachment,
    list_notes,
    update_note,
    upload_attachment,
)
from app.schemas.inspiration import NoteCreateIn, NoteUpdateIn
from app.services.inspiration.attachment_service import AttachmentStorageFailed
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
async def test_list_threads_min_rating_to_service():
    svc = AsyncMock()
    svc.list_notes.return_value = []
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        await list_notes(
            date=None,
            tag=None,
            q=None,
            min_rating=4,
            limit=50,
            before_id=None,
            current_user=USER,
        )
    assert svc.list_notes.await_args.kwargs["min_rating"] == 4


def test_min_rating_accepts_explicit_zero():
    """0 is a legal value (= no filter), not an error.

    `ge=1` would turn an external client's explicit `min_rating=0` — the
    dropdown's own "Any rating" value — into a 422, contradicting the
    "0 means no filter" contract the other three layers implement. The
    tests above all call the endpoint function directly, which bypasses
    FastAPI's validation entirely, so nothing else pins this bound.

    Constraints live in the Query object's pydantic-v2 `metadata`, not as
    attributes. If this parameter ever moves to `Annotated[..., Query(...)]`
    the default becomes a plain None and this lookup fails loudly.
    """
    import inspect

    import annotated_types

    q = inspect.signature(list_notes).parameters["min_rating"].default
    lower = next(m for m in q.metadata if isinstance(m, annotated_types.Ge))
    assert lower.ge == 0


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


@pytest.mark.asyncio
async def test_upload_returns_string_id():
    """Verify bigint id is coerced to string to preserve precision > 2^53."""
    svc = AsyncMock()
    svc.assert_owned = AsyncMock()  # Mock the assert_owned check
    att_svc = AsyncMock()
    # id > 2^53 — would lose precision if returned as JSON number
    att_svc.store.return_value = {
        "id": 9007199254740993,
        "note_id": 42,
        "mime": "image/png",
        "size_bytes": 4,
        "original_name": "pic.png",
    }
    upload = UploadFile(
        file=BytesIO(b"\x89PNG"),
        filename="pic.png",
        headers=Headers({"content-type": "image/png"}),
    )
    with (
        patch("app.api.inspiration_router.get_notes_service", return_value=svc),
        patch("app.api.inspiration_router._attachments", return_value=att_svc),
    ):
        out = await upload_attachment("42", file=upload, current_user=USER)
    # Verify id is a string (coerced by AttachmentOut schema)
    assert out.id == "9007199254740993"
    assert isinstance(out.id, str)


@pytest.mark.asyncio
async def test_upload_maps_storage_failed_to_502():
    svc = AsyncMock()
    svc.assert_owned = AsyncMock()
    att_svc = AsyncMock()
    att_svc.store.side_effect = AttachmentStorageFailed()
    upload = UploadFile(
        file=BytesIO(b"\x89PNG"),
        filename="pic.png",
        headers=Headers({"content-type": "image/png"}),
    )
    with (
        patch("app.api.inspiration_router.get_notes_service", return_value=svc),
        patch("app.api.inspiration_router._attachments", return_value=att_svc),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_attachment("42", file=upload, current_user=USER)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_get_attachment_with_valid_token_query_streams():
    """Browser <img>/<video>/<audio>/<a> src cannot send Authorization headers.
    A validated ?token= (media/temp token) must be enough to authorize a GET,
    matching resources_crud_router.serve_resource_file's dual-channel pattern.

    The response streams through serve_stored_file rather than 302-ing to a
    signed storage URL: that URL was built from the backend's SUPABASE_URL —
    the LAN address on prod — so every public-internet browser got an
    unreachable redirect (white page).
    """
    repo = AsyncMock()
    repo.get_by_id.return_value = {
        "id": 7,
        "user_id": "u1",
        "bucket": "inspiration",
        "path": "2026/07/14/abc/image.png",
        "mime": "image/png",
    }
    served = MagicMock(name="streamed-response")
    serve_mock = AsyncMock(return_value=served)
    fake_request = MagicMock()
    with (
        patch(
            "app.api.inspiration_router.get_inspiration_attachments_repository",
            return_value=repo,
        ),
        patch(
            "app.services.library.media_serving.serve_stored_file",
            serve_mock,
        ),
        patch(
            "app.api.media_auth.validate_media_cookie",
            AsyncMock(return_value="u1"),
        ),
    ):
        resp = await get_attachment(
            "7", fake_request, authorization=None, token="signed-media-tok"
        )
    assert resp is served
    args, kwargs = serve_mock.call_args
    assert args[0] == "sb://inspiration/2026/07/14/abc/image.png"
    assert kwargs["mime"] == "image/png"
    assert kwargs["request"] is fake_request


@pytest.mark.asyncio
async def test_get_attachment_without_credentials_rejected():
    """No Authorization header and no ?token= must be rejected outright —
    never fall through to an unauthenticated repo lookup."""
    with pytest.raises(HTTPException) as exc:
        await get_attachment("7", MagicMock(), authorization=None, token=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_delete_attachment_maps_repo_failure_to_502():
    repo = AsyncMock()
    repo.get_by_id.return_value = {"id": 7, "user_id": "u1"}
    att_svc = AsyncMock()
    att_svc.delete.return_value = False
    with (
        patch(
            "app.api.inspiration_router.get_inspiration_attachments_repository",
            return_value=repo,
        ),
        patch("app.api.inspiration_router._attachments", return_value=att_svc),
    ):
        with pytest.raises(HTTPException) as exc:
            await delete_attachment("7", current_user=USER)
    assert exc.value.status_code == 502


# ── rating (mig 448) ───────────────────────────────────────────────────────

_ROW = {
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


@pytest.mark.asyncio
async def test_create_threads_rating_and_echoes_it():
    """iOS Shortcut posts content+rating in ONE request; the router must pass
    body.rating down and surface it in the response."""
    svc = AsyncMock()
    svc.create_note.return_value = {**_ROW, "rating": 4}
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await create_note(
            NoteCreateIn(content_md="x", rating=4), current_user=USER
        )
    assert svc.create_note.call_args.kwargs["rating"] == 4
    assert out.rating == 4


@pytest.mark.asyncio
async def test_create_without_rating_passes_none():
    svc = AsyncMock()
    svc.create_note.return_value = {**_ROW, "rating": 0}
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await create_note(NoteCreateIn(content_md="x"), current_user=USER)
    assert svc.create_note.call_args.kwargs["rating"] is None
    assert out.rating == 0  # DB server_default


@pytest.mark.asyncio
async def test_create_rejects_rating_out_of_range():
    from pydantic import ValidationError

    for bad in (-1, 6):
        with pytest.raises(ValidationError):
            NoteCreateIn(content_md="x", rating=bad)


@pytest.mark.asyncio
async def test_update_threads_rating_zero():
    """Clearing a star rating (→ 0) must reach the service, not be swallowed."""
    svc = AsyncMock()
    svc.update_note.return_value = {**_ROW, "rating": 0}
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await update_note("123", NoteUpdateIn(rating=0), current_user=USER)
    assert svc.update_note.call_args.kwargs["rating"] == 0
    assert out.rating == 0


@pytest.mark.asyncio
async def test_update_rejects_rating_out_of_range():
    from pydantic import ValidationError

    for bad in (-1, 6):
        with pytest.raises(ValidationError):
            NoteUpdateIn(rating=bad)


def test_note_out_defaults_rating_to_zero_for_legacy_rows():
    """Rows written before mig 448 (or a repo dict without the key) must not
    500 the response model."""
    from app.schemas.inspiration import NoteOut

    assert NoteOut(**_ROW).rating == 0


# ── HTTP boundary: JSON body key → Pydantic field (mig 448) ────────────────
#
# Every other rating test calls the handler function directly with an
# already-constructed NoteCreateIn, which skips the one seam the iOS Shortcut
# actually depends on: the raw JSON key "rating" binding to the model field.
# This drives it through a real request/response cycle instead.


def _rating_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.api.inspiration_router import get_inspiration_actor
    from app.api.inspiration_router import router as inspiration_router

    app = FastAPI()
    app.include_router(inspiration_router, prefix="/api/v1")

    async def _actor():
        return USER

    app.dependency_overrides[get_inspiration_actor] = _actor
    return TestClient(app), app


def test_post_notes_binds_rating_from_json_body():
    svc = AsyncMock()
    svc.create_note.return_value = {**_ROW, "rating": 4}
    client, app = _rating_client()
    try:
        with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
            resp = client.post(
                "/api/v1/inspiration/notes",
                json={"content_md": "x", "rating": 4},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["rating"] == 4
    assert svc.create_note.call_args.kwargs["rating"] == 4


def test_post_notes_rejects_out_of_range_rating_with_422():
    """The ge/le bounds are enforced by FastAPI at the boundary, not only by a
    hand-built model in a unit test."""
    svc = AsyncMock()
    client, app = _rating_client()
    try:
        with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
            resp = client.post(
                "/api/v1/inspiration/notes",
                json={"content_md": "x", "rating": 6},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    svc.create_note.assert_not_awaited()


# ── Archive (mig 478) ────────────────────────────────────────────────────────
#
# Archive is a PATCH field rather than its own endpoint, which makes the
# router's job the interesting one: `archived` and the ordinary fields go to
# two DIFFERENT service calls (archiving also clears `pinned`, so it cannot
# ride the generic SET clause). These pin which call each body reaches.

_ROW = {
    "id": 9,
    "content_md": "x",
    "tags": [],
    "note_date": "2026-09-20",
    "pinned": False,
    "attachments": [],
    "ref_hotspot": None,
    "created_at": "2026-09-20T00:00:00Z",
    "updated_at": "2026-09-20T00:00:00Z",
}


@pytest.mark.asyncio
async def test_list_defaults_to_the_live_view():
    """The flag has to default to False at the ROUTER, not only in the repo —
    an archived note must not reappear because a caller omitted it."""
    svc = AsyncMock()
    svc.list_notes.return_value = []
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        await list_notes(
            date=None, tag=None, q=None, limit=50, before_id=None, current_user=USER
        )
    assert svc.list_notes.await_args.kwargs["archived"] is False


@pytest.mark.asyncio
async def test_list_threads_the_archive_cursor_to_the_service():
    svc = AsyncMock()
    svc.list_notes.return_value = []
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        await list_notes(
            date=None,
            tag=None,
            q=None,
            limit=50,
            before_id="7",
            before_archived_at="2026-09-19T10:00:00+00:00",
            archived=True,
            current_user=USER,
        )
    kwargs = svc.list_notes.await_args.kwargs
    assert kwargs["archived"] is True
    assert kwargs["before_id"] == "7"
    assert kwargs["before_archived_at"] == "2026-09-19T10:00:00+00:00"


@pytest.mark.asyncio
async def test_archive_only_patch_never_touches_the_body():
    """A body of just {"archived": true} must not reach update_note — doing so
    would rewrite content_md/tags on every archive."""
    svc = AsyncMock()
    svc.set_archived.return_value = dict(_ROW, archived_at="2026-09-20T01:00:00Z")
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await update_note("9", NoteUpdateIn(archived=True), current_user=USER)
    svc.set_archived.assert_awaited_once_with("u1", "9", True)
    svc.update_note.assert_not_awaited()
    assert out.archived_at == "2026-09-20T01:00:00Z"


@pytest.mark.asyncio
async def test_unarchive_is_the_same_path_with_false():
    """False is a value, not an absence — `archived=False` must restore, not
    be read as "no archive field given"."""
    svc = AsyncMock()
    svc.set_archived.return_value = dict(_ROW, archived_at=None)
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await update_note("9", NoteUpdateIn(archived=False), current_user=USER)
    svc.set_archived.assert_awaited_once_with("u1", "9", False)
    assert out.archived_at is None


@pytest.mark.asyncio
async def test_a_body_without_archived_still_goes_to_update_note():
    svc = AsyncMock()
    svc.update_note.return_value = _ROW
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        await update_note("9", NoteUpdateIn(pinned=True), current_user=USER)
    svc.set_archived.assert_not_awaited()
    assert svc.update_note.await_args.kwargs["pinned"] is True


@pytest.mark.asyncio
async def test_archive_maps_notfound_to_404():
    svc = AsyncMock()
    svc.set_archived.side_effect = NoteNotFound()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await update_note("9", NoteUpdateIn(archived=True), current_user=USER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_archive_maps_persist_failed_to_502():
    """A failed archive must be a typed failure the UI can show, never a
    silent no-op that leaves the note where it was."""
    svc = AsyncMock()
    svc.set_archived.side_effect = NotePersistFailed()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await update_note("9", NoteUpdateIn(archived=True), current_user=USER)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_archiving_and_pinning_in_one_patch_is_refused():
    """Archiving gives the pin up; a patch that does both would rebuild the
    state archiving exists to clear — a pinned note in nobody's list."""
    svc = AsyncMock()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await update_note(
                "9", NoteUpdateIn(archived=True, pinned=True), current_user=USER
            )
    assert exc.value.status_code == 400
    svc.set_archived.assert_not_awaited()
    svc.update_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_unpinning_while_archiving_is_allowed():
    """`pinned=False` asks for the same end state archiving produces, so there
    is nothing to refuse — the guard is about the contradiction, not about the
    two fields appearing together."""
    svc = AsyncMock()
    svc.set_archived.return_value = dict(_ROW, archived_at="2026-09-20T01:00:00Z")
    svc.update_note.return_value = dict(_ROW, archived_at="2026-09-20T01:00:00Z")
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        await update_note(
            "9", NoteUpdateIn(archived=True, pinned=False), current_user=USER
        )
    svc.set_archived.assert_awaited_once()
