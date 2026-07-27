"""DerivedArtifactPaths —— 派生产物落位的唯一入口。

spec #2 要把缩略图/sprite 改写 S3,届时只改这个类,不用再翻 thumbnail_service。
"""

from pathlib import Path

import pytest

from app.services.library.derived_paths import DerivedArtifactPaths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    return DerivedArtifactPaths()


def test_filesystem_source_lands_next_to_source(paths, tmp_path):
    src = tmp_path / "web" / "vid" / "video.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    out = paths.thumbnail_dir("web/vid/video.mp4", "123", src)
    assert out == src.parent


def test_object_store_source_lands_in_derived_tree(paths, tmp_path):
    """sb:// 源在 DOWNLOAD_PATH 下没有"旁边"可言,落 resource_id 键控的树。"""
    src = tmp_path / "tmpXYZ.mp4"
    src.write_bytes(b"x")
    out = paths.thumbnail_dir("sb://library/ab/cd/deadbeef", "456", src)
    assert out == tmp_path / "derived" / "thumbnails" / "456"


def test_object_store_dir_is_created(paths, tmp_path):
    src = tmp_path / "tmpXYZ.mp4"
    src.write_bytes(b"x")
    out = paths.thumbnail_dir("sb://library/ab/cd/deadbeef", "789", src)
    assert out.is_dir()
