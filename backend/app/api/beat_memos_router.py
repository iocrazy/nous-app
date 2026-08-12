"""Beat Memos Router — timeline memo CRUD + image upload/serve (Beats M5).

Endpoints:
  GET    /scripts/{script_id}/memos                     — verify_script_read_access
  POST   /scripts/{script_id}/memos                     — verify_script_access
  POST   /scripts/{script_id}/memos/upload             — verify_script_access
  PATCH  /memos/{memo_id}                                — verify_memo_access
  DELETE /memos/{memo_id}                                — verify_memo_access
  GET    /scripts/{script_id}/memos/{memo_id}/images/{idx}  — dual-channel auth (read)

A memo is a laper-style note anchored to a whole-second offset on a script's
Beats arrangement timeline — its OWN table (beat_memos), never the inspiration
library. Pure synchronous CRUD — no AI, no workflow. Ownership is enforced by
verify_script_access / verify_script_read_access (script-scoped routes) /
verify_memo_access (id-scoped write routes); the image serve route
authenticates the media-token / JWT dual channel itself (browser-native <img>
loads cannot send an Authorization header) and then bounds every read to a
memo the caller's script owns, via ``_assert_script_access(..., write=False)``
(team membership OR an explicit project_members row — 2026-08-12 fix).
"""

from __future__ import annotations

import mimetypes
from typing import Any, Dict, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from loguru import logger

from app.core.deps import AuthDep, get_current_user
from app.core.scope_guards import (
    _assert_script_access,
    verify_memo_access,
    verify_script_access,
    verify_script_read_access,
)
from app.repositories.beat_memo_repository import get_beat_memo_repository
from app.schemas.beat_memo import MemoCreate, MemoOut, MemoUpdate
from app.services.beats.memo_image_service import (
    MEMO_BUCKET,
    MemoImageStorageFailed,
    MemoImageTooLarge,
    get_memo_image_service,
)

router = APIRouter()


@router.get("/scripts/{script_id}/memos")
async def list_memos(
    script_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_script_read_access),
) -> Dict[str, Any]:
    """List all memos for a script, ordered along the timeline."""
    try:
        memos = await get_beat_memo_repository().list_by_script(script_id)
        return {"success": True, "data": [MemoOut(**m).model_dump() for m in memos]}
    except Exception as exc:
        logger.error(f"[Memos] list for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list memos")


@router.post("/scripts/{script_id}/memos")
async def create_memo(
    script_id: str,
    auth: AuthDep,
    body: MemoCreate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Create a memo under a script."""
    try:
        data = body.model_dump()
        data["script_id"] = script_id
        memo = await get_beat_memo_repository().create(data)
        return {"success": True, "data": MemoOut(**memo).model_dump()}
    except Exception as exc:
        logger.error(f"[Memos] create for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create memo")


@router.post("/scripts/{script_id}/memos/upload")
async def upload_memo_image(
    script_id: str,
    auth: AuthDep,
    file: UploadFile = File(...),
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Store one memo image; return its object-store path (the string the caller
    then persists into the memo's ``images`` array)."""
    mime = file.content_type or "application/octet-stream"
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail="only image files are allowed")
    data = await file.read()
    try:
        path = await get_memo_image_service().store(
            script_id, file.filename or "image", mime, data
        )
    except MemoImageTooLarge as e:
        raise HTTPException(
            status_code=413, detail=f"image exceeds the {e.limit_mb} MB limit"
        )
    except MemoImageStorageFailed:
        raise HTTPException(status_code=502, detail="image storage unavailable")
    return {"success": True, "data": {"path": path}}


@router.patch("/memos/{memo_id}")
async def update_memo(
    memo_id: str,
    auth: AuthDep,
    body: MemoUpdate,
    _guard: None = Depends(verify_memo_access),
) -> Dict[str, Any]:
    """Update a memo's anchor_sec / content / images (true PATCH semantics)."""
    try:
        # exclude_unset: an absent field stays untouched. All three columns are
        # NOT NULL, so a stray explicit null is dropped rather than forwarded.
        data = body.model_dump(exclude_unset=True)
        for not_null in ("anchor_sec", "content", "images"):
            if not_null in data and data[not_null] is None:
                data.pop(not_null)
        memo = await get_beat_memo_repository().update(memo_id, data)
        if memo is None:
            raise HTTPException(status_code=404, detail="Memo not found")
        return {"success": True, "data": MemoOut(**memo).model_dump()}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Memos] update {memo_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update memo")


@router.delete("/memos/{memo_id}")
async def delete_memo(
    memo_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_memo_access),
) -> Dict[str, Any]:
    """Delete a memo."""
    try:
        await get_beat_memo_repository().delete(memo_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Memos] delete {memo_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete memo")


@router.get("/scripts/{script_id}/memos/{memo_id}/images/{idx}")
async def get_memo_image(
    script_id: str,
    memo_id: str,
    idx: int,
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    """Stream a memo image's bytes.

    Dual-channel auth mirrors inspiration_router.get_attachment: browser-native
    loads (`<img src>`) cannot attach an Authorization header, so the frontend
    passes the short-lived media token via `?token=`; fetch()-based callers use
    the Bearer JWT. After resolving the user, the read is bounded to a memo the
    caller's script owns (memo → this script → team membership), then to a valid
    image index — a caller can never read an arbitrary bucket path.
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
        user_id = str(current_user["id"])

    memo = await get_beat_memo_repository().get_by_id(memo_id)
    if not memo or str(memo.get("script_id")) != str(script_id):
        raise HTTPException(status_code=404, detail="Memo not found")
    # Read-access check: raises 403/404 if the caller can't reach the script
    # (team membership OR an explicit project_members row — 2026-08-12 fix).
    await _assert_script_access(str(script_id), user_id, write=False)

    images = memo.get("images") or []
    if idx < 0 or idx >= len(images):
        raise HTTPException(status_code=404, detail="Memo image not found")
    path = images[idx]

    from app.services.library.media_serving import serve_stored_file

    mime, _ = mimetypes.guess_type(path)
    return await serve_stored_file(
        f"sb://{MEMO_BUCKET}/{path}",
        mime=mime or "application/octet-stream",
        request=request,
        extra_headers={"Cache-Control": "private, no-store"},
    )
