"""下载链路接 S3 —— 开关开时新下载落对象存储,file_path 写 sb://。

这是「先接线后迁移」的第一步:止血,让文件系统不再增长。存量数据本任务不动。

覆盖两层:
  1. ``store_local_file`` 本身产出 sb:// 形态(既有能力,FakeStore 验证 —— 不碰
     真实 storage-api,遵循仓库约定:live 网络测试收敛到
     test_storage_object_store_smoke.py,默认跳过)。
  2. downloader.py 新增的接线点 ``_upload_downloaded_video_to_s3``:开关 /
     user_id 两个维度的分支,以及失败时异常上抛(不静默回退,交给外层既有的
     "写失败即报 FAILED" 语义处理,retry 可重跑)。
"""

import hashlib

import pytest

from app.services.library.media_storage import StoredObject, resolve_media_source


class FakeStore:
    """镜像 test_media_storage_unified.py 里的 FakeStore 约定 —— 不打真实网络。"""

    bucket = "library"

    def __init__(self, already_exists: bool = False):
        self.puts: list = []
        self._exists = already_exists

    async def exists(self, key):
        return self._exists

    async def put_file(self, key, path, mime):
        self.puts.append((key, path, mime))


@pytest.mark.asyncio
async def test_store_local_file_produces_sb_path(tmp_path):
    """store_local_file 把本地文件"传"对象存储(FakeStore)并返回 sb:// 路径。"""
    from app.services.library.media_storage import store_local_file

    payload = b"x" * (1024 * 512)
    src = tmp_path / "v.mp4"
    src.write_bytes(payload)

    fake = FakeStore(already_exists=False)
    stored = await store_local_file(
        scope_id=1, source_path=str(src), mime="video/mp4", filename="v.mp4", store=fake
    )

    assert isinstance(stored, StoredObject)
    assert stored.file_path.startswith("sb://library/")
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    loc = resolve_media_source(stored.file_path)
    assert loc.is_object_store
    assert len(fake.puts) == 1


@pytest.mark.asyncio
async def test_upload_wiring_skips_when_flag_off(monkeypatch, tmp_path):
    """开关关闭时保持旧行为 —— 原样返回本地相对路径,不触碰对象存储。"""
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import (
        _upload_downloaded_video_to_s3,
    )

    async def _disabled():
        return False

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _disabled)

    local = tmp_path / "video.mp4"
    local.write_bytes(b"abc")
    result = await _upload_downloaded_video_to_s3(
        user_id="user-1", local_path=str(local), relative_path="teams/1/x/video.mp4"
    )
    assert result == "teams/1/x/video.mp4"


@pytest.mark.asyncio
async def test_upload_wiring_skips_when_no_user_id(monkeypatch, tmp_path):
    """user_id 缺失(系统/孤儿触发的下载)时无法解析 scope,同样保持旧行为。"""
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import (
        _upload_downloaded_video_to_s3,
    )

    async def _enabled():
        return True

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)

    local = tmp_path / "video.mp4"
    local.write_bytes(b"abc")
    result = await _upload_downloaded_video_to_s3(
        user_id=None, local_path=str(local), relative_path="teams/1/x/video.mp4"
    )
    assert result == "teams/1/x/video.mp4"


@pytest.mark.asyncio
async def test_upload_wiring_rewrites_to_sb_path_when_enabled(monkeypatch, tmp_path):
    """开关开 + user_id 存在 —— 解析 scope、调用 store_local_file、改写 sb://。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.resources_service as resources_service
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import (
        _upload_downloaded_video_to_s3,
    )

    async def _enabled():
        return True

    async def _fake_resolve(user_id):
        assert user_id == "user-1"
        return "331438215859255"

    fake_store = FakeStore(already_exists=False)
    calls = {}

    async def _fake_store_local_file(*, scope_id, source_path, mime, filename, store):
        calls["scope_id"] = scope_id
        calls["source_path"] = source_path
        calls["mime"] = mime
        calls["filename"] = filename
        assert store is fake_store
        return StoredObject(
            file_path="sb://library/t331438215859255/ab/cd/abcd1234.mp4",
            size_bytes=3,
            sha256="abcd1234",
        )

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _fake_resolve)
    monkeypatch.setattr(media_storage, "store_local_file", _fake_store_local_file)
    monkeypatch.setattr(media_storage, "library_store", lambda: fake_store)

    local = tmp_path / "video.mp4"
    local.write_bytes(b"abc")
    result = await _upload_downloaded_video_to_s3(
        user_id="user-1", local_path=str(local), relative_path="teams/1/x/video.mp4"
    )

    assert result == "sb://library/t331438215859255/ab/cd/abcd1234.mp4"
    assert calls["scope_id"] == 331438215859255
    assert calls["source_path"] == str(local)
    assert calls["mime"] == "video/mp4"
    assert calls["filename"] == "video.mp4"


@pytest.mark.asyncio
async def test_upload_wiring_propagates_store_failure(monkeypatch, tmp_path):
    """存储写失败必须上抛,不静默回退到本地路径 —— 让外层现有的失败语义接管。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.resources_service as resources_service
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import (
        _upload_downloaded_video_to_s3,
    )

    async def _enabled():
        return True

    async def _fake_resolve(user_id):
        return "1"

    async def _boom(**kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _fake_resolve)
    monkeypatch.setattr(media_storage, "store_local_file", _boom)

    local = tmp_path / "video.mp4"
    local.write_bytes(b"abc")
    with pytest.raises(RuntimeError, match="storage-api unreachable"):
        await _upload_downloaded_video_to_s3(
            user_id="user-1", local_path=str(local), relative_path="teams/1/x/video.mp4"
        )
