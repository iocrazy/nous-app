"""Q3 — PDF page rendering service tests.

Uses a real pypdfium2 in-memory PDF document for end-to-end coverage of
the actual render path. No mocks needed for the PDF library itself.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.agent_framework.multimodal import AttachmentKind
from app.services.media.render import pdf_renderer as pr
@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    """Create a real 3-page PDF on disk via pypdfium2."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(3):
        # Letter size (612x792 pt)
        doc.new_page(612, 792)
    out = tmp_path / "tiny.pdf"
    doc.save(str(out))
    doc.close()
    return out


# ─── Validation ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_file_returns_error():
    result = pr.render_pdf("/nope/does-not-exist.pdf")
    assert result.attachments == []
    assert result.error and "not found" in result.error
    assert result.page_count == 0


@pytest.mark.unit
def test_clamping_dpi_jpeg_max_pages(tiny_pdf):
    """All numeric inputs clamped to safe ranges."""
    result = pr.render_pdf(
        str(tiny_pdf),
        dpi=9999,         # → 300
        jpeg_quality=200, # → 100
        max_pages=999,    # → 50 (but only 3 pages exist)
    )
    assert result.error is None
    assert len(result.attachments) == 3  # only 3 pages exist


# ─── Real rendering ─────────────────────────────────────────────────


@pytest.mark.unit
def test_renders_all_pages_default(tiny_pdf):
    result = pr.render_pdf(str(tiny_pdf))
    assert result.error is None
    assert result.page_count == 3
    assert len(result.attachments) == 3
    assert result.rendered_pages == [1, 2, 3]


@pytest.mark.unit
def test_attachments_are_pdf_page_kind_with_data_url(tiny_pdf):
    result = pr.render_pdf(str(tiny_pdf))
    for a in result.attachments:
        assert a.kind == AttachmentKind.PDF_PAGE
        assert a.mime == "image/jpeg"
        assert a.data_url and a.data_url.startswith("data:image/jpeg;base64,")
        # Decode → non-empty
        import base64
        b64_part = a.data_url.split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert len(decoded) > 100  # real JPEG, not just header


@pytest.mark.unit
def test_max_pages_caps_output(tiny_pdf):
    result = pr.render_pdf(str(tiny_pdf), max_pages=2)
    assert result.page_count == 3
    assert len(result.attachments) == 2
    assert result.rendered_pages == [1, 2]


# ─── Page range ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_page_range_inclusive_subset(tiny_pdf):
    result = pr.render_pdf(str(tiny_pdf), page_range=(2, 3))
    assert result.page_count == 3
    assert result.rendered_pages == [2, 3]
    assert len(result.attachments) == 2


@pytest.mark.unit
def test_page_range_clamps_overflow(tiny_pdf):
    """Range past end clamps to actual page count."""
    result = pr.render_pdf(str(tiny_pdf), page_range=(2, 999))
    assert result.rendered_pages == [2, 3]


@pytest.mark.unit
def test_page_range_negative_start_clamps(tiny_pdf):
    """Negative start clamps to page 1."""
    result = pr.render_pdf(str(tiny_pdf), page_range=(-5, 2))
    assert result.rendered_pages == [1, 2]


# ─── Library missing ────────────────────────────────────────────────


@pytest.mark.unit
def test_pypdfium2_missing_returns_error(tiny_pdf, monkeypatch):
    """Simulate pypdfium2 import failure — should not crash."""
    import sys
    real_pypdfium2 = sys.modules.pop("pypdfium2", None)

    def _broken_import(name, *args, **kwargs):
        if name == "pypdfium2":
            raise ImportError("simulated missing pypdfium2")
        return _orig_import(name, *args, **kwargs)

    import builtins
    _orig_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _broken_import)
    try:
        result = pr.render_pdf(str(tiny_pdf))
        assert result.attachments == []
        assert result.error and "pypdfium2" in result.error
    finally:
        if real_pypdfium2 is not None:
            sys.modules["pypdfium2"] = real_pypdfium2


# ─── Corrupted PDF ──────────────────────────────────────────────────


@pytest.mark.unit
def test_corrupted_pdf_returns_error(tmp_path):
    """Garbage file masquerading as PDF."""
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a real pdf\x00\xff\xfe")
    result = pr.render_pdf(str(bad))
    assert result.attachments == []
    assert result.error  # any error string OK
