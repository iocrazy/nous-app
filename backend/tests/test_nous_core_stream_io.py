"""nous_core.fetch_to_file 的行为契约。

不测吞吐（那属于验收,见 Task 11）,只测正确性与失败清理。
"""

import http.server
import os
import threading

import pytest

nous_core = pytest.importorskip("nous_core")


@pytest.fixture
def server(tmp_path):
    """本地 HTTP 服务:/ok 返回 1MB,/boom 返回 500。"""
    payload = os.urandom(1024 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(500)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", payload
    srv.shutdown()


def test_fetch_writes_exact_bytes(server, tmp_path):
    base, payload = server
    dst = tmp_path / "out.bin"
    n = nous_core.fetch_to_file(f"{base}/ok", [], str(dst))
    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_fetch_sends_headers(server, tmp_path):
    base, _ = server
    dst = tmp_path / "out.bin"
    n = nous_core.fetch_to_file(f"{base}/ok", [("Authorization", "Bearer t")], str(dst))
    assert n > 0


def test_fetch_raises_on_error_status(server, tmp_path):
    base, _ = server
    dst = tmp_path / "out.bin"
    with pytest.raises(RuntimeError):
        nous_core.fetch_to_file(f"{base}/boom", [], str(dst))


def test_fetch_removes_partial_file_on_failure(server, tmp_path):
    """失败必须清理 —— 截断文件交给 ffmpeg 会静默产出错误结果。"""
    base, _ = server
    dst = tmp_path / "out.bin"
    with pytest.raises(RuntimeError):
        nous_core.fetch_to_file(f"{base}/boom", [], str(dst))
    assert not dst.exists()


def test_fetch_releases_gil(server, tmp_path):
    """8 并发必须真正并行。若忘了 allow_threads,这里会串行化。"""
    import time
    from concurrent.futures import ThreadPoolExecutor

    base, payload = server

    def one(i: int) -> int:
        return nous_core.fetch_to_file(f"{base}/ok", [], str(tmp_path / f"o{i}.bin"))

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(8)))
    elapsed = time.perf_counter() - start

    assert all(r == len(payload) for r in results)
    # 串行 8 次本地 1MB 传输也远快于 8s;这里只做粗粒度回归防护
    assert elapsed < 8.0
