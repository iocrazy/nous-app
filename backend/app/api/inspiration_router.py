"""Inspiration notes REST API (spec §4). PAT auth + /tokens arrive in P4."""

from __future__ import annotations

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse

from app.core.deps import get_current_user
from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.schemas.inspiration import AttachmentOut, NoteCreateIn, NoteOut, NoteUpdateIn
from app.services.inspiration.attachment_service import (
    AttachmentService,
    AttachmentStorageFailed,
    AttachmentTooLarge,
)
from app.services.inspiration.notes_service import (
    NoteNotFound,
    NotePersistFailed,
    get_notes_service,
)

router = APIRouter(prefix="/inspiration", tags=["Inspiration"])

_attachment_service: Optional[AttachmentService] = None


def _attachments() -> AttachmentService:
    global _attachment_service
    if _attachment_service is None:
        _attachment_service = AttachmentService()
    return _attachment_service


def _uid(current_user: dict) -> str:
    return str(current_user["id"])


@router.get("/notes", response_model=list[NoteOut])
async def list_notes(
    date: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    before_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return await get_notes_service().list_notes(
        _uid(current_user), date=date, tag=tag, q=q, limit=limit, before_id=before_id
    )


@router.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreateIn, current_user: dict = Depends(get_current_user)
):
    row = await get_notes_service().create_note(
        _uid(current_user), body.content_md, ref_hotspot=body.ref_hotspot
    )
    if row is None:
        raise HTTPException(status_code=502, detail="note persistence failed")
    return NoteOut(**row)


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str, body: NoteUpdateIn, current_user: dict = Depends(get_current_user)
):
    try:
        row = await get_notes_service().update_note(
            _uid(current_user), note_id, content_md=body.content_md, pinned=body.pinned
        )
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    if row is None:
        raise HTTPException(status_code=502, detail="note update failed")
    return NoteOut(**row)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: str, current_user: dict = Depends(get_current_user)):
    try:
        await get_notes_service().delete_note(_uid(current_user), note_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    except NotePersistFailed:
        raise HTTPException(status_code=502, detail="note deletion failed")


@router.get("/notes/activity")
async def notes_activity(
    date_from: str,
    date_to: str,
    current_user: dict = Depends(get_current_user),
):
    return await get_notes_service().activity(_uid(current_user), date_from, date_to)


@router.get("/notes/tags")
async def notes_tags(current_user: dict = Depends(get_current_user)):
    return await get_notes_service().tag_counts(_uid(current_user))


@router.post(
    "/attachments/upload",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    note_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    try:
        await get_notes_service().assert_owned(_uid(current_user), note_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    data = await file.read()
    try:
        row = await _attachments().store(
            _uid(current_user),
            note_id,
            file.filename or "file",
            file.content_type or "application/octet-stream",
            data,
        )
    except AttachmentTooLarge as e:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds the {e.limit_mb} MB attachment limit",
        )
    except AttachmentStorageFailed:
        raise HTTPException(status_code=502, detail="attachment storage unavailable")
    if row is None:
        raise HTTPException(status_code=502, detail="attachment persistence failed")
    return AttachmentOut(**row)


@router.get("/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: str,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    """Serve (redirect to) an attachment's signed storage URL.

    Browser-native loads (`<img src>`, `<video src>`, `<audio src>`,
    `<a href>` downloads) cannot attach an Authorization header, so this
    dual-channel pattern mirrors resources_crud_router.serve_resource_file:
    - Authorization header (Bearer JWT) — used by fetch()-based callers.
    - ?token= query param — the frontend's signed media/temp token, with a
      raw-JWT fallback for callers that pass one through this param instead.
    """
    user_id: Optional[str] = None

    if token and not authorization:
        from app.api.media_auth import validate_media_cookie

        user_id = await validate_media_cookie(token)

    if user_id is None:
        effective_auth = authorization
        if not effective_auth and token:
            effective_auth = f"Bearer {token}"
        if not effective_auth:
            raise HTTPException(status_code=401, detail="missing credentials")
        current_user = await get_current_user(effective_auth)
        user_id = _uid(current_user)

    att = await get_inspiration_attachments_repository().get_by_id(attachment_id)
    if not att or str(att.get("user_id")) != user_id:
        raise HTTPException(status_code=404, detail="attachment not found")
    try:
        url = await _attachments().sign_get(att)
    except AttachmentStorageFailed:
        raise HTTPException(status_code=502, detail="attachment storage unavailable")
    return RedirectResponse(url, status_code=302)


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str, current_user: dict = Depends(get_current_user)
):
    att = await get_inspiration_attachments_repository().get_by_id(attachment_id)
    if not att or str(att.get("user_id")) != _uid(current_user):
        raise HTTPException(status_code=404, detail="attachment not found")
    if not await _attachments().delete(att):
        raise HTTPException(status_code=502, detail="attachment deletion failed")
