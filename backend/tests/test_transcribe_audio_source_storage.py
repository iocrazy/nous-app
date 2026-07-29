"""ai_transcription 两条 ASR 分支改对象存储感知(Task C4,存储全量迁 S3 PR-1)。

Task C3 后 extract_audio_path 会是 sb://,而 run_whisper / _run_volcengine_asr
之前都通过纯文件系统的 AudioSourceResolver 解析路径,落到 sb:// 会
RuntimeError("audio file missing")。本文件覆盖:

  1. whisper 分支 + sb:// 源 —— materialize 拉临时文件,transcribe_and_save
     收到的是本地临时路径,不是 sb:// 值。
  2. whisper 分支 + 文件系统源 —— materialize 零开销透传,原路径不变。
  3. volcengine 分支 + sb:// 源 —— ObjectStore.signed_url 拿到的 LAN URL 被换成
     STORAGE_SIGNED_URL_PUBLIC_BASE 的 host 再传给 VolcengineASRService。
  4. volcengine 分支 + sb:// 源但未配置 STORAGE_SIGNED_URL_PUBLIC_BASE —— raise
     明确错误,不拼坏 URL 让火山静默失败。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_SB_AUDIO = "sb://library/t42/ab/cd/deadbeef1234.m4a"


class _FakeWhisperService:
    """镜像 test_transcribe_whisper_model_assignment.py 的捕获约定。"""

    captured: dict = {}

    def __init__(self, provider_key: str = "openai", provider_config: dict = None):
        pass

    async def transcribe_and_save(
        self,
        resource_id: str,
        audio_path: str,
        language: str = "auto",
        whisper_model: str = "whisper-1",
    ):
        type(self).captured["audio_path"] = audio_path
        return SimpleNamespace(language="en", duration=1.0, text="hi", segments=[])


async def _run_whisper_branch(audio_path: str):
    import app.workflows.ai_transcription as m

    _FakeWhisperService.captured = {}
    fn = getattr(m.run_whisper, "__wrapped__", m.run_whisper)
    with patch(
        "app.services.ai.transcribe.whisper_service.WhisperService",
        _FakeWhisperService,
    ):
        await fn(
            audio_path=audio_path,
            resource_id="5",
            provider_key="openai",
            provider_config={"api_key": "k"},
            language="auto",
            task_assignment="whisper-1",
        )
    return _FakeWhisperService.captured["audio_path"]


# ═══════════════════════════════════════════════════════════════════
# whisper 分支
# ═══════════════════════════════════════════════════════════════════


async def test_whisper_sb_source_forwards_materialized_temp_path(tmp_path):
    """sb:// 源:materialize 产出的临时路径被传给 transcribe_and_save,不是 sb:// 值。"""
    local = tmp_path / "materialized_audio.m4a"
    local.write_bytes(b"FAKE_AUDIO_BYTES")

    @asynccontextmanager
    async def _fake_materialize(file_path):
        assert file_path == _SB_AUDIO
        yield local

    with patch("app.services.library.media_storage.materialize", _fake_materialize):
        received = await _run_whisper_branch(_SB_AUDIO)

    assert received == str(local)
    assert not received.startswith("sb://")


async def test_whisper_filesystem_source_passes_through_unchanged(
    tmp_path, monkeypatch
):
    """文件系统源:materialize 零开销透传,transcribe_and_save 收到原路径不变。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"FAKE_AUDIO_BYTES")

    received = await _run_whisper_branch(str(audio))

    assert received == str(audio)


# ═══════════════════════════════════════════════════════════════════
# volcengine 分支
# ═══════════════════════════════════════════════════════════════════


def _patch_volcengine_service():
    """Patch VolcengineASRService so tests can inspect transcribe_and_save's kwargs."""
    captured: dict = {}

    class _FakeVolcengineASRService:
        def __init__(self, app_id="", access_token="", asr_resource_id=""):
            captured["asr_resource_id"] = asr_resource_id

        async def transcribe_and_save(self, resource_id, audio_url, audio_format):
            captured["audio_url"] = audio_url
            captured["audio_format"] = audio_format
            captured["resource_id"] = resource_id
            return SimpleNamespace(language="en", duration=1.0, text="hi", segments=[])

    return captured, _FakeVolcengineASRService


async def test_volcengine_sb_source_swaps_signed_url_host(monkeypatch):
    """sb:// 源:ObjectStore.signed_url 返回的 LAN URL 被换成
    STORAGE_SIGNED_URL_PUBLIC_BASE 的 scheme+host 再传给 VolcengineASRService。"""
    from app.core.config import settings
    from app.workflows.ai_transcription import _run_volcengine_asr

    monkeypatch.setattr(
        settings, "STORAGE_SIGNED_URL_PUBLIC_BASE", "https://cn-sb.nous.ink:88"
    )

    captured, fake_service_cls = _patch_volcengine_service()

    async def _fake_signed_url(self, key, *, ttl_seconds=300):
        assert key == "t42/ab/cd/deadbeef1234.m4a"
        return (
            "http://10.0.0.10:8000/storage/v1/object/sign/library/"
            "t42/ab/cd/deadbeef1234.m4a?token=xyz"
        )

    with (
        patch(
            "app.services.library.media_storage.ObjectStore.signed_url",
            _fake_signed_url,
        ),
        patch(
            "app.services.ai.transcribe.volcengine_asr_service.VolcengineASRService",
            fake_service_cls,
        ),
    ):
        await _run_volcengine_asr(
            audio_path=_SB_AUDIO,
            resource_id="5",
            provider_config={"app_id": "a", "api_key": "k"},
            language="auto",
            task_assignment="",
        )

    assert captured["audio_url"] == (
        "https://cn-sb.nous.ink:88/storage/v1/object/sign/library/"
        "t42/ab/cd/deadbeef1234.m4a?token=xyz"
    )


async def test_volcengine_sb_source_without_public_base_raises(monkeypatch):
    """sb:// 源但未配置 STORAGE_SIGNED_URL_PUBLIC_BASE —— 明确 raise,不拼坏 URL。"""
    from app.core.config import settings
    from app.workflows.ai_transcription import _run_volcengine_asr

    monkeypatch.setattr(settings, "STORAGE_SIGNED_URL_PUBLIC_BASE", "")

    captured, fake_service_cls = _patch_volcengine_service()
    signed_url_mock = AsyncMock()

    with (
        patch(
            "app.services.library.media_storage.ObjectStore.signed_url", signed_url_mock
        ),
        patch(
            "app.services.ai.transcribe.volcengine_asr_service.VolcengineASRService",
            fake_service_cls,
        ),
    ):
        with pytest.raises(RuntimeError, match="STORAGE_SIGNED_URL_PUBLIC_BASE"):
            await _run_volcengine_asr(
                audio_path=_SB_AUDIO,
                resource_id="5",
                provider_config={"app_id": "a", "api_key": "k"},
                language="auto",
                task_assignment="",
            )

    signed_url_mock.assert_not_called()
    assert "audio_url" not in captured
