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


# ─── put_file ───────────────────────────────────────────────────────────────
#
# 与 fetch_to_file 同样的教训:每条断言都要问"这个断言在实现被破坏时
# 会不会真的变红"。下面每条测试都做过反向验证,记录在 task-10-report.md。


def test_put_file_uploads_bytes(tmp_path):
    """PUT 收到的字节必须与源文件逐字节一致。"""
    payload = os.urandom(512 * 1024)
    src = tmp_path / "in.bin"
    src.write_bytes(payload)

    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers["Content-Length"])
            received["body"] = self.rfile.read(n)
            received["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        nous_core.put_file(
            str(src),
            f"http://127.0.0.1:{srv.server_port}/k",
            [("Authorization", "Bearer t")],
        )
    finally:
        srv.shutdown()

    assert received["body"] == payload
    assert received["auth"] == "Bearer t"


def test_put_file_raises_on_error_status(tmp_path):
    """服务端返回 5xx 必须抛异常,不能被静默吞掉当成功处理。

    覆盖缺口:brief 只给了成功路径的测试。若 Rust 侧漏掉
    `status.is_success()` 检查,调用方(Task 11 的 materialize)会把
    上传失败误判为完成。
    """
    src = tmp_path / "in.bin"
    src.write_bytes(b"x" * 100)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            # 先把 body 读完再回 500,否则连接会在客户端还没发完时被
            # reset,报的是连接错误而不是我们要验证的状态码错误。
            n = int(self.headers.get("Content-Length", 0))
            if n:
                self.rfile.read(n)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError):
            nous_core.put_file(str(src), f"http://127.0.0.1:{srv.server_port}/k", [])
    finally:
        srv.shutdown()


def test_put_file_releases_gil(tmp_path):
    """8 并发上传必须真正并行。

    与 fetch 侧同样的陷阱:必须用 `ThreadingHTTPServer` + 人为延迟,
    否则普通单线程 `HTTPServer` 本身就串行处理连接,区分不出
    allow_threads 是否生效。另外这条也覆盖了 runtime 方案本身的风险
    ——若把共享 runtime 包了一层 Mutex,或换成 current_thread runtime,
    请求会被串行化,时间断言会抓到(死锁类测试抓不到这种回归)。
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    payload = os.urandom(256 * 1024)
    srcs = []
    for i in range(8):
        p = tmp_path / f"in{i}.bin"
        p.write_bytes(payload)
        srcs.append(str(p))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            time.sleep(0.2)
            n = int(self.headers["Content-Length"])
            self.rfile.read(n)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def one(i: int) -> None:
        nous_core.put_file(srcs[i], f"http://127.0.0.1:{srv.server_port}/k", [])

    try:
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(one, range(8)))
        elapsed = time.perf_counter() - start
    finally:
        srv.shutdown()

    # 真正并行:接近单次请求延迟(~0.2-0.3s)。
    # 串行(GIL 未释放,或 runtime 被隐性串行化):接近 8 * 0.2s = 1.6s。
    assert elapsed < 1.0


def test_put_file_streams_without_buffering_whole_file(tmp_path):
    """put_file 必须流式发送,不能把整个源文件先读进内存。

    512KB 的载荷区分不出流式还是缓冲(brief 里没覆盖这条)。用 150MB
    文件 + 进程 RSS 峰值前后差值断言:若 Rust 侧改成先
    `tokio::fs::read()` 整个文件再塞进 body,增量会逼近文件大小
    (150MB);流式实现的增量应远小于此。反向验证时把生产代码换成
    `tokio::fs::read` 整读,这条测试确实由绿转红(实测增量 157664KB,
    超过下面的阈值一倍多,不是踩线过关,见 task-10-report.md)。

    ⚠️ 假设与静默失效风险:`resource.getrusage().ru_maxrss` 是**进程
    峰值**且单调不减,不是"当前占用"。这条断言隐含假设"本测试跑之前,
    进程 RSS 峰值没有被同进程内其他测试推高过"——当前文件里其余测试
    都是 KB 级载荷,不会触发这个问题。但如果将来:
    (a) 本文件新增另一个大载荷测试且排在这条前面执行,或
    (b) pytest 把本文件与更重的测试文件合并到同一个进程收集,
    都可能让 before/after 的峰值都已经超过阈值,delta 趋近 0,导致这条
    断言对任何实现(流式或整读)都通过而不再有区分力——不会报错,只会
    悄悄失去保护。阈值(文件大小的一半,76800KB)不是拍的,来自反向验证
    实测的两倍安全边际。
    """
    import resource

    size_mb = 150
    chunk = os.urandom(1024 * 1024)  # 复用同一个 1MB 块,避免生成 150MB 随机数拖慢测试
    src = tmp_path / "big.bin"
    with open(src, "wb") as f:
        for _ in range(size_mb):
            f.write(chunk)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers["Content-Length"])
            total = 0
            while total < n:
                buf = self.rfile.read(min(1024 * 1024, n - total))
                if not buf:
                    break
                total += len(buf)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        nous_core.put_file(str(src), f"http://127.0.0.1:{srv.server_port}/k", [])
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    finally:
        srv.shutdown()

    delta_kb = after - before
    half_file_kb = (size_mb * 1024 * 1024) // 1024 // 2
    assert delta_kb < half_file_kb, (
        f"进程 RSS 峰值增量 {delta_kb}KB,超过文件大小一半"
        f"({half_file_kb}KB),疑似整份读入内存而非流式发送"
    )
