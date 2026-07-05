"""Vision injection tests for conversation_agent_turn.

Pins the contract that lets group-chat agents SEE uploaded images:
  - image bodies render as "[image: alt]" placeholders (never raw dicts)
  - _inject_image_blocks turns image messages into multimodal content when
    the model supports vision (data URL from local storage bytes)
  - text-only models keep placeholders (no injection)
  - cross-conversation media ids are skipped (exfiltration guard)
  - oversize files are skipped
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat.conversation_agent_turn import (
    _MAX_VISION_IMAGE_BYTES,
    _build_history,
    _inject_image_blocks,
    _render_body,
)

CONV_ID = 42


def _image_msg(gm_id: int = 7, alt: str = "photo.png") -> dict:
    return {
        "sender_type": "user",
        "type": "image",
        "body": {"kind": "image", "generated_media_id": gm_id, "alt": alt},
    }


def _text_msg(text: str = "hello") -> dict:
    return {"sender_type": "user", "type": "text", "body": {"text": text}}


# ── placeholder rendering ────────────────────────────────────────────────────


def test_image_body_renders_placeholder_not_raw_dict() -> None:
    assert (
        _render_body({"kind": "image", "alt": "cat.png"}, "image") == "[image: cat.png]"
    )
    assert _render_body({"kind": "image"}, "image") == "[image]"


# ── injection ────────────────────────────────────────────────────────────────


def _media_row(tmp_path, name="img.png", data=b"\x89PNG fake", conv=CONV_ID, size=None):
    f = tmp_path / name
    f.write_bytes(data)
    return {
        "file_path": name,
        "mime": "image/png",
        "file_size_bytes": size if size is not None else len(data),
        "conversation_id": conv,
    }


@pytest.mark.asyncio
async def test_injects_data_url_for_vision_model(tmp_path) -> None:
    recent = [_text_msg(), _image_msg()]
    history = _build_history(recent)
    row = _media_row(tmp_path)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.db_engine.fetch_one",
            new=AsyncMock(return_value=row),
        ),
        patch("app.services.chat.conversation_agent_turn.settings") as mock_settings,
    ):
        mock_settings.DOWNLOAD_PATH = str(tmp_path)
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 1
    content = history[1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "[image: photo.png]"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # text message untouched
    assert history[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_text_only_model_keeps_placeholders() -> None:
    recent = [_image_msg()]
    history = _build_history(recent)
    with patch(
        "app.services.chat.conversation_agent_turn.model_supports_vision",
        new=AsyncMock(return_value=False),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-lite",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0
    assert history[0]["content"] == "[image: photo.png]"


@pytest.mark.asyncio
async def test_cross_conversation_media_is_skipped(tmp_path) -> None:
    """An id-swapped body pointing at another conversation's media is refused."""
    recent = [_image_msg()]
    history = _build_history(recent)
    row = _media_row(tmp_path, conv=CONV_ID + 1)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.db_engine.fetch_one",
            new=AsyncMock(return_value=row),
        ),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0
    assert history[0]["content"] == "[image: photo.png]"


@pytest.mark.asyncio
async def test_oversize_file_is_skipped(tmp_path) -> None:
    recent = [_image_msg()]
    history = _build_history(recent)
    row = _media_row(tmp_path, size=_MAX_VISION_IMAGE_BYTES + 1)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.db_engine.fetch_one",
            new=AsyncMock(return_value=row),
        ),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0


@pytest.mark.asyncio
async def test_missing_media_row_is_skipped() -> None:
    recent = [_image_msg()]
    history = _build_history(recent)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.db_engine.fetch_one",
            new=AsyncMock(return_value=None),
        ),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0
