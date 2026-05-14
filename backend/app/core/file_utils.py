# backend/app/core/file_utils.py

"""
Shared upload-handling utilities.

Centralizes filename sanitization and streamed disk writes so every upload
endpoint behaves identically. Before this module, resources_service and
projects_service each carried their own copy of `_sanitize_filename` and each
did `content = await file.read()` — loading the entire upload into RAM and
trusting the client-supplied `file.size` for the size limit.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional

import aiofiles
import filetype
from fastapi import HTTPException, UploadFile

# Windows-reserved characters plus C0 control bytes. Path separators are in
# here, so a value passed through sanitize_filename can never carry a
# directory component.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MB

# Single source of truth for the resource/project upload size cap. Routers
# may keep a cheap pre-check on the (untrusted) Content-Length header, but
# stream_upload_to_disk is the real enforcement point.
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


async def stream_upload_to_disk(
    file: UploadFile,
    target_path: Path,
    max_size: int,
    chunk_size: int = _STREAM_CHUNK_SIZE,
) -> tuple[int, str]:
    """Stream an UploadFile to *target_path* in chunks.

    Returns ``(size_bytes, sha256_hex)`` computed from the bytes actually
    written — never from the client-supplied ``file.size`` (the multipart
    Content-Length header), which an attacker can understate to slip a huge
    body past a naive pre-check.

    Enforces *max_size* server-side: if the stream exceeds it, the partial
    file is removed and HTTPException(413) is raised. Any other failure mid
    stream also removes the partial file before re-raising, so a failed
    upload never leaves a half-written file on disk.
    """
    hasher = hashlib.sha256()
    total = 0
    try:
        async with aiofiles.open(target_path, "wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File too large. Maximum size is "
                            f"{max_size // (1024 * 1024)} MB."
                        ),
                    )
                hasher.update(chunk)
                await out.write(chunk)
    except BaseException:
        target_path.unlink(missing_ok=True)
        raise
    return total, hasher.hexdigest()


def sniff_mime(path: Path) -> Optional[str]:
    """Sniff the real MIME type from file content (reads the first ~261 bytes).

    Returns None for formats `filetype` cannot identify — notably plain text
    (txt / csv / json / md). That is intentional: the risk this closes is a
    binary executable masquerading as media via a forged multipart
    Content-Type (declared `video/mp4`, actually a PE/ELF), which would
    otherwise be handed to ffmpeg / the transcode pipeline. Text-ish
    documents have no such codepath, so callers fall back to the declared
    type for them.
    """
    try:
        kind = filetype.guess(str(path))
    except Exception:
        return None
    return kind.mime if kind is not None else None


def sanitize_filename(filename: str) -> str:
    """Reduce an uploaded filename to a safe basename.

    - strips any path component via ``Path(...).name`` so ``../../etc/passwd``
      or ``C:\\Windows\\x`` cannot escape the upload directory
    - replaces Windows-reserved characters and C0 control bytes with ``_``
    - strips leading/trailing dots and spaces, so ``.htaccess``,
      trailing-space tricks, and the special names ``.`` / ``..`` cannot
      survive
    - falls back to ``untitled`` when nothing usable remains (empty input,
      all-separator input, etc.)
    - caps the result at 255 characters
    """
    name = Path(filename or "").name
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name.strip(". ")
    if not name:
        name = "untitled"
    return name[:255]
