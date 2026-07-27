"""MediaKeyBuilder —— 对象键构造集中化。

保留 media_storage 里的模块级函数作为薄转发,所以本任务对调用方零影响;
spec #4 迁移校验需要一个可单独测试的键构造器。
"""

import pytest

from app.services.library.media_keys import MediaKeyBuilder


@pytest.fixture
def keys():
    return MediaKeyBuilder()


def test_content_key_is_scope_scoped_and_sharded(keys):
    k = keys.content_key_from_sha(
        scope_id=310812366953241,
        sha="0bde134795d32e26fbee0100112233445566778899aabbccddeeff0011223344",
        mime="video/mp4",
        filename="clip.mp4",
    )
    assert k.startswith("t310812366953241/0b/de/")
    assert k.endswith(".mp4")


def test_content_key_is_deterministic(keys):
    args = dict(
        scope_id=1,
        sha="a" * 64,
        mime="image/webp",
        filename="x.webp",
    )
    assert keys.content_key_from_sha(**args) == keys.content_key_from_sha(**args)


def test_hls_prefix_shape(keys):
    assert keys.hls_prefix("123", "456") == "hls/123/456"


def test_hls_key_joins_relative_path(keys):
    assert keys.hls_key("123", "456", "720p/seg0.ts") == "hls/123/456/720p/seg0.ts"


def test_hls_key_rejects_traversal(keys):
    with pytest.raises(ValueError):
        keys.hls_key("123", "456", "../escape.ts")


def test_to_file_path_builds_sb_scheme(keys):
    assert keys.to_file_path("library", "ab/cd/ef") == "sb://library/ab/cd/ef"


def test_module_level_functions_still_work():
    """薄转发:12 个既有调用方不需要改。"""
    from app.services.library import media_storage

    assert media_storage.to_file_path("library", "k") == "sb://library/k"
