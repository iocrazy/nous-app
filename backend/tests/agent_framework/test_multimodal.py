"""Q1 — multi-modal user message construction."""
from __future__ import annotations

import pytest

from app.agent_framework.multimodal import (
    Attachment,
    AttachmentKind,
    build_user_message,
    flatten_attachments_to_text,
    flatten_to_text,
    looks_like_data_url,
    sniff_supports_vision,
)


# ─── sniff_supports_vision ────────────────────────────────────────────


@pytest.mark.unit
def test_known_vision_models():
    for m in ["gpt-4o", "claude-sonnet-4-6", "qwen-vl-plus", "qwen2.5-vl-72b"]:
        assert sniff_supports_vision(m), f"{m} should be vision-capable"


@pytest.mark.unit
def test_text_only_models():
    for m in ["gpt-3.5-turbo", "qwen-max", "qwen-plus", "deepseek-chat"]:
        assert not sniff_supports_vision(m), f"{m} should NOT be vision-capable"


@pytest.mark.unit
def test_empty_model_no_vision():
    assert not sniff_supports_vision("")
    assert not sniff_supports_vision(None)  # type: ignore[arg-type]


# ─── build_user_message ───────────────────────────────────────────────


@pytest.mark.unit
def test_no_attachments_returns_string_content():
    msg = build_user_message("hello", target_model="gpt-4o")
    assert msg == {"role": "user", "content": "hello"}


@pytest.mark.unit
def test_text_only_model_with_attachments_flattens():
    """Text-only model + image → degrade to placeholder text."""
    att = Attachment(kind=AttachmentKind.IMAGE, url="https://x.com/img.png", alt_text="diagram")
    msg = build_user_message("look:", [att], target_model="qwen-max")
    assert isinstance(msg["content"], str)
    assert "diagram" in msg["content"] or "img.png" in msg["content"]
    assert "look:" in msg["content"]


@pytest.mark.unit
def test_vision_model_with_image_returns_multipart():
    att = Attachment(kind=AttachmentKind.IMAGE, url="https://x.com/i.png")
    msg = build_user_message("look:", [att], target_model="gpt-4o")
    assert isinstance(msg["content"], list)
    assert msg["content"][0] == {"type": "text", "text": "look:"}
    assert msg["content"][1]["type"] == "image_url"
    assert msg["content"][1]["image_url"]["url"] == "https://x.com/i.png"


@pytest.mark.unit
def test_vision_model_no_text_just_image():
    att = Attachment(kind=AttachmentKind.IMAGE, url="https://x.com/i.png")
    msg = build_user_message("", [att], target_model="claude-sonnet-4-6")
    # No text part; only image
    assert isinstance(msg["content"], list)
    assert all(p.get("type") == "image_url" for p in msg["content"])


@pytest.mark.unit
def test_pdf_page_treated_as_image():
    att = Attachment(kind=AttachmentKind.PDF_PAGE, url="https://x.com/page1.png")
    msg = build_user_message("p1", [att], target_model="gpt-4o")
    parts = msg["content"]
    assert any(p.get("type") == "image_url" for p in parts if isinstance(p, dict))


@pytest.mark.unit
def test_data_url_attachment_used_when_no_url():
    att = Attachment(
        kind=AttachmentKind.IMAGE,
        data_url="data:image/png;base64,iVBORw0KGgo=",
    )
    msg = build_user_message("inline", [att], target_model="gpt-4o")
    parts = msg["content"]
    assert isinstance(parts, list)
    assert parts[1]["image_url"]["url"].startswith("data:image/png")


@pytest.mark.unit
def test_attachment_without_url_or_data_skipped():
    """Empty attachment shouldn't crash; just skipped from output."""
    att = Attachment(kind=AttachmentKind.IMAGE, alt_text="no url")
    msg = build_user_message("hi", [att], target_model="gpt-4o")
    parts = msg["content"]
    # Only the text part, skip the empty attachment
    assert len([p for p in parts if isinstance(p, dict) and p.get("type") == "image_url"]) == 0


# ─── flatten_attachments_to_text ──────────────────────────────────────


@pytest.mark.unit
def test_flatten_includes_alt_text():
    atts = [
        Attachment(kind=AttachmentKind.IMAGE, url="https://x", alt_text="my chart"),
    ]
    out = flatten_attachments_to_text("explain:", atts)
    assert "my chart" in out
    assert "explain:" in out


@pytest.mark.unit
def test_flatten_falls_back_to_url_when_no_alt():
    atts = [Attachment(kind=AttachmentKind.IMAGE, url="https://example.com/x.png")]
    out = flatten_attachments_to_text("", atts)
    assert "x.png" in out


# ─── flatten_to_text (downstream consumers) ─────────────────────────


@pytest.mark.unit
def test_flatten_string_content_passthrough():
    msg = {"role": "user", "content": "plain text"}
    assert flatten_to_text(msg) == "plain text"


@pytest.mark.unit
def test_flatten_multipart_drops_image_keeps_text_with_marker():
    msg = {"role": "user", "content": [
        {"type": "text", "text": "before"},
        {"type": "image_url", "image_url": {"url": "https://x.com/i.png"}},
        {"type": "text", "text": "after"},
    ]}
    out = flatten_to_text(msg)
    assert "before" in out
    assert "after" in out
    assert "[image:" in out
    assert "i.png" in out


@pytest.mark.unit
def test_flatten_unknown_part_type_marked():
    msg = {"role": "user", "content": [
        {"type": "fictional_future_type", "data": "..."}
    ]}
    out = flatten_to_text(msg)
    assert "[fictional_future_type]" in out


# ─── looks_like_data_url ─────────────────────────────────────────────


@pytest.mark.unit
def test_data_url_detection():
    assert looks_like_data_url("data:image/png;base64,abc")
    assert looks_like_data_url("data:application/pdf;base64,xyz")
    assert not looks_like_data_url("https://example.com/x.png")
    assert not looks_like_data_url("")
