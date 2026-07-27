"""AudioSourceResolver —— 合并 ai_transcription 与 whisper_service 的两条解析链。

关键:必须取两者并集。whisper 侧有 glob 兜底,ai_transcription 侧有
isdir/size 守卫,任一丢失都会让某条 provider 路径回退到旧 bug。
"""

import os

import pytest

from app.services.media.audio_source import AudioSourceResolver


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    return AudioSourceResolver()


def test_resolve_absolute_path_passthrough(resolver, tmp_path):
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x" * 16)
    assert resolver.resolve(str(f)) == str(f)


def test_resolve_relative_joins_download_path(resolver, tmp_path):
    (tmp_path / "web").mkdir()
    f = tmp_path / "web" / "audio.m4a"
    f.write_bytes(b"x" * 16)
    assert resolver.resolve("web/audio.m4a") == str(f)


def test_resolve_globs_when_extension_differs(resolver, tmp_path):
    """downloader 存 .mp3 但 DB 记的是 .m4a —— whisper 侧原有的兜底。"""
    (tmp_path / "web").mkdir()
    actual = tmp_path / "web" / "audio.mp3"
    actual.write_bytes(b"x" * 16)
    assert resolver.resolve("web/audio.m4a") == str(actual)


def test_resolve_missing_raises(resolver):
    with pytest.raises(FileNotFoundError):
        resolver.resolve("web/nope.m4a")


def test_assert_playable_rejects_directory(resolver, tmp_path):
    """图集没下载背景音乐时 path 是目录,旧 exists() 放行导致 ASR 报
    opaque 'Invalid audio URI' —— ai_transcription 侧原有的守卫。"""
    d = tmp_path / "dir.m4a"
    d.mkdir()
    with pytest.raises(RuntimeError, match="directory"):
        resolver.assert_playable("dir.m4a")


def test_assert_playable_rejects_empty_file(resolver, tmp_path):
    f = tmp_path / "empty.m4a"
    f.write_bytes(b"")
    with pytest.raises(RuntimeError, match="missing or empty"):
        resolver.assert_playable("empty.m4a")


def test_assert_playable_returns_original_arg(resolver, tmp_path):
    """调用方把返回值原样存回 DB,必须是相对路径而非解析后的绝对路径。"""
    f = tmp_path / "ok.m4a"
    f.write_bytes(b"x" * 16)
    assert resolver.assert_playable("ok.m4a") == "ok.m4a"


def test_assert_playable_accepts_glob_match(resolver, tmp_path):
    """DB 记 .m4a 而磁盘是 .mp3 时,闸门放行 —— 这是合并解析链带来的
    行为变化(原 assert_audio_present_step 没有 glob 兜底,此处会拒绝)。

    放行是安全的:下游 whisper 与 volcengine 现在都会 glob 到同一个候选,
    所以闸门不该比它保护的下游更严格。但这是行为变化,故显式钉住。
    """
    (tmp_path / "web").mkdir()
    actual = tmp_path / "web" / "audio.mp3"
    actual.write_bytes(b"x" * 16)
    assert resolver.assert_playable("web/audio.m4a") == "web/audio.m4a"


def test_to_relative_strips_download_root(resolver, tmp_path):
    assert resolver.to_relative(str(tmp_path / "web" / "a.m4a")) == "web/a.m4a"


def test_to_relative_passthrough_when_outside_root(resolver):
    assert resolver.to_relative("/elsewhere/a.m4a") == "/elsewhere/a.m4a"
