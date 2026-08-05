"""ai_transcription steps hit the ORM (SQLAlchemy 2.0 session) with correct
statements/params after the Phase C task 1 raw-SQL-to-ORM migration (the
prior psycopg→engine migration's raw ``db_engine.fetch_one``/``.execute()``
calls on ``resources``/``parsed_media`` are now ``select()``/``update()``
statements run through ``read_scope()``/``write_scope()``)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest


class _FakeExecuteResult:
    """Stand-in for the awaited ``session.execute(stmt)`` Result on the
    parsed_media+resources JOIN read path — only ``.mappings().first()`` is
    exercised (mirrors the real ``.mappings().first()`` call in
    ``load_transcribe_inputs``)."""

    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeScopeSession:
    """Minimal stand-in for the ORM AsyncSession. Supports BOTH
    ``execute()`` (the parsed_media+resources JOIN read AND the volcengine
    creator_id read use ``execute``/``scalar`` respectively) and ``scalar()``
    (the user_settings.settings_json read) — every read call in this module
    flows through the SAME patched ``read_scope`` seam."""

    def __init__(self, execute_row=None, scalar_value=None):
        self._execute_row = execute_row
        self._scalar_value = scalar_value

    async def execute(self, stmt):
        return _FakeExecuteResult(self._execute_row)

    async def scalar(self, stmt):
        return self._scalar_value


def _fake_read_scope(execute_row=None, scalar_value=None):
    """Factory for a zero-arg ``read_scope()`` replacement yielding a fresh
    session per call — used where the test doesn't care about one of the two
    read shapes (the assertion under test fires before or independently of
    it)."""

    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(execute_row=execute_row, scalar_value=scalar_value)

    return _read_scope


class _UnreachableReadScope:
    """``read_scope()`` replacement whose very entry raises — proves a code
    path never reaches the DB at all (the ``app.db.engine.fetch_one``
    'unreachable' pattern this replaces, updated for the ORM seam)."""

    async def __aenter__(self):
        raise AssertionError(
            "read_scope must not be reached — resolve() has to fail first"
        )

    async def __aexit__(self, *exc):
        return False


def _unreachable_read_scope():
    return _UnreachableReadScope()


class _FakeWriteResult:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class _CapturingWriteSession:
    """Captures every statement passed to ``execute()`` for compile-level
    assertions (column/param inspection) — stands in for ``write_scope()``."""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeWriteResult()


class _BoomWriteSession:
    """``write_scope()`` session whose ``execute()`` always raises — for the
    best-effort-swallow regression test."""

    async def execute(self, stmt):
        raise RuntimeError("pg down")


def _fake_write_scope(session):
    @asynccontextmanager
    async def _write_scope():
        yield session

    return _write_scope


def _compiled(stmt):
    """Compile an UPDATE statement to (sql_text, bound_params) for
    assertions — dialect-agnostic (default/generic compiler), mirroring how
    production runs it (no literal_binds — the same reason the choke point
    itself avoids literal_binds, see app/db/scope.py)."""
    compiled = stmt.compile()
    return str(compiled), dict(compiled.params)


async def test_load_transcribe_inputs_raises_when_no_media():
    import app.workflows.ai_transcription as m

    with patch("app.db.session.read_scope", _fake_read_scope(execute_row=None)):
        with pytest.raises(RuntimeError, match="no parsed_media"):
            await m.load_transcribe_inputs(1, "u")


async def test_load_transcribe_inputs_raises_when_no_audio_path():
    import app.workflows.ai_transcription as m

    # media row exists but has neither extract_audio_path, music_download_path
    # nor download_path
    media_row = {
        "id": 1,
        "download_path": None,
        "extract_audio_path": None,
        "music_download_path": None,
        "platform_id": "p",
        "resource_id": 5,
    }

    with patch(
        "app.db.session.read_scope",
        _fake_read_scope(execute_row=media_row, scalar_value=None),
    ):
        with pytest.raises(RuntimeError, match="no audio_path"):
            await m.load_transcribe_inputs(1, "u")


async def test_load_transcribe_inputs_gallery_uses_music_download_path():
    """Image galleries (douyin 图文) have no video → extract_audio_path is
    always empty. The background music at music_download_path is the only
    on-disk audio; the selector must pick it, NOT fall back to download_path
    (which for a gallery is a directory → ASR 45000006 Invalid audio URI)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.workflows.ai_transcription as m

    media_row = {
        "id": 1,
        "download_path": "global/resources/web/douyin/999",  # a DIRECTORY
        "extract_audio_path": None,
        "music_download_path": "global/resources/web/douyin/999/audio.mp3",
        "platform_id": "douyin",
        "resource_id": 5,
    }
    cfg = SimpleNamespace(
        origin="governance",
        provider_key="volcengine",
        provider_config={"api_key": "k"},
        model="volcengine:bigasr",
    )
    with (
        patch(
            "app.db.session.read_scope",
            _fake_read_scope(execute_row=media_row, scalar_value=None),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers."
            "resolve_transcription_config",
            AsyncMock(return_value=cfg),
        ),
    ):
        out = await m.load_transcribe_inputs(1, "u")

    assert out["audio_path"] == "global/resources/web/douyin/999/audio.mp3"


async def test_load_transcribe_inputs_video_prefers_extract_audio_path():
    """Video downloads have ffmpeg-extracted audio → extract_audio_path wins
    over both music_download_path and download_path (regression guard: the
    gallery fix must not divert the video path)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.workflows.ai_transcription as m

    media_row = {
        "id": 1,
        "download_path": "global/resources/web/douyin/1/video.mp4",
        "extract_audio_path": "global/resources/web/douyin/1/audio.wav",
        "music_download_path": "global/resources/web/douyin/1/bgm.mp3",
        "platform_id": "douyin",
        "resource_id": 5,
    }
    cfg = SimpleNamespace(
        origin="governance",
        provider_key="volcengine",
        provider_config={"api_key": "k"},
        model="volcengine:bigasr",
    )
    with (
        patch(
            "app.db.session.read_scope",
            _fake_read_scope(execute_row=media_row, scalar_value=None),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers."
            "resolve_transcription_config",
            AsyncMock(return_value=cfg),
        ),
    ):
        out = await m.load_transcribe_inputs(1, "u")

    assert out["audio_path"] == "global/resources/web/douyin/1/audio.wav"


async def test_assert_audio_present_rejects_directory(tmp_path):
    """A directory path (gallery whose music was never downloaded, falling back
    to download_path) must fast-fail with a directory-specific message — the
    old os.path.exists guard let directories through to the ASR provider."""
    import app.workflows.ai_transcription as m

    with pytest.raises(RuntimeError, match="audio path is a directory"):
        await m.assert_audio_present_step(str(tmp_path))


async def test_assert_audio_present_rejects_missing(tmp_path):
    """A missing/empty file must fail with the missing-or-empty message."""
    import app.workflows.ai_transcription as m

    missing = str(tmp_path / "nope.mp3")
    with pytest.raises(RuntimeError, match="missing or empty"):
        await m.assert_audio_present_step(missing)


async def test_assert_audio_present_accepts_real_file(tmp_path):
    """A non-empty real file passes through unchanged."""
    import app.workflows.ai_transcription as m

    f = tmp_path / "audio.mp3"
    f.write_bytes(b"\x00\x01\x02")
    assert await m.assert_audio_present_step(str(f)) == str(f)


async def test_assert_audio_present_sb_source_exists_and_nonempty_passes():
    """sb:// audio (Task C3+) must not be routed through the filesystem
    resolver — it should short-circuit via ObjectStore.exists/get_size and
    return the ORIGINAL sb:// path unchanged (the caller re-threads it into
    run_whisper's materialize()/ _run_volcengine_asr's resolve_media_source
    dispatch, both of which expect the sb:// form, not a resolved local
    path)."""
    from unittest.mock import AsyncMock, patch

    import app.workflows.ai_transcription as m

    audio_path = "sb://media-bucket/global/resources/web/douyin/1/audio.wav"
    with (
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.get_size",
            AsyncMock(return_value=1234),
        ),
    ):
        result = await m.assert_audio_present_step(audio_path)

    assert result == audio_path


async def test_assert_audio_present_sb_source_missing_raises():
    """sb:// object absent at dispatch time → RuntimeError, same diagnosable
    'missing or empty' semantics as the filesystem branch, audio_path
    included for diagnosability."""
    from unittest.mock import AsyncMock, patch

    import app.workflows.ai_transcription as m

    audio_path = "sb://media-bucket/global/resources/web/douyin/2/audio.wav"
    with patch(
        "app.services.library.media_storage.ObjectStore.exists",
        AsyncMock(return_value=False),
    ):
        with pytest.raises(RuntimeError, match="missing or empty"):
            await m.assert_audio_present_step(audio_path)


async def test_assert_audio_present_sb_source_zero_size_raises():
    """sb:// object exists but is a zero-byte object (aborted/partial upload)
    → must be treated the same as missing, not passed through to the
    (expensive, network-bound) ASR call."""
    from unittest.mock import AsyncMock, patch

    import app.workflows.ai_transcription as m

    audio_path = "sb://media-bucket/global/resources/web/douyin/3/audio.wav"
    with (
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.get_size",
            AsyncMock(return_value=0),
        ),
    ):
        with pytest.raises(RuntimeError, match="missing or empty"):
            await m.assert_audio_present_step(audio_path)


async def test_mark_transcript_completed_uses_media_id_column():
    import app.workflows.ai_transcription as m

    session = _CapturingWriteSession()
    with patch("app.db.session.write_scope", _fake_write_scope(session)):
        await m.mark_transcript_completed(42)

    assert len(session.statements) == 1
    sql_text, params = _compiled(session.statements[0])
    # Must key on media_id, NOT id — wrong column silently updates 0 rows.
    assert "resources.media_id" in sql_text
    assert "transcript_status" in sql_text
    assert 42 in params.values()
    assert "completed" in params.values()


async def test_mark_transcript_failed_uses_media_id_column():
    """On failure the resource's transcript_status must flip to 'failed' so
    the frontend Transcript tab stops polling. Must key on media_id (not id)
    and never clobber an already-'completed' row."""
    import app.workflows.ai_transcription as m

    session = _CapturingWriteSession()
    with patch("app.db.session.write_scope", _fake_write_scope(session)):
        await m.mark_transcript_failed(42)

    assert len(session.statements) == 1
    sql_text, params = _compiled(session.statements[0])
    assert "resources.media_id" in sql_text
    # Both the SET clause and the "not already completed" guard reference
    # transcript_status — one occurrence of each.
    assert sql_text.count("transcript_status") >= 2
    assert 42 in params.values()
    assert "failed" in params.values()
    assert "completed" in params.values()  # the <> 'completed' guard


async def test_mark_transcript_failed_swallows_write_error():
    """Best-effort: a write hiccup here must not mask the real error the
    workflow is about to record — the step must not raise."""
    import app.workflows.ai_transcription as m

    with patch("app.db.session.write_scope", _fake_write_scope(_BoomWriteSession())):
        await m.mark_transcript_failed(7)  # must not raise


def test_transcription_workflow_marks_failed_before_recording():
    """The direct-transcribe failure path must flip transcript_status='failed'
    (via mark_transcript_failed) inside the except, before returning the
    uniform failure dict — otherwise the resource stays 'none' and the
    frontend spinner never resolves."""
    import inspect

    import app.workflows.ai_transcription as m

    source = inspect.getsource(m.ai_transcription_workflow)
    assert "await mark_transcript_failed(parsed_media_id)" in source
    fail_idx = source.index("await mark_transcript_failed(parsed_media_id)")
    record_idx = source.index("record_workflow_failure(", fail_idx)
    assert fail_idx < record_idx, "must mark failed BEFORE record_workflow_failure"


def test_transcription_workflow_awaits_assert_audio_present():
    """assert_audio_present_step is now an async @DBOS.step() (object-store
    dispatch needs await ObjectStore.exists/get_size) — the workflow body
    call site must await it, or a sync call would hand run_whisper a bare
    coroutine instead of the resolved audio_path (Task C7)."""
    import inspect

    import app.workflows.ai_transcription as m

    source = inspect.getsource(m.ai_transcription_workflow)
    assert "await assert_audio_present_step(" in source


async def test_run_volcengine_asr_raises_when_audio_missing(tmp_path, monkeypatch):
    """volcengine 分支的路径解析失败必须立即抛 FileNotFoundError,不能像
    合并 AudioSourceResolver 之前那样静默把无法解析的相对路径继续传给远端
    ASR API —— 那样产生的是不可诊断的失败,还会白白花掉一次付费调用(且被
    DBOS 的 max_attempts=2 重试策略再打一次)。

    ``_run_volcengine_asr`` 现在在函数体最开头就调用
    ``AudioSourceResolver.resolve()``,早于任何 DB 查询 / HMAC 签名 /
    VolcengineASRService 构造。这里让 ``read_scope`` 断言不可达,
    以证明失败确实发生在触达远端之前,而不是侥幸经过 DB 层才失败。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))

    import app.workflows.ai_transcription as m

    with patch("app.db.session.read_scope", _unreachable_read_scope):
        with pytest.raises(FileNotFoundError):
            await m._run_volcengine_asr(
                audio_path="web/missing.m4a",
                resource_id="5",
                provider_config={},
                language="auto",
            )


@pytest.mark.asyncio
async def test_load_transcribe_inputs_locked_to_catalog_model():
    """Locked TO a platform-catalog model → provider/key/app_id come from the
    catalog (ungated); blank manual api_key must NOT fail-closed."""
    from unittest.mock import AsyncMock

    import app.workflows.ai_transcription as m
    from app.services.ai.governance.ai_governance import AIModuleGovernance

    media_row = {
        "id": 1,
        "download_path": "/tmp/audio.mp3",
        "extract_audio_path": None,
        "platform_id": "p",
        "resource_id": 5,
    }
    gov = AIModuleGovernance(
        allowed=False, base_url="", model="mediahub-volc-asr", api_key=""
    )
    with (
        patch(
            "app.db.session.read_scope",
            _fake_read_scope(execute_row=media_row, scalar_value=None),
        ),
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=gov),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(
                return_value=(
                    "volcengine",
                    {
                        "api_key": "cat-key",
                        "base_url": "",
                        "model": "seed-asr",
                        "app_id": "the-app-id",
                    },
                    "seed-asr",
                )
            ),
        ),
    ):
        out = await m.load_transcribe_inputs(1, "u")

    assert out["provider_key"] == "volcengine"
    assert out["provider_config"]["api_key"] == "cat-key"
    assert out["provider_config"]["app_id"] == "the-app-id"
