# Admin Media 页存储增强 设计文档

**Goal:** 在 admin 现有 Media 页(不新建独立页)集成存储可观测性:每个视频的详情面板加 "Storage" 段(各资产 sb:// key/大小/单条 S3 核验),页面顶部加存储健康统计卡 + 存储状态筛选 + 全库深度 S3 扫描按钮——让管理员在"存储已全迁 S3、DB 是索引"的模型下看清"哪个视频在哪里、哪些坏了"。

**Architecture:** 只读。统计/清单列增强/廉价坏链判定走**实时 DB 查询**(秒回);"S3 对象真实存在性"深度探测走**按钮触发的后台 DBOS workflow + 缓存结果**(结果存 task metadata,不建新表);详情面板另有**单视频即时核验**(几个 HEAD,同步返回)。全部塞进现有 media 页/详情弹窗,复用 NotionTable / apiClient / videos_router 模式。

**Tech Stack:** 后端 FastAPI + DBOS workflow + asyncpg;前端 admin React app(admin/src),NotionTable + useNotionTable + apiClient。

**Approved mockup:** https://claude.ai/code/artifact/e8f895b4-f72b-4299-a369-d44006e7c0ad(布局/配色/状态徽章按此)

## Global Constraints

- 存储后端:Supabase Storage(Kong)→ S3 → SeaweedFS(nas-B),bucket=`library`。
- sb:// key 形态:视频/版本文件 = 内容寻址 `sb://library/t{scope}/{sha[:2]}/{sha[2:4]}/{sha}{ext}`;派生 = `sb://library/derived/{rid}/{filename}`;HLS = `sb://library/hls/{rid}/{vid}/master.m3u8`。
- DB 索引列:`parsed_media.download_path`(视频)/`cover_download_path`、`resources.thumbnail_path`/`cover_image_path`/`file_path`、`resource_versions.hls_path`/`file_path`。
- 页面**只读**:不带写操作(重下/删/迁移复用已有端点,留第二期)。
- admin 权限:`AdminAuthDep`(user_profiles.role='admin')。
- ⚠️ 部署:后端走 deploy-gpu.yml;**admin 前端无自动部署**,需手动 `cd deploy/gpu-server && NOUS_ANON_KEY=<key> docker compose up -d --build admin`。
- UI 文案英文(Title Case),跟随 admin 现有页风格。
- 存储状态四态与配色(mockup 图例):`ok`=绿 / `broken`=红(S3 对象缺失,来自扫描) / `no_video`=灰(download_path NULL,可 retry) / `fs_residue`=橙(回归探针,应恒 0)。`failed` 归入 no_video 展示(徽章文案 "No video")。

---

## 组件与接口

### 1. 后端 `backend/app/api/admin/storage_router.py`(新)

挂载:`admin_router.include_router(storage_router, prefix="/storage", tags=["Admin - Storage"])`,全端点 `AdminAuthDep`。

#### `GET /admin/storage/stats`
```
{
  "videos": { "count": int, "size_bytes": int },      // download_path sb:// 的 count + sum(storage_size)
  "fs_residue": int,     // 7 个产物列任一仍 FS 的行数(应 0)
  "orphans": int,        // parsed_media 无对应 resource
  "hls_ready": int,      // rv.hls_path sb:// 计数
  "broken": int|null,    // 最近一次深扫的 missing 数(无扫描 null)
  "last_scan": { "at": str, "scanned": int, "missing": int, "errors": int } | null
}
```
纯 DB 聚合,`broken`/`last_scan` 读最近一次 audit workflow 的 task metadata。

#### `GET /admin/storage/media-status`
给 media 列表行叠加存储信息(前端与现有 videos 列表数据合并渲染,不改 videos_router):

Query:`media_ids`(逗号分隔,≤200 个)。
```
{ "rows": [ { "media_id": str,
    "video_key": str|null, "video_size": int|null,
    "cover_ok": bool, "thumbnail_ok": bool, "hls_ok": bool,
    "storage_status": "ok"|"no_video"|"fs_residue",   // broken 由前端叠加 audit missing 集
    "scope_id": str|null } ] }
```
SQL:`parsed_media pm` 左连 `resources r`、LATERAL `resource_versions`(最新一条)、LATERAL `resource_items`(一个 scope);`storage_status` CASE 派生(任一产物列 FS→fs_residue;download_path NULL 或 status failed→no_video;否则 ok)。

#### `GET /admin/storage/media/{media_id}/detail`
详情面板 Storage 段的数据:
```
{ "assets": [ { "kind": "video"|"cover"|"thumbnail"|"sprite"|"hls",
    "key": str|null, "size_bytes": int|null, "present_in_db": bool } ],
  "scope_id": str|null }
```
- video:pm.download_path + pm.storage_size;cover:pm.cover_download_path;thumbnail:r.thumbnail_path;sprite:约定 key `derived/{rid}/preview_sprite.jpg`(无 DB 列,present_in_db 恒 false,仅给 key);hls:rv.hls_path(附 tier 数,从 master.m3u8 不解析——只报 key)。
- size 只有视频有(pm.storage_size);其余 null(不为小资产补列)。

#### `POST /admin/storage/media/{media_id}/verify`
**单视频即时核验**(同步,详情面板 "Verify on S3" 按钮):对该视频 detail 里每个非 null key 逐个 `store.exists()`(去 `sb://library/` 前缀),≤6 个 HEAD,同步返回:
```
{ "results": [ { "kind", "key", "exists": bool|null } ] }   // null=存储调用异常(不确定)
```

#### `POST /admin/storage/verify`
派发**全库深度扫描**(镜像 storage_migration_router 的 dispatch 范式:建 task_tracking 行 + DBOS.start_workflow)。已有 `storage_audit` 在 queued/in_progress → 返回其 workflow_id + `already_running:true`(防重复风暴)。

#### `GET /admin/storage/audit`
最近一次扫描结果(读 task metadata):
```
{ "status": "none"|"queued"|"in_progress"|"completed"|"failed",
  "scanned": int, "errors": int, "scanned_at": str|null,
  "missing": [ { "key", "kind", "media_id"|null, "resource_id"|null } ] }
```

### 2. 后端 workflow `storage_audit_workflow`(新,`backend/app/workflows/storage_audit.py`)

`@DBOS.workflow`,由全库 /verify 派发,task_tracking 追踪(路线 C:phase 由 trigger 同步,业务结果 patch_metadata 写,失败 raise)。

1. `collect_keys_step`:一次 SQL UNION 收集全部 sb:// key + (kind, media_id/resource_id 归属),仅 LIKE 'sb://%',去重。七个来源列:pm.download_path(video)/pm.cover_download_path(cover)、r.thumbnail_path(thumbnail)/r.cover_image_path(cover_image)/r.file_path(file)、rv.hls_path(hls)/rv.file_path(version_file)。
2. 分块并发探测:每块 50 个 `asyncio.gather(store.exists(key)…)`;`store=library_store()`,key 去 `sb://library/` 前缀。单 key 调用异常按"不确定"计入 `errors`,**不**计 missing(网络抖动不误报坏链)。
3. `patch_metadata(task_id, {"kind":"storage_audit","scanned":N,"errors":E,"missing":[…],"scanned_at":<ISO>})`。missing 通常很小,metadata jsonb 足够;若 missing > 500,截断存前 500 + `missing_truncated:true`。
4. 失败路径 raise(不 return failed dict)。

### 3. 前端改动(全部在现有 media 页内)

#### `admin/src/api/endpoints/storage.ts`(新)
`getStorageStats()`、`getMediaStatus(ids)`、`getMediaStorageDetail(id)`、`verifyMedia(id)`、`postDeepVerify()`、`getAudit()` —— 薄封装 apiClient + TS 类型。

#### `admin/src/pages/media/index.tsx`(改)
- **统计卡行**:现有 Statistic 行追加 4 卡(mockup 样式):FS Residue、Orphans、Broken on S3(红,来自 audit)、HLS Ready;下方一行小字 last scan 信息("Last S3 scan X ago · N objects · M missing")。
- **工具栏**:加存储状态筛选 chips(All/OK/Broken/No video/FS residue,单选);加 "Deep Verify" 按钮(POST /verify → 轮询 task_tracking → 完成刷新 stats+audit;运行中 disable + spinner)。
- **表格**:加两列——Assets(C/T/H 三小格,绿=sb:// 齐/灰=缺,数据来自 media-status 按当前页 media_ids 批量取)、Storage(状态徽章;broken 由前端拿 audit.missing 的 media_id 集叠加)。筛选 chips 作用于当前已加载数据 + broken 筛选用 audit 集;不改 videos_router 的服务端筛选(YAGNI:行数千级,前端筛可用)。
- **详情弹窗**(现有 VideoDetail):追加 **Storage 段**(mockup 右栏样式):每资产一行(状态点+kind+key(mono,截断)+size),"Verify on S3" 按钮(POST /media/{id}/verify,行内更新状态点:绿=exists/红=missing/灰=不确定),按钮旁显示单视频核验结果时间。

#### 不新建路由/页面/导航项。

### 4. 数据流

1. 进 media 页 → 现有 videos 列表照旧 + 并行 `GET /stats`、`GET /audit`;列表渲染后按当页 media_ids `GET /media-status` 合并进行。
2. 点行 → 现有详情照旧 + `GET /media/{id}/detail` 渲染 Storage 段。
3. 详情 "Verify on S3" → 同步 POST,行内更新状态点。
4. "Deep Verify" → POST /verify → 轮询 → 刷新 stats+audit → 表格 broken 标红。

## 错误处理

- 非 admin → 403。
- 全库 /verify 已在跑 → already_running,前端提示 + 继续轮询现有 workflow。
- /audit 无扫描 → status:"none",前端统计卡 Broken 显示 "—" + "Run Deep Verify"。
- 单视频 verify 存储调用异常 → exists:null,前端灰点 + tooltip "Storage API unreachable"。
- media-status 的 media_ids >200 → 422。

## 测试

- **后端**(pytest):stats 聚合;media-status 的 storage_status 派生(ok/no_video/fs_residue 三态构造行);detail 的资产 key 拼装(含 sprite 约定 key、audio .png 缩略图);单视频 verify(mock exists 部分 False/异常→null);collect_keys_step UNION 去重只收 sb://;audit workflow(mock exists→missing+errors 计数、>500 截断);全库 verify 防重复派发。
- **前端**(vitest/RTL,若 admin 有测试基建则加;无则手测清单):统计卡渲染、chips 筛选、Deep Verify disable 态、详情 Storage 段渲染与单条核验状态点更新。

## 明确不做(YAGNI)

- 写操作(重下/删孤儿/触发迁移)留第二期。
- 不建新表(扫描结果进 task metadata)。
- 封面/缩略图不补 size 列;用量以视频 storage_size 为准。
- 不做定时扫描(只按钮触发)。
- 不做 SeaweedFS 物理 volume 视图(运维层,weed shell 管)。
- 不改 videos_router 服务端筛选(存储筛选前端做)。
