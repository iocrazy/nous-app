#!/usr/bin/env python3
"""前置闸门 —— 实测 ``ObjectStore.get_stream``(httpx)的真实吞吐基线。

## 为什么要测这个

PR3(nous_core Rust I/O 整合)的全部前提是"Python 是字节路径的瓶颈"。这个前提
来自设计阶段的分层实测:

    curl → SeaweedFS 直连                476 MB/s
    curl → Supabase Storage → SeaweedFS  470 MB/s   ← Node/Kong 层只损 1%
    Python(手写 urllib) → Supabase Storage  243 MB/s   ← 损失 48% 在此

但那个 243 是用手写 ``urllib`` 脚本测的,不是应用实际走的
``ObjectStore.get_stream``(httpx + storage3 代理)。本脚本直接调用应用
代码里真实的那条路径,把"Python 是瓶颈"这个假设换成实测数字。

## 方法学(``scripts/bench-storage.py`` 踩过的坑,本次刻意反其道而行)

bench-storage.py 的教训是"测量工具本身不能进入字节路径"(dd/curl 外包字节搬运,
Python 只管签名和计时)。但**这次要测的恰恰是 Python 在字节路径里的开销本身**
——httpx.AsyncClient.stream() + resp.aiter_bytes() 就是被测对象,不能再外包给
curl,否则测的是别的东西。所以这里反过来:

1. 被测对象是 ``get_stream`` 本身,循环里只做 ``len(chunk)`` 计数,不做任何
   额外拷贝/解析/IO,避免测量脚本自己叠加不属于生产路径的开销。
2. 样本足够大(默认选 storage.objects 里最大的 library 对象,~900MB)——
   小文件测的是 IOPS 不是带宽。
3. 多次重复(默认 5 次)取中位数,同时打印每次原始值。
4. 如实记录首次 vs 后续读取的差异(SeaweedFS/Supabase Storage 侧可能有缓存),
   不做任何"预热后再测"的清洗。

## 判定

    中位吞吐 >= 400 MB/s  →  Python/httpx 不是瓶颈,PR3 的 Rust 方案失去依据 → exit 2
    中位吞吐 <  400 MB/s  →  前提成立,继续 Task 9-11                      → exit 0

## 用法

    cd backend
    SUPABASE_URL=http://127.0.0.1:9082 \
    SUPABASE_SERVICE_ROLE_KEY=<service_role_key> \
    uv run python scripts/probe_httpx_baseline.py [object_key]

不传 ``object_key`` 时使用下面 ``DEFAULT_KEY``(2026-07-27 在 library bucket
里实测到的最大对象,943191620 字节 ≈ 899.5 MB)。取样 SQL 见文件末尾注释。
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

from app.services.library.media_storage import LIBRARY_BUCKET, ObjectStore

# storage.objects 里 library bucket 实测最大对象(2026-07-27):
#   SELECT name, (metadata->>'size')::bigint AS sz
#   FROM storage.objects
#   WHERE bucket_id='library' AND (metadata->>'size')::bigint > 200000000
#   ORDER BY sz DESC LIMIT 1;
# 注:resources 表没有 size_bytes 列(真实列名是 file_size_bytes),且 resources
# 里的样本上限只有 136 MB —— 测带宽应该用更大的 storage.objects 直接样本。
DEFAULT_KEY = (
    "t310812366953241/80/ab/"
    "80ab4a6a550bf67aa3798657d4b509ac5a285e2a8b358fd185e670d1c1f8adf0.mp4"
)
REPEATS = 5
THRESHOLD_MB_S = 400.0
MB = 1024 * 1024


async def _one_pass(store: ObjectStore, key: str) -> tuple[float, int]:
    """跑一遍 get_stream,只计字节数和耗时 —— 不做任何额外工作。"""
    start = time.perf_counter()
    total = 0
    async for chunk in store.get_stream(key):
        total += len(chunk)
    elapsed = time.perf_counter() - start
    return elapsed, total


async def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KEY
    store = ObjectStore(LIBRARY_BUCKET)

    print(f"对象: sb://{LIBRARY_BUCKET}/{key}")
    print(f"重复次数: {REPEATS}\n")

    throughputs: list[float] = []
    for i in range(1, REPEATS + 1):
        elapsed, total = await _one_pass(store, key)
        if total == 0:
            print(f"  第 {i} 次: 读到 0 字节 —— 对象不存在或 Range 有误,中止。")
            return 1
        mb = total / MB
        mbps = mb / elapsed if elapsed > 0 else float("inf")
        throughputs.append(mbps)
        tag = "  (首次读,可能含冷启动/未命中缓存)" if i == 1 else ""
        print(
            f"  第 {i} 次: {mb:>8.1f} MB / {elapsed:>6.3f} s = {mbps:>7.1f} MB/s{tag}"
        )

    median = statistics.median(throughputs)
    print(f"\n各次原始值(MB/s): {[round(t, 1) for t in throughputs]}")
    print(f"中位吞吐: {median:.1f} MB/s  (阈值 {THRESHOLD_MB_S:.0f} MB/s)")

    if median >= THRESHOLD_MB_S:
        print(
            "\n判定: GATE FAIL —— 中位吞吐 >= 阈值,httpx 本身跑得够快,"
            "Python 不是字节路径瓶颈。PR3 的 Rust I/O 方案失去依据,应终止 PR3。"
        )
        return 2

    print(
        "\n判定: GATE PASS —— 中位吞吐 < 阈值,Python 仍是瓶颈,"
        "前提成立,继续 Task 9-11。"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
