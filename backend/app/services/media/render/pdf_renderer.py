"""Q3 — PDF page rendering → multimodal Attachment[].

Used by:
  - chat agents that receive a PDF attachment (vision-capable models can
    read rendered page images)
  - any pipeline needing per-page screenshots without poppler/system deps

Strategy:
  - pypdfium2 (pure-Python wheel — no system-level poppler/MuPDF needed)
  - Render each page to PIL Image at requested DPI
  - Encode as JPEG → data_url
  - Returns Attachment list (kind=PDF_PAGE)

Sizing notes:
  - 96 DPI ≈ 800 px wide for US Letter (small but readable for vision)
  - 120 DPI ≈ 1000 px wide (default — clear text, ~80 KB JPEG/page)
  - 150+ DPI rapidly inflates payload — only useful for fine print
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.agent_framework.multimodal import Attachment, AttachmentKind

DEFAULT_DPI = 120
DEFAULT_JPEG_QUALITY = 80
DEFAULT_MAX_PAGES = 20  # cap on attachment count to stay under payload limits


@dataclass(frozen=True)
class PdfRenderResult:
    """Bundle of page attachments + diagnostics."""

    attachments: List[Attachment]
    page_count: int
    """Total pages in the PDF (may be > len(attachments) if max_pages was hit)."""
    rendered_pages: List[int]
    """1-indexed page numbers actually rendered (parallel to attachments)."""
    error: Optional[str] = None


def _image_to_data_url(img, jpeg_quality: int) -> Optional[str]:
    """PIL.Image → base64 data URL (JPEG)."""
    try:
        buf = io.BytesIO()
        # Convert RGBA → RGB for JPEG (no alpha channel)
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:
        logger.warning(f"[PdfRenderer] image encode failed: {exc}")
        return None


def render_pdf(
    pdf_path: str,
    *,
    dpi: int = DEFAULT_DPI,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_range: Optional[tuple[int, int]] = None,
) -> PdfRenderResult:
    """Render a PDF's pages to JPEG attachments.

    Synchronous (CPU-bound — pdfium does decode + raster in C). Caller
    should run inside ``asyncio.to_thread`` if invoked from async code.

    Args:
        pdf_path: filesystem path to the PDF
        dpi: render resolution (clamped 36..300)
        jpeg_quality: 1..100 (clamped)
        max_pages: hard cap on rendered pages (clamped 1..50)
        page_range: optional (start, end) 1-indexed inclusive. None = all.

    Returns:
        PdfRenderResult with attachments + diagnostics. On failure
        returns empty attachments + error string (graceful degrade).
    """
    dpi = max(36, min(300, dpi))
    jpeg_quality = max(1, min(100, jpeg_quality))
    max_pages = max(1, min(50, max_pages))

    src = Path(pdf_path)
    if not src.exists() or not src.is_file():
        return PdfRenderResult(
            attachments=[],
            page_count=0,
            rendered_pages=[],
            error=f"pdf not found: {pdf_path}",
        )

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        return PdfRenderResult(
            attachments=[],
            page_count=0,
            rendered_pages=[],
            error=f"pypdfium2 not installed: {exc}",
        )

    try:
        doc = pdfium.PdfDocument(str(src))
    except Exception as exc:
        return PdfRenderResult(
            attachments=[],
            page_count=0,
            rendered_pages=[],
            error=f"failed to open pdf: {exc}",
        )

    page_count = len(doc)
    if page_count == 0:
        return PdfRenderResult(
            attachments=[],
            page_count=0,
            rendered_pages=[],
            error="pdf has no pages",
        )

    # Resolve page indices (0-indexed for pdfium)
    if page_range:
        start = max(1, page_range[0]) - 1
        end = min(page_count, page_range[1])
        page_indices = list(range(start, end))
    else:
        page_indices = list(range(page_count))

    page_indices = page_indices[:max_pages]

    attachments: List[Attachment] = []
    rendered: List[int] = []

    # pdfium uses scale = dpi / 72
    scale = dpi / 72.0

    try:
        for idx in page_indices:
            try:
                page = doc[idx]
                pil_image = page.render(scale=scale).to_pil()
                data_url = _image_to_data_url(pil_image, jpeg_quality)
                if data_url:
                    attachments.append(
                        Attachment(
                            kind=AttachmentKind.PDF_PAGE,
                            data_url=data_url,
                            mime="image/jpeg",
                            alt_text=f"PDF page {idx + 1}",
                        )
                    )
                    rendered.append(idx + 1)
            except Exception as exc:
                logger.warning(f"[PdfRenderer] page {idx + 1} render failed: {exc}")
                continue
    finally:
        try:
            doc.close()
        except Exception:
            pass

    return PdfRenderResult(
        attachments=attachments,
        page_count=page_count,
        rendered_pages=rendered,
    )


__all__ = [
    "PdfRenderResult",
    "render_pdf",
    "DEFAULT_DPI",
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_MAX_PAGES",
]
