"""run_whisper must forward the user's assigned ASR model to WhisperService.

Regression guard for the bug where the non-volcengine branch dropped
`task_assignment` and WhisperService defaulted to 'whisper-1', so any user
model pick (nous:<model> / BYOK) reached the provider as 'whisper-1' →
404 "no active grant for service 'whisper-1' on this key".
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@asynccontextmanager
async def _passthrough_materialize(file_path: str):
    """Stand-in for app.services.library.media_storage.materialize.

    Task C4 wrapped run_whisper's transcribe call in `materialize()` so sb://
    audio sources get streamed to a temp file first. These tests are only
    about model-assignment forwarding (orthogonal concern), so the real
    materialize() — which validates the path lives under DOWNLOAD_PATH — is
    stubbed out with a plain passthrough rather than pulled into scope here.
    Object-store-source materialize behavior is covered by
    test_transcribe_audio_source_storage.py.
    """
    yield Path(file_path)


class _FakeWhisperService:
    """Captures the kwargs run_whisper passes to transcribe_and_save.

    Class-level capture (not instance) because run_whisper constructs the
    service internally; the test can't reach the instance otherwise.
    """

    captured: dict = {}

    def __init__(self, provider_key: str = "openai", provider_config: dict = None):
        type(self).captured["provider_key"] = provider_key
        type(self).captured["provider_config"] = provider_config or {}

    async def transcribe_and_save(
        self,
        resource_id: str,
        audio_path: str,
        language: str = "auto",
        whisper_model: str = "whisper-1",
    ):
        type(self).captured["whisper_model"] = whisper_model
        return SimpleNamespace(language="en", duration=1.0, text="hi", segments=[])


async def _run(task_assignment: str, provider_config: dict) -> str:
    """Invoke run_whisper's non-volcengine branch and return the model that
    reached WhisperService.transcribe_and_save."""
    import app.workflows.ai_transcription as m

    _FakeWhisperService.captured = {}

    # run_whisper is decorated with @DBOS.step. Its underlying function is
    # exposed via __wrapped__; call it directly (no workflow context needed)
    # with a patched WhisperService import.
    fn = getattr(m.run_whisper, "__wrapped__", m.run_whisper)
    with (
        patch(
            "app.services.ai.transcribe.whisper_service.WhisperService",
            _FakeWhisperService,
        ),
        patch(
            "app.services.library.media_storage.materialize",
            _passthrough_materialize,
        ),
    ):
        await fn(
            audio_path="/tmp/audio.mp3",
            resource_id="5",
            provider_key="openai",
            provider_config=provider_config,
            language="auto",
            task_assignment=task_assignment,
        )
    return _FakeWhisperService.captured["whisper_model"]


@pytest.mark.asyncio
async def test_nous_platform_pick_forwards_bare_model():
    """assignment '<provider>:moss-asr' (platform origin) → 'moss-asr'."""
    model = await _run("nous:moss-asr", {"api_key": "k"})
    assert model == "moss-asr"


@pytest.mark.asyncio
async def test_bare_assignment_passes_through():
    """A bare 'whisper-1' assignment (no colon) passes through unchanged."""
    model = await _run("whisper-1", {"api_key": "k"})
    assert model == "whisper-1"


@pytest.mark.asyncio
async def test_empty_assignment_falls_back_to_provider_config_model():
    """Governance-locked → task_assignment='' → fall back to
    provider_config['model']."""
    model = await _run("", {"api_key": "k", "model": "foo"})
    assert model == "foo"


@pytest.mark.asyncio
async def test_all_empty_falls_back_to_whisper_1_default():
    """No assignment and no provider_config model → 'whisper-1' default."""
    model = await _run("", {"api_key": "k"})
    assert model == "whisper-1"


def test_assignment_model_helper_shapes():
    """Direct unit coverage of the shared derivation helper."""
    from app.workflows.ai_transcription import _assignment_model

    # prefixed → part after first colon
    assert _assignment_model("openai:moss-asr", {}, "whisper-1") == "moss-asr"
    # bare → unchanged (idempotent split)
    assert _assignment_model("whisper-1", {}, "whisper-1") == "whisper-1"
    # empty → provider_config model
    assert _assignment_model("", {"model": "foo"}, "whisper-1") == "foo"
    # empty everywhere → default
    assert _assignment_model("", {}, "whisper-1") == "whisper-1"
    # volcengine branch parity: default="" preserves prior fall-through
    assert _assignment_model("", {}, "") == ""
    assert _assignment_model("volcengine:seed-asr", {}, "") == "seed-asr"
