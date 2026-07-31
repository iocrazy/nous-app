"""派生产物(缩略图/preview_sprite)接 S3。

thumbnail_service 生成后原先只 persist 文件系统相对路径,是 PR-1 S3 接线漏网的
第三条派生产物路径(download/cover 已修)——每次重新生成缩略图/sprite 都往
文件系统回潮,破坏"零 FS"。现在生成后上传到 ``derived/{rid}/`` 前缀
(serve_resource_cover / serve_preview_sprite 已读的规范 key),thumbnail_path
写 sb://。flag 关闭时保持 FS 相对路径(可回退)。
"""

import pytest


@pytest.mark.asyncio
async def test_derived_upload_skips_when_flag_off(monkeypatch, tmp_path):
    """开关关闭 —— 原样返回 FS 相对路径,不触碰对象存储。"""
    import app.services.library.storage_flag as storage_flag
    from app.core.config import settings
    from app.services.media.render.thumbnail_service import ThumbnailService

    async def _disabled():
        return False

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _disabled)
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))

    thumb = tmp_path / "derived" / "1" / "thumbnail.webp"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"x")

    result = await ThumbnailService()._upload_derived_to_s3("1", thumb, None)
    assert result == "derived/1/thumbnail.webp"


@pytest.mark.asyncio
async def test_derived_upload_rewrites_sb_and_uploads_sprite(monkeypatch, tmp_path):
    """开关开 —— thumbnail + sprite 都上传到 derived/{rid}/,thumbnail_path 写 sb://。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.storage_flag as storage_flag
    from app.core.config import settings
    from app.services.media.render.thumbnail_service import ThumbnailService

    async def _enabled():
        return True

    puts = []

    class FakeStore:
        bucket = "library"

        async def put_file(self, key, path, mime, *, upsert=True):
            puts.append((key, path, mime))

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeStore())
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))

    d = tmp_path / "derived" / "thumbnails" / "42"
    d.mkdir(parents=True)
    thumb = d / "thumbnail.webp"
    thumb.write_bytes(b"t")
    sprite = d / "preview_sprite.jpg"
    sprite.write_bytes(b"s")

    result = await ThumbnailService()._upload_derived_to_s3("42", thumb, sprite)

    assert result == "sb://library/derived/42/thumbnail.webp"
    keys = [p[0] for p in puts]
    assert "derived/42/thumbnail.webp" in keys
    assert "derived/42/preview_sprite.jpg" in keys


@pytest.mark.asyncio
async def test_derived_upload_no_sprite_for_non_video(monkeypatch, tmp_path):
    """无 sprite(图片/音频缩略图)时只上传 thumbnail,不误传 sprite。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.storage_flag as storage_flag
    from app.core.config import settings
    from app.services.media.render.thumbnail_service import ThumbnailService

    async def _enabled():
        return True

    puts = []

    class FakeStore:
        bucket = "library"

        async def put_file(self, key, path, mime, *, upsert=True):
            puts.append((key, path, mime))

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeStore())
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))

    d = tmp_path / "derived" / "7"
    d.mkdir(parents=True)
    thumb = d / "thumbnail.webp"
    thumb.write_bytes(b"t")

    result = await ThumbnailService()._upload_derived_to_s3("7", thumb, None)

    assert result == "sb://library/derived/7/thumbnail.webp"
    assert [p[0] for p in puts] == ["derived/7/thumbnail.webp"]
