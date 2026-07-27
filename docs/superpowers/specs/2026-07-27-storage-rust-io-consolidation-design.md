# Rust 流式 IO 接入 + 存储函数整合（设计）

- 日期：2026-07-27
- 状态：设计已确认，待实施
- 定位：**存储全量 S3 化路线的 spec #1（共 4）**

## 路线全景

用户目标是把媒体存储全部迁到 S3（SeaweedFS on nas-B），含原件与派生产物，并整合重复的路径解析函数、该用 Rust 的地方替换 Python。全量改造拆成四个独立 spec：

| spec | 内容 | 状态 |
|------|------|------|
| **#1（本文）** | Rust 流式 IO 接入 + 函数整合 + 品牌改名 | 设计确认 |
| #2 | 写入改走 S3（含派生产物） | 待开始 |
| #3 | 读取路径（nginx / Rust 直连） | 待开始 |
| #4 | 存量 18 GB / 629 文件迁移 | 待开始 |

先做 #1 的理由：**存储位置完全不变**，任何一步都能独立回滚，且立刻兑现 Python→Rust 的吞吐收益。

## 背景：实测数据

万兆网卡升级后（`enp2s0f1np1` 协商到 10000 Mb/s），用 `scripts/bench-storage.py` 与手工探针得到：

| 路径 | 单流吞吐 | 并发扩展性 |
|------|---------|-----------|
| CIFS 文件系统（`dd`） | 123 MB/s | 1→8 并发仅 +11%（SMB 单连接） |
| SeaweedFS S3（`curl`） | 469 MB/s | 1→8 并发 264→634 MB/s |

分层探针进一步定位瓶颈：

| 层 | 吞吐 | 结论 |
|----|------|------|
| A. `curl` → SeaweedFS 直连 | 476 MB/s | 基准 |
| B. `curl` → Supabase Storage → SeaweedFS | 470 MB/s | **Node 层只损失 1%** |
| C. Python → Supabase Storage → SeaweedFS | 243 MB/s | **损失 48% 发生在 Python** |

B 与 A 几乎相等，证明 Supabase Storage 那层 Node 不是瓶颈；从 470 掉到 243 的部分全部发生在 Python 的 chunk 循环里。

### 测量方法学（三次踩坑换来的）

前三轮测量全部失败，原因同一个 —— **测量工具自己进入了字节路径**：

| 轮次 | 错误做法 | 虚假结果 |
|------|---------|---------|
| 1 | `dd` 反复读同一文件 | 页缓存命中，CIFS 虚报 92 MB/s / 0 ms |
| 2 | Python `urllib` 读循环 | S3 被压到 243（真实 476） |
| 3 | `curl \| head -c` 管道 | S3 被压到 264（真实 469） |

因此本 spec 的所有性能验收必须遵守：字节路径里不得有 Python 或额外管道；每次换不同文件并 `posix_fadvise(DONTNEED)` 清缓存；必须扫并发（单流数字区分不出「协议单连接瓶颈」与「介质瓶颈」）。

## 现状盘点

### Python 字节路径热点

`backend/app/services/library/media_storage.py`（551 行）：

| 行 | 代码 | 作用 |
|----|------|------|
| 333 | `async for chunk in resp.aiter_bytes(chunk_size)` | `get_stream` 读取 |
| 547-548 | `async for chunk … / await out.write(chunk)` | `materialize` 落临时文件 |
| 256 | `put_file` | 上传 |
| 395 | `put_dir` | HLS 多文件上传 |

### 已存在但未接线的 Rust crate

`mediahub-core` 已由 `Dockerfile` Stage 1 用 maturin 编译并 `pip install` 进生产镜像（`Dockerfile:5-23`、`Dockerfile:76-78`），但**全仓没有任何 Python 代码 import 它**。

它导出 `extract_audio` / `get_video_metadata` / `generate_thumbnail` / `segment_video` / `is_segmented` / `cleanup_segments`，全部通过 `std::process::Command` 调用 ffmpeg 二进制（`ffmpeg.rs` 11 处、`hls.rs` 2 处），依赖仅 `pyo3 / serde / serde_json / thiserror`，**没有任何 HTTP、S3 或流式 IO 能力**。

因此：**接线这些 ffmpeg wrapper 不会带来任何性能收益** —— Python 调 ffmpeg 也是 subprocess，字节全在 ffmpeg 进程内部流动，两边都不经过解释器。本 spec 不接它们。

### 重复的路径解析

音频路径解析散在 4 处，注释自己都写着 "mirror it here"：

- `app/workflows/ai_transcription.py:124`、`:266`、`:293`
- `app/services/ai/transcribe/whisper_service.py:65`

两者是上下层关系（DBOS workflow 层 → provider 层），不是二选一，因此切 S3 时两处都要改，漏一处即断。

## 范围

### 做

1. `mediahub-core` → `nous-core`（crate 名、模块名、`pyproject.toml`、`Dockerfile`）
2. `nous_core` 新增 Rust 流式 IO：`fetch_to_file` / `put_file`
3. `materialize()` / `ObjectStore.put_file()` 内部改调 Rust（带开关）
4. 抽出 `AudioSourceResolver` / `DerivedArtifactPaths` / `MediaKeyBuilder` 三个类
5. 拆分 `transcode_service.py`（1112 行）
6. 安全范围的品牌改名

### 不做（及原因）

| 不做 | 原因 |
|------|------|
| 接线现有 ffmpeg wrapper | 零性能收益（见上），白担回归风险 |
| Rust 直连 SeaweedFS S3（SigV4） | 收益仅 1%（476 vs 470），却要绕过 Supabase 权限层、维护第二套凭证 |
| 改 `public.mediahub_models` 表名 | **活表**。CLAUDE.md 明载「migration 与代码部署无顺序保证」，需独立 PR + view 兼容过渡 |
| 改 `mediahub_model_repository` 及其标识符 | 与上一条**必须同批**。该 Python 名镜像的正是活表 `mediahub_models`，只改代码不改表会变成 `NousModelRepository` 去读 `mediahub_models`，比两边都不改更糟。且涉及 30 个文件 / 98 行，其中 26 个测试文件用**字符串路径 monkeypatch**（`"app.repositories.mediahub_model_repository.get_…"`）—— 漏改一处会静默 patch 失败、测试照常通过却测了假目标 |
| 改 `deploy/nas/`、`docker/` 里的 `mediahub-*` 容器名 | 那是 NAS 老栈**真实跑过**的容器名。CLAUDE.md 保留这些文件正是为了将来恢复 NAS 双轨，改名会让恢复时对不上 |
| 改 `MEDIAHUB_ROLE` / `MEDIAHUB_TOKEN` | 值在仓库外的 `secrets/backend.env`。CLAUDE.md 已记录同类事故：改了 git 配置、PR 合了、CI 绿了、容器也重建了，但配置没生效 |
| 改动存储位置 | 属 spec #2 |

## 架构

### 责任分层

| 层 | 职责 | 禁止 |
|----|------|------|
| Python | 算 key、鉴权、路由决策、任务编排 | **不碰字节** |
| `nous_core`（Rust） | HTTP 拉/推、落盘 | 不懂业务语义 |
| ffmpeg | 转码 / 抽帧 / 切片 | 本 spec 不改 |

### `nous_core` 新增接口

```rust
#[pyfunction]
fn fetch_to_file(url: &str, headers: Vec<(String, String)>, dst: &str) -> PyResult<u64>

#[pyfunction]
fn put_file(src: &str, url: &str, headers: Vec<(String, String)>) -> PyResult<()>
```

新依赖：`reqwest`（启用 `rustls-tls`，不引入 OpenSSL，避免 slim 镜像缺库）+ `tokio`。

**硬性实现约束：两个函数都必须用 `py.allow_threads()` 释放 GIL。** 否则并发下载时 Rust 会比 Python 更慢 —— Python 的 `async for` 至少在 await 点让出控制权，而持有 GIL 的同步 Rust 调用会把整个事件循环卡死。

### 重命名影响面

| 位置 | 改动 |
|------|------|
| `mediahub-core/Cargo.toml` | `name = "nous_core"` |
| `mediahub-core/pyproject.toml` | 包名 |
| `Dockerfile:5-23` | build stage 的 `COPY mediahub-core/` |
| `Dockerfile:76-78` | `pip install /tmp/mediahub_core*.whl` |
| 目录本身 | `mediahub-core/` → `nous-core/` |

因为当前无任何 Python 调用方，重命名的调用方风险为零 —— 这是做这件事的最佳时机。

## 函数整合

| 新类 | 收拢什么 | 收益 |
|------|---------|------|
| `AudioSourceResolver` | `ai_transcription.py:124/266/293` + `whisper_service.py:65` 共 4 处重复 | spec #2 改音频落位只需改一处 |
| `DerivedArtifactPaths` | 缩略图 / sprite / HLS 的落盘位置决策 | spec #2 把派生产物改写 S3 时只改这一个类 |
| `MediaKeyBuilder` | `content_key` / `content_key_from_sha` / `_object_key` / `to_file_path` / `hls_key*` | 键构造集中，便于 spec #4 迁移校验 |

`transcode_service.py`（1112 行）拆为：

- `transcode_service.py` — 编排
- `hls_publisher.py` — HLS 产物发布
- `transcode_probe.py` — 探测 / ffprobe

## 数据流

### 读取

```
async with materialize(file_path) as p:      # 调用方写法不变
    ffmpeg(p)

resolve_media_source(file_path) → MediaLocation
  ├ 文件系统行 → 直接 yield 真实路径            （零拷贝，不变）
  └ sb:// 行:
      ① Python  算出 signed URL + headers        （鉴权决策）
      ② Rust    nous_core.fetch_to_file(...)     ← 字节不进解释器
      ③ yield   临时文件路径
      ④ 退出    删除临时文件                      （不变）
```

### 写入

```
store_local_file(path)
  ├ sha256_file()                  ← 保持 Python（hashlib 是 C 实现且释放 GIL，非瓶颈）
  └ ObjectStore.put_file()  →  nous_core.put_file(src, url, headers)
```

## 错误处理

| 场景 | 处理 |
|------|------|
| Rust 侧 IO 失败 | 映射为 Python 异常（`errors.rs` 的 `MediaError` 扩展 IO 变体） |
| **中途断连 / 写入中断** | **Rust 侧负责 `unlink` 半成品文件** |
| 超时 | 沿用 `_STORAGE_CALL_TIMEOUT_S` 语义，传给 reqwest |

中断清理是最容易漏的一条：现行 Python 版本靠 `finally` 删临时文件，换成 Rust 后失败路径改变。若不清理，`materialize` 会 yield 一个被截断的文件，ffmpeg 拿到后**产出静默错误的结果**（不报错、内容不完整），是最难排查的故障形态。

## 回滚

```python
FEATURE_RUST_STREAM_IO: bool = False   # 默认关
```

`materialize` / `ObjectStore.put_file` 内部二选一，关掉即回到纯 Python 路径。

不走 Module Control Center（`storage_flag.py` 那套）—— 那是业务能力开关，本项只是实现替换，用 env flag 更轻，也不需要 DB 往返。

## 测试策略

| # | 测什么 | 为什么必须有 |
|---|--------|-------------|
| 1 | **httpx `aiter_bytes` 真实基线** | 243 MB/s 是用 `urllib` 测的，非应用实际路径。**若 httpx 本就跑 400+，整个 Rust 方案作废** —— 前置闸门 |
| 2 | 字节等价性 | 同一对象经两条路径拉取，sha256 必须一致 |
| 3 | 失败注入 | 中途断连 → dst 被清理 + 异常正确抛出 |
| 4 | **并发下 GIL 释放** | 8 并发 `materialize`。忘记 `allow_threads` 会在此处表现为**比 Python 更慢** |
| 5 | 回归 | `materialize` 现有 8 个调用方的测试全绿 |
| 6 | 性能验收 | 用 `scripts/bench-storage.py` 的方法学（无 Python 在字节路径、清缓存、扫并发） |

第 1 项是前置闸门，必须在写 Rust 代码之前完成。

## PR 切分

CLAUDE.md 强制「纯 refactor PR 不许夹带逻辑改动，24h 内必须合入」，且改名会产生 181 个文档文件的 diff，与结构改动混在一起无法 review。故拆三个 PR：

```
PR1  chore/brand-nous                纯改名,零逻辑
      ├ mediahub-core → nous-core（含目录、Cargo.toml、pyproject.toml、Dockerfile）
      ├ docs × 181
      └ 修正 pyproject.toml:91-93 写错的 github URL（真实仓库是 iocrazy/nous-app）

PR2  refactor/storage-consolidation   纯结构,零行为变化      ← 24h 内合入
      ├ AudioSourceResolver / DerivedArtifactPaths / MediaKeyBuilder
      └ 拆 transcode_service.py（1112 行）

PR3  feature/nous-core-stream-io      Rust IO 接入
      ├ 前置:测出 httpx 真实基线（测试策略 #1）
      ├ nous_core 新增 fetch_to_file / put_file
      └ FEATURE_RUST_STREAM_IO 开关,默认关
```

顺序不可调换：PR3 要改的 `materialize()` 正处于 PR2 的整合范围内，反序必然产生语义冲突。

## 风险

| 风险 | 缓解 |
|------|------|
| httpx 基线证伪前提 | 测试策略 #1 作为前置闸门，证伪则本 spec 的 Rust 部分终止，仅保留 PR1/PR2 |
| 忘记释放 GIL 导致并发劣化 | 测试策略 #4 显式覆盖 |
| 中断留下截断文件 | Rust 侧清理 + 测试策略 #3 |
| `reqwest` 引入 OpenSSL 依赖使 slim 镜像构建失败 | 指定 `rustls-tls` feature |
| PR2 与他人并行改动冲突 | 遵守 24h 合入纪律；开工前 `git rebase origin/master` |

## 未决

- spec #3（读取路径）可能选择 nginx 直连 S3。若如此，服务端读取不再经过 Python，`fetch_to_file` 的价值将收敛到「后处理拉取源文件」这一场景。这不影响本 spec 的正确性 —— 后处理拉取本身就是热路径 —— 但会影响后续对 Rust 投入的评估。

---

# 事后结论（2026-07-27）：PR3 的前提被证伪，Rust 部分未合并

**PR1（品牌统一）与 PR2（函数整合 + 拆分 transcode_service）已合入并在生产验证。PR3（Rust 流式 IO）实现完成、测试齐备，但实测显示零收益，故未合并。**

分支保留在 `feature/nous-core-stream-io`（6 个 commit，11 文件，+2380 行）。

## 证伪过程

本 spec 的全部依据是「Python 是字节路径的瓶颈」。这个判断经历了四轮测量，**每次改进方法学，收益都在缩水，直到归零**：

| 阶段 | 测法 | httpx 读数 | 推出的 Rust 收益 |
|------|------|-----------|-----------------|
| spec 设计 | `urllib` 手写脚本 | 243 MB/s | ~2× |
| Task 8 闸门 | `ObjectStore.get_stream`（真实路径） | 369 MB/s | ~27% |
| Task 11 A/B | 交错测 Rust vs Python | — | 区分不出（±2%，符号翻转） |
| 事后复核 | **交错测 curl vs Python** | 392 MB/s | **0%** |

最后一轮是决定性的。用与 Task 11 相同的交错方法学（同一对象、同一负载窗口、A/B 交替、多轮取中位）直接比较 curl 与 Python：

```
第1轮  curl 346.6   python 374.2
第2轮  curl 384.3   python 359.9
第3轮  curl 398.0   python 394.6
第4轮  curl 396.1   python 404.5
第5轮  curl 401.6   python 389.5
第6轮  curl 393.9   python 398.6
────────────────────────────────
中位数  curl 395.0   python 392.0   差异 +0.8%
范围    curl 347–402  python 360–404   完全重叠，六轮互有胜负
```

**Task 8 的「httpx 369 vs curl 470 = 27% 缺口」是测量伪影** —— 两个数字取自不同时段，而该路径实测有 ~30% 的时序漂移（同一对象同一代码路径，一次会话内从 476 漂到 365）。

结论：**Python 本来就不慢，所以 Rust 不可能更快。** Task 11 的 A/B 结果正确，且与实施者猜测的 `reqwest::Client::new()` 无连接复用无关 —— 端点是 `http://`（无 TLS），局域网 TCP 握手 <1ms，对 899 MB 对象仅占 0.04%，填不上 27% 的缺口。

## 方法学教训

这条路上共有六次测量失败，全部是同一模式：**测量工具自己进入了字节路径，或比较的两个数字来自不同时段**。

| # | 错误 | 虚假结果 |
|---|------|---------|
| 1 | `dd` 反复读同一文件 | 页缓存命中，CIFS 虚报 92 MB/s / 0 ms |
| 2 | Python `urllib` 读循环 | S3 被压到 243（真实 476） |
| 3 | `curl \| head -c` 管道 | S3 被压到 264（真实 469） |
| 4 | 千兆网卡下比较存储 | 92 vs 64 是链路上限，不是存储差异 |
| 5 | 非交错比较 httpx 与 curl | 30% 漂移伪装成 27% 系统性差距 |
| 6 | `probe_httpx_baseline.py` 切换开关 | 该脚本从不读 `FEATURE_RUST_STREAM_IO`，测的是同一条路径两次 |

**可复用的规则：**

1. 字节路径里不得有测量工具本身（不要管道、不要 Python 读循环、不要额外拷贝）
2. 每次换不同文件并 `posix_fadvise(DONTNEED)`，否则测的是页缓存
3. 比较两个方案必须**交错采样**（A,B,A,B…），不能先跑完 A 再跑 B
4. 必须扫并发档位 —— 单流数字区分不出「协议单连接瓶颈」与「介质瓶颈」
5. 报告每轮原始值与分布范围，不只报中位数；分布重叠就是「区分不出」
6. 先确认被测开关真的被读取（第 6 条那种 bug 会让 A/B 悄悄测同一条路径）

工具：`scripts/bench-storage.py`（CIFS vs S3，含上述 1/2/4）、`backend/scripts/probe_rust_vs_python_stream_io.py`（交错 A/B，含 3/5/6）。

## PR3 分支里仍有价值的东西

若将来存储或网络条件变化（更快的介质、更高的并发、或 Python 侧出现新瓶颈），分支可直接重测：

- `nous_core.fetch_to_file` / `put_file` —— 实现完整，9 个测试全部做过反向验证（临时破坏被守护的生产代码、确认测试变红）
- `OnceLock<Runtime>` 进程级共享 runtime —— 避免每次调用起 48 个 worker 线程（一次 HLS 发布约 300 次调用 = 14,400 次线程创建）
- `FEATURE_RUST_STREAM_IO` 开关 —— 默认关，接线已完成
- 交错 A/B 脚本 —— 重测只需跑一条命令

## 一个与性能无关、但值得单独记的发现

**`py.allow_threads()` 不是性能优化，是正确性要求。**

反向验证时去掉它，测试不是变慢而是**真死锁** —— 测试服务端跑在同进程另一个 Python 线程，其 handler 是纯 Python 代码、执行前必须拿到 GIL；调用方持有 GIL 不放，而 Rust/tokio 内部不回调 Python（无自然释放点），于是服务端线程永远等不到 GIL 去响应它自己应该处理的请求。实测连单次请求都挂死 170+ 秒，线程全部 parked 在 `futex_do_wait`。

生产后果更严重：接线方式是 `await asyncio.to_thread(nous_core.fetch_to_file, ...)`，而后端是 FastAPI（async），主事件循环需要 GIL 才能继续跑 —— **不释放则传输期间整个进程冻结**，不只是调用方。`materialize()` 有 14 个调用方，大文件传输 0.5–2 秒。

**任何未来的 PyO3 扩展都必须遵守这条**，且必须有一条并发计时测试（而非依赖死锁副作用）：死锁只能抓「完全不释放 GIL」，抓不到「释放了但请求被串行化」（例如把 runtime 包进 `Mutex`、或改用 `current_thread`）。后者已用 Task 10 的证伪实验确认 —— 故意加 `Mutex` 包裹后，8 并发耗时从 <1s 变成 1.80s（≈8×0.2s 精确串行）。
