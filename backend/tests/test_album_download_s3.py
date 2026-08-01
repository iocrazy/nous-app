"""图集(carousel)下载接 S3 —— 第 4 条写路径接线。

新图集整目录(slides/ + audio.mp3)此前只落文件系统;现在下载成功后经
``_upload_album_to_s3`` put_dir 到 ``t{scope}/album/{rid}/``(与
storage_migration._migrate_album_row 同 key 方案),返回 sb:// 前缀供
download_path / music_download_path / resources.file_path 重指。
开关关 / user 缺失 / rid 缺失 → 原样返回 FS 相对路径(可回退)。
"""

import pytest


@pytest.mark.asyncio
async def test_album_upload_skips_when_flag_off(monkeypatch, tmp_path):
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _disabled():
        return False

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _disabled)
    result = await _upload_album_to_s3(
        user_id="u1",
        resource_id="42",
        local_dir=str(tmp_path),
        relative_path="global/resources/web/douyin/9",
    )
    assert result == "global/resources/web/douyin/9"


@pytest.mark.asyncio
async def test_album_upload_skips_when_no_rid(monkeypatch, tmp_path):
    """resource 建行失败(rid=None)时保持 FS 行为,不上传。"""
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _enabled():
        return True

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    result = await _upload_album_to_s3(
        user_id="u1",
        resource_id=None,
        local_dir=str(tmp_path),
        relative_path="global/resources/web/douyin/9",
    )
    assert result == "global/resources/web/douyin/9"


@pytest.mark.asyncio
async def test_album_upload_put_dir_and_prefix(monkeypatch, tmp_path):
    """开关开 —— put_dir 按 album 前缀映射 rel→key,返回 sb:// 前缀(带尾斜杠)。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.resources_service as resources_service
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _enabled():
        return True

    async def _fake_resolve(user_id):
        assert user_id == "u1"
        return "5"

    calls = {}

    class FakeStore:
        bucket = "library"

        async def put_dir(self, local_dir, key_for, *, skip_existing=False):
            calls["local_dir"] = local_dir
            calls["skip_existing"] = skip_existing
            # 复刻 put_dir 的契约:key_for 接收相对 POSIX 路径
            calls["mapped"] = [
                key_for("slides/001.jpg"),
                key_for("audio.mp3"),
            ]
            return 2

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _fake_resolve)
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeStore())

    result = await _upload_album_to_s3(
        user_id="u1",
        resource_id="42",
        local_dir=str(tmp_path),
        relative_path="global/resources/web/douyin/9",
    )

    assert result == "sb://library/t5/album/42/"
    assert calls["mapped"] == ["t5/album/42/slides/001.jpg", "t5/album/42/audio.mp3"]
    assert calls["skip_existing"] is True
    assert calls["local_dir"] == str(tmp_path)


@pytest.mark.asyncio
async def test_album_upload_propagates_store_failure(monkeypatch, tmp_path):
    """上传失败上抛(外层'写失败即 FAILED,retry 重跑'语义接管),不静默留 FS。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.resources_service as resources_service
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _enabled():
        return True

    async def _fake_resolve(user_id):
        return "5"

    class BoomStore:
        bucket = "library"

        async def put_dir(self, local_dir, key_for, *, skip_existing=False):
            raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _fake_resolve)
    monkeypatch.setattr(media_storage, "library_store", lambda: BoomStore())

    with pytest.raises(RuntimeError, match="storage-api unreachable"):
        await _upload_album_to_s3(
            user_id="u1",
            resource_id="42",
            local_dir=str(tmp_path),
            relative_path="global/resources/web/douyin/9",
        )
