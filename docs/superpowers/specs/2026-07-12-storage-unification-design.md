# 存储统一（Storage Unification）设计

> Status: DRAFT（2026-07-12，用户与 Claude brainstorming 定稿）
> 决策来源：2026-07-10 用户翻案原 ADR（`docs/architecture/2026-07-06-adr-object-storage-vs-filesystem.md`，实施时需更新其状态）——"不想走两套系统，统一走自托管 Supabase Storage"。
> 先例：小图对象存储（`docs/superpowers/plans/2026-07-05-chat-images-object-storage.md`，已 LIVE）。

## 1. 范围

### 迁移（文件系统 → Supabase Storage）

| 模块 | 现路径（相对 `DOWNLOAD_PATH`） | 写入代码 |
|---|---|---|
| 上传 resources / chat 附件 / 版本 | `teams/{scope}/uploads/{rid}/v{N}/{name}` | `resources_service.py` upload_resource / 多版本 |
| project_files | `mediatrack/{project_id}/...`（**无 scope，顺手修**） | `projects_service.py` |
| storyboard 产物 | `teams/{team}/storyboard/{proj}/{images,previews,splits}/`（**独立根 `NAS_BASE_PATH`，废除**） | `storyboard_service.py` |
| generated_media | 双轨 flag（`FEATURE_CHAT_MEDIA_OBJECT_STORE` 默认关） | 已完成，本 epic 收官：flag 常开 |

### 明确不迁（及理由）

| 模块 | 理由 |
|---|---|
| 下载媒体 parsed_media（`global/resources/web/{platform}/{id}/`） | 用户拍板"下载先不动"（2026-07-12）。全局共享去重 + yt-dlp/ffmpeg 直写，POSIX 主场 |
| HLS 转码产物（`.../hls/`） | ffmpeg 写盘 + 播放器高频拉段；可再生衍生数据。原片进 Storage，HLS 段留盘 |
| sideload inbox（`sideload-inbox/`） | O(1) rename 是其存在意义；对象存储下退化为全量复制（百万文件 4h → 不可行） |
| inspiration 附件 | 已在 Storage（bucket `inspiration`，bucket+path 分列存），不动 |

## 2. 存储路径方案

### 逻辑寻址（我们的设计）

一个总 bucket `library`，全模块统一**内容寻址** key，模块语义全留 DB：

```
sb://library/t{scope_id}/{sha256[:2]}/{sha256[2:4]}/{sha256}{ext}
```

- **`t{scope_id}`**：key 第一段。scope = team snowflake，个人 = personal-team（Spec1 之后 user 即 scope），用户/团队同一维度，不需要两套前缀。
- **project 不进 key**：项目归属是 DB 关系；项目可移动、素材可跨项目复用，语义进 key 是负债。
- **文件名/版本/resource_id 不进 key**：DB（`resources` / `file_versions` / `project_files` / `storyboard_assets`）是唯一权威；文件名经 `Content-Disposition` 返回。
- **内容寻址收益**：scope 内跨模块自动去重（storyboard 图 promote 成 resource = 同一对象，零复制）；与已上线 chat-media / `content_sha256` 列 / `resolve_media_source` 完全同构。

### bucket 全景

| bucket | 角色 | 状态 |
|---|---|---|
| `library`（新） | 总资产桶：uploads / project_files / storyboard | 本 epic 新建（migration） |
| `chat-media` | generated_media | 已上线，共存；epic 尾声可选并入 `library`（resolver 认路径里的 bucket，共存零成本） |
| `inspiration` | 灵感附件 | 不动 |
| `thumbnails`（mig 052） | 休眠 public 桶，0 对象 | 清理项：删除 |

### 物理布局（storage-api file backend，NAS）

```
/volume2/sources/MediaHub.library/
├── teams/… global/… mediatrack/…     ← 文件系统轨（容器 /app/downloads = DOWNLOAD_PATH），迁完后只剩下载链路
└── object-storage/
    └── nous/nous/                 ← GLOBAL_S3_BUCKET=nous + STORAGE_TENANT_ID=nous（顶层，用户定稿）
        ├── library/t{scope}/{ab}/{cd}/{sha}.ext
        ├── chat-media/…
        └── inspiration/…
```

- 两段顶层前缀是 storage-api 写死的布局（模拟 S3 全局桶 + 租户），只能改名不能减层。
- **前置 ops 项：`media/` → `nous/` 改名**（现值 `GLOBAL_S3_BUCKET=media`）。同卷顶层目录 rename，O(1) 原子：
  1. `sudo docker stop <storage 容器>`（chat 上传有文件系统回落兜底）
  2. `mv object-storage/media object-storage/nous`
  3. compose 改 `GLOBAL_S3_BUCKET: nous` → `docker compose up -d --no-deps storage`（铁律 `--no-deps`）
  4. 冒烟：拉已有 chat 图 + signed URL
  - 必须在开新写路径之前完成，避免新对象落旧前缀。
- 物理路径没有业务消费方（只有备份脚本可见）；寻址永远走 API 层 `sb://bucket/key`。
- **禁止绕过 storage-api 直接动 `object-storage/` 内部文件**（`storage.objects` 元数据库会孤儿化）——顶层改名是唯一例外（元数据不记物理前缀）。

## 3. 位置真相与路由

### 位置列（沿用 generated_media 双轨约定）

各表 `file_path` 列继续是唯一位置真相，混存两种形态：
- legacy 行：相对路径（如 `teams/42/uploads/123/v1/a.mp4`）→ 文件系统
- 新行：`sb://library/t42/ab/cd/{sha}.mp4` → object store

**`resolve_media_source(file_path)`（`media_storage.py`）是唯一解释点**，推广到全部读端；禁止散落 `startswith("sb://")`。

### 路由改前 → 改后

| 读端 | 改前 | 改后 |
|---|---|---|
| `/resources/{id}/file`、`/cover` | `FileResponse(DOWNLOAD_PATH/rel)` | legacy 行不变；`sb://` 行 → **默认流式代理（Range 透传，沿用 generated_media 已上线模式）**；`STORAGE_SIGNED_URL_PUBLIC_BASE` 配置后升级为 302 签名 URL（前提 ops 项：公网入口路由 `/storage/v1`，kong 目前未配） |
| project_files / storyboard 读端 | FileResponse | 同上，经 resolver 分流 |
| P3 nginx 302 直出（`/f/{rel}` secure_link，未激活） | 只对 fs 有意义 | 只服务 legacy 行；`sb://` 行走 storage 签名 URL。**对前端都是 302，前端零改动**；存量迁完整体退役 |
| HLS 播放 | FastAPI FileResponse | 不变（fs） |
| ffmpeg / 转码 / 缩略图 / promote | 直读路径 | 新 helper `materialize(source) -> local_path`：legacy 直接返回路径；`sb://` 拉临时文件（用完删）。**这是原 ADR 三大适配项的落地形态** |
| agent vision / publish | 已走签名 URL（`STORAGE_SIGNED_URL_PUBLIC_BASE` 改写） | 不变，模式推广 |

### 权限模型（本 epic 不变）

bucket 全 private，只有后端 service key 读写；所有读经 FastAPI endpoint 做 membership 校验后签 URL/代理。浏览器直连 + bucket RLS 是以后的独立决策（参考 `bug_rls_log_tables_world_readable` 教训）。

## 4. 写路径设计

- 统一入口：`ObjectStore.store(scope_id, bytes|stream, ext) -> sb://library/...`（扩展现有 `media_storage.py`：sha256 → HEAD 去重 → PUT → 返回 key）。
- 各 service 换写入调用，受 **`FEATURE_UNIFIED_STORAGE`** flag 控制（默认 false，标准 2 周 flag 生命周期，按模块可再细分子 flag 视 PR 拆分而定）。
- 大文件：流式 temp 文件 + TUS/流式 PUT（storage-api `FILE_SIZE_LIMIT` 已调 5GB；≥5GB 拒绝并回落）。
- 失败模式：storage PUT 失败 → 回落文件系统写 legacy 路径 + 响亮日志（上传永不因 storage-api down 而硬失败，沿用 chat-media 先例）。

## 5. 迁移方式（双轨 + 按行渐进，行级幂等）

1. **写路径切换**：新写全走 Storage（flag 灰度：dev 冒烟 → prod flag on）。
2. **存量迁移 job**（DBOS workflow）：逐行——读 fs 文件 → sha256 → PUT（存在即跳过=幂等）→ UPDATE `file_path` 为 `sb://` → 读回验证 → 删原文件。
   - 每行独立事务：天然断点续传、可按模块/scope 分批、随时暂停。
   - 迁移是同 NAS 本地拷贝（backend 与 storage-api 同机），无网络瓶颈。
   - URL 对外不变（前端只认 `/file` 端点，端点内部经 resolver 分流）。
3. **收尾**：
   - 某模块 legacy 行清零 → 删该模块 fs 写路径代码 + `NAS_BASE_PATH` 配置源；
   - 全部清零 → P3 nginx 直出退役、`DOWNLOAD_PATH` 只剩下载/HLS/sideload 使用；
   - 可选：`chat-media` 并入 `library`、删 `thumbnails` 桶。

## 6. 风险

| 风险 | 缓解 |
|---|---|
| storage-api down 时上传 | 文件系统回落 + 响亮日志（既有模式） |
| 迁移 job 删原文件后对象损坏 | 删除前读回验证 sha256；job 支持 dry-run（只 PUT 不删） |
| 双轨解释散落 | resolver 唯一解释点，code review 红线 |
| `nous/` 改名与新写路径顺序颠倒 | 改名列为前置 ops 项，flag 开启 checklist 里硬性核对 |
| 漂移栈级联重启 | 一切 storage 容器操作 `--no-deps`（既有铁律） |
| 同盘"耐久性错觉" | 明示：本 epic 买的是单一系统/统一 key 空间/去重，不是耐久性 |

## 7. 非目标

- 下载链路 / HLS / sideload 迁移（明确不动）。
- 浏览器直连签名上传/下载 + bucket RLS。
- 云 OSS / CDN / MinIO（S3 面已就位，未来是配置级变更）。
- imgproxy 动态缩略图。

## 8. 成功标准

- 新上传 / project_files / storyboard 新产物落 `sb://library/...`（storage 健康时 100%；回落仅发生在 storage-api 故障，且有 ERROR 日志可查）。
- 存量迁移 job 可分批跑完目标模块，legacy 行清零，删码收尾。
- 全程前端零改动、URL 不变、错误漏斗（`application_logs`）无新增 storage 相关 ERROR。
