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
