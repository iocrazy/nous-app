"""nous_core.fetch_to_file 的行为契约。

不测吞吐（那属于验收,见 Task 11）,只测正确性与失败清理。

三条测试历史上都测过"看起来对但实际测不到回归"的假阳性,详见各测试
的 docstring —— 都在 code review 里被指出并修复,教训是:每条断言都要
问"这个断言在实现被破坏时会不会真的变红"。
"""

import http.server
import os
import threading

import pytest

nous_core = pytest.importorskip("nous_core")


@pytest.fixture
def server(tmp_path):
    """本地 HTTP 服务:/ok 返回 1MB,/boom 直接 500,/truncated 声明的
    Content-Length 大于实际写出字节数后主动断连(模拟传输中途失败)。
    """
    payload = os.urandom(1024 * 1024)
    received_headers = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path == "/ok":
                received_headers["Authorization"] = self.headers.get("Authorization")
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif self.path == "/truncated":
                # 谎报比实际写出多 10 倍的长度,写一点就断连 —— 逼客户端
                # 在 body 读到一半时报错,而不是在 send() 阶段就失败。
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload) * 10))
                self.end_headers()
                self.wfile.write(payload[:1024])
                self.close_connection = True
            else:
                self.send_response(500)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", payload, received_headers
    srv.shutdown()


@pytest.fixture
def slow_server(tmp_path):
    """支持并发连接的 HTTP 服务,每个请求人为延迟 0.2s。

    专供 GIL 测试使用 —— 普通 `HTTPServer` 单线程逐个处理连接,不管
    Rust 侧是否释放 GIL,8 个并发客户端在服务端都会被串行处理,两种
    情况耗时区分不出来(2026-07-27 code review 抓到的假阳性)。
    """
    import time

    payload = os.urandom(1024 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(0.2)
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", payload
    srv.shutdown()


def test_fetch_writes_exact_bytes(server, tmp_path):
    base, payload, _ = server
    dst = tmp_path / "out.bin"
    n = nous_core.fetch_to_file(f"{base}/ok", [], str(dst))
    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_fetch_sends_headers(server, tmp_path):
    """必须断言服务端真的收到了 header,而不只是"请求没报错"。

    之前的版本只断言 `n > 0`,即使 Rust 侧完全不转发 headers,handler
    也不检查任何 header 就返回 200 + 完整 payload,测试照样绿。
    """
    base, _, received_headers = server
    dst = tmp_path / "out.bin"
    nous_core.fetch_to_file(f"{base}/ok", [("Authorization", "Bearer t")], str(dst))
    assert received_headers["Authorization"] == "Bearer t"


def test_fetch_raises_on_error_status(server, tmp_path):
    base, _, _ = server
    dst = tmp_path / "out.bin"
    with pytest.raises(RuntimeError):
        nous_core.fetch_to_file(f"{base}/boom", [], str(dst))


def test_fetch_removes_partial_file_on_failure(server, tmp_path):
    """失败必须清理 —— 截断文件交给 ffmpeg 会静默产出错误结果。

    用 `/boom`(请求还没建文件就已失败)测不到这个分支:外层
    `remove_file` 对着一个从未存在的路径操作,静默失败,
    `assert not dst.exists()` 恒成立,和清理逻辑是否存在无关。

    必须用 `/truncated`:文件先被 `File::create` 建出来、部分字节已经
    写入,然后在 `stream.next()` 读取阶段才报错 —— 这才是文档注释真正
    担心的"截断文件"场景,也是唯一能验证清理分支被执行到的路径。
    """
    base, _, _ = server
    dst = tmp_path / "out.bin"
    with pytest.raises(RuntimeError):
        nous_core.fetch_to_file(f"{base}/truncated", [], str(dst))
    assert not dst.exists()


def test_fetch_releases_gil(slow_server, tmp_path):
    """8 并发必须真正并行。若忘了 allow_threads,这里会串行化。

    必须用 `ThreadingHTTPServer` + 人为延迟:普通单线程 `HTTPServer`
    本身就只能串行处理 8 个连接,allow_threads 生不生效耗时都差不多,
    区分不出回归(2026-07-27 code review 指出)。
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    base, payload = slow_server

    def one(i: int) -> int:
        return nous_core.fetch_to_file(f"{base}/ok", [], str(tmp_path / f"o{i}.bin"))

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(8)))
    elapsed = time.perf_counter() - start

    assert all(r == len(payload) for r in results)
    # 真正并行:接近单次请求的延迟(~0.2-0.3s)。
    # 串行(GIL 未释放):接近 8 * 0.2s = 1.6s。留够 CI 抖动余量。
    assert elapsed < 1.0
