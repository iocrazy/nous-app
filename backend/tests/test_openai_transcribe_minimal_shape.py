"""OpenAI-compatible transcription — minimal-server response normalization.

The self-hosted moss-asr endpoint answers with the minimal shape
``{"text": "...", "language": null, "usage": {"type": "duration", "seconds": N}}``
— ``duration``/``language`` are present-but-None on the SDK model (so getattr
defaults never engage) and the real length rides in ``usage.seconds``.  The
un-normalized None duration crashed ``f"{result.duration:.1f}"`` downstream
(prod 2026-07-21: DBOSMaxStepRetriesExceeded TypeError NoneType.__format__).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.providers.ai_provider import OpenAIProvider


def _provider_with_response(tmp_path, response):
    provider = OpenAIProvider(api_key="k", base_url="http://x/v1")
    provider._client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=AsyncMock(return_value=response))
        )
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFxxxx")
    return provider, str(audio)


@pytest.mark.asyncio
async def test_transcribe_normalizes_none_duration_and_language(tmp_path):
    response = SimpleNamespace(
        text="hello",
        segments=None,
        language=None,
        duration=None,
        usage={"type": "duration", "seconds": 2},
    )
    provider, audio = _provider_with_response(tmp_path, response)
    result = await provider.transcribe(audio, model="moss-asr")
    assert result.duration == 2.0
    assert result.language == "unknown"
    assert result.segments == []
    # The downstream log formatting must not crash on the result.
    assert f"{result.duration:.1f}" == "2.0"


@pytest.mark.asyncio
async def test_transcribe_usage_namespace_and_all_missing(tmp_path):
    # usage as an object attribute (SDK may model it), no seconds → 0.0
    response = SimpleNamespace(
        text=None,
        segments=None,
        language=None,
        duration=None,
        usage=SimpleNamespace(type="tokens"),
    )
    provider, audio = _provider_with_response(tmp_path, response)
    result = await provider.transcribe(audio, model="moss-asr")
    assert result.duration == 0.0
    assert result.text == ""


@pytest.mark.asyncio
async def test_transcribe_moss_timestamps_segments(tmp_path):
    # moss `timestamps=true` shape: dict segments with start/end/speaker/text
    # and the duration still only in usage.seconds.
    response = SimpleNamespace(
        text="今天天气不错。",
        segments=[
            {"start": 0.0, "end": 6.62, "speaker": "S01", "text": "今天天气不错。"}
        ],
        language=None,
        duration=None,
        usage={"type": "duration", "seconds": 7},
    )
    provider, audio = _provider_with_response(tmp_path, response)
    result = await provider.transcribe(audio, model="moss-asr")
    assert result.duration == 7.0
    assert len(result.segments) == 1
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 6.62
    assert result.segments[0].text == "今天天气不错。"
    # The diarization label is parsed onto the segment.
    assert result.segments[0].speaker == "S01"
    # The request must carry the moss timestamps switch.
    call_kwargs = provider._client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs.get("extra_body") == {"timestamps": True, "merge_segments": True}


@pytest.mark.asyncio
async def test_transcribe_segments_without_speaker_default_none(tmp_path):
    # A verbose_json segment with no `speaker` key → speaker stays None (old
    # OpenAI Whisper / non-diarizing shape is unchanged).
    response = SimpleNamespace(
        text="hi",
        segments=[{"start": 0.0, "end": 1.5, "text": "hi"}],
        language="en",
        duration=1.5,
        usage=None,
    )
    provider, audio = _provider_with_response(tmp_path, response)
    result = await provider.transcribe(audio, model="whisper-1")
    assert result.segments[0].speaker is None


@pytest.mark.asyncio
async def test_transcribe_hotwords_present_sends_context_and_prompt(tmp_path):
    # Non-empty hotwords → moss `context` (extra_body) + OpenAI `prompt`
    # (first-class kwarg), sent together, harmlessly. timestamps stays on.
    response = SimpleNamespace(
        text="hi", segments=None, language="en", duration=1.0, usage=None
    )
    provider, audio = _provider_with_response(tmp_path, response)
    await provider.transcribe(audio, model="whisper-1", hotwords="Ada Lovelace, RLHF")
    call_kwargs = provider._client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs.get("extra_body") == {
        "timestamps": True,
        "merge_segments": True,
        "context": "Ada Lovelace, RLHF",
    }
    assert call_kwargs.get("prompt") == "Ada Lovelace, RLHF"
    # hotwords must never leak through as a raw multipart param.
    assert "hotwords" not in call_kwargs


@pytest.mark.asyncio
async def test_transcribe_no_hotwords_omits_context_and_prompt(tmp_path):
    # Empty / absent hotwords → request is byte-for-byte the pre-feature shape:
    # extra_body carries only `timestamps`, and there is no `prompt`.
    response = SimpleNamespace(
        text="hi", segments=None, language="en", duration=1.0, usage=None
    )
    provider, audio = _provider_with_response(tmp_path, response)
    await provider.transcribe(audio, model="whisper-1", hotwords="   ")
    call_kwargs = provider._client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs.get("extra_body") == {"timestamps": True, "merge_segments": True}
    assert "prompt" not in call_kwargs
    assert "hotwords" not in call_kwargs


@pytest.mark.asyncio
async def test_transcribe_verbose_shape_unchanged(tmp_path):
    # Full whisper verbose_json shape keeps working exactly as before.
    response = SimpleNamespace(
        text="hi",
        segments=[{"start": 0.0, "end": 1.5, "text": "hi"}],
        language="en",
        duration=1.5,
        usage=None,
    )
    provider, audio = _provider_with_response(tmp_path, response)
    result = await provider.transcribe(audio, model="whisper-1")
    assert result.duration == 1.5
    assert result.language == "en"
    assert len(result.segments) == 1
