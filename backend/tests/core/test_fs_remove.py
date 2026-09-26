"""remove_local_path never follows a final symlink (real filesystem)."""

import os

import pytest

from app.core.fs_remove import remove_local_path

pytestmark = pytest.mark.unit


def _target_with_file(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("precious")
    return target


def test_link_to_directory_removes_only_the_link(tmp_path):
    target = _target_with_file(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert remove_local_path(link) == "link"
    assert not os.path.lexists(link)
    assert (target / "keep.txt").read_text() == "precious"


def test_dangling_link_is_removed(tmp_path):
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "gone")

    assert remove_local_path(link) == "link"
    assert not os.path.lexists(link)


def test_real_directory_is_removed_without_following_inner_links(tmp_path):
    target = _target_with_file(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (real / "inner").symlink_to(target, target_is_directory=True)

    assert remove_local_path(real) == "directory"
    assert not real.exists()
    assert (target / "keep.txt").read_text() == "precious"


def test_regular_file_and_missing_path(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")

    assert remove_local_path(f) == "file"
    assert not f.exists()
    assert remove_local_path(f) == "missing"
