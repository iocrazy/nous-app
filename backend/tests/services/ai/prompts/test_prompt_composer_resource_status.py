"""``<available_resources>`` renders the AI processing status (RECON#3).

Without it the agent has no way to say "your video is still transcribing,
ask me again in a minute" — it can only call ResourceFetch and relay a flat
failure (spec 2026-08-17 §1-F1).
"""

from __future__ import annotations

from app.services.ai.prompts.prompt_composer import render_available_resources


def _video(**over):
    ref = {
        "id": "2",
        "name": "pitch.mp4",
        "kind": "video",
        "mime": "video/mp4",
        "size": 18_000_000,
        "scope": "personal",
        "updated_at": "2026-08-01T00:00:00Z",
        "brief": None,
        "transcript_status": "processing",
        "summary_status": "none",
    }
    ref.update(over)
    return ref


def test_video_ref_renders_both_statuses():
    block = render_available_resources([_video()])
    assert 'status="transcript:processing summary:none"' in block


def test_completed_media_still_reports_its_status():
    block = render_available_resources(
        [_video(transcript_status="completed", summary_status="completed")]
    )
    assert 'status="transcript:completed summary:completed"' in block


def test_audio_ref_also_gets_status():
    block = render_available_resources(
        [_video(kind="audio", mime="audio/mpeg", name="voice.mp3")]
    )
    assert 'status="transcript:processing summary:none"' in block


def test_in_flight_status_adds_a_retry_instruction():
    """The whole point: the agent must be told what 'processing' means, not
    left to guess from an opaque attribute."""
    block = render_available_resources([_video()])
    lowered = block.lower()
    assert "processing" in lowered
    assert "retry" in lowered


def test_doc_ref_gets_no_status_attribute():
    """Transcript/summary are video/audio concepts; rendering
    ``transcript:none`` on a markdown file is noise that would also churn the
    prompt-cache key for every doc mention."""
    doc = {
        "id": "1",
        "name": "story.md",
        "kind": "doc",
        "mime": "text/markdown",
        "size": 2438,
        "scope": "personal",
        "updated_at": "2026-05-24T10:00:00Z",
        "brief": None,
        "transcript_status": "none",
        "summary_status": "none",
    }
    block = render_available_resources([doc])
    assert "status=" not in block


def test_refs_without_status_keys_render_exactly_as_before():
    """Back-compat: callers that predate the resolver change (and the
    existing composer tests) must produce a byte-identical block."""
    legacy = _video()
    legacy.pop("transcript_status")
    legacy.pop("summary_status")
    block = render_available_resources([legacy])
    assert "status=" not in block
    assert "retry" not in block.lower()
    assert 'name="pitch.mp4"' in block
