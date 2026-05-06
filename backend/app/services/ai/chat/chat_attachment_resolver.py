"""G2 — Resolve user-supplied AttachmentRequest[] into multimodal
Attachment[] suitable for build_user_message().

Routes by kind:
  - image  → pass-through (Q1 layer handles it)
  - video  → Q2 extract_frames (returns N VIDEO_THUMBNAIL attachments)
  - pdf    → Q3 render_pdf (returns N PDF_PAGE attachments)

Failure isolation: any single attachment's resolution failure is
logged + skipped. The chat turn proceeds with the remaining ones.
A small failure summary is returned alongside so the chat service
can surface "I couldn't read 1 of your 3 attachments" if desired.

C1 SECURITY: video/pdf attachments pass req.url to ffmpeg/pdfium as
filesystem paths. Without validation a malicious caller could probe
arbitrary files (/etc/passwd, /proc/self/environ, secret files in
project mounts). resolve_attachments enforces that any url-as-path
lives strictly under CHAT_ATTACHMENT_BASE_DIR and contains no '..'
traversal. Image attachments are url/data_url only — those go to the
LLM which fetches them itself, so the constraint doesn't apply.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.agent_framework.multimodal import Attachment, AttachmentKind
from app.schemas.ai_library_chat import AttachmentRequest


# Cap how much per-turn we'll do — protects against a user pasting 12
# huge PDFs and OOM-ing the chat process.
MAX_VIDEO_FRAMES_PER_ATTACHMENT = 6
MAX_PDF_PAGES_PER_ATTACHMENT = 8
MAX_ATTACHMENTS_PER_TURN = 8

# C1: video/pdf url must live under this base. Mirrors the constant
# in api/ai_library_router.py — kept in sync via env override for tests.
CHAT_ATTACHMENT_BASE_DIR = Path(
    os.environ.get(
        "CHAT_ATTACHMENT_BASE_DIR",
        "/tmp/mediahub_chat_attachments",
    )
).resolve()


def _path_is_inside_base(candidate: str, base: Path) -> bool:
    """Return True iff ``candidate`` resolves to a path strictly under
    ``base`` after symlink resolution. Returns False on any failure
    (missing file, traversal, absolute path outside base).

    Both candidate and base are resolved before comparison so that
    macOS symlinks like /tmp → /private/tmp don't cause false negatives.
    """
    try:
        resolved = Path(candidate).resolve(strict=False)
        resolved_base = base.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(resolved_base)
    except ValueError:
        return False
    return True


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
        # Pass through — Q1's build_user_message handles image input.
        # Either url or data_url must be set for the LLM to actually
        # see it.
        if not req.url and not req.data_url:
            return []
        return [
            Attachment(
                kind=AttachmentKind.IMAGE,
                url=req.url,
                data_url=req.data_url,
                mime=req.mime,
                alt_text=req.alt_text,
            )
        ]

    if kind == "video":
        if not req.url:
            return []
        # C1: video url is treated as a filesystem path → must live
        # under CHAT_ATTACHMENT_BASE_DIR. Reject anything else to
        # block path traversal / arbitrary file probing.
        if not _path_is_inside_base(req.url, CHAT_ATTACHMENT_BASE_DIR):
            raise ValueError(
                f"video path outside chat attachment base dir: {req.url!r}"
            )
        # Run frame extraction in a thread (ffmpeg is blocking via
        # subprocess.communicate)
        from app.services.media.render.video_frame_extractor import extract_frames
        result = await extract_frames(
            req.url,
            num_frames=MAX_VIDEO_FRAMES_PER_ATTACHMENT,
        )
        if result.error:
            raise RuntimeError(result.error)
        return list(result.attachments)

    if kind == "pdf":
        if not req.url:
            return []
        # C1: same path-traversal defense for pdf
        if not _path_is_inside_base(req.url, CHAT_ATTACHMENT_BASE_DIR):
            raise ValueError(
                f"pdf path outside chat attachment base dir: {req.url!r}"
            )
        from app.services.media.render.pdf_renderer import render_pdf
        # Sync (CPU-bound pdfium decode); thread-pool offload
        result = await asyncio.to_thread(
            render_pdf,
            req.url,
            max_pages=MAX_PDF_PAGES_PER_ATTACHMENT,
        )
        if result.error:
            raise RuntimeError(result.error)
        return list(result.attachments)

    # Unknown kind — caller error, surface as failure
    raise ValueError(f"unsupported attachment kind: {kind!r}")


__all__ = [
    "ResolveResult",
    "ResolutionFailure",
    "resolve_attachments",
    "MAX_ATTACHMENTS_PER_TURN",
    "MAX_VIDEO_FRAMES_PER_ATTACHMENT",
    "MAX_PDF_PAGES_PER_ATTACHMENT",
]
