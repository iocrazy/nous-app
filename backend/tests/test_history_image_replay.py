"""History image replay — follow-up turns must still see recent images.

Pre-fix behaviour: ai_library_chat_service rebuilt history as
{"role", "content"} text only, so "now look at the top-left corner"
on turn 2 reached the model with no image at all (the bytes lived only
in turn 1's live request). Persisted user messages DO keep display
metadata (kind / resource_id / mime — no bytes), so recent images can
be re-resolved through resources.file_path.

Contract pinned here:
  - the most recent ≤ max_messages user messages with image attachments
    are rebuilt as multipart (re-resolved via resource_id);
  - older image messages and non-vision models stay text-only;
  - resolution failures degrade to text (never break the turn);
  - a global max_images budget caps S3 fetch + payload growth.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.multimodal import Attachment, AttachmentKind
from app.services.ai.chat.history_image_replay import build_history_messages

UID = "b2180063-6860-4f97-9785-ad4eede16064"


def _img_att(rid: str):
    return {"kind": "image", "resource_id": rid, "mime": "image/png", "name": "x.png"}


def _history():
    return [
        {
            "role": "user",
            "content": "old turn with image",
            "attachments": [_img_att("1")],
        },
        {"role": "assistant", "content": "reply 1"},
        {"role": "user", "content": "look at this", "attachments": [_img_att("2")]},
        {"role": "assistant", "content": "it is red"},
        {"role": "user", "content": "plain text turn"},
    ]


def _fake_loader(paths: dict[str, str]):
    async def _load(resource_id: str, user_id: str):
        return paths.get(resource_id)

    return _load


def _fake_resolved(url_token: str):
    async def _resolve(requests):
        from types import SimpleNamespace

        atts = [
            Attachment(
                kind=AttachmentKind.IMAGE,
                data_url=f"data:image/png;base64,{url_token}{i}",
            )
            for i, _ in enumerate(requests)
        ]
        return SimpleNamespace(attachments=atts, failures=[])

    return _resolve


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recent_image_message_rebuilt_as_multipart():
    with patch(
        "app.services.ai.chat.history_image_replay.resolve_attachments",
        AsyncMock(side_effect=_fake_resolved("IMG")),
    ):
        msgs = await build_history_messages(
            _history(),
            user_id=UID,
            supports_vision=True,
            max_messages=1,
            load_file_path=_fake_loader({"2": "sb://library/t1/aa/bb/x.png"}),
        )

    assert len(msgs) == 5
    # Most recent image message (index 2) → multipart with image part.
    replayed = msgs[2]
    assert isinstance(replayed["content"], list)
    kinds = {p["type"] for p in replayed["content"]}
    assert "image_url" in kinds and "text" in kinds
    # Older image message (index 0) stays text (max_messages=1).
    assert isinstance(msgs[0]["content"], str)
    # Non-image messages untouched.
    assert msgs[4]["content"] == "plain text turn"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_vision_model_keeps_text_only():
    msgs = await build_history_messages(
        _history(),
        user_id=UID,
        supports_vision=False,
        load_file_path=_fake_loader({"2": "whatever"}),
    )
    assert all(isinstance(m["content"], str) for m in msgs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolution_failure_degrades_to_text():
    async def _boom(requests):
        raise RuntimeError("s3 down")

    with patch(
        "app.services.ai.chat.history_image_replay.resolve_attachments",
        AsyncMock(side_effect=_boom),
    ):
        msgs = await build_history_messages(
            _history(),
            user_id=UID,
            supports_vision=True,
            load_file_path=_fake_loader({"2": "sb://library/t1/aa/bb/x.png"}),
        )
    assert isinstance(msgs[2]["content"], str)
    assert msgs[2]["content"] == "look at this"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_max_images_budget_caps_replay():
    history = [
        {
            "role": "user",
            "content": "many images",
            "attachments": [_img_att(str(i)) for i in range(10)],
        },
    ]
    seen: dict = {}

    async def _resolve(requests):
        from types import SimpleNamespace

        seen["n"] = len(requests)
        atts = [
            Attachment(kind=AttachmentKind.IMAGE, data_url="data:image/png;base64,A")
            for _ in requests
        ]
        return SimpleNamespace(attachments=atts, failures=[])

    with patch(
        "app.services.ai.chat.history_image_replay.resolve_attachments",
        AsyncMock(side_effect=_resolve),
    ):
        await build_history_messages(
            history,
            user_id=UID,
            supports_vision=True,
            max_images=4,
            load_file_path=_fake_loader({str(i): f"p{i}" for i in range(10)}),
        )
    assert seen["n"] <= 4
