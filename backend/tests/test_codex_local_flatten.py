from __future__ import annotations

import pytest

from app.services.codex.flatten import flatten_for_codex

SYS = "You are a script assistant."


@pytest.mark.unit
def test_flatten_labels_roles_and_ends_with_the_instruction():
    prompt, images = flatten_for_codex(
        SYS,
        [
            {"role": "user", "content": "write a logline"},
            {"role": "assistant", "content": "A cat runs for mayor."},
            {"role": "user", "content": "make it darker"},
        ],
    )
    assert prompt == (
        "[System]\n"
        "You are a script assistant.\n\n"
        "[Conversation]\n"
        "User: write a logline\n\n"
        "Assistant: A cat runs for mayor.\n\n"
        "User: make it darker\n\n"
        "Reply to the last user message directly, as plain text. "
        "Do not read or modify any files."
    )
    assert images == []


@pytest.mark.unit
def test_multipart_content_extracts_text_and_image_urls():
    prompt, images = flatten_for_codex(
        "",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://api.nous.ink/x.png"},
                    },
                ],
            }
        ],
    )
    assert "User: describe this" in prompt
    assert "[System]" not in prompt
    assert images == ["https://api.nous.ink/x.png"]


@pytest.mark.unit
def test_tool_role_is_a_bug():
    with pytest.raises(ValueError):
        flatten_for_codex(SYS, [{"role": "tool", "content": "x", "tool_call_id": "1"}])
