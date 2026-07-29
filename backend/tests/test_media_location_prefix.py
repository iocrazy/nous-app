"""图集前缀形态:sb://.../album/{rid}/ 以 / 结尾表示「一个前缀下多个对象」。

单对象资源(视频)file_path 是 sb://library/ab/cd/sha.mp4;
图集资源迁 S3 后 file_path 是 sb://library/{scope}/album/{rid}/,读取端
按前缀 list_prefix 取全部 slides。
"""

from app.services.library.media_storage import resolve_media_source


def test_single_object_is_not_prefix():
    loc = resolve_media_source("sb://library/ab/cd/deadbeef.mp4")
    assert loc.is_object_store
    assert loc.is_prefix is False
    assert loc.key == "ab/cd/deadbeef.mp4"


def test_trailing_slash_is_prefix():
    loc = resolve_media_source("sb://library/t1/album/123/")
    assert loc.is_object_store
    assert loc.is_prefix is True
    assert loc.key == "t1/album/123/"


def test_filesystem_path_not_prefix():
    loc = resolve_media_source("global/resources/web/douyin/x/video.mp4")
    assert loc.is_object_store is False
    assert loc.is_prefix is False
