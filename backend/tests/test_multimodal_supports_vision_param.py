"""Pins the new build_user_message signature post-Task 4c.

The old `target_model: str` param was removed in favor of an explicit
`supports_vision: bool` so callers can choose any capability source
(DB-backed registry, env override, hardcoded).
"""

from app.agent_framework.multimodal import (
    Attachment,
    AttachmentKind,
    build_user_message,
)


def _img_att(url: str = "https://x.com/a.png") -> Attachment:
    return Attachment(kind=AttachmentKind.IMAGE, url=url, mime="image/png")


def test_no_attachments_returns_plain_user_text():
    out = build_user_message("hello", None, supports_vision=False)
    assert out == {"role": "user", "content": "hello"}


def test_attachments_with_vision_returns_multipart():
    out = build_user_message("see this", [_img_att()], supports_vision=True)
    assert out["role"] == "user"
    assert isinstance(out["content"], list)
    text_parts = [p for p in out["content"] if p.get("type") == "text"]
    image_parts = [p for p in out["content"] if p.get("type") == "image_url"]
    assert text_parts == [{"type": "text", "text": "see this"}]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "https://x.com/a.png"


def test_attachments_without_vision_degrades_to_text():
    """When the model can't see images, images flatten to text placeholders."""
    out = build_user_message("see this", [_img_att()], supports_vision=False)
    assert out["role"] == "user"
    # Must be a string (no multipart) so a text-only adapter accepts it.
    assert isinstance(out["content"], str)
    # The text should still contain the original message body.
    assert "see this" in out["content"]


def test_supports_vision_defaults_to_false():
    """The default is conservative — a forgetful caller doesn't accidentally
    send image_url parts to a text-only model."""
    out = build_user_message("hi", [_img_att()])
    assert isinstance(out["content"], str)
