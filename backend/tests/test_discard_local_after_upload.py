"""上传 S3 成功后回收本地工作副本(堵住 global/ 与 derived/hls/ 的持续增长)。

旧行为是"上传后保留本地文件给 materialize 读",该前提已失效(materialize 对
sb:// 走 S3 + 读通缓存),于是每次下载/转码白留一份:2026-08-02 实测
global 1.2G + derived/hls 1.1G 且每天新增十几个目录。

安全语义(这些断言就是护栏):
- stored_path 非 sb://(开关关/降级)→ 绝不删,本地副本此时是唯一成品
- DOWNLOAD_PATH 之外的路径 → 拒删
- 删除失败 → 只 warning,不上抛(回收失败不该判一次成功的下载为失败)
"""

import os

from app.core.config import settings
from app.services.library.media_storage import discard_local_source

SB = "sb://library/t5/ab/cd/abcd.mp4"


def test_removes_file_and_empty_parents(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "global" / "resources" / "web" / "douyin" / "123"
    d.mkdir(parents=True)
    f = d / "video.mp4"
    f.write_bytes(b"x")

    discard_local_source(f, SB)

    assert not f.exists()
    assert not d.exists()  # 空父目录逐级收掉
    # 不会把 DOWNLOAD_PATH 本身或还有内容的层级删掉
    assert tmp_path.exists()


def test_stops_at_non_empty_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "global" / "123"
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"x")
    sibling = tmp_path / "global" / "keepme"
    sibling.mkdir()

    discard_local_source(d / "video.mp4", SB)

    assert not d.exists()
    assert sibling.exists()  # 非空的上层保住
    assert (tmp_path / "global").exists()


def test_removes_directory_tree(monkeypatch, tmp_path):
    """图集/HLS 传的是目录。"""
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "derived" / "hls" / "9" / "10"
    (d / "480p").mkdir(parents=True)
    (d / "master.m3u8").write_bytes(b"#EXTM3U")
    (d / "480p" / "seg0.ts").write_bytes(b"ts")

    discard_local_source(d, SB)

    assert not d.exists()


def test_never_deletes_when_not_uploaded(monkeypatch, tmp_path):
    """开关关闭/降级时 helper 返回的是 FS 相对路径 —— 此时本地是唯一成品。"""
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    f = tmp_path / "global" / "v.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")

    discard_local_source(f, "global/resources/web/douyin/1/video.mp4")
    assert f.exists()
    discard_local_source(f, "")
    assert f.exists()
    discard_local_source(f, None)
    assert f.exists()


def test_refuses_path_outside_download_path(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path / "downloads"))
    (tmp_path / "downloads").mkdir()
    outside = tmp_path / "etc_passwd"
    outside.write_bytes(b"important")

    discard_local_source(outside, SB)
    assert outside.exists()

    # ".." 逃逸也挡住(realpath 归一后不在 DOWNLOAD_PATH 下)
    discard_local_source(tmp_path / "downloads" / ".." / "etc_passwd", SB)
    assert outside.exists()


def test_missing_file_is_noop(monkeypatch, tmp_path):
    """幂等:重放/已被清理时不该抛。"""
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    discard_local_source(tmp_path / "global" / "gone.mp4", SB)  # 不抛即通过


def test_unlink_failure_does_not_raise(monkeypatch, tmp_path):
    """回收失败只记 warning —— 一次成功的下载不能因清理失败被判失败。

    只读父目录 → unlink 拿 EACCES(目录写位才决定能否删条目)。
    """
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "locked"
    d.mkdir()
    f = d / "v.mp4"
    f.write_bytes(b"x")
    os.chmod(d, 0o555)
    try:
        discard_local_source(f, SB)  # 不抛即通过
        assert f.exists()  # 确实没删掉(证明触发的是失败路径)
    finally:
        os.chmod(d, 0o755)
