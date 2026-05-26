"""G2 — Resolve user-supplied AttachmentRequest[] into multimodal
Attachment[] suitable for build_user_message().

Routes by kind:
  - image  → data_url / public URL pass-through, OR read off the shared
             library and inline as a base64 data URL
  - video  → Q2 extract_frames (returns N VIDEO_THUMBNAIL attachments)
  - pdf    → Q3 render_pdf (returns N PDF_PAGE attachments)

Failure isolation: any single attachment's resolution failure is
logged + skipped. The chat turn proceeds with the remaining ones.
A small failure summary is returned alongside so the chat service
can surface "I couldn't read 1 of your 3 attachments" if desired.

C1 SECURITY: image/video/pdf attachments whose ``url`` is a filesystem
path (i.e. not a public http(s)/data: URL) are resolved under
CHAT_ATTACHMENT_BASE_DIR. ``..`` traversal, absolute paths outside the
base, and symlinks pointing outside the base are all rejected — without
the guard a malicious caller could probe arbitrary files (/etc/passwd,
/proc/self/environ, project secrets). Pass-through url/data_url images
skip the guard since the bytes are model-supplied / publicly fetchable.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.agent_framework.multimodal import Attachment, AttachmentKind
from app.core.config import settings
from app.schemas.ai_library_chat import AttachmentRequest

# Cap how much per-turn we'll do — protects against a user pasting 12
# huge PDFs and OOM-ing the chat process.
MAX_VIDEO_FRAMES_PER_ATTACHMENT = 6
MAX_PDF_PAGES_PER_ATTACHMENT = 8
MAX_ATTACHMENTS_PER_TURN = 8

# Per-image cap for inlining as base64 data URL. The upload endpoint
# allows 50MB, but inlining 8×50MB into one turn would peak the worker
# at ~1.5GB; 10MB per image is generous for vision models (most accept
# ≤20MB base64 per image) and keeps the worst-case bounded.
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024

# C1: chat-upload attachments are stored as temp resources on the shared
# library volume (settings.DOWNLOAD_PATH), which BOTH the gateway and the
# worker container mount. A stored attachment's ``url`` is a path relative
# to this base (e.g. "personal/<uid>/temp/x.png"). image/video/pdf file
# paths must resolve strictly under this base; public http(s)/data: URLs
# bypass the path check and go straight to the model. The env override
# remains for tests.
CHAT_ATTACHMENT_BASE_DIR = Path(
    os.environ.get(
        "CHAT_ATTACHMENT_BASE_DIR",
        settings.DOWNLOAD_PATH,
    )
).resolve()


def _is_public_url(value: str) -> bool:
    """True for URLs the model can fetch directly (http/https) or inline
    base64 data URLs. These bypass the filesystem path check."""
    v = (value or "").strip().lower()
    return v.startswith(("http://", "https://", "data:"))


def _resolve_under_base(ref: str, base: Path) -> Optional[Path]:
    """Resolve a stored attachment ``ref`` to an absolute path strictly
    under ``base`` (the shared library). ``ref`` is normally relative to
    the base (e.g. "personal/<uid>/temp/x.png"), but an absolute ref or
    any ``..`` traversal that escapes the base resolves to ``None``.

    Both sides are resolved before comparison so that macOS symlinks like
    /tmp → /private/tmp don't cause false negatives.
    """
    try:
        candidate = (base / ref).resolve(strict=False)
        resolved_base = base.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        return None
    return candidate


def _file_to_data_url(path: Path, mime: Optional[str]) -> str:
    """Read a file off the shared library and inline it as a base64 data
    URL so the model — and the worker container, which has no public URL
    for the file — can consume it directly. Refuses files larger than
    ``MAX_INLINE_IMAGE_BYTES`` so a single turn cannot blow up the
    worker's memory."""
    size = path.stat().st_size
    if size > MAX_INLINE_IMAGE_BYTES:
        raise ValueError(
            f"image too large to inline ({size} bytes > "
            f"{MAX_INLINE_IMAGE_BYTES}); use a public URL instead"
        )
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    resolved_mime = mime or mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{resolved_mime};base64,{b64}"


@dataclass(frozen=True)
class ResolutionFailure:
    """One attachment that couldn't be resolved. Surfaced to the chat
    service so the user gets feedback ("I couldn't read X.pdf")."""

    request_index: int
    kind: str
    reason: str


@dataclass(frozen=True)
class ResolveResult:
    attachments: List[Attachment]
    failures: List[ResolutionFailure]


async def resolve_attachments(
    requests: List[AttachmentRequest],
) -> ResolveResult:
    """Resolve a list of AttachmentRequest into multimodal Attachment[].

    Returns the flattened attachment list plus a parallel list of
    failures. The caller decides whether to surface failures to the
    user (chat service does so on a best-effort basis).
    """
    if not requests:
        return ResolveResult(attachments=[], failures=[])

    # Hard cap before doing any work
    capped = requests[:MAX_ATTACHMENTS_PER_TURN]
    if len(requests) > MAX_ATTACHMENTS_PER_TURN:
        logger.warning(
            f"[chat_attachment_resolver] capping {len(requests)} attachments "
            f"→ {MAX_ATTACHMENTS_PER_TURN}"
        )

    out: List[Attachment] = []
    failures: List[ResolutionFailure] = []

    for idx, req in enumerate(capped):
        try:
            resolved = await _resolve_one(req)
        except Exception as exc:
            logger.warning(
                f"[chat_attachment_resolver] req[{idx}] kind={req.kind} "
                f"failed: {exc}"
            )
            failures.append(
                ResolutionFailure(
                    request_index=idx,
                    kind=req.kind,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        if not resolved:
            failures.append(
                ResolutionFailure(
                    request_index=idx,
                    kind=req.kind,
                    reason="produced no usable attachments",
                )
            )
            continue
        out.extend(resolved)

    return ResolveResult(attachments=out, failures=failures)


async def _resolve_one(req: AttachmentRequest) -> List[Attachment]:
    """Dispatch one request → one or more Attachments."""
    kind = (req.kind or "").strip().lower()

    if kind == "image":
        return await _resolve_image(req)

    if kind == "video":
        if not req.url:
            return []
        # C1: the url is a path on the shared library → resolve it under
        # CHAT_ATTACHMENT_BASE_DIR and reject anything that escapes the
        # base (traversal / arbitrary file probing).
        abs_path = _resolve_under_base(req.url, CHAT_ATTACHMENT_BASE_DIR)
        if abs_path is None:
            raise ValueError(
                f"video path outside chat attachment base dir: {req.url!r}"
            )
        # Run frame extraction in a thread (ffmpeg is blocking via
        # subprocess.communicate)
        from app.services.media.render.video_frame_extractor import extract_frames

        result = await extract_frames(
            str(abs_path),
            num_frames=MAX_VIDEO_FRAMES_PER_ATTACHMENT,
        )
        if result.error:
            raise RuntimeError(result.error)
        return list(result.attachments)

    if kind == "pdf":
        if not req.url:
            return []
        # C1: same path-traversal defense for pdf
        abs_path = _resolve_under_base(req.url, CHAT_ATTACHMENT_BASE_DIR)
        if abs_path is None:
            raise ValueError(f"pdf path outside chat attachment base dir: {req.url!r}")
        from app.services.media.render.pdf_renderer import render_pdf

        # Sync (CPU-bound pdfium decode); thread-pool offload
        result = await asyncio.to_thread(
            render_pdf,
            str(abs_path),
            max_pages=MAX_PDF_PAGES_PER_ATTACHMENT,
        )
        if result.error:
            raise RuntimeError(result.error)
        return list(result.attachments)

    # Unknown kind — caller error, surface as failure
    raise ValueError(f"unsupported attachment kind: {kind!r}")


async def _resolve_image(req: AttachmentRequest) -> List[Attachment]:
    """Resolve an image attachment for the model.

    - inline ``data_url`` → pass through as-is.
    - public http(s) ``url`` → pass through (the model fetches it).
    - shared-library file path ``url`` → read off the shared volume and
      inline as a base64 data URL, so both the gateway and the worker
      can serve it to the model without a public URL.
    """
    if req.data_url:
        return [
            Attachment(
                kind=AttachmentKind.IMAGE,
                data_url=req.data_url,
                mime=req.mime,
                alt_text=req.alt_text,
            )
        ]
    if not req.url:
        return []
    if _is_public_url(req.url):
        return [
            Attachment(
                kind=AttachmentKind.IMAGE,
                url=req.url,
                mime=req.mime,
                alt_text=req.alt_text,
            )
        ]
    # Otherwise the url is a path on the shared library.
    abs_path = _resolve_under_base(req.url, CHAT_ATTACHMENT_BASE_DIR)
    if abs_path is None or not abs_path.is_file():
        raise ValueError(
            f"image path outside chat attachment base dir or missing: {req.url!r}"
        )
    # File-read + base64 encode happens on a worker thread to avoid
    # blocking the event loop on a large NAS read.
    data_url = await asyncio.to_thread(_file_to_data_url, abs_path, req.mime)
    logger.debug(
        f"[chat_attachment_resolver] inlined image {req.url!r} "
        f"({abs_path.stat().st_size} bytes) as data URL"
    )
    return [
        Attachment(
            kind=AttachmentKind.IMAGE,
            data_url=data_url,
            mime=req.mime,
            alt_text=req.alt_text,
        )
    ]


__all__ = [
    "ResolveResult",
    "ResolutionFailure",
    "resolve_attachments",
    "MAX_ATTACHMENTS_PER_TURN",
    "MAX_VIDEO_FRAMES_PER_ATTACHMENT",
    "MAX_PDF_PAGES_PER_ATTACHMENT",
]
