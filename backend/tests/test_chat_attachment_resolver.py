"""G2 — chat_attachment_resolver tests.

Verifies dispatch by kind (image/video/pdf), failure isolation,
and the per-turn cap.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.multimodal import AttachmentKind
from app.schemas.ai_library_chat import AttachmentRequest
from app.services import chat_attachment_resolver as resolver


# ─── Empty / cap behavior ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_requests_returns_empty():
    result = await resolver.resolve_attachments([])
    assert result.attachments == []
    assert result.failures == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_turn_cap_drops_excess():
    """More than MAX_ATTACHMENTS_PER_TURN images → cap silently."""
    n = resolver.MAX_ATTACHMENTS_PER_TURN + 5
    reqs = [
        AttachmentRequest(kind="image", url=f"https://x.com/{i}.png")
        for i in range(n)
    ]
    result = await resolver.resolve_attachments(reqs)
    assert len(result.attachments) == resolver.MAX_ATTACHMENTS_PER_TURN


# ─── image kind ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_pass_through():
    req = AttachmentRequest(
        kind="image",
        url="https://x.com/a.png",
        alt_text="diagram",
        mime="image/png",
    )
    result = await resolver.resolve_attachments([req])
    assert len(result.attachments) == 1
    a = result.attachments[0]
    assert a.kind == AttachmentKind.IMAGE
    assert a.url == "https://x.com/a.png"
    assert a.alt_text == "diagram"
    assert a.mime == "image/png"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_with_data_url_passes_through():
    req = AttachmentRequest(
        kind="image", data_url="data:image/png;base64,abc",
    )
    result = await resolver.resolve_attachments([req])
    assert len(result.attachments) == 1
    assert result.attachments[0].data_url == "data:image/png;base64,abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_with_no_url_or_data_url_is_failure():
    req = AttachmentRequest(kind="image", alt_text="orphan")
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1
    assert result.failures[0].kind == "image"


# ─── video kind ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_calls_extract_frames(monkeypatch):
    """Video resolves via Q2's extract_frames."""
    from pathlib import Path as _P
    from app.agent_framework.multimodal import Attachment, AttachmentKind
    from app.services.video_frame_extractor import FrameExtractionResult

    # C1: guard requires url to live under base — point base at /tmp
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_attachments = [
        Attachment(kind=AttachmentKind.VIDEO_THUMBNAIL,
                   data_url="data:image/jpeg;base64,frame1"),
        Attachment(kind=AttachmentKind.VIDEO_THUMBNAIL,
                   data_url="data:image/jpeg;base64,frame2"),
    ]
    fake_result = FrameExtractionResult(
        attachments=fake_attachments,
        duration_seconds=30.0,
        sampled_at_seconds=[5.0, 15.0],
    )

    req = AttachmentRequest(kind="video", url="/tmp/clip.mp4")
    with patch(
        "app.services.video_frame_extractor.extract_frames",
        AsyncMock(return_value=fake_result),
    ):
        result = await resolver.resolve_attachments([req])

    assert len(result.attachments) == 2
    assert all(a.kind == AttachmentKind.VIDEO_THUMBNAIL for a in result.attachments)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_failure_recorded_not_raised(monkeypatch):
    from pathlib import Path as _P
    from app.services.video_frame_extractor import FrameExtractionResult
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_result = FrameExtractionResult(
        attachments=[], duration_seconds=None, sampled_at_seconds=[],
        error="ffmpeg not installed",
    )

    req = AttachmentRequest(kind="video", url="/tmp/x.mp4")
    with patch(
        "app.services.video_frame_extractor.extract_frames",
        AsyncMock(return_value=fake_result),
    ):
        result = await resolver.resolve_attachments([req])

    assert result.attachments == []
    assert len(result.failures) == 1
    assert "ffmpeg" in result.failures[0].reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_without_url_is_failure():
    req = AttachmentRequest(kind="video")  # no url
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1


# ─── pdf kind ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pdf_calls_render_pdf(monkeypatch):
    from pathlib import Path as _P
    from app.agent_framework.multimodal import Attachment, AttachmentKind
    from app.services.pdf_renderer import PdfRenderResult
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_attachments = [
        Attachment(kind=AttachmentKind.PDF_PAGE, data_url="data:image/jpeg;base64,p1"),
    ]
    fake_result = PdfRenderResult(
        attachments=fake_attachments, page_count=1, rendered_pages=[1],
    )

    req = AttachmentRequest(kind="pdf", url="/tmp/doc.pdf")
    with patch(
        "app.services.pdf_renderer.render_pdf",
        return_value=fake_result,
    ):
        result = await resolver.resolve_attachments([req])

    assert len(result.attachments) == 1
    assert result.attachments[0].kind == AttachmentKind.PDF_PAGE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pdf_failure_recorded_not_raised(monkeypatch):
    from pathlib import Path as _P
    from app.services.pdf_renderer import PdfRenderResult
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_result = PdfRenderResult(
        attachments=[], page_count=0, rendered_pages=[],
        error="pdf not found: /tmp/missing.pdf",
    )

    req = AttachmentRequest(kind="pdf", url="/tmp/missing.pdf")
    with patch(
        "app.services.pdf_renderer.render_pdf",
        return_value=fake_result,
    ):
        result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1


# ─── unknown kind ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_kind_recorded_as_failure():
    req = AttachmentRequest(kind="audio", url="https://x.com/a.mp3")
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1
    assert "unsupported" in result.failures[0].reason.lower()


# ─── isolation: 1 of N fails ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_failure_others_succeed(monkeypatch):
    """Mixed batch: image OK + bad video → image still resolves."""
    from pathlib import Path as _P
    from app.services.video_frame_extractor import FrameExtractionResult
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    img_req = AttachmentRequest(kind="image", url="https://x.com/i.png")
    vid_req = AttachmentRequest(kind="video", url="/tmp/broken.mp4")
    bad_video = FrameExtractionResult(
        attachments=[], duration_seconds=None, sampled_at_seconds=[],
        error="ffmpeg failed",
    )

    with patch(
        "app.services.video_frame_extractor.extract_frames",
        AsyncMock(return_value=bad_video),
    ):
        result = await resolver.resolve_attachments([img_req, vid_req])

    assert len(result.attachments) == 1  # the image
    assert result.attachments[0].kind == AttachmentKind.IMAGE
    assert len(result.failures) == 1
    assert result.failures[0].kind == "video"


# ─── C1: Path-traversal / SSRF guard ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_path_outside_base_is_rejected(monkeypatch, tmp_path):
    """Critical security path — arbitrary filesystem read via video kind."""
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    req = AttachmentRequest(kind="video", url="/etc/passwd")
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1
    assert "outside chat attachment base" in result.failures[0].reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_path_traversal_is_rejected(monkeypatch, tmp_path):
    """`..` traversal out of base also rejected."""
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path / "base")
    (tmp_path / "base").mkdir()
    req = AttachmentRequest(
        kind="video", url=str(tmp_path / "base" / ".." / "secret.mp4"),
    )
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pdf_path_outside_base_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    req = AttachmentRequest(kind="pdf", url="/etc/shadow")
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_path_inside_base_passes_guard(monkeypatch, tmp_path):
    """Legit upload path under base passes the guard (and reaches the
    extractor — which we mock to confirm it WAS called)."""
    from app.services.video_frame_extractor import FrameExtractionResult
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    legit = tmp_path / "user1" / "abc.mp4"
    legit.parent.mkdir()
    legit.write_bytes(b"fake")

    fake_result = FrameExtractionResult(
        attachments=[], duration_seconds=10.0, sampled_at_seconds=[],
    )
    with patch(
        "app.services.video_frame_extractor.extract_frames",
        AsyncMock(return_value=fake_result),
    ) as m:
        req = AttachmentRequest(kind="video", url=str(legit))
        await resolver.resolve_attachments([req])
        m.assert_awaited_once()  # guard let it through
