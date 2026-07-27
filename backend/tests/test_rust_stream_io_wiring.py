"""开关接线:开则走 Rust,关则走 Python,两条路径产出必须一致。

Task 9/10 的教训是"测试名承诺 A、实际测不到 A"的假阳性(删掉被守护的
代码照样绿)。这里每条新增测试都做过反向验证——临时破坏被守护的生产
代码,确认测试真的会变红——观测结果记在 task-11-report.md。

覆盖面:
  - materialize flag off/on(含事件循环是否被同步调用冻结)
  - put_file flag off/on
  - put_file upsert=False 时,即使开关打开也必须回落到原 SDK 路径
    (裸 PUT 对本仓库使用的 storage-api 版本无条件覆盖,没有"仅当不存在
    才写入"的原生语义——实测见 task-11-report.md)
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import os
import threading
import time
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.services.library.media_storage import ObjectStore

# ─── fixtures: 本地 HTTP server 模拟对象存储(不依赖真实 SeaweedFS) ─────────


@pytest.fixture
def get_server():
    """GET 端点,固定返回一段随机字节。"""
    payload = os.urandom(256 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", payload
    srv.shutdown()


@pytest.fixture
def slow_get_server():
    """GET 端点,人为延迟 0.3s 再返回——专供事件循环阻塞检测使用。"""
    payload = os.urandom(256 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(0.3)
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", payload
    srv.shutdown()


@pytest.fixture
def put_server():
    """PUT 端点,记录收到的字节与 headers。"""
    received: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers["Content-Length"])
            received["body"] = self.rfile.read(n)
            received["content_type"] = self.headers.get("Content-Type")
            received["x_upsert"] = self.headers.get("X-Upsert")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", received
    srv.shutdown()


# ─── materialize ────────────────────────────────────────────────────────────


async def test_materialize_uses_python_when_flag_off(monkeypatch, tmp_path):
    """默认关闭时不得触碰 nous_core。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", False)

    called = {"rust": False}

    def _boom(*a, **k):
        called["rust"] = True
        raise AssertionError("Rust path must not be used when flag is off")

    monkeypatch.setattr(media_storage, "_rust_fetch_to_file", _boom, raising=False)

    chunks = [b"a" * 1024, b"b" * 1024]

    class _Store:
        async def get_stream(self, key, chunk_size=None):
            for c in chunks:
                yield c

    monkeypatch.setattr(media_storage, "ObjectStore", lambda bucket: _Store())

    async with media_storage.materialize("sb://library/k") as p:
        assert p.read_bytes() == b"".join(chunks)
    assert called["rust"] is False


async def test_materialize_deletes_temp_on_exit(monkeypatch, tmp_path):
    """两条路径都必须在退出时删临时文件。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", False)

    class _Store:
        async def get_stream(self, key, chunk_size=None):
            yield b"x" * 16

    monkeypatch.setattr(media_storage, "ObjectStore", lambda bucket: _Store())

    async with media_storage.materialize("sb://library/k") as p:
        held = p
        assert held.exists()
    assert not held.exists()


async def test_materialize_uses_rust_when_flag_on(monkeypatch, get_server):
    """开关打开:必须真的调用 nous_core.fetch_to_file,产出字节与源一致。

    用本地 HTTP GET server 模拟对象存储(参考 test_nous_core_stream_io.py
    的 fixture 写法),不依赖真实 SeaweedFS。`_Store.get_stream` 若被调用
    会直接 AssertionError——双重防线:既证明走了 Rust,也证明没有同时
    误走 Python 分支。

    反向验证:把生产代码的 `if settings.FEATURE_RUST_STREAM_IO:` 改成
    `if False:`(强迫走 Python 分支),这条测试从 PASS 变为 FAIL——
    `_Store.get_stream` 被真实调用并抛出 AssertionError,而不是无声通过。
    已实测,见 task-11-report.md。
    """
    pytest.importorskip("nous_core")
    from app.core import config

    base_url, payload = get_server
    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", True)

    class _Store:
        async def _proxy(self):
            return object()

        def _object_target(self, proxy, key):
            return f"{base_url}/{key}", {}

        async def get_stream(self, key, chunk_size=None):
            raise AssertionError("Python 分支不该在 flag=on 时被调用")
            yield b""  # pragma: no cover - 上一行必抛,这里只为保持生成器形态

    monkeypatch.setattr(media_storage, "ObjectStore", lambda bucket: _Store())

    async with media_storage.materialize("sb://library/k") as p:
        assert p.read_bytes() == payload


async def test_materialize_rust_path_keeps_event_loop_running(
    monkeypatch, slow_get_server
):
    """开关打开时,字节传输必须经 asyncio.to_thread 派发到线程池。

    若有人误删 to_thread、在协程里直接同步调用 nous_core.fetch_to_file,
    整个事件循环会在传输窗口内被这一个 OS 线程完全占住——不管 Rust 内部
    是否 py.allow_threads,因为压根没有第二个线程可以切换过去跑其他协程。
    这与 Task 9/10 测的"Rust 扩展本身是否释放 GIL"是不同层面的风险:那
    边测的是"扩展内部并发是否真并行",这里测的是"调用方有没有把它扔到
    另一个线程"。

    用后台 ticker 协程数 tick 次数验证:0.3s 传输窗口 + 10ms ticker 间隔,
    事件循环若仍在跑理论上能推进 ~30 次;若被同步调用冻结,ticker 一次
    都跑不到(冻结期间连 `asyncio.sleep` 的唤醒回调都排不上）。

    反向验证:把生产代码的 `await asyncio.to_thread(nous_core.fetch_to_file,
    ...)` 改成直接同步调用 `nous_core.fetch_to_file(...)`,这条测试从
    PASS 变为 FAIL(ticks 从 >=10 掉到 0~1)。已实测,见 task-11-report.md。
    """
    pytest.importorskip("nous_core")
    from app.core import config

    base_url, payload = slow_get_server
    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", True)

    class _Store:
        async def _proxy(self):
            return object()

        def _object_target(self, proxy, key):
            return f"{base_url}/{key}", {}

    monkeypatch.setattr(media_storage, "ObjectStore", lambda bucket: _Store())

    ticks = {"n": 0}

    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.01)

    task = asyncio.ensure_future(ticker())
    try:
        async with media_storage.materialize("sb://library/k") as p:
            assert p.read_bytes() == payload
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert ticks["n"] >= 10, f"事件循环疑似被阻塞,ticker 仅推进 {ticks['n']} 次"


# ─── put_file ───────────────────────────────────────────────────────────────


async def test_put_file_uses_python_when_flag_off(monkeypatch, tmp_path):
    """默认关闭时 put_file 不得触碰 nous_core,走原有 SDK 路径。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", False)

    called = {"rust": False}

    def _boom(*a, **k):
        called["rust"] = True
        raise AssertionError("Rust path must not be used when flag is off")

    monkeypatch.setattr(media_storage, "_rust_put_file", _boom, raising=False)

    src = tmp_path / "in.bin"
    src.write_bytes(b"hello")

    store = ObjectStore("library")
    proxy = AsyncMock()
    proxy.upload.return_value = None
    monkeypatch.setattr(store, "_proxy", AsyncMock(return_value=proxy))

    await store.put_file("k", str(src), "application/octet-stream")

    proxy.upload.assert_awaited_once()
    assert called["rust"] is False


async def test_put_file_flag_on_uses_rust_and_matches_bytes(
    monkeypatch, put_server, tmp_path
):
    """开关打开(默认 upsert=True):必须真的调用 nous_core.put_file,
    收到的字节 + content-type + x-upsert header 都要与预期一致。

    `_object_target` / `_proxy` 直接指向本地 PUT server 的地址,不依赖
    真实 storage-api——但 `nous_core.put_file` 本身是真实的 Rust 扩展,
    端到端验证接线。proxy.upload 若被调用会直接抛异常,双重确认没有
    同时落到 SDK 路径。

    反向验证:把生产代码的判断条件改成 `if False:`(强迫走 SDK 路径),
    这条测试从 PASS 变为 FAIL——`proxy.upload` 被调用并抛出预设异常。
    已实测,见 task-11-report.md。
    """
    pytest.importorskip("nous_core")
    from app.core import config

    base_url, received = put_server
    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", True)

    payload = os.urandom(128 * 1024)
    src = tmp_path / "in.bin"
    src.write_bytes(payload)

    store = ObjectStore("library")

    def _boom_upload(*a, **k):
        raise AssertionError("SDK path must not be used when rust path applies")

    proxy = AsyncMock()
    proxy.upload.side_effect = _boom_upload
    monkeypatch.setattr(store, "_proxy", AsyncMock(return_value=proxy))
    monkeypatch.setattr(
        store, "_object_target", lambda p, key: (f"{base_url}/{key}", {})
    )

    await store.put_file("probe/k.bin", str(src), "video/mp4", upsert=True)

    assert received["body"] == payload
    assert received["content_type"] == "video/mp4"
    assert received["x_upsert"] == "true"


async def test_put_file_flag_on_upsert_false_falls_back_to_sdk(monkeypatch, tmp_path):
    """upsert=False 时,即使开关打开也必须回落到原有 SDK 路径。

    背景:裸 PUT 到本仓库使用的 storage-api 版本无论带不带 x-upsert 头都
    无条件覆盖已存在对象(实测,见 task-11-report.md)——没有对应"仅当
    不存在才写入"的原生语义,与 SDK 路径(POST,upsert=False 时故意省略
    x-upsert,由 storage-api 拒绝已存在对象)不同。若也让 upsert=False 走
    Rust,会静默破坏 `put_file(..., upsert=False)` 签名承诺的"拒绝覆盖"
    契约。

    反向验证:把生产代码 `if _s.FEATURE_RUST_STREAM_IO and upsert:` 的
    `and upsert` 删掉,这条测试从 PASS 变为 FAIL——实测报的是 TypeError
    (代码转而尝试用裸 AsyncMock 当 storage3 proxy 走 `_object_target`,
    `proxy._headers` 在测试里没有意义因而崩在更早的一行,而不是我们原本
    预期的 `proxy.upload` 未被调用)。不管报错落在哪一行,只要保护条件被
    删掉测试确实会变红。已实测,见 task-11-report.md。
    """
    from app.core import config

    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", True)

    src = tmp_path / "in.bin"
    src.write_bytes(b"data")

    store = ObjectStore("library")
    proxy = AsyncMock()
    proxy.upload.return_value = None
    monkeypatch.setattr(store, "_proxy", AsyncMock(return_value=proxy))

    await store.put_file("k", str(src), "application/octet-stream", upsert=False)

    proxy.upload.assert_awaited_once()
    args, _ = proxy.upload.call_args
    assert args[2]["upsert"] == "false"
