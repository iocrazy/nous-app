"""共用的 CLI 临时目录回收器（3a 补验缺陷 1）。

前缀集合照抄两个 CLI provider 真实使用的 ``tempfile.mkdtemp(prefix=…)``：
jimeng-cli 是 ``jimeng_``，codex CLI 是 ``codeximg_``
（``services/media/parsers/video_providers/codex_cli.py:412``）。
"""

from __future__ import annotations

import os

from app.services.library.scratch_reaper import reap_scratch_dir


def _make(tmp_path, dirname: str) -> str:
    d = tmp_path / dirname
    d.mkdir()
    f = d / "out.png"
    f.write_bytes(b"x")
    return str(f)


def test_reaps_codex_scratch_dir(tmp_path):
    path = _make(tmp_path, "codeximg_abc")
    reap_scratch_dir(path)
    assert not os.path.exists(os.path.dirname(path))


def test_reaps_jimeng_scratch_dir(tmp_path):
    path = _make(tmp_path, "jimeng_abc")
    reap_scratch_dir(path)
    assert not os.path.exists(os.path.dirname(path))


def test_leaves_non_scratch_dir_alone(tmp_path):
    path = _make(tmp_path, "media_library")
    reap_scratch_dir(path)
    assert os.path.exists(path)


def test_scratch_like_filename_does_not_arm_the_reaper(tmp_path):
    """只看父目录 basename——文件名像临时产物不代表目录可以删。"""
    d = tmp_path / "downloads"
    d.mkdir()
    f = d / "codeximg_out.png"
    f.write_bytes(b"x")
    reap_scratch_dir(str(f))
    assert os.path.exists(f)


def test_missing_path_does_not_raise(tmp_path):
    reap_scratch_dir(str(tmp_path / "codeximg_gone" / "out.png"))


def test_empty_path_does_not_raise():
    reap_scratch_dir("")
