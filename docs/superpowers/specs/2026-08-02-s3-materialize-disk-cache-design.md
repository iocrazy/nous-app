# S3 materialize 磁盘缓存 设计文档

**Goal:** 消除下载后 followup 链(缩略图/抽音频/转码/AI)对同一 S3 对象的重复全量拉取——`materialize()` 加内容寻址的本地磁盘读通缓存,同链 4 次拉取降为 1 次。

**背景与量化**(2026-08-02 实测):`materialize()` 对 sb:// 是"临时文件→用完即删、零缓存",每个新视频的 followup 链各自独立拉 3-4 次。S3(SeaweedFS,内网)拉取 266MB/s;**CIFS(DOWNLOAD_PATH 所在)只有 145MB/s——比 S3 还慢**,因此:
1. 缓存必须放 **gpupc 本地 NVMe**(新增 bind mount),放 CIFS 上毫无意义;
2. **不做上传时 seed**(从 CIFS 拷源文件进缓存比首次从 S3 拉还慢)——纯**读通缓存**:首个消费者从 S3 拉进缓存(266MB/s),后续命中本地 NVMe(GB/s)。

**为什么不是 Redis / 不是显式全链持有**(讨论结论,2026-08-01):Redis 是内存库、单值 512MB 上限、ffmpeg 需要真实路径,塞视频进 Redis 还得再落盘,负优化;显式"做完才释放"= 跨进程分布式引用计数(链条是各自独立异步的 DBOS workflow,还有三天后才来的 AI 任务),计数泄漏就是 FS 债务回潮。LRU 读通缓存是同等效果的免协调实现:存在即缓存、冷了即删、无状态崩溃安全。

## 设计

### 配置(config.py,DOWNLOAD_PATH 旁)
- `MEDIA_S3_CACHE_DIR: str = ""` —— 空 = 禁用(默认,安全回退到现行 temp 行为)
- `MEDIA_S3_CACHE_MAX_GB: float = 20`

### materialize() sb:// 单对象分支(media_storage.py)
- 禁用/目录不可用 → 现行为逐字不变(temp 下载、退出删)。
- 启用:
  - 缓存文件名 = `sha256(key)[:32] + suffix`(**对 key 哈希而非假设 key 含内容 sha**——derived/album/hls key 不是内容寻址的)。
  - 命中 → `os.utime()` 刷新 mtime(LRU 信号;atime 受 relatime 不可靠)→ yield 缓存路径,**退出不删**。
  - miss → 下载到同目录 `.tmp-{uuid}` → `os.replace` 原子入位(并发同 key 竞态安全:各写各的 tmp,replace 幂等)→ `chmod 0o444`(防调用方误改;转码/缩略图/whisper 均已确认只读源文件)→ 触发淘汰 → yield。
  - 下载中途失败 → 删 tmp,异常上抛(不留半文件;.tmp- 前缀不会被当命中)。
- 淘汰:目录总大小 > 上限 → 按 mtime 从旧到新删至上限内,忽略单条删除失败(best-effort)。**正在被 ffmpeg 读的文件被删也安全**(POSIX unlink 语义:fd 持有者不受影响)。`.tmp-*` 不参与淘汰统计外的删除(按 mtime 一并可清,老 tmp 是残尸)。

### 部署(deploy/gpu-server/docker-compose.yml)
- backend + worker 加 bind:`/media/heygo/program/datahub/nous/data/s3cache:/app/s3cache`
- 两容器 `environment` 加 `MEDIA_S3_CACHE_DIR=/app/s3cache`(进 compose IaC,不进 backend.env)
- 部署前宿主机 `mkdir -p` 该目录(uid 1000)。

### 不做(YAGNI)
- 上传 seed(见上,负收益);album/hls 前缀 materialize(不存在此用法);跨机共享缓存;命中率指标。

## 测试
- 禁用 → temp 行为(退出后文件消失)。
- miss → store.get_stream 调 1 次,缓存文件存在且 0444。
- 命中 → store 不被调用,mtime 被刷新,退出后文件仍在。
- 淘汰 → 超上限时最旧 mtime 文件被删、新文件保留。
- 下载失败 → tmp 清掉、异常上抛、缓存无残留。

## 验收(部署后)
- 触发一次真实重下 → followup 链日志确认后续阶段命中(首拉 1 次后无再拉);`ls /app/s3cache` 出现 0444 缓存文件;readyz 正常。
