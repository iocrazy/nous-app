"""<available_resources> renders when refs present, omits when absent."""

from __future__ import annotations

from app.services.ai.prompts.prompt_composer import render_available_resources


def test_renders_block_for_each_ref():
    refs = [
        {
            "id": "1",
            "name": "story.md",
            "kind": "doc",
            "mime": "text/markdown",
            "size": 2438,
            "scope": "personal",
            "updated_at": "2026-05-24T10:00:00Z",
            "brief": None,
        },
        {
            "id": "2",
            "name": "pitch.mp4",
            "kind": "video",
            "mime": "video/mp4",
            "size": 18_000_000,
            "scope": "team:alpha",
            "updated_at": "2026-05-20T10:00:00Z",
            "brief": "Storyboard pitch",
        },
    ]
    block = render_available_resources(refs)
    assert "<available_resources>" in block
    assert "</available_resources>" in block
    assert 'id="1"' in block
    assert 'name="story.md"' in block
    assert 'name="pitch.mp4"' in block
    assert "ResourceFetch" in block


def test_omits_block_when_no_refs():
    assert render_available_resources([]) == ""
    assert render_available_resources(None) == ""
