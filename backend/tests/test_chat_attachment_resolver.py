"""G2 — chat_attachment_resolver tests.

Verifies dispatch by kind (image/video/pdf), failure isolation,
and the per-turn cap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.multimodal import AttachmentKind
from app.schemas.ai_library_chat import AttachmentRequest
from app.services.ai.chat import chat_attachment_resolver as resolver

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
        AttachmentRequest(kind="image", url=f"https://x.com/{i}.png") for i in range(n)
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
        kind="image",
        data_url="data:image/png;base64,abc",
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
    from app.services.media.render.video_frame_extractor import FrameExtractionResult

    # C1: guard requires url to live under base — point base at /tmp
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_attachments = [
        Attachment(
            kind=AttachmentKind.VIDEO_THUMBNAIL,
            data_url="data:image/jpeg;base64,frame1",
        ),
        Attachment(
            kind=AttachmentKind.VIDEO_THUMBNAIL,
            data_url="data:image/jpeg;base64,frame2",
        ),
    ]
    fake_result = FrameExtractionResult(
        attachments=fake_attachments,
        duration_seconds=30.0,
        sampled_at_seconds=[5.0, 15.0],
    )

    req = AttachmentRequest(kind="video", url="/tmp/clip.mp4")
    with patch(
        "app.services.media.render.video_frame_extractor.extract_frames",
        AsyncMock(return_value=fake_result),
    ):
        result = await resolver.resolve_attachments([req])

    assert len(result.attachments) == 2
    assert all(a.kind == AttachmentKind.VIDEO_THUMBNAIL for a in result.attachments)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_failure_recorded_not_raised(monkeypatch):
    from pathlib import Path as _P

    from app.services.media.render.video_frame_extractor import FrameExtractionResult

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_result = FrameExtractionResult(
        attachments=[],
        duration_seconds=None,
        sampled_at_seconds=[],
        error="ffmpeg not installed",
    )

    req = AttachmentRequest(kind="video", url="/tmp/x.mp4")
    with patch(
        "app.services.media.render.video_frame_extractor.extract_frames",
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
    from app.services.media.render.pdf_renderer import PdfRenderResult

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_attachments = [
        Attachment(kind=AttachmentKind.PDF_PAGE, data_url="data:image/jpeg;base64,p1"),
    ]
    fake_result = PdfRenderResult(
        attachments=fake_attachments,
        page_count=1,
        rendered_pages=[1],
    )

    req = AttachmentRequest(kind="pdf", url="/tmp/doc.pdf")
    with patch(
        "app.services.media.render.pdf_renderer.render_pdf",
        return_value=fake_result,
    ):
        result = await resolver.resolve_attachments([req])

    assert len(result.attachments) == 1
    assert result.attachments[0].kind == AttachmentKind.PDF_PAGE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pdf_failure_recorded_not_raised(monkeypatch):
    from pathlib import Path as _P

    from app.services.media.render.pdf_renderer import PdfRenderResult

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    fake_result = PdfRenderResult(
        attachments=[],
        page_count=0,
        rendered_pages=[],
        error="pdf not found: /tmp/missing.pdf",
    )

    req = AttachmentRequest(kind="pdf", url="/tmp/missing.pdf")
    with patch(
        "app.services.media.render.pdf_renderer.render_pdf",
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

    from app.services.media.render.video_frame_extractor import FrameExtractionResult

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", _P("/tmp"))

    img_req = AttachmentRequest(kind="image", url="https://x.com/i.png")
    vid_req = AttachmentRequest(kind="video", url="/tmp/broken.mp4")
    bad_video = FrameExtractionResult(
        attachments=[],
        duration_seconds=None,
        sampled_at_seconds=[],
        error="ffmpeg failed",
    )

    with patch(
        "app.services.media.render.video_frame_extractor.extract_frames",
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
        kind="video",
        url=str(tmp_path / "base" / ".." / "secret.mp4"),
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
    from app.services.media.render.video_frame_extractor import FrameExtractionResult

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    legit = tmp_path / "user1" / "abc.mp4"
    legit.parent.mkdir()
    legit.write_bytes(b"fake")

    fake_result = FrameExtractionResult(
        attachments=[],
        duration_seconds=10.0,
        sampled_at_seconds=[],
    )
    with patch(
        "app.services.media.render.video_frame_extractor.extract_frames",
        AsyncMock(return_value=fake_result),
    ) as m:
        req = AttachmentRequest(kind="video", url=str(legit))
        await resolver.resolve_attachments([req])
        m.assert_awaited_once()  # guard let it through


# ─── shared-library (DOWNLOAD_PATH) relative-path resolution ──────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_relative_path_inlined_as_data_url(monkeypatch, tmp_path):
    """An image url that is a path relative to the shared library is read
    off disk and inlined as a base64 data URL (so the worker can serve it
    without a public URL)."""
    import base64

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    content = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-BYTES"
    img = tmp_path / "personal" / "u1" / "temp" / "x.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(content)

    req = AttachmentRequest(
        kind="image", url="personal/u1/temp/x.png", mime="image/png"
    )
    result = await resolver.resolve_attachments([req])

    assert len(result.attachments) == 1
    a = result.attachments[0]
    assert a.kind == AttachmentKind.IMAGE
    assert not a.url  # not a pass-through url
    expected = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
    assert a.data_url == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_relative_path_outside_base_is_failure(monkeypatch, tmp_path):
    """A non-public image url that escapes the base is rejected (failure),
    not read."""
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    req = AttachmentRequest(kind="image", url="/etc/passwd")
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1
    assert result.failures[0].kind == "image"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_relative_path_missing_file_is_failure(monkeypatch, tmp_path):
    """A relative image url under the base but with no file present is a
    failure (not a crash)."""
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    req = AttachmentRequest(kind="image", url="personal/u1/temp/missing.png")
    result = await resolver.resolve_attachments([req])
    assert result.attachments == []
    assert len(result.failures) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_relative_path_resolved_under_base(monkeypatch, tmp_path):
    """A video url relative to the shared library is resolved to its
    absolute path before being handed to the extractor."""
    from pathlib import Path as _P

    from app.services.media.render.video_frame_extractor import FrameExtractionResult

    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    clip = tmp_path / "team" / "9" / "temp" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"fake")

    fake_result = FrameExtractionResult(
        attachments=[], duration_seconds=1.0, sampled_at_seconds=[]
    )
    with patch(
        "app.services.media.render.video_frame_extractor.extract_frames",
        AsyncMock(return_value=fake_result),
    ) as m:
        req = AttachmentRequest(kind="video", url="team/9/temp/clip.mp4")
        await resolver.resolve_attachments([req])

    m.assert_awaited_once()
    called_with = _P(m.call_args.args[0]).resolve()
    assert called_with == clip.resolve()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_too_large_to_inline_is_failure(monkeypatch, tmp_path):
    """An image whose file exceeds MAX_INLINE_IMAGE_BYTES is rejected as a
    failure (not inlined) — otherwise the worker would peak its memory on
    an 8×50MB turn."""
    monkeypatch.setattr(resolver, "CHAT_ATTACHMENT_BASE_DIR", tmp_path)
    monkeypatch.setattr(resolver, "MAX_INLINE_IMAGE_BYTES", 8)
    img = tmp_path / "big.png"
    img.write_bytes(b"x" * 100)

    req = AttachmentRequest(kind="image", url="big.png", mime="image/png")
    result = await resolver.resolve_attachments([req])

    assert result.attachments == []
    assert len(result.failures) == 1
    assert "too large" in result.failures[0].reason.lower()


# ─── dict re-hydration regression (2026-07-06 "图片要显示") ─────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normalized_dict_rehydrates_to_request_and_resolves():
    """Regression: the chat service normalizes incoming attachments to plain
    dicts (via model_dump) for the resource_ref split, then must re-hydrate
    them to AttachmentRequest before calling resolve_attachments — passing
    the raw dicts crashed with "'dict' object has no attribute 'kind'" and
    silently degraded every image turn to text-only."""
    original = AttachmentRequest(
        kind="image",
        url="https://x.com/a.png",
        mime="image/png",
        alt_text="screenshot.png",
        resource_id="310812366953241",
    )
    # Same normalization the service applies (model_dump keeps None fields).
    as_dict = original.model_dump()
    rehydrated = AttachmentRequest.model_validate(as_dict)

    result = await resolver.resolve_attachments([rehydrated])

    assert result.failures == []
    assert len(result.attachments) == 1
    assert result.attachments[0].url == "https://x.com/a.png"


# ─── sb:// unified-storage paths (2026-07-25 storage migration) ─────


def _fake_get_stream(data: bytes):
    """Replace ObjectStore.get_stream with a canned async byte stream."""

    async def _gen(self, key, **kwargs):
        yield data

    return _gen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_sb_path_inlined_as_data_url():
    """Since the unified-storage switch flipped, /chat-attachments/upload
    returns sb://library/... paths. The resolver must fetch the object
    off the store and inline it as a base64 data URL — before this fix
    it rejected the path as "outside chat attachment base dir"."""
    import base64

    from app.services.library.media_storage import ObjectStore

    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    req = AttachmentRequest(
        kind="image",
        url="sb://library/t42/ab/cd/abcd1234.png",
        mime="image/png",
    )
    with patch.object(ObjectStore, "get_stream", _fake_get_stream(png)):
        result = await resolver.resolve_attachments([req])

    assert result.failures == []
    assert len(result.attachments) == 1
    a = result.attachments[0]
    assert a.data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(a.data_url.split(",", 1)[1]) == png


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_sb_path_unknown_bucket_rejected():
    """Only the content-addressed media buckets (library / chat-media) may
    be fetched — an arbitrary bucket in a crafted sb:// ref must fail
    WITHOUT touching the object store."""
    from app.services.library.media_storage import ObjectStore

    def _must_not_fetch(self, key, **kwargs):
        raise AssertionError("object store must not be touched")

    req = AttachmentRequest(kind="image", url="sb://secrets/anything.png")
    with patch.object(ObjectStore, "get_stream", _must_not_fetch):
        result = await resolver.resolve_attachments([req])

    assert result.attachments == []
    assert len(result.failures) == 1
    assert "bucket" in result.failures[0].reason.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_video_sb_path_materializes_to_local_temp():
    """sb:// video refs must be materialized to a real local file before
    ffmpeg sees them — extract_frames must receive a temp path, never the
    sb:// string."""
    from app.agent_framework.multimodal import Attachment, AttachmentKind
    from app.services.library.media_storage import ObjectStore
    from app.services.media.render.video_frame_extractor import FrameExtractionResult

    fake_result = FrameExtractionResult(
        attachments=[
            Attachment(
                kind=AttachmentKind.VIDEO_THUMBNAIL,
                data_url="data:image/jpeg;base64,frame1",
            )
        ],
        duration_seconds=10.0,
        sampled_at_seconds=[5.0],
    )
    seen: dict = {}

    async def _extract(path, num_frames):
        # The materialized temp file must really exist WITH the object's
        # bytes at call time — a bogus joined path (base + "sb://...")
        # would blow up here, not pass vacuously.
        from pathlib import Path as _P

        seen["path"] = path
        seen["bytes"] = _P(path).read_bytes()
        return fake_result

    req = AttachmentRequest(kind="video", url="sb://library/t42/ab/cd/abcd.mp4")
    with (
        patch.object(ObjectStore, "get_stream", _fake_get_stream(b"vid-bytes")),
        patch(
            "app.services.media.render.video_frame_extractor.extract_frames",
            AsyncMock(side_effect=_extract),
        ),
    ):
        result = await resolver.resolve_attachments([req])

    assert result.failures == []
    assert len(result.attachments) == 1
    assert not seen["path"].startswith("sb://")
    assert seen["path"].endswith(".mp4")
    assert seen["bytes"] == b"vid-bytes"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pdf_sb_path_materializes_to_local_temp():
    """Same materialize-first contract for pdf → pdfium."""
    from app.agent_framework.multimodal import Attachment, AttachmentKind
    from app.services.library.media_storage import ObjectStore
    from app.services.media.render.pdf_renderer import PdfRenderResult

    fake_result = PdfRenderResult(
        attachments=[
            Attachment(
                kind=AttachmentKind.PDF_PAGE, data_url="data:image/jpeg;base64,p1"
            )
        ],
        page_count=1,
        rendered_pages=[1],
    )
    seen: dict = {}

    def _render(path, max_pages):
        from pathlib import Path as _P

        seen["path"] = path
        seen["bytes"] = _P(path).read_bytes()
        return fake_result

    req = AttachmentRequest(kind="pdf", url="sb://library/t42/ab/cd/abcd.pdf")
    with (
        patch.object(ObjectStore, "get_stream", _fake_get_stream(b"%PDF-1.4")),
        patch(
            "app.services.media.render.pdf_renderer.render_pdf",
            side_effect=_render,
        ),
    ):
        result = await resolver.resolve_attachments([req])

    assert result.failures == []
    assert len(result.attachments) == 1
    assert not seen["path"].startswith("sb://")
    assert seen["path"].endswith(".pdf")
    assert seen["bytes"] == b"%PDF-1.4"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_sb_path_respects_inline_size_cap(monkeypatch):
    """The MAX_INLINE_IMAGE_BYTES guard applies to store-fetched images
    exactly as it does to shared-volume ones."""
    from app.services.library.media_storage import ObjectStore

    monkeypatch.setattr(resolver, "MAX_INLINE_IMAGE_BYTES", 8)
    req = AttachmentRequest(
        kind="image", url="sb://library/t42/ab/cd/big.png", mime="image/png"
    )
    with patch.object(ObjectStore, "get_stream", _fake_get_stream(b"x" * 100)):
        result = await resolver.resolve_attachments([req])

    assert result.attachments == []
    assert len(result.failures) == 1
    assert "too large" in result.failures[0].reason.lower()


# ── vision gate: dropped attachments must be reported ─────────────────────
#
# 真链验收第 4 项: 模型不支持视觉时 build_user_message 把附件摊成文字占位符,
# 而 attachment_failures 是空的 —— 用户传了图、模型没收到、没有任何一处说过。
# 这里钉住"丢了就必须有类型化回显"。

from app.services.ai.chat.chat_attachment_resolver import (  # noqa: E402
    ResolutionFailure,
    ResolveResult,
    vision_gate_failures,
)


def _res(n_atts: int = 1, failures=None) -> ResolveResult:
    # 只有 .attachments 的真假与 .failures 的 index 被读到,用轻量占位即可。
    return ResolveResult(attachments=[object()] * n_atts, failures=list(failures or []))


@pytest.mark.unit
def test_vision_gate_reports_every_dropped_image():
    reqs = [{"kind": "image"}, {"kind": "image"}]
    out = vision_gate_failures(reqs, _res(2), supports_vision=False)

    assert [f.request_index for f in out] == [0, 1]
    assert {f.reason for f in out} == {"model_no_vision"}
    assert {f.kind for f in out} == {"image"}


@pytest.mark.unit
def test_vision_capable_model_reports_nothing():
    """反向对照 —— 没有它,一个"永远追加失败"的实现同样会绿。"""
    reqs = [{"kind": "image"}]
    assert vision_gate_failures(reqs, _res(1), supports_vision=True) == []


@pytest.mark.unit
def test_nothing_resolved_reports_nothing():
    """没有任何附件成功解析时,视觉闸门没丢掉任何东西,不该凭空造失败。"""
    reqs = [{"kind": "image"}]
    assert vision_gate_failures(reqs, _res(0), supports_vision=False) == []


@pytest.mark.unit
def test_already_failed_request_is_not_double_reported():
    """解析阶段就失败的那张已经在列表里了,而且它的原因更准确 —— 不能报两次。"""
    reqs = [{"kind": "image"}, {"kind": "image"}]
    prior = [ResolutionFailure(request_index=0, kind="image", reason="boom")]
    out = vision_gate_failures(reqs, _res(1, prior), supports_vision=False)

    assert [f.request_index for f in out] == [1]


@pytest.mark.unit
def test_one_pdf_is_one_failure_not_one_per_page():
    """一个 PDF 会扇出成 N 张页图。按 request 记,否则用户传 1 个文件却被告知
    "8 个附件失败"。"""
    reqs = [{"kind": "pdf"}]
    out = vision_gate_failures(reqs, _res(8), supports_vision=False)

    assert len(out) == 1
    assert out[0].request_index == 0 and out[0].kind == "pdf"


@pytest.mark.unit
def test_non_visual_kinds_are_not_reported_as_no_vision():
    """audio 不是被"看不见"挡掉的,挂上 model_no_vision 是错误归因。"""
    reqs = [{"kind": "audio"}, {"kind": "image"}]
    out = vision_gate_failures(reqs, _res(2), supports_vision=False)

    assert [f.request_index for f in out] == [1]
