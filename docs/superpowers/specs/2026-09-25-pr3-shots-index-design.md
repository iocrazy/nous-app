# PR 3 · 切镜索引与画面层（doubao 起步，引擎可换）

母 spec：`2026-09-16-video-vector-layers-design.md` §4.2 / §4.3 / §4.4 / §5。本文只写 PR 3 的实施口径，与母 spec 不同处在 §6 逐条列出。画板：`2026-09-25-pr3-shots-board.html`（Artifact 同步发布）。

## 1. 为什么现在做、为什么不等引擎

母 spec 把 PR 3 记成「依赖 nous-engine 多模态端点」。那是为了省钱：切点靠 1 fps 帧向量的余弦距离曲线，一支 10 分钟视频要嵌 600 帧，只有本地免费模型才付得起。

改法：**切点不用向量，只用本地像素信号**；向量只嵌每个镜头的代表帧。于是 PR 3 的付费调用从「每秒一次」降到「每镜头一次」（≈ 时长 / 4.5 s），doubao-embedding-vision 今天就能跑；WeMM-2B 部署到 nous-engine 后，就是 Settings → Vectors 里 Add Space 换一个空间的事，本 PR 的代码一行不改（#2433 已上线）。

## 2. 范围

做：`video_shots` / `video_shot_embeddings` / `video_shot_indexes` 三张表 + RPC；本地切镜；DBOS workflow `index_video_shots`；单支按需索引与全局回填两个入口；`GET /resources/{id}/shots` 与 `GET /resources/{id}/frame?ms=`；混合搜索的 **visual** 腿；Vectors 面板 Visual 行；Shots tab 三态；命中徽标带时间码；`LibrarySearch` 工具自动多一条腿。

不做（PR 4）：`clip` 向量、Camera 腿、HDBSCAN 簇、精排、簇标签。不做（PR 5b）：第四个推送意图。

## 3. 数据（mig 507）

```
video_shots            id bigint PK(snowflake), resource_id bigint FK resources ON DELETE CASCADE,
                       shot_index int, start_ms int, end_ms int, rep_frame_ms int,
                       cut_score real, created_at timestamptz
                       UNIQUE (resource_id, shot_index); INDEX (resource_id)
video_shot_embeddings  shot_id bigint FK video_shots ON DELETE CASCADE,
                       kind text CHECK (kind IN ('frame','clip')),
                       space_id bigint FK embedding_spaces ON DELETE CASCADE,
                       embedding halfvec(2048) NOT NULL, source_hash text NOT NULL, created_at
                       PK (shot_id, kind, space_id); HNSW (embedding halfvec_cosine_ops); INDEX (space_id, kind)
video_shot_indexes     resource_id bigint PK FK resources ON DELETE CASCADE,
                       algo_version text, shot_count int, duration_ms int, indexed_at timestamptz
```

- `video_shot_indexes` 是「这支视频切过镜了」的事实，独立于向量：0 镜头的视频（纯音频轨、黑场）也算索引过；Visual 层覆盖率 = 在当前空间有 `frame` 向量的资源数 / 视频资源总数，`stale` = 索引过但当前空间没向量（换空间后）或 `algo_version` 落后。
- `cluster_id` 留给 PR 4 加列，本期不建空列。
- RPC `match_video_shot_embeddings(query halfvec, p_space_id, p_kind, threshold, count, p_user_id)`：`DISTINCT ON (resource_id)` 取每支视频最佳镜头，返回资源字段 + `shot_id / start_ms / end_ms / similarity`。与 499 同款：函数级 `SET hnsw.iterative_scan = 'relaxed_order'`、`ef_search = 100`、MATERIALIZED CTE 重排；`REVOKE EXECUTE FROM PUBLIC, anon, authenticated`（只有后端直连调）。
- RLS 开，策略镜像 499（经 `resources.user_id` 归属）。
- ORM 三张模型 + `VideoShotsRepository` / `VideoShotEmbeddingsRepository`；真执行覆盖挂 schema-drift（`tests/db/test_video_shots_repository_integration.py`）。

## 4. 切镜（本地，免费）

`app/services/library/shot_cut.py`，纯 Python + Pillow，无 numpy 依赖：

1. `materialize` 源视频 → `ffprobe` 时长 → `ffmpeg -vf fps=3,scale=-2:448` 抽 3 fps JPEG 到 0700 私有临时目录（`hist_v2`；`hist_v1` 是 1 fps，见 §11 的基准）。时长 > 60 min 时 fps 降到 10800 / 时长，帧数封顶 10800。
2. 每帧缩到 32×32 → HSV 直方图（H 16 桶 + S 8 桶 + V 8 桶）→ 相邻帧 L1 距离序列 `d[i]`。
3. 切点：PySceneDetect `AdaptiveDetector` 的骨架——`d[i]` 除以滚动窗口（±2 帧）均值得到比值，比值 ≥ 3.0 且 `d[i]` ≥ 绝对下限 0.15 记为切点；闪光抑制：切点后 1 帧又切回（与切前帧距离 < 0.1）则丢弃两点。
4. 最短镜头 1.5 s（3 fps 下即 ≥ 5 帧）；超长镜头每 30 s 硬切；每支封顶 600 镜头（超出按 cut_score 高者保留）。
5. 代表帧 = 镜头中点帧（母 spec 的「离均值向量最近」需要帧向量，本期不付这个钱）；相邻镜头直方图距离 < 0.05 合并（口播视频去重）。
6. 输出 `list[Shot(start_ms, end_ms, rep_frame_ms, cut_score)]` + `algo_version = "hist_v2"`（3 fps、ratio 2.5；`hist_v1` = 1 fps、ratio 3.0，被它切过的索引读作 `stale`）。

验收对照物改为 **PySceneDetect `ContentDetector`（默认阈值 27）**，本机 pip 装即可跑，不依赖 OmniShotCut：30 支视频（附录 A 的基准集里挑）±0.5 s 容差 F1 ≥ 0.8。达不到先调参再上线，参数进 `algo_version`。

## 5. Workflow `index_video_shots`（task_type `index_shots`）

路线 C，照 `cover_frames` 与 `analyze_l1` 的写法：

| step | 做什么 | 进度 subtitle |
|---|---|---|
| resolve | 当前空间的 embedder；能力表不含 `image` → `raise ShotIndexError("provider_no_image")` | Resolving provider |
| extract + cut | §4，全程 `async_heartbeat_loop`；USER scope 读 resources | Extracting frames 100% → 62 cuts |
| embed | 每镜头代表帧 → `ImageUrlItem(data URI)` → `try_embed_items`；并发按 provider（本地 8 / 网络 3）；连续 3 次 `provider_error` 中止并 raise | Embedding 143 / 200 |
| write | 事务：删旧 `video_shots`（级联删向量）→ 写 shots → 写 frame 向量 → upsert `video_shot_indexes` → 删临时目录 | Indexed · 200 shots |

- 失败一律 raise，subtitle 写类型化原因（`embedding failed: provider_error`）；`task_tracking.metadata.shots = {shots, embedded, skipped, algo_version, space_id, cost_tokens?}`。
- 重复索引（Re-index / `force=true`）走同一 workflow，写步骤整体替换。
- 上界：抽帧 `_TOTAL_DEADLINE_SECONDS = 900`（比封面抽帧长，要解整支）、ffmpeg `_EXTRACT_TIMEOUT_SECONDS = 600`。

## 6. 端点

| 端点 | 行为 |
|---|---|
| `POST /ai/analyze/index-shots/{resource_id}` `{force?}` | 建 `task_tracking` 行 + `start_workflow_routed`，202 `{task_id, workflow_id}`。409 `already_indexed`（未 force 且 `video_shot_indexes` 有行且当前空间有向量）、409 `provider_no_image`、422 `not_a_video`、404。显式 `request_scope(Scope(user_id))`（同 cover 端点的教训） |
| `POST /ai/analyze/backfill-shots` `{dry_run, limit≤50}` | 与 `backfill-embeddings` 同形。候选 = 用户的视频资源中当前空间无 frame 向量者（含 stale）。dry_run 返回 `{candidates, estimated_shots, estimated_tokens, skipped[]}`；实跑建一个父任务（`shot_backfill`，metadata 记 dispatched/done/skipped）+ 每支一行子任务（`metadata.parent_task_id`）。类型化 skipped：`no_video_file` / `provider_no_image` / `already_indexed` / `embedder_unconfigured` |
| `GET /resources/{id}/shots` | `{indexed, index: {algo_version, shot_count, indexed_at, space_id, stale}, shots: [{id, shot_index, start_ms, end_ms, rep_frame_ms}]}`；未索引 `indexed=false, shots=[]` 而不是 404 |
| `GET /resources/{id}/frame?ms=` | `extract_frame_at` 现切 JPEG（宽 480），`Cache-Control: private, max-age=86400`；越界 422 |
| `GET /search/vectors/status` | `layers` 多一行 `visual`（`LayerStatus.layer` 枚举加 `visual`），`status: ok / not_built`，覆盖率口径见 §3 |
| hybrid search | 新增 **visual** 腿：查询文本用当前空间 embedder 嵌一次（doubao 文本↔图片同空间），`match_video_shot_embeddings(kind='frame')`，`SearchResultItem.layer='visual'` + 新字段 `shot: {shot_id, start_ms, end_ms} \| null`；`legs.visual` 计数；腿 outcome 沿用 `VECTOR_LEG_OUTCOMES`，加 `visual_leg` 字段（与 `vector_leg` 并列，不改旧字段语义） |

`LibrarySearch` 工具：结果行带 `shot`，`layers` 参数接受 `visual`；skill 正文补一段「找画面用 visual」。

## 7. UI（先画板后代码，画板见文件头）

- **搜索结果卡**：命中徽标 `0:48 · Visual · 0.62`；封面底部 3 px 位置条上白色小段标出命中镜头位置；未索引的视频图标行末 `shots queued` 小标（灰）；点卡进详情自动 seek 到 `start_ms`。
- **Shots tab 三态**：① 未索引：估算行 + **Index This Video**（真按钮）+ 提示；② 索引中：同一块换成进度（读 Task Center realtime 的 subtitle/progress）+ Cancel（走现有取消）；③ 已索引：索引信息行（`indexed 2026-09-30 · space doubao · 2048` + Re-index）→ 2×2 数字（Shots / Vectors · frame / Average shot / Clusters —，PR 4）→ 镜头条（顺序单色，点击 seek，命中描白边）→ 镜头列表（命中行高亮，时间码）。
- **Settings → AI → Vectors**：Retrieval Layers 表 Visual · frame 行变 ok，`covered / total`、Dry Run / Run 20（网络 provider 下 Run 前必须先 Dry Run 一次，按钮禁用直到本会话跑过 dry run）；Last backfill 行。
- **Task Center**：父任务 `Index shots · 20 videos` 显示 N/M，子任务各一行；单支按需索引一行。

## 8. 验收（可证伪）

1. 切镜 F1 ≥ 0.8（§4 对照法，30 支，脚本 `backend/scripts/bench_shot_cut.py`，结果贴 PR）。
2. 20 条画面类查询（"傍晚骑楼街景""白底产品特写""屏幕录制"…）visual 腿 hit@10 ≥ 0.6，本机 27B 判；同批查询语义腿对照，写差异。
3. 时间码：20 条命中人工点，`start_ms` 与播放器跳转一致。
4. 延迟：开 visual 腿后混合搜索 p95 ≤ 2.5 s（doubao）；腿超时不拖文本腿。
5. 成本：Dry Run 估算 vs 实跑 `cost_tokens` 差 ≤ 20%；单支 10 min 视频 ≤ 2 min 跑完；中转目录跑完为空。
6. 降级：embedder 未配置 / 能力不含 image → 索引端点 409 类型化、搜索 `visual_leg=unconfigured`、文本腿不变。
7. 换空间：Add Space 到 WeMM 后 Visual 行对新空间 `stale`，回填后旧空间不再被查（复用 #2433 的路径）。

## 9. 与母 spec 的偏离

| 母 spec | 本 PR | 原因 |
|---|---|---|
| 切点用 1 fps 帧向量距离 | 像素直方图 + 自适应阈值 | 付费模型下每秒一次嵌入不可接受；F1 门槛不变 |
| 代表帧 = 离均值向量最近 | 镜头中点帧 | 同上；相邻去重改用直方图 |
| 对照 OmniShotCut | 对照 PySceneDetect ContentDetector | 本机可跑、可复现 |
| PR 3 依赖引擎多模态端点 | 不依赖；doubao 起步，引擎到了换空间 | #2433 已提供切换 |
| `video_shots.cluster_id` 建表即有 | PR 4 加列 | 不建没有写方的列 |

## 10. 分期任务

| T | 内容 | 门禁 |
|---|---|---|
| T1 | mig 507 + ORM + 两个 repository + RPC + drift 集成测试 | schema-drift 绿；RPC 在本机 drift-pg 上 EXPLAIN 走 HNSW |
| T2 | `shot_cut.py` + 合成序列单测 + `bench_shot_cut.py` | F1 ≥ 0.8 |
| T3 | workflow + index-shots 端点 + `/shots` + `/frame` | 真栈：一支视频 Task Center 走完、`/shots` 有行 |
| T4 | backfill-shots + vectors status Visual 行 | Dry Run 估算 vs 实跑 |
| T5 | visual 腿 + `shot` 字段 + LibrarySearch | 契约快照重导出（`export_openapi` + 两处 `gen:api`） |
| T6 | UI 四块（画板批准后） | e2e 用真实 wire 形状 mock |
| T7 | 验收脚本与数字进 PR | §8 全部 |

## 11. Known Limitations and Deferred Work

- 直方图切点对渐变转场（dissolve）弱，会晚 1–2 s 或漏切；帧向量法（引擎到位后）可作为 `hist_v2` 替换，`algo_version` 让两代并存可比。
- 3 fps 采样让切点精度 ±0.33 s（`hist_v1` 的 1 fps 是 ±0.5 s）；更细需要二次精定位（未做）。
- **2026-09-25 本机 7 支真内容视频基准**（`bench_shot_cut.py`，非 §8 的 30 支）：`hist_v1` 平均 F1 0.578，20 刀的胶片老素材召回只有 0.25（1 fps 下镜头内相邻帧距离 0.2–0.4，刀口 0.3–0.6 的比值分不开）；`hist_v2` 平均 0.630、该支 0.76，两支单镜头短片仍零误报。剩下的两类漏切：渐变转场（两支 stock 片各漏 1 刀，见上一条）与低对比度 MV 上距离 0.13–0.15 卡在 `min_abs=0.15` 之下（降到 0.10 会让该支误报翻倍，不取）。30 支的正式数字仍待真视频。
- 代表帧是中点帧，镜头内有大运动时不一定最有代表性。
- doubao 图片 token 计价按图尺寸变化，`estimated_tokens` 是经验常数（每镜头 ≈ 300），先按实跑校准。
- 回填是父子任务，没有 Pause；取消父任务不回收已派发的子任务（现有取消语义）。
