"""Shared media reader — the ONE place that turns a file_path into a Response.

Storage unification (spec 2026-07-12): every endpoint that serves stored
media bytes routes through `serve_stored_file`, so the legacy-vs-sb:// split
never leaks into routers:

  filesystem row → FileResponse (sendfile; Starlette answers Range natively)
  sb:// row + STORAGE_SIGNED_URL_PUBLIC_BASE → 302 to a signed URL with the
      LAN storage host swapped for the public base
  sb:// row otherwise → StreamingResponse with Range passthrough (206/416)

The Range parsing + streamed-proxy logic here was moved verbatim from
generated_media_router (its endpoints delegate to the same helpers), so both
readers share one implementation.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from app.core.config import settings
from app.services.library.media_storage import ObjectStore, resolve_media_source


class RangeNotSatisfiable(Exception):
    """A syntactically valid byte-range that falls outside the object (→ 416)."""


def parse_byte_range(
    range_header: Optional[str], size: int
) -> Optional[tuple[int, int]]:
    """Parse a single HTTP byte-range against a known object ``size``.

    Returns an inclusive ``(start, end)`` pair, or ``None`` when there is no
    usable range and the full object should be served (200). A malformed or
    multi-range header is ignored per RFC 7233 (serve full), but a well-formed
    range that lies outside the object raises ``RangeNotSatisfiable`` (416).
    """
    if not range_header:
        return None
    header = range_header.strip()
    if not header.startswith("bytes="):
        return None  # unknown unit → ignore
    spec = header[len("bytes=") :].strip()
    if "," in spec or "-" not in spec:
        return None  # multi-range unsupported / malformed → serve full
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # suffix range: bytes=-N → last N bytes
            n = int(end_s)
            if n <= 0:
                raise RangeNotSatisfiable
            start, end = max(0, size - n), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s != "" else size - 1
    except ValueError:
        return None  # non-integer bounds → ignore
    if size == 0 or start > end or start >= size:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


def filesystem_response(
    rel_path: str, mime: str, headers: Optional[dict] = None
) -> FileResponse:
    """Serve a filesystem-backed media file (FileResponse; Range-capable).

    Guards against path traversal escaping DOWNLOAD_PATH. 404 on escape/miss.
    """
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(settings.DOWNLOAD_PATH, rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        raise HTTPException(status_code=404, detail="not found")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(real, media_type=mime, headers=headers)


async def range_stream_response(
    store: ObjectStore,
    key: str,
    mime: str,
    request: Request,
    headers: Optional[dict] = None,
) -> Response:
    """Stream an object-store key with HTTP Range support (seekable proxy).

    The request's Range header is validated against the object size (HEAD)
    and passed through to storage — no full-file memory spike. A well-formed
    but out-of-bounds range yields 416; a missing object yields 404.
    """
    headers = dict(headers or {})
    try:
        size = await store.get_size(key)
    except Exception:
        raise HTTPException(status_code=404, detail="file missing")

    try:
        byte_range = parse_byte_range(request.headers.get("range"), size)
    except RangeNotSatisfiable:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    if byte_range is None:
        return StreamingResponse(
            store.get_stream(key),
            status_code=200,
            media_type=mime,
            headers={
                **headers,
                "Accept-Ranges": "bytes",
                "Content-Length": str(size),
            },
        )

    start, end = byte_range
    return StreamingResponse(
        store.get_stream(key, start=start, end=end),
        status_code=206,
        media_type=mime,
        headers={
            **headers,
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )


async def serve_stored_file(
    file_path: str,
    *,
    mime: str,
    request: Request,
    disposition: str = "inline",
    extra_headers: Optional[dict] = None,
) -> Response:
    """Serve any stored file_path shape — the unified read endpoint helper.

    filesystem row → FileResponse; sb:// row → 302 signed URL when
    STORAGE_SIGNED_URL_PUBLIC_BASE is configured, else a streamed Range proxy.
    ``disposition`` other than "inline" adds a Content-Disposition header.
    """
    headers = dict(extra_headers or {})
    if disposition != "inline":
        headers["Content-Disposition"] = disposition

    loc = resolve_media_source(file_path)
    if not loc.is_object_store:
        return filesystem_response(loc.rel_path or "", mime, headers)

    store = ObjectStore(loc.bucket)
    public_base = (settings.STORAGE_SIGNED_URL_PUBLIC_BASE or "").strip()
    if public_base:
        try:
            signed = await store.signed_url(loc.key, ttl_seconds=300)
        except Exception:
            raise HTTPException(status_code=404, detail="file missing")
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(signed)
        base = urlsplit(public_base.rstrip("/"))
        # signed URL is LAN SUPABASE_URL-based; swap scheme+host for the base
        url = urlunsplit((base.scheme, base.netloc, parts.path, parts.query, ""))
        return RedirectResponse(url, status_code=302, headers=headers)

    return await range_stream_response(store, loc.key, mime, request, headers)
