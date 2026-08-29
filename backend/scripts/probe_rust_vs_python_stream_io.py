#!/usr/bin/env python3
"""Task 11 A/B —— Python(httpx/get_stream) vs Rust(nous_core.fetch_to_file) 交错实测。

## 为什么不能直接照抄 brief 里的 Step 9

brief 建议用环境变量切换 `FEATURE_RUST_STREAM_IO` 前后各跑一遍
``scripts/probe_httpx_baseline.py``。但那个脚本直接调用
``ObjectStore.get_stream``,从未读取 ``settings.FEATURE_RUST_STREAM_IO``,
也没有经过 ``materialize``——两次跑出来的必然是同一条代码路径的两份采样,
开关摆在哪里都不影响它测的是什么。这是 brief 里的一个真实漏洞,不是本
脚本刻意标新立异。

## 方法学(team lead 派发前提出的 5 条,这里逐条落地)

1. **交错测量**:round 内先 Python 后 Rust(A, B, A, B, ...),不是先跑完
   所有 A 再跑所有 B —— Task 8 实测同一对象同一路径一次会话内从 476
   漂到 365(30% 漂移),批量测会把这类系统负载漂移误读成方案差异。
2. **同一批对象**:两条臂用同一个 key(Task 8 定下的 library bucket 最大
   对象,899.5 MB),不用大小不同的样本互相比较。
3. **多轮取中位数**,每轮原始值全部打印,不做任何"去掉异常值"的清洗。
4. 如果两组分布重叠,如实报告"区分不出",不是选对 Rust 有利的轮次拼数据。

## 两条臂具体测的是什么

- Python 臂:``ObjectStore.get_stream`` 循环里只做 ``len(chunk)`` 计数
  (与 ``probe_httpx_baseline.py`` 同构),不写盘、不做多余拷贝。
- Rust 臂:与 ``materialize`` 的 Rust 分支逐字节同构 —— 同一个
  ``store._object_target`` 拿到的 (url, headers),``asyncio.to_thread``
  派发给 ``nous_core.fetch_to_file`` 写到临时文件,量的是这一整条路径
  (含线程派发开销),不是 Rust 扩展的裸执行时间。每轮量完就删临时文件,
  避免占满磁盘。

## 用法

    cd backend
    SUPABASE_URL=http://127.0.0.1:9082 uv run python scripts/probe_rust_vs_python_stream_io.py [object_key] [rounds]
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time

from app.services.library.media_storage import LIBRARY_BUCKET, ObjectStore

DEFAULT_KEY = (
    "t310812366953241/80/ab/"
    "80ab4a6a550bf67aa3798657d4b509ac5a285e2a8b358fd185e670d1c1f8adf0.mp4"
)
DEFAULT_ROUNDS = 6
MB = 1024 * 1024


async def _python_pass(store: ObjectStore, key: str) -> tuple[float, int]:
    """与 probe_httpx_baseline.py 同构:只计字节数和耗时。"""
    start = time.perf_counter()
    total = 0
    async for chunk in store.get_stream(key):
        total += len(chunk)
    elapsed = time.perf_counter() - start
    return elapsed, total


async def _rust_pass(store: ObjectStore, key: str) -> tuple[float, int]:
    """与 materialize 的 Rust 分支逐字节同构。"""
    import nous_core

    proxy = await store._proxy()
    url, headers = store._object_target(proxy, key)
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        start = time.perf_counter()
        n = await asyncio.to_thread(
            nous_core.fetch_to_file, url, list(headers.items()), tmp
        )
        elapsed = time.perf_counter() - start
        return elapsed, n
    finally:
        os.unlink(tmp)


async def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KEY
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ROUNDS
    store = ObjectStore(LIBRARY_BUCKET)

    print(f"对象: sb://{LIBRARY_BUCKET}/{key}")
    print(f"轮数: {rounds}(每轮交错跑一次 Python + 一次 Rust)\n")

    py_mbps: list[float] = []
    rs_mbps: list[float] = []

    for i in range(1, rounds + 1):
        elapsed_py, total_py = await _python_pass(store, key)
        if total_py == 0:
            print(f"第 {i} 轮 python: 读到 0 字节,中止。")
            return 1
        mbps_py = (total_py / MB) / elapsed_py if elapsed_py > 0 else float("inf")
        py_mbps.append(mbps_py)

        elapsed_rs, total_rs = await _rust_pass(store, key)
        if total_rs == 0:
            print(f"第 {i} 轮 rust: 读到 0 字节,中止。")
            return 1
        mbps_rs = (total_rs / MB) / elapsed_rs if elapsed_rs > 0 else float("inf")
        rs_mbps.append(mbps_rs)

        print(
            f"第 {i} 轮:  python {mbps_py:>7.1f} MB/s"
            f"  |  rust {mbps_rs:>7.1f} MB/s"
            f"  (delta {mbps_rs - mbps_py:+.1f})"
        )

    print(f"\npython 各轮原始值(MB/s): {[round(x, 1) for x in py_mbps]}")
    print(f"rust   各轮原始值(MB/s): {[round(x, 1) for x in rs_mbps]}")

    med_py = statistics.median(py_mbps)
    med_rs = statistics.median(rs_mbps)
    print(f"\npython 中位: {med_py:.1f} MB/s")
    print(f"rust   中位: {med_rs:.1f} MB/s")
    print(f"中位差: {med_rs - med_py:+.1f} MB/s ({(med_rs / med_py - 1) * 100:+.1f}%)")

    # 重叠判定:两组的 [min, max] 区间是否有交集——不是统计检验,只是给出
    # "肉眼能否分辨"的粗判据,严谨结论要看下面打印的完整原始值分布。
    overlap = max(min(py_mbps), min(rs_mbps)) <= min(max(py_mbps), max(rs_mbps))
    print(f"两组区间是否重叠: {'是 —— 区分不出' if overlap else '否 —— 有分离'}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
