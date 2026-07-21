"""WhisperService — speaker persistence + hotwords forwarding.

Guards the two enhancements the OpenAI-compatible transcription path added:
  1. ``segments_json`` carries ``speaker`` ONLY when the provider diarized —
     a None speaker omits the key entirely, so old transcripts keep their exact
     {start, end, text} shape (no migration).
  2. Hotwords riding on ``provider_config`` reach ``provider.transcribe`` as a
     call kwarg, and are absent when not configured.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.providers.ai_provider import (
    TranscriptResult,
    TranscriptSegment,
)
from app.services.ai.transcribe.whisper_service import WhisperService


def _result_with(segments):
    return TranscriptResult(
        text="full text", segments=segments, language="zh", duration=7.0
    )


async def _run_and_capture(tmp_path, provider_config, segments):
    """Run transcribe_and_save against a fake provider + repo; return
    (captured segments_json, kwargs the provider.transcribe received)."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake-audio")

    transcribe_mock = AsyncMock(return_value=_result_with(segments))
    fake_provider = SimpleNamespace(transcribe=transcribe_mock)

    saved: dict = {}

    async def _fake_save(resource_id, payload):
        saved["resource_id"] = resource_id
        saved["payload"] = payload

    svc = WhisperService(provider_key="openai", provider_config=provider_config)
    svc._repo = SimpleNamespace(save_transcript=_fake_save)

    with patch(
        "app.services.ai.transcribe.whisper_service.AIProviderFactory.get_provider",
        return_value=fake_provider,
    ):
        await svc.transcribe_and_save(
            resource_id="42",
            audio_path=str(audio),
            language="auto",
            whisper_model="moss-asr",
        )

    return saved["payload"]["segments"], transcribe_mock.call_args.kwargs


@pytest.mark.asyncio
async def test_segments_json_includes_speaker_and_omits_when_none(tmp_path):
    segments = [
        TranscriptSegment(start=0.0, end=2.0, text="a", speaker="S01"),
        TranscriptSegment(start=2.0, end=4.0, text="b"),  # no speaker
    ]
    segments_json, _ = await _run_and_capture(tmp_path, {"api_key": "k"}, segments)

    assert segments_json[0] == {"start": 0.0, "end": 2.0, "text": "a", "speaker": "S01"}
    # None speaker → key omitted, old shape preserved byte-for-byte.
    assert segments_json[1] == {"start": 2.0, "end": 4.0, "text": "b"}
    assert "speaker" not in segments_json[1]


@pytest.mark.asyncio
async def test_hotwords_forwarded_from_provider_config(tmp_path):
    _, kwargs = await _run_and_capture(
        tmp_path,
        {"api_key": "k", "hotwords": "Ada, RLHF"},
        [TranscriptSegment(start=0.0, end=1.0, text="a")],
    )
    assert kwargs.get("hotwords") == "Ada, RLHF"


@pytest.mark.asyncio
async def test_no_hotwords_key_when_config_has_none(tmp_path):
    _, kwargs = await _run_and_capture(
        tmp_path,
        {"api_key": "k"},
        [TranscriptSegment(start=0.0, end=1.0, text="a")],
    )
    assert "hotwords" not in kwargs
