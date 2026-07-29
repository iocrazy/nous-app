"""extract_audio_from_video 按源存储后端分派(Task C3,存储全量迁 S3 PR-1)。

下载链路已把 parsed_media.download_path 写成 sb:// 对象存储路径,但
extract_audio_from_video 之前始终把 download_path 当文件系统路径喂 ffmpeg、
且把输出写在视频同目录 —— sb:// 下读写两侧都炸。本文件覆盖：

  1. sb:// 源 —— materialize 拉视频到本地临时文件喂 ffmpeg,输出音频经
     store_local_file 传 S3,extract_audio_path 写回 sb:// 值,scope_id
     从对象键的 t{scope}/... 段解析(不查库,不依赖可能为 None 的 user_id)。
  2. 文件系统源(未迁移视频)—— 完全保持原逻辑:写视频同目录、相对路径回写。
  3. 无音频流(ffmpeg rc!=0)—— 两个分支都仍 AudioExtractError,且 sb:// 分支
     失败时不触碰 store_local_file / repo.update。
  4. 对象键 scope 段缺失/畸形 —— 提前 AudioExtractError,不发起 ffmpeg/网络调用。
"""

from __future__ import annotations

import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.services.library.media_storage import StoredObject


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


class FakeMediaRepo:
    """镜像 test_extract_audio_observability.py 的 FakeRepo 约定。"""

    def __init__(self, media: dict | None) -> None:
        self._media = media
        self.updates: list[tuple[str, dict]] = []

    async def get_by_platform_id(self, platform_id: str):
        return self._media

    async def update(self, platform_id: str, fields: dict):
        self.updates.append((platform_id, fields))
        return True


@pytest.fixture
def patch_repo(monkeypatch: pytest.MonkeyPatch):
    """download_helpers 内 lazy import 的 MediaRepository 换成 FakeMediaRepo。"""

    def _install(media: dict | None) -> FakeMediaRepo:
        repo = FakeMediaRepo(media)
        monkeypatch.setattr(
            "app.repositories.media_repository.MediaRepository", lambda: repo
        )
        return repo

    return _install


# ═══════════════════════════════════════════════════════════════════
# sb:// 源
# ═══════════════════════════════════════════════════════════════════


def test_object_store_source_uploads_audio_to_s3(monkeypatch, tmp_path, patch_repo):
    """成功路径:materialize + ffmpeg + store_local_file,extract_audio_path
    写回 stored.file_path(sb://),mime 为 audio/mp4,scope 从对象键解析。"""
    import app.services.library.media_storage as media_storage
    from app.tasks import download_helpers as dh

    video_key = "t42/ab/cd/deadbeef1234.mp4"
    repo = patch_repo({"id": 1, "download_path": f"sb://library/{video_key}"})

    video_src = tmp_path / "materialized_video.mp4"
    video_src.write_bytes(b"FAKE_VIDEO_BYTES")

    @asynccontextmanager
    async def _fake_materialize(file_path):
        assert file_path == f"sb://library/{video_key}"
        yield video_src

    def _fake_run(cmd, **kwargs):
        output_path = cmd[-1]  # 命令最后一个参数是 ffmpeg 输出路径
        Path(output_path).write_bytes(b"FAKE_AUDIO_BYTES")
        return _FakeCompletedProcess(returncode=0)

    calls: dict = {}

    async def _fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        calls["scope_id"] = scope_id
        calls["source_path"] = source_path
        calls["mime"] = mime
        calls["filename"] = filename
        assert Path(source_path).read_bytes() == b"FAKE_AUDIO_BYTES"
        return StoredObject(
            file_path="sb://library/t42/de/ad/beef.m4a",
            size_bytes=16,
            sha256="deadbeef",
        )

    monkeypatch.setattr(media_storage, "materialize", _fake_materialize)
    monkeypatch.setattr(media_storage, "store_local_file", _fake_store_local_file)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert dh.extract_audio_from_video("p1") is True

    assert calls["scope_id"] == 42
    assert calls["mime"] == "audio/mp4"
    assert calls["filename"] == "audio.m4a"
    assert repo.updates == [
        ("p1", {"extract_audio_path": "sb://library/t42/de/ad/beef.m4a"})
    ]
    # 上传后本地临时音频文件必须被清理,不留垃圾。
    assert not Path(calls["source_path"]).exists()


def test_object_store_source_no_audio_stream_raises(monkeypatch, tmp_path, patch_repo):
    """ffmpeg rc!=0(无音频流)—— 仍 AudioExtractError,且不碰 store_local_file/repo.update。"""
    import app.services.library.media_storage as media_storage
    from app.tasks import download_helpers as dh

    video_key = "t7/ab/cd/deadbeef1234.mp4"
    repo = patch_repo({"id": 1, "download_path": f"sb://library/{video_key}"})

    video_src = tmp_path / "materialized_video.mp4"
    video_src.write_bytes(b"FAKE_VIDEO_BYTES")

    @asynccontextmanager
    async def _fake_materialize(file_path):
        yield video_src

    def _fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            returncode=1, stderr="Output file does not contain any stream"
        )

    async def _fake_store_local_file(**kwargs):
        raise AssertionError("store_local_file must not be called on ffmpeg failure")

    monkeypatch.setattr(media_storage, "materialize", _fake_materialize)
    monkeypatch.setattr(media_storage, "store_local_file", _fake_store_local_file)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(dh.AudioExtractError, match="ffmpeg rc=1"):
        dh.extract_audio_from_video("p1")

    assert repo.updates == []


def test_object_store_source_empty_output_raises(monkeypatch, tmp_path, patch_repo):
    """ffmpeg rc=0 但输出空文件(0 字节)—— 仍 AudioExtractError。"""
    import app.services.library.media_storage as media_storage
    from app.tasks import download_helpers as dh

    video_key = "t7/ab/cd/deadbeef1234.mp4"
    repo = patch_repo({"id": 1, "download_path": f"sb://library/{video_key}"})

    video_src = tmp_path / "materialized_video.mp4"
    video_src.write_bytes(b"FAKE_VIDEO_BYTES")

    @asynccontextmanager
    async def _fake_materialize(file_path):
        yield video_src

    def _fake_run(cmd, **kwargs):
        output_path = cmd[-1]
        Path(output_path).write_bytes(b"")  # 空输出
        return _FakeCompletedProcess(returncode=0)

    async def _fake_store_local_file(**kwargs):
        raise AssertionError("store_local_file must not be called on empty output")

    monkeypatch.setattr(media_storage, "materialize", _fake_materialize)
    monkeypatch.setattr(media_storage, "store_local_file", _fake_store_local_file)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(dh.AudioExtractError, match="missing/empty"):
        dh.extract_audio_from_video("p1")

    assert repo.updates == []


def test_object_store_source_bad_scope_key_raises_before_ffmpeg(
    monkeypatch, tmp_path, patch_repo
):
    """对象键没有 t{scope}/ 段(畸形)—— 提前 AudioExtractError,不发起
    materialize/ffmpeg 调用(scope 解析在 I/O 之前)。"""
    import app.services.library.media_storage as media_storage
    from app.tasks import download_helpers as dh

    repo = patch_repo(
        {"id": 1, "download_path": "sb://library/badsegment/deadbeef.mp4"}
    )

    async def _fake_materialize(file_path):  # pragma: no cover - must not run
        raise AssertionError("materialize must not be called before scope resolves")

    monkeypatch.setattr(media_storage, "materialize", _fake_materialize)

    with pytest.raises(dh.AudioExtractError, match="scope_id"):
        dh.extract_audio_from_video("p1")

    assert repo.updates == []


# ═══════════════════════════════════════════════════════════════════
# 文件系统源(未迁移视频)—— 原逻辑不变
# ═══════════════════════════════════════════════════════════════════


def test_filesystem_source_keeps_legacy_behavior(monkeypatch, tmp_path, patch_repo):
    """download_path 是普通相对路径 —— 走原逻辑:写视频同目录、相对路径回写。
    证明未迁移视频不受影响。"""
    from app.core.utils import Utils
    from app.tasks import download_helpers as dh

    monkeypatch.setattr(
        Utils, "get_download_base_path", classmethod(lambda cls: str(tmp_path))
    )

    video_rel = "user1/2026/07/28/abc/video.mp4"
    video_full = tmp_path / video_rel
    video_full.parent.mkdir(parents=True)
    video_full.write_bytes(b"FAKE_VIDEO_BYTES")

    repo = patch_repo({"id": 1, "download_path": video_rel})

    def _fake_run(cmd, **kwargs):
        output_path = cmd[-1]
        Path(output_path).write_bytes(b"FAKE_AUDIO_BYTES")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert dh.extract_audio_from_video("p1") is True

    expected_audio_full = video_full.parent / "audio.m4a"
    assert expected_audio_full.exists()
    expected_rel = str(Path(video_rel).parent / "audio.m4a")
    assert repo.updates == [("p1", {"extract_audio_path": expected_rel})]


def test_filesystem_source_no_audio_stream_still_raises(
    monkeypatch, tmp_path, patch_repo
):
    """文件系统源上 ffmpeg rc!=0 —— 行为保持不变,仍 AudioExtractError。"""
    from app.core.utils import Utils
    from app.tasks import download_helpers as dh

    monkeypatch.setattr(
        Utils, "get_download_base_path", classmethod(lambda cls: str(tmp_path))
    )

    video_rel = "user1/2026/07/28/abc/video.mp4"
    video_full = tmp_path / video_rel
    video_full.parent.mkdir(parents=True)
    video_full.write_bytes(b"FAKE_VIDEO_BYTES")

    repo = patch_repo({"id": 1, "download_path": video_rel})

    def _fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(returncode=1, stderr="no audio stream")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(dh.AudioExtractError, match="ffmpeg rc=1"):
        dh.extract_audio_from_video("p1")

    assert repo.updates == []
