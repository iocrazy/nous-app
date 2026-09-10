"""Outpaint transform shared by the canvas derive path (+ 6f AI generative path).

Extends encoded image bytes by per-side padding.

Fill modes
----------
deterministic (default)
    Blur-fill via ``extend_canvas``.  Always free, no external deps.

ai
    Routes ``prompt`` + padding to a nous-center outpaint workflow identified
    by the ``NOUS_CENTER_OUTPAINT_SLUG`` setting.  Falls back silently to the
    deterministic fill when the slug is absent or nous-center is unavailable —
    callers and users never see an error from the AI path.

Once the real nous outpaint workflow ships, set ``NOUS_CENTER_OUTPAINT_SLUG``
in backend/.env (or compose environment) and the AI path becomes live with no
code changes.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from loguru import logger

from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_outpaint import (
    OutpaintError,
    Padding,
    extend_canvas,
)

# Same (status_code, detail) error contract as the other derive services.
OutpaintDeriveError = DeriveError

# Settings key for the nous-center outpaint workflow slug.
# Keep unset (or empty) to use deterministic blur fill for all requests.
_OUTPAINT_SLUG_KEY = "NOUS_CENTER_OUTPAINT_SLUG"


async def run_outpaint_via_nous(
    *,
    settings: Any,
    prompt: str,
    source_bytes: bytes,
    padding: Padding,
    mime_type: str = "image/png",
) -> bytes:
    """Dispatch to the nous-center outpaint workflow and return image bytes.

    The workflow slug is read from ``NOUS_CENTER_OUTPAINT_SLUG`` in settings.
    Raises ``NousCenterNotConfigured`` when the slug or nous-center credentials
    are absent so the caller can fall back gracefully to deterministic fill.

    Expected nous workflow output shape::

        {"image_b64": "<base64-encoded PNG/WebP bytes>"}

    Note (v1 seam): ``source_bytes`` are not yet forwarded to nous-center —
    ``run_nous_workflow`` sends only the prompt text.  A future iteration will
    upload the source via the workflow's binary-input channel once the real
    outpaint workflow contract is finalised.
    """
    from app.services.canvas.nous_center_runner import (
        NousCenterNotConfigured,
        run_nous_workflow,
    )

    slug_raw = getattr(settings, _OUTPAINT_SLUG_KEY, None)
    slug = str(slug_raw).strip() if slug_raw else None
    if not slug:
        raise NousCenterNotConfigured(
            f"{_OUTPAINT_SLUG_KEY} is not configured for this deployment"
        )

    # Encode padding context into the prompt so the workflow has spatial info.
    enriched_prompt = (
        f"{prompt}\n"
        f"[outpaint l={padding.left:.2f} t={padding.top:.2f} "
        f"r={padding.right:.2f} b={padding.bottom:.2f}]"
    )

    result = await run_nous_workflow(
        settings=settings,
        workflow_slug=slug,
        prompt=enriched_prompt,
    )

    if not result.ok:
        raise RuntimeError(f"nous outpaint workflow failed: {result.error}")

    # Decode base64-encoded image bytes from the workflow output.
    try:
        outputs = json.loads(result.text)
        image_b64 = outputs.get("image_b64") or ""
        if image_b64:
            return base64.b64decode(image_b64)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    raise RuntimeError(
        f"nous outpaint workflow completed but returned no decodable image "
        f"bytes (text_len={len(result.text)})"
    )


async def extend_image(
    file_bytes: bytes,
    mime_type: str | None,
    padding: Padding,
    *,
    prompt: str | None = None,
    mode: str = "deterministic",
    label: str = "",
) -> bytes:
    """Extend encoded image bytes by ``padding``.

    ``mode='ai'`` with a prompt tries nous-center and falls back to the
    deterministic blur fill on any failure; ``label`` only feeds the log line.
    """
    if prompt and mode == "ai":
        return await _fill_via_ai_or_fallback(
            file_bytes, mime_type, padding, prompt, label
        )
    return _deterministic_fill(file_bytes, mime_type, padding)


def _deterministic_fill(
    file_bytes: bytes, mime_type: str | None, padding: Padding
) -> bytes:
    try:
        return extend_canvas(file_bytes, padding, mime_type=mime_type)
    except OutpaintError as exc:
        raise OutpaintDeriveError(status_code=400, detail=str(exc)) from exc


async def _fill_via_ai_or_fallback(
    file_bytes: bytes,
    mime_type: str | None,
    padding: Padding,
    prompt: str,
    label: str,
) -> bytes:
    """Try AI fill; fall back silently to deterministic on any failure."""
    from app.core.config import settings

    try:
        image_bytes = await run_outpaint_via_nous(
            settings=settings,
            prompt=prompt,
            source_bytes=file_bytes,
            padding=padding,
            mime_type=mime_type or "image/png",
        )
        logger.info(
            f"outpaint AI path succeeded source={label} "
            f"slug_key={_OUTPAINT_SLUG_KEY}"
        )
        return image_bytes
    except Exception as exc:
        logger.info(
            f"outpaint AI unavailable, falling back to deterministic fill "
            f"source={label} reason={exc!r}"
        )

    return _deterministic_fill(file_bytes, mime_type, padding)
