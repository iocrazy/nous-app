"""Inspiration notes REST API (spec §4), including PAT external ingestion (§3.3)."""

from __future__ import annotations

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

from app.core.deps import get_current_user
from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.schemas.inspiration import (
    ApiTokenCreated,
    ApiTokenCreateIn,
    ApiTokenOut,
    AttachmentOut,
    NoteCreateIn,
    NoteOut,
    NoteUpdateIn,
)
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
from app.services.inspiration.token_service import (
    TOKEN_PREFIX,
    get_inspiration_token_service,
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


async def get_inspiration_actor(authorization: str = Header(...)) -> dict:
    """Dual auth for inspiration write endpoints.

    Accepts either a Supabase JWT (the page) or a `mhk_`-prefixed Personal
    Access Token (external scripts / shortcuts / bots). A PAT is scoped to
    inspiration read/write only — it can create notes and upload attachments
    but cannot manage tokens (those endpoints stay JWT-only).
    """
    token = authorization.replace("Bearer ", "", 1).strip()
    if token.startswith(TOKEN_PREFIX):
        user_id = await get_inspiration_token_service().authenticate(token)
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or revoked API token",
            )
        return {
            "id": user_id,
            "email": None,
            "role": None,
            "aud": None,
            "auth_via": "pat",
        }
    return await get_current_user(authorization)


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
    body: NoteCreateIn, current_user: dict = Depends(get_inspiration_actor)
):
    """Create a note. Accepts a Supabase JWT or a PAT for external ingestion:

        curl -X POST .../api/v1/inspiration/notes \\
          -H "Authorization: Bearer mhk_..." \\
          -H "Content-Type: application/json" \\
          -d '{"content_md": "a captured idea #inbox"}'
    """
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
    current_user: dict = Depends(get_inspiration_actor),
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
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    """Stream an attachment's bytes through the backend.

    Previously this 302-redirected to a signed storage URL — but that URL
    is built from the backend's SUPABASE_URL, which on prod is the LAN
    address (http://192.168.50.9:9082): every public-internet browser got
    an unreachable redirect and a white page. Streaming through
    serve_stored_file (the storage-unification shared reader, Range-aware
    for audio/video scrubbing) works from anywhere and matches how every
    other media route serves object-store bytes.

    Browser-native loads (`<img src>`, `<video src>`, `<audio src>`,
    `<a href>` downloads) cannot attach an Authorization header, so this
    dual-channel pattern mirrors resources_crud_router.serve_resource_file:
    - Authorization header (Bearer JWT) — used by fetch()-based callers.
    - ?token= query param — the frontend always sends the short-lived,
      independently-revocable media token here (validate_media_cookie).
      The raw-JWT fallback below only exists for parity with the resources
      route's defense-in-depth; it must never be what the frontend puts in
      a URL, since a long-lived session JWT in a URL leaks into nginx/app
      logs, browser history, and Referer headers, and can't be revoked
      without killing the whole session.
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

    from app.services.library.media_serving import serve_stored_file

    bucket = att.get("bucket") or "inspiration"
    return await serve_stored_file(
        f"sb://{bucket}/{att['path']}",
        mime=att.get("mime") or "application/octet-stream",
        request=request,
        extra_headers={"Cache-Control": "private, no-store"},
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str, current_user: dict = Depends(get_current_user)
):
    att = await get_inspiration_attachments_repository().get_by_id(attachment_id)
    if not att or str(att.get("user_id")) != _uid(current_user):
        raise HTTPException(status_code=404, detail="attachment not found")
    if not await _attachments().delete(att):
        raise HTTPException(status_code=502, detail="attachment deletion failed")


# ─── Personal Access Tokens (spec §3.3) ──────────────────────────────────────
# Token management is JWT-only: a leaked PAT must not be able to mint or revoke
# tokens, only exercise the inspiration write scope.


@router.get("/tokens", response_model=list[ApiTokenOut])
async def list_tokens(current_user: dict = Depends(get_current_user)):
    return await get_inspiration_token_service().list(_uid(current_user))


@router.post(
    "/tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED
)
async def create_token(
    body: ApiTokenCreateIn, current_user: dict = Depends(get_current_user)
):
    """Mint a PAT. The plaintext `token` is returned exactly once here."""
    result = await get_inspiration_token_service().create(
        _uid(current_user), body.name.strip()
    )
    if result is None:
        raise HTTPException(status_code=502, detail="token creation failed")
    row, plaintext = result
    return ApiTokenCreated(**row, token=plaintext)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: str, current_user: dict = Depends(get_current_user)):
    if not await get_inspiration_token_service().revoke(_uid(current_user), token_id):
        raise HTTPException(status_code=404, detail="token not found")
