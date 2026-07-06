# 百万文件单用户 Readiness — 设计方案

> Status: **DRAFT — 待用户评审**
> Scope: 单用户 10万-100万 小文件(Eagle 式素材库)下的导入、缩略图、文件分发、DB、前端内存五轴。
> Non-goal: 百万**用户**并发(那是对象存储/CDN/多节点的事,与本方案无关)。

## 0. 判断框架

百万文件单用户**不需要架构跃迁**。PG 单表百万行 + nginx 静态直出 + 懒生成,就是正确的杠杆。已完成的地基(不在本方案 scope):

- PostgREST 1000 行轴全闭(keyset 分页 / RPC 聚合 / #1069 长尾清扫)
- 三视图虚拟化(grid #927 / list #933 / justified #1070+#1072)
- 批量导入两阶段并行流水线(#931)
- gateway / worker 物理分离(PR-D8)

五轴现状与瓶颈定量:

| 轴 | 现状 | 百万级会发生什么 |
|---|---|---|
| 缩略图生成 | 上传/下载完成即 chain `thumbnail_workflow`(DBOS,单 worker) | 百万导入 = 百万 workflow 排队,单 worker 按 ~1s/个要跑 **11 天**;期间队列挤占其他任务 |
| 导入本身 | #931 并行流水线,但仍是 per-file HTTP POST | 百万次 HTTP 往返 + 字节二次搬运(文件通常已在 NAS 上) |
| 文件字节 | `GET /resources/{id}/cover` 等由 FastAPI FileResponse 逐块搬运 | 一屏 50 缩略图 = 50 个 Python 协程搬字节;滚动浏览时 gateway 吃满 |
| DB 查询 | keyset + RPC 已 scale-safe;搜索是 `filename ILIKE` | 百万行 ILIKE 顺序扫 ~秒级;`COUNT(*)` 数百 ms |
| 前端内存 | DOM 已虚拟化;keyset 累积的 JS 对象**不释放** | 无限下滑几十万条 → JS 堆数百 MB |

---

## Phase 1 — 缩略图懒生成(纯代码,最高杠杆)

**原则:百万文件里绝大多数永远不会被看到。预生成全量是在为不存在的浏览买单。**

### 设计

1. **导入路径不再 chain 缩略图**:批量导入(#931 上传 / P2 sideload)建行时 `thumbnail_path=NULL`,不 enqueue workflow。单文件常规上传保留即时生成(体验不变)。
2. **cover 端点变成懒触发点**(`resources_crud_router` 的 cover serve):
   - `thumbnail_path` 命中 → 现行为不变(FileResponse + immutable 缓存)。
   - miss → ① enqueue `thumbnail_workflow`,**workflow id = `thumb-{resource_id}`**(DBOS 幂等键天然防并发惊群,同资源多次 miss 只排一次);② 立即返回按 mime 分类的内置 SVG 占位(`Cache-Control: no-store`)。
   - 缩略图就绪后,前端已有的 `?v=` cache-bust 机制让下次渲染命中真图;资源卡已有 onError/重渲染路径,无需新前端逻辑(可选:占位响应带 `Retry-After` 头 + 前端定时重拉,作增强项)。
3. **视口自然优先**:用户滚到哪、哪里的 cover 先被请求、先入队 —— 无需显式优先级队列。
4. **后台补全(可选开关)**:低速率 sweeper(如每分钟 60 个)在空闲时消化存量 NULL,补齐长尾;默认关,admin 开关走 DB 配置(env→DB 铁律)。

### 验收

- 导入 10k 文件:导入完成时间与缩略图无关;浏览首屏缩略图在滚动到位后 <3s 出现。
- 同一资源并发 20 个 cover miss 请求:DBOS 只产生 1 个 workflow(幂等键断言)。
- 存量已生成缩略图行为字节不变。

### 风险

- SVG 占位的视觉一致性(justified 视图 aspect ratio 来自 resolution 列,不依赖缩略图,布局不抖)。
- worker 死时 miss 永远占位 → 依赖既有 worker 健康告警;占位不是错误态。

---

## Phase 2 — 本地 Sideload 导入(纯代码;Eagle 迁移的正解)

**场景:素材文件反正要先拷到 NAS 卷上。既然字节已经在 `/volume2/sources/MediaHub.library` 同卷,就不该再走百万次 HTTP POST。**

### 设计

1. **收件目录约定**:`{library}/sideload-inbox/<batch-name>/...`(容器内 `/app/downloads/sideload-inbox/`)。用户把 Eagle 导出/任意目录树拷进去。
2. **API**:`POST /api/v1/resources/sideload`
   ```json
   { "inbox_path": "<batch-name>", "scope_id": "...", "folder_id": null,
     "mode": "move", "recursive": true }
   ```
   - `inbox_path` 强制白名单前缀(只允许 sideload-inbox 之下,拒绝 `..`/绝对路径逃逸)——防任意文件注册。
   - 权限:scope owner/admin。
3. **DBOS workflow(`sideload_workflow`)**,遵守任务系统路线 C 全部纪律(manager.create / raise 不 return / 业务字段进 metadata):
   - **扫描 step**:walk 目录,产出清单(路径+大小),写 task metadata `{total}`。
   - **分批处理 step(500/批,checkpoint 边界)**:流式 SHA-256 → 批量 `find_by_hashes` 去重 → 重复的 zero-copy link(现有 link-existing 语义),新文件按 `mode`:
     - `move`(推荐默认):移动到标准日期分桶布局 `{yyyy}/{mm}/{dd}/{uuid}/`(存储铁律),同卷 rename 是 O(1);
     - `register`:原地注册,`file_path` 指向 inbox 内路径(读端走 DB path 列,符合 feedback 的免迁移口径)。
   - 建 `resources` + `resource_items` 行;**不触发缩略图**(P1 懒生成接管);目录结构 → 可选映射为 folders(见决策点③)。
   - 进度:每批 update_progress(`processed/total`),Task Center 实时可见。
4. **吞吐预估**:瓶颈是 NAS 磁盘顺序读(SHA-256 流式)。按 200MB/s、平均 3MB/文件 ≈ 70 文件/s ≈ **百万文件 ~4 小时后台跑完**,对比 HTTP 路径的数量级提升;DB 写按 500/批 UPSERT,毫无压力。
5. **P2b(可选,独立 PR)**:Eagle `metadata.json` 解析 —— tags → 现有标签系统、Eagle folders → folders、annotation → notes。仅当用户确认 Eagle 元数据有保留价值时做。

### 验收

- 1 万文件 inbox:一次 API 调用,Task Center 可见进度,完成后全量可浏览、dedup 命中的是 link 非拷贝;中途 kill worker,DBOS 恢复后从 checkpoint 批次续跑,零重复行。
- 逃逸路径(`../`、绝对路径、symlink 指向卷外)全部 4xx。

---

## Phase 3 — nginx 静态直出(代码 + 一次 NAS 手动 compose)

**复用既有的 `mediahub-app-nginx` 容器(:8081,现已 alias `/app/downloads/` 服务 HLS)——不新增 nginx,不碰群晖自带 nginx(80/443),无端口冲突。**

### 方案选型:签名 URL(推荐)而非 X-Accel-Redirect

X-Accel 要求 nginx 反代在 FastAPI **前面**,而现状 `:88` 直达 backend 容器 —— 引入反代等于改动主请求链路,风险大收益同。签名 URL 只在**旁路**(:8081)加能力,主链路零改动,且与既有 HLS 通路、Temp Token 思路同构。

### 设计

1. **nginx-hls.conf 增加受签 location**(nginx:alpine 自带 `secure_link` 模块):
   ```nginx
   location /f/ {
       alias /app/downloads/;
       secure_link $arg_st,$arg_e;
       secure_link_md5 "$secure_link_expires$uri <SECRET>";
       if ($secure_link = "") { return 403; }
       if ($secure_link = "0") { return 410; }
       add_header Cache-Control "private, max-age=86400";
   }
   ```
   SECRET 经容器 env 注入(envsubst 模板),来源 DB/部署 env,与后端共享。
2. **后端签名器**:`sign_file_url(rel_path, ttl=24h)` → `https://<hls-host>:8081/f/{path}?st=<md5>&e=<expires>`。
   - 列表/详情响应附带 `cover_url`(已生成缩略图的行);前端 `getResourceCoverUrl` 优先用它,**miss 或 403/410 时回退现有 FastAPI cover 端点**(懒生成触发点仍在 FastAPI,两条路互补:nginx 服务命中,FastAPI 服务 miss+触发)。
3. **覆盖顺序**:cover(最高频)→ 原文件下载/版本文件 → HLS(已直出,不动)。
4. **NAS 手动操作(一次,runbook 随 PR 附)**:compose 给 nginx 容器加 env + conf 模板挂载 → `docker compose up -d --no-deps nginx`(**铁律:--no-deps**,防漂移栈级联)。

### 验收

- 带签 URL 200 且 `Server: nginx`;篡改路径 403;过期 410;伪造 secret 403。
- gateway 上 cover 请求量在浏览场景下降 >90%(application_logs/请求日志对比)。
- nginx 容器停掉 → 前端回退 FastAPI,浏览不中断(降级验证)。

---

## Phase 4 — DB 长尾(一条 migration)

1. **pg_trgm**:`CREATE EXTENSION pg_trgm; CREATE INDEX CONCURRENTLY ... ON resources USING gin (filename gin_trgm_ops);`(notes 视需要)。现有 ILIKE 查询自动吃索引,零代码改动;百万行搜索从秒级到 ~10ms。
2. **计数策略**:侧栏计数 RPC(`count_scope_resources`)在行数 >100k 时切 `reltuples` 估算或 60s 缓存(精确计数在百万行上数百 ms×高频调用不值得)。UI 上 `≈` 前缀标注估算。

## Phase 5 — 前端数据窗口化(长尾,可最后)

`useKeysetPagination` 加保留窗(如最近 40 页):滚离超窗的页丢弃 items、记 cursor,滚回时重取。JS 堆从 O(已滚过) 降为 O(窗口)。配合产品引导(按文件夹/筛选浏览为主),优先级最低。

## Worker 扩 N — 有意不进本方案

懒生成(P1)把缩略图从"导入风暴"变成"浏览节奏",单 worker 大概率够用。若仍需扩 N:前置是 Worker Foundation P2/P3(liveness + generation fencing,当初**故意 DEFER 到 `--scale ≥2`**),须先做 HA enablement 再扩,不可直接 scale。

---

## 实施顺序与体量

| Phase | 体量 | NAS 手动? | 依赖 |
|---|---|---|---|
| P1 懒生成 | 1 PR(后端为主) | 否 | 无 |
| P2 sideload | 1-2 PR(后端 workflow+API,前端入口按钮可选) | 否(拷文件是用户操作) | P1(不然 sideload 又触发风暴) |
| P3 nginx 直出 | 1 PR + runbook | **是**(一次 compose up --no-deps) | 无(与 P1/P2 正交) |
| P4 DB 长尾 | 1 migration PR | 否 | 无 |
| P2b Eagle 元数据 | 1 PR | 否 | P2 |
| P5 窗口化 | 1 PR(前端) | 否 | 无 |

建议:**P1 → P2 → P3 → P4**,P2b/P5 视实际需要。

## 待拍板的决策点

1. **Phase 顺序**:按上表推进?
2. **P2 sideload 默认模式**:`move` 统一日期分桶布局(推荐)vs `register` 原地注册?
3. **P2b Eagle 元数据**(tags/folders/notes)要不要进 scope?
4. **P3 选型**:签名 URL 走既有 :8081(推荐)vs X-Accel(需把 nginx 反代进主链路)?
5. **P4 计数**:接受大库侧栏计数显示 `≈` 估算?
