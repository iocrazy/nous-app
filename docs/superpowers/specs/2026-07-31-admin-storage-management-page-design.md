# Admin Storage 管理页 设计文档

**Goal:** 在 admin 界面加一个只读的存储/库管理页,让管理员在"存储已全迁 S3(sb://)、DB 是索引"的新模型下,能可搜索地浏览"哪个视频在哪里"、看用量统计、并发现孤儿/坏链。

**Architecture:** 只读看板。清单/统计/廉价坏链判定走**实时 DB 查询**(秒回);"S3 对象是否真实存在"这种深度探测(几千个 HEAD)走**按钮触发的后台 DBOS workflow + 缓存结果**(结果存 task metadata,不建新表)。复用现有 admin 模式(NotionTable / apiClient / AdminAuthDep / storage_migration_router 的 dispatch 范式)。

**Tech Stack:** 后端 FastAPI + DBOS workflow + asyncpg;前端独立 admin React app(admin/src,Vite),NotionTable + useNotionTable + apiClient。

## Global Constraints

- 存储后端:Supabase Storage(Kong `nous-kong:8000`)→ S3 → SeaweedFS(nas-B),bucket=`library`。
- sb:// key 形态:视频/封面/版本文件 = 内容寻址 `sb://library/t{scope}/{sha[:2]}/{sha[2:4]}/{sha}{ext}`;派生(缩略图/sprite/cover) = 固定前缀 `sb://library/derived/{rid}/{filename}`;HLS = `sb://library/hls/{rid}/{vid}/master.m3u8`。
- DB 索引列:`parsed_media.download_path`(视频)、`parsed_media.cover_download_path`、`resources.thumbnail_path`/`cover_image_path`/`file_path`、`resource_versions.hls_path`/`file_path`。
- 页面**只读**:不带任何写操作(重下/删/迁移复用已有 admin 端点,留第二期)。
- admin 权限:`AdminAuthDep`(user_profiles.role='admin')。
- ⚠️ 部署:后端 storage_router 走常规后端链(deploy-gpu.yml);**admin 前端无自动部署**,需手动 `cd deploy/gpu-server && NOUS_ANON_KEY=<key> docker compose up -d --build admin`(见 CLAUDE.md「已知缺口」)。
- UI 文案英文(Title Case),i18n 非硬性(admin 现有页多为英文直写,跟随)。

---

## 组件与接口

### 1. 后端 `backend/app/api/admin/storage_router.py`(新)

挂载:`admin_router.include_router(storage_router, prefix="/storage", tags=["Admin - Storage"])`,全端点 `AdminAuthDep`。

#### `GET /admin/storage/manifest`
视频为行的库清单,分页 + 搜索 + 筛选。

Query 参数:`q`(标题/platform_id ILIKE)、`media_type`(int)、`storage_status`(见下)、`scope_id`(bigint)、`limit`(默认 50,≤200)、`offset`。

行 SQL(核心)——`parsed_media pm` 左连 `resources r`(r.media_id=pm.id)、`resource_versions rv`(rv.resource_id=r.id)、`resource_items ri`(取一个 scope_id):
```sql
SELECT
  pm.id::text AS media_id, pm.platform_id, pm.title, pm.media_type,
  pm.download_path AS video_key, pm.storage_size AS video_size,
  (pm.cover_download_path LIKE 'sb://%') AS cover_ok,
  (r.thumbnail_path LIKE 'sb://%')       AS thumbnail_ok,
  (rv.hls_path LIKE 'sb://%')            AS hls_ok,
  ri.scope_id, pm.created_at, pm.updated_at,
  <storage_status 表达式> AS storage_status
FROM parsed_media pm
LEFT JOIN resources r ON r.media_id = pm.id
LEFT JOIN LATERAL (SELECT hls_path, file_path FROM resource_versions
                   WHERE resource_id = r.id ORDER BY id DESC LIMIT 1) rv ON true
LEFT JOIN LATERAL (SELECT scope_id FROM resource_items
                   WHERE resource_id = r.id ORDER BY id LIMIT 1) ri ON true
WHERE (:q IS NULL OR pm.title ILIKE :q OR pm.platform_id ILIKE :q)
  AND (:media_type IS NULL OR pm.media_type = :media_type)
  AND (:scope_id IS NULL OR ri.scope_id = :scope_id)
ORDER BY pm.updated_at DESC
LIMIT :limit OFFSET :offset
```
`storage_status` 派生(CASE,DB 层廉价判定):
- `fs_residue` —— 任一产物列 LIKE '%/%' 且非 sb://(回归探针,正常应 0)
- `no_video` —— download_path IS NULL(图片类/损坏待重下)
- `failed` —— video_download_status='failed'
- `ok` —— download_path LIKE 'sb://%'

`broken`(S3 对象缺失)不在此 SQL 派生——它来自深度扫描(见 /audit),前端把 audit 的 missing 集叠加到行上标红。`storage_status` 过滤对 `broken` 的支持:manifest 接口接受 `storage_status=broken` 时,改为 `WHERE media_id IN (<最近一次 audit 的 missing media_id 集>)`。

响应:`{ "rows": [ {…行…} ], "total": <匹配总数>, "limit", "offset" }`。

#### `GET /admin/storage/stats`
```
{
  "videos": { "count": int, "size_bytes": int },        // download_path sb:// 的 sum(storage_size)
  "by_media_type": [ { "media_type": int, "count", "size_bytes" } ],
  "by_scope": [ { "scope_id": str|null, "count", "size_bytes" } ],
  "object_counts": { "video": int, "cover": int, "thumbnail": int, "hls": int },  // 各 sb:// 列计数
  "fs_residue": int,     // 任一产物列仍 FS 的行数(应 0)
  "orphans": int,        // parsed_media 无对应 resource
  "broken": int,         // 最近一次 audit 的 missing 数(无扫描则 null)
  "last_scan_at": str|null
}
```
全部来自 DB 聚合查询(instant),`broken`/`last_scan_at` 读最近一次 audit workflow 的 metadata。

#### `POST /admin/storage/verify`
派发深度 S3 存在性扫描(镜像 storage_migration_router 的 dispatch 范式:建 task_tracking 行 + `DBOS.start_workflow`)。若已有一个 `storage_audit` 扫描在 `queued/in_progress`,直接返回其 workflow_id(**防重复扫描风暴**)。
响应:`{ "workflow_id": str, "already_running": bool }`。

#### `GET /admin/storage/audit`
读最近一次(最新 created_at)`storage_audit` workflow 的结果 metadata:
```
{ "scanned": int, "missing": [ { "key", "kind", "media_id"|null, "resource_id"|null } ],
  "scanned_at": str|null, "status": "queued|in_progress|completed|failed|none" }
```

### 2. 后端 workflow `storage_audit_workflow`(新,放 `backend/app/workflows/storage_migration.py` 旁)

`@DBOS.workflow`,由 /verify 派发,经 task_tracking 追踪(phase 由 trigger 同步,业务字段 patch_metadata 写)。

步骤:
1. `collect_keys_step` —— 一次 SQL UNION 收集全部 sb:// key + 归属:
   - `(pm.download_path,'video',pm.id)`、`(pm.cover_download_path,'cover',pm.id)`
   - `(r.thumbnail_path,'thumbnail',r.id)`、`(r.cover_image_path,'cover_image',r.id)`、`(r.file_path,'file',r.id)`
   - `(rv.hls_path,'hls',rv.resource_id)`、`(rv.file_path,'version_file',rv.resource_id)`
   - 仅 LIKE 'sb://%',去重。
2. 分块并发探测:每块(如 50 个)`asyncio.gather(store.exists(key) …)`,收集不存在的。`store` = `library_store()`;key 从 sb:// 去 `sb://library/` 前缀。
3. `patch_metadata(task_id, {"scanned": N, "missing": [...], "scanned_at": <trigger completed_at>})`。missing 一般很小,存 metadata jsonb 足够。
4. 失败路径 `raise`(路线 C 纪律)。

并发/限流:块大小 + `_capped` 已有的存储调用超时;总量数千个 exists 在几十秒内完成。

### 3. 前端 `admin/src/pages/storage/index.tsx`(新)

- 顶部 `Statistic` 卡片行:视频数/总量、各类型对象数、FS 残留、孤儿、坏链(last_scan_at)。
- `Tabs`:**Manifest**(可搜索/筛选 NotionTable,列:Title｜Platform ID｜Type｜Video Size｜Cover/Thumb/HLS 齐全图标｜Scope｜Storage Status 徽章)、**Broken**(audit.missing 列表 —— DB 说在 S3 但对象实际不存在的)、**Orphan**(无对应 resource 的 parsed_media —— stats.orphans 对应的行;单独 SQL:`parsed_media LEFT JOIN resources … WHERE r.id IS NULL`)。
- "Deep Verify" 按钮:`POST /verify` → 轮询 task → 完成后刷新 stats+audit;显示 last scan 时间 + 运行中状态。
- 复用 `apiClient`、`NotionTable`/`useNotionTable`、`formatBytes`/`formatDateTime`;`API_URL` 取法同 media 页。
- Storage Status 徽章配色:ok=绿、failed=红、broken=红(叠加 audit)、no_video=灰、fs_residue=橙。

### 4. 路由 + 导航

- `admin/src/App.tsx`:`import { StoragePage } from './pages/storage'` + `<Route path="/storage" element={<ProtectedRoute><StoragePage/></ProtectedRoute>}/>`。
- 侧栏导航(admin layout 的菜单配置)加一项 `Storage`(icon:数据库/硬盘),置于 Media 附近。

### 5. 前端 API 封装 `admin/src/api/endpoints/storage.ts`(新)

`getManifest(params)`、`getStats()`、`postVerify()`、`getAudit()` —— 薄封装 apiClient,附 TS 类型。

---

## 数据流

1. 进页 → `GET /stats` + `GET /manifest`(纯 DB,秒回) + `GET /audit`(展示上次扫描)。
2. 搜索/筛选/翻页 → 重新 `GET /manifest`。
3. 点 "Deep Verify" → `POST /verify` → 拿 workflow_id → 轮询 task_tracking(复用 admin 现有 task 轮询)→ 完成 → 刷新 `/stats`+`/audit`,manifest 行叠加 broken 标红。

## 错误处理

- 非 admin → 403(AdminAuthDep)。
- `/verify` 已有扫描在跑 → 返回现有 workflow_id + `already_running:true`,不重复派发。
- `/audit` 无扫描 → `status:"none"`,`missing:[]`,`scanned_at:null`。
- manifest `limit` 上限 200;`offset` 非负。
- 扫描中存储 API 抖动 → 单 key exists 失败按"不确定"处理:不计入 missing,记入 metadata `errors` 计数(避免把网络抖动误报成坏链)。

## 测试

- **后端**(pytest):
  - manifest:构造 parsed_media/resources/rv 测试行,验证 q/media_type/scope_id 筛选、storage_status 派生、分页 total。
  - stats:验证聚合数(videos count/size、object_counts、fs_residue、orphans)。
  - verify:已有扫描在跑时返回 already_running。
  - audit:读最近扫描 metadata。
  - `collect_keys_step`:UNION 收集去重正确、只收 sb://。
  - `storage_audit_workflow`:mock `store.exists`(部分返回 False)→ 断言 missing 记录 + errors 计数。
- **前端**(admin 现有测试模式,vitest/RTL):页面渲染、搜索触发 manifest 重取、Tab 切换、Deep Verify 按钮 disable+轮询。

## 明确不做(YAGNI)

- 任何写操作(重下/删孤儿/触发迁移)——复用已有 `storage-migration`/`media/retry` 端点,留第二期按需加行内按钮。
- 不建新表:扫描结果进 task metadata jsonb。
- 封面/缩略图无精确大小列 → 用量统计以视频 storage_size 为主 + 各类对象计数;不为小资产补 size 列。
- 不做定时扫描:只按钮触发(避免无人看的周期任务 + 存储 API 压力)。
- 不做跨 bucket / SeaweedFS 物理 volume 视图(那是运维层,`weed shell` 管;本页只管逻辑索引)。
