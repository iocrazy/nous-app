"""素材暂存：下载边界与清理保证（design doc 7.6）。

这个服务不挂任何存储卷，素材只经 HTTP 流转到一个临时目录，用完必须消失。
"用完"包括发布失败、下载中途报错、以及 publish 体内抛异常这三条路径 ——
任何一条漏掉，泄漏的素材就留在容器可写层里慢慢涨，而且不会有任何报错。

另一半是**上限**：一个没有字节上限的下载等于把远端 URL 的大小当成本地
磁盘配额。`Content-Length` 不可信（chunked 响应可以不给，也可以撒谎），
所以声明值和实收字节都要挡。
"""

from __future__ import annotations

import os

import pytest

from app import assets as assets_mod
from app.assets import (
    AssetError,
    sanitize_filename,
    stage_assets,
    validate_extension,
    validate_media_url,
)
from app.schemas import MediaItem, SessionStatus

pytestmark = pytest.mark.unit


def _item(filename: str = "clip.mp4", url: str = "http://nous-kong:8000/x") -> MediaItem:
    return MediaItem(kind="video", url=url, filename=filename)


def _fake_download(payload: bytes = b"data"):
    """替掉真正的阻塞下载，把 payload 写进 dest 并返回字节数。"""

    def _impl(url, dest, *, max_bytes, chunk, timeout):
        with open(dest, "wb") as handle:
            handle.write(payload)
        return len(payload)

    return _impl


# ── 清理：三条路径都必须不留东西 ────────────────────────────────


async def test_scratch_directory_is_removed_after_success(monkeypatch):
    monkeypatch.setattr(assets_mod, "_download_blocking", _fake_download())

    seen: list[str] = []
    async with stage_assets([("video", _item())]) as staged:
        seen.append(staged["video"].path)
        assert os.path.isfile(seen[0])

    assert not os.path.exists(seen[0])
    assert not os.path.exists(os.path.dirname(seen[0]))


async def test_scratch_directory_is_removed_when_the_body_raises(monkeypatch):
    """发布逻辑自己炸了,素材照样要清掉。

    这是最容易漏的一条:成功路径上删干净了,而真实世界里发布失败远比成功
    常见 —— 页面改版、会话过期、上传超时,每一次都留下几百 MB。
    """
    monkeypatch.setattr(assets_mod, "_download_blocking", _fake_download())

    captured: list[str] = []
    with pytest.raises(RuntimeError):
        async with stage_assets([("video", _item())]) as staged:
            captured.append(staged["video"].path)
            raise RuntimeError("publish blew up")

    assert not os.path.exists(os.path.dirname(captured[0]))


async def test_scratch_directory_is_removed_when_a_later_download_fails(monkeypatch):
    """多素材时,第二个下崩了,第一个已落盘的也要跟着消失。

    删的是目录而不是逐个文件,所以部分完成的批次不会留下半套。
    """
    calls: list[str] = []

    def _impl(url, dest, *, max_bytes, chunk, timeout):
        calls.append(dest)
        if len(calls) == 1:
            with open(dest, "wb") as handle:
                handle.write(b"first")
            return 5
        raise OSError("connection reset")

    monkeypatch.setattr(assets_mod, "_download_blocking", _impl)

    with pytest.raises(AssetError):
        async with stage_assets(
            [("video", _item("a.mp4")), ("cover", _item("b.jpg"))]
        ):
            pass

    assert calls, "第一个素材应当已经开始下载"
    assert not os.path.exists(os.path.dirname(calls[0]))


async def test_cleanup_failure_does_not_replace_the_real_outcome(monkeypatch):
    """清理失败只记日志,不能把发布结果改写成一个文件系统错误。

    用户关心的是"发出去了没有";rmtree 失败应当被看见(日志),但不应当
    冒充成发布本身的失败。
    """
    monkeypatch.setattr(assets_mod, "_download_blocking", _fake_download())

    def _boom(path):
        raise PermissionError("read-only layer")

    monkeypatch.setattr(assets_mod.shutil, "rmtree", _boom)

    async with stage_assets([("video", _item())]) as staged:
        assert staged["video"].size_bytes == 4
    # 没有异常冒出来就是本测试的断言


# ── 上限与空文件 ────────────────────────────────────────────────


async def test_declared_length_over_the_ceiling_is_rejected(monkeypatch):
    def _impl(url, dest, *, max_bytes, chunk, timeout):
        raise AssetError(
            SessionStatus.FAILED, "asset is huge", reason="asset_too_large"
        )

    monkeypatch.setattr(assets_mod, "_download_blocking", _impl)

    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", _item())]):
            pass
    assert excinfo.value.detail["reason"] == "asset_too_large"


async def test_empty_download_is_an_error_not_a_success(monkeypatch):
    """0 字节的文件会"上传成功"然后把编辑器卡在一个没有错误信息的状态。

    在这里挡下来,用户至少知道是素材的问题。
    """
    monkeypatch.setattr(assets_mod, "_download_blocking", _fake_download(b""))

    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", _item())]):
            pass
    assert excinfo.value.detail["reason"] == "asset_empty"


async def test_http_error_carries_the_status_code(monkeypatch):
    """签名 URL 过期(403)与素材不存在(404)是不同的处置,状态码不能丢。"""
    import urllib.error

    def _impl(url, dest, *, max_bytes, chunk, timeout):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(assets_mod, "_download_blocking", _impl)

    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", _item())]):
            pass
    assert excinfo.value.detail["reason"] == "asset_unavailable"
    assert excinfo.value.detail["http_status"] == 403


# ── 纯函数校验 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,ok",
    [
        ("http://nous-kong:8000/storage/v1/object/sign/x", True),
        ("https://example.com/a.mp4", True),
        ("file:///etc/passwd", False),
        ("ftp://example.com/a.mp4", False),
        ("", False),
    ],
)
def test_only_http_urls_are_accepted(url, ok):
    """file:// 会把这个服务变成一个任意文件读取器。"""
    assert (validate_media_url(url) is None) is ok


def test_extension_must_match_the_kind():
    from app.assets import VIDEO_EXTENSIONS

    assert validate_extension("a.mp4", VIDEO_EXTENSIONS, "video") is None
    assert validate_extension("a.exe", VIDEO_EXTENSIONS, "video") is not None


@pytest.mark.parametrize(
    "raw,expected_absent",
    [("../../etc/passwd", ".."), ("a/b.mp4", "/"), ("x\ry.mp4", "\r")],
)
def test_filename_is_sanitised_before_touching_the_filesystem(raw, expected_absent):
    """文件名来自请求体。路径分隔符必须消失,否则可以写出暂存目录之外。"""
    cleaned = sanitize_filename(raw, fallback="asset.bin")
    assert expected_absent not in cleaned
    assert cleaned
