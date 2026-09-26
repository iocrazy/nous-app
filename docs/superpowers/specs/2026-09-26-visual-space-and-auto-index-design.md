# 画面层专属空间 + 切镜索引自动化 —— 设计（2026-09-26）

母 spec：`2026-09-16-video-vector-layers-design.md`（§4.1 空间、§4.3 策略、§4.6 UI）。PR 3 收口后画面层是空的：用户库里 1,118 支视频一支都没索引，唯一入口是 Vectors 面板的 Run 20（Dry Run 先行、每次 20 支），跑满全库要手点 56 次。同时 nous-engine 已部署 WeMM-2B（2048 维、声明 text/image/video、免费），而语义层留在 doubao 的理由仍在（库特定查询文本召回 doubao 0.77 vs WeMM 0.66）。本设计做两件事：**画面层可以指向另一个空间**，**索引可以自己跑**。画板：`2026-09-26-visual-space-and-auto-index-board.html`。

## 1. 目标 / 非目标

- 目标：语义层留 doubao、画面层走 WeMM（免费）；新下载的视频按策略自动索引；存量视频由后台分批回填；Vectors 面板能看到策略与进度。
- 非目标：clip / camera / cluster / rerank（PR 4，建立在本设计的画面层空间之上）；第四个意图 `shots` 的推送入口（PR 5b，本设计只留钩子）；跨空间比分数（仍然禁止）。

## 2. 分层空间

### 2.1 治理键

`ai_module.embedding.visual_model` = 目录行 `name`（如 `nous-wemm-embedding-2b`），**空 = 跟随 `ai_module.embedding.model`**。只有这一个新键；语义层继续用现有键。

### 2.2 解析（唯一入口）

`app/services/library/embedding_spaces.py` 加：

- `visual_catalog_name()`：读新键，空则读语义键；每一步失败 → `ActiveSpaceUnknown`（同 `active_actual_model`）。
- `resolve_visual_space_and_embedder()`：目录行 → `config_for_catalog_model` → `capabilities_for` 必须含 `image`（否则 `provider_no_image`，**空键时也检查**，行为与现在一致）→ `EmbeddingService(cfg)` → `space_spec` → `get_or_create`。`shot_index.resolve_space_and_embedder()` 改为薄包装调用它（保留名字与错误码，四个调用方——索引 workflow、两个端点、`/shots` 覆盖——零改动）。
- **WeMM 行的能力表随本设计翻成 text + image**（`embedding_capabilities._engine_chat`）：此前 `wemm-*` 行只声明 text，Use For Visual 指向它会被 `provider_no_image` 拒掉，整个「画面层走 WeMM」无从谈起。依据是 2026-09-23 经 nous-engine 网关实测：`messages[].content` 里的 `image_url` 部件跨模态正常（红色方块图 vs 其说明 0.36 / 无关说明 0.12）。所以 chat 协议的 payload（`build_openai_chat_payload`）改为收 items：文本部件合一、每张图一个 `image_url` 部件；video（帧列表）仍不在 chat 协议的模态里，等 grouped 端点。**真栈验收（§5 第 1 条）是这条翻转的最终裁决**——网关拒绝 image 部件的话，索引任务会以 `provider_error` 失败，不会静默。
- 键指向的目录行不可用（删除 / 禁用 / 私人 key）→ `visual_space_unavailable`，**不**回退到当前空间——回退会把帧向量写进错误的空间。空键才跟随。

读方三处改成用它：

| 读方 | 现在 | 改后 |
|---|---|---|
| `search_service._visual_hits` | `self.embedding_service` + `current_space_id()`（语义空间） | `visual_embedding_service` + 画面空间 id（各自缓存，`_SPACE_ID_CACHE` 按 spec 键控已能容纳两个） |
| `search_router._space_statuses` / 顶层 `layers` 的 visual 行 | 按当前空间算 | 顶层 visual 行按**画面空间**算；`spaces[]` 里每个空间的 visual 行不变（那是"这个空间里有多少"） |
| `search_router._visual_coverage` | 传入的 space_id | 同上 |

搜索侧一个隐含约束：`SearchService` 现在只有一个 `embedding_service`；加 `visual_embedding_service`（懒解析，失败按 `VECTOR_LEG_OUTCOMES` 归码到 `visual_leg`，**不影响语义腿**）。

### 2.3 端点（admin）

- `PUT /search/vectors/visual-space {space_id}`：该空间的目录行必须可用且声明 `image`（422 `provider_no_image` / 409 `space_catalog_row_missing`）；写治理键；返回新的 status。
- `DELETE /search/vectors/visual-space`：清键（跟随当前空间）。
- `DELETE /search/vectors/spaces/{id}` 加拒绝：是画面空间 → 409 `space_in_use_visual`。
- Switch 当前空间不动画面键（两者独立）。
- `GET /search/vectors/status` 加 `visual_space: {id, actual_model, catalog_name, follows_active: bool}`；`LayerStatus` 顶层 visual 行改按画面空间。

### 2.4 UI（Vectors 面板）

- 候选空间卡加 **Use For Visual**（admin；已是画面空间时显示 `Visual layer` 徽标 + **Follow Current** 按钮）。
- Retrieval Layers 的 Visual 行 Source 列：`space wemm-embedding-2b` 或 `follows current space`。
- 当前空间卡下方一行：`Visual layer · wemm-embedding-2b（separate space）`。

## 3. 索引自动化

### 3.1 治理键（全局，admin）

| 键 | 取值 | 默认 | 含义 |
|---|---|---|---|
| `ai_module.shots.auto_index` | `off` / `local_only` / `always` | `local_only` | 新下载的视频是否自动派 `index_shots` |
| `ai_module.shots.backfill` | `off` / `local_only` / `always` | `off` | 后台是否分批索引存量 |
| `ai_module.shots.backfill_batch` | 1–50 | 5 | 每个 tick 派多少支 |
| `ai_module.shots.backfill_daily_cap` | 0–10000 | 200 | 网络 provider 下每天最多派多少支（0 = 不限） |

「本地 provider」= 画面空间目录行的 `actual_provider == NOUS_ENGINE_PROVIDER`（`"nous"`），与 `nous_model_repository` 的唯一定义共用。`local_only` 在 doubao 上等于 off，面板会明说。

### 3.2 新下载

`download.chain_followups_step` 末尾加 `maybe_chain_index_shots(resource_id, user_id, flow_id)`（`app/tasks/download_helpers.py`，与 `maybe_chain_ai_pipeline` 同形、best-effort、不失败 workflow）：视频（`_is_video`）且策略允许 → 复用 `ai_router._dispatch_index_shots`（搬到 `app/services/library/shot_dispatch.py`，router 与 helper 都调它）；`flow_id` 透传，Task Center 里挂在同一条下载链上。资源带 `shots` 意图标签时无视策略直接派（PR 5b 的钩子，本期只读标签不写）。

### 3.3 存量回填（scheduled）

`app/workflows/shots_backfill_sweep.py`，`@DBOS.scheduled("*/10 * * * *")`，进 `_scheduled_bundle`（只在 worker 注册）。tick：

1. 策略 `off` 或（`local_only` 且 provider 非本地）→ 返回；解析画面空间失败（`embedder_unconfigured` / `provider_no_image` / `store_missing`）→ 记日志返回，**不**建任务。
2. 背压：`task_tracking` 里 `index_shots` 活跃（queued/in_progress）≥ batch → 返回。
3. 网络 provider 且当天已派 ≥ daily_cap → 返回。
4. 候选 = `pending_for_user` 的跨用户版 `pending_all(space_id, kind, algo_version, limit=batch)`（同一条 SQL 去掉 creator 过滤，按 resource id 升序，missing 优先），逐支 `_dispatch_index_shots(flow_id=None)`；派发本身在 workflow 体里，不在 step 里（DBOS 约束，同 sweeper）。
5. 计数进 `system_settings` 的 `ai_module.shots.backfill_state`（json：`{day, dispatched_today, last_tick, last_error}`），面板读它。

路线 C 不变：每支仍是一行 `index_shots`，失败 raise。

### 3.4 状态

`GET /search/vectors/status` 加 `shots_policy: {auto_index, backfill, batch, daily_cap, provider_local: bool, dispatched_today, active, pending_total, last_tick, last_error}`（非 admin 也可读，只有写要 admin）。`PUT /search/vectors/shots-policy`（admin）写四个键，白名单校验取值。

### 3.5 UI（Vectors 面板新增 Indexing Policy 段）

母 spec §4.6 画板已有骨架：Auto-index new videos 三态、Background backfill 三态 + batch/day cap、进度行 `today 37 / 200 · 3 running · 1,081 remaining · last tick 10:20`、provider 非本地时 `local_only` 旁灰字 `current visual provider is doubao (network) — this equals Off`。非 admin 只读。

## 4. 降级与一致性

- 画面键指向的目录行被删 / 禁用 → `visual_leg = unconfigured`、`/shots` 覆盖按 `store_missing` 同款 503 之外新增 409 `visual_space_unavailable`；语义腿不受影响。
- 换画面空间 = 旧空间里的帧向量不再被查（`covered=false` → Shots tab 提示 Re-index），与换当前空间同一语义；Delete 画面空间被拒（2.3）。
- 自动派发只看策略与背压，不看用户是否在线；每支的费用仍按现有 BYOK/积分裁定走（`index_shots` 已在链上）。

## 5. 验收（可证伪）

1. Add Space WeMM-2B → Use For Visual → `status.visual_space.actual_model == wemm-embedding-2b`，`space.actual_model` 仍是 doubao；Shots tab 索引一支 → `video_shot_embeddings.space_id` 是 WeMM 空间；混合搜索 `visual_leg=ok` 且命中来自该支；语义腿仍走 doubao（`api_request_logs` 里两条不同 provider 的请求）。
2. 策略 `backfill=local_only`、batch 5：两个 tick 后 Task Center 有 ≤10 行 `index_shots`，`dispatched_today` 对得上；`backfill=off` 后不再新增。
3. 策略 `auto_index=local_only` 下解析一支新视频 → 下载链里出现 `index_shots` 子任务，与 thumbnail / extract_audio 同一 flow。
4. 画面 provider 是 doubao 且 `local_only` → 面板灰字提示，tick 不派。
5. Delete 画面空间 → 409 `space_in_use_visual`。

## 6. 分期

| PR | 内容 |
|---|---|
| A | 2.1–2.3 后端 + status 字段（`visual_space` / `visual_status` / `spaces[].visual`）+ 契约快照；`_visual_hits` 用画面空间；WeMM 能力表翻 image |
| B | 2.4 + 3.5 前端（画板已出）|
| C | 3.1–3.4 后端：键、钩子、sweeper、状态 |

A 与 C 可并行；B 等 A 的契约。

## 7. Known Limitations and Deferred Work

- 只有画面层可以分空间；转录层（二期）与镜头语言层（PR 4）默认跟画面层同一空间，PR 4 再决定要不要第三个键。
- 后台回填的顺序是 resource id 升序，不按「最近下载优先」；要优先级得加列。
- daily cap 按派发计数，不按实际 token；实际花费看积分流水。
