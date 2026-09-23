# 资源库视频向量分层检索 — 设计

日期：2026-09-16 · 状态：草案待用户验 · 前置：PR 1（feat/semantic-search-wiring，后端接线）

配套文档：`2026-09-16-nous-engine-multimodal-embedding-request.md`（交给 nous-engine 实现的部分）；UI 画板 `2026-09-16-video-vector-layers-mockup.html`（四屏：搜索命中 / island 三大 tab 与 Shots / Settings → AI → Vectors / 推送入口，在线预览 https://claude.ai/artifact/Sc6x5tc1DAjgGRj44i4ELz ）。

## 1. 起因与证据

- 生产库 1285 支 web 视频（中位 218 s，平均 530 s，p90 21 min，最长 3.75 h，合计 189 h），搜索只有 ILIKE 文本腿。`resource_analysis` 里 **0 条向量、无向量索引**，语义层是空的。
- 2026-09-15/16 真库基准（57 条关键词查询，ILIKE 命中当真值，recall@10）：
  - 查询指令是唯一的大杠杆：doubao 0.48 → 0.77，WeMM-2B 0.31 → 0.66，4B 0.19 → 0.61，9B 0.12 → 0.60。措辞之间在 doubao/2B 上差 ≤0.03（噪声），库特定措辞 `en_keyword` 各模型最优或并列。
  - 图文跨模态（标题↔封面 hit@10）四个模型持平在 0.62 左右。
  - **参数量买不到召回**：4B/9B 在文本和图文上都不优于 2B；mixpeek 的文本→视频横评同样给出 InternVideo2-6B 输给 512 维 X-CLIP。
  - **帧平均是差代理**：横评里 SigLIP2 逐帧平均 0.33 对时序感知模型 0.47。镜头语言必须嵌整段片段，不能几帧取平均。
- 产品参考 Indexed（xiaotianfotos/indexed，Apache-2.0）：7:25 的视频用 446 个逐秒临时帧向量找切点，切出 100 个镜头（平均 4.5 s），每镜头两条检索向量（画面语义 + 镜头语言），镜头向量无监督聚成 8 簇；画面与字幕是两个独立索引；搜索接口做成 AI Skill；结果必须能回到来源时间码。
- 用户原则（2026-09-16）：nous-app 是业务层，换 nous-engine 本地模型或网络模型 API 功能无太大差异；模型部署归 nous-engine。

## 2. 目标 / 非目标

目标：
- 四层向量检索：语义（文本）/ 画面（镜头代表帧）/ 镜头语言（片段）/ 转录（时间码分段）。
- 每个结果带来源时间码；agent 拿同一个接口当工具。
- provider 可换：doubao、nous-engine 本地 WeMM/Qwen3-VL-Embedding 任一都能跑全部功能，缺能力的层类型化降级。
- 存储留在 pgvector，不引入第二种向量库。

非目标（本轮不做）：
- VLM 结构化镜头卡（光影、景别等 facet）— 二期。
- 非视频资源（PDF、截图、文稿）入索引 — 后续。
- 顶栏全局搜索 UI — 已有占位，另立 spec。

## 3. 参考什么

| 参考 | 用在哪 | 不用哪 |
|---|---|---|
| Indexed | 产品形态：镜头级索引、两套向量、簇、时间码回源、agent skill、独立索引、卡片显示分数与最佳命中片段 | 代码栈（Node/Swift/zvec）不通用 |
| Breakthrough/PySceneDetect（BSD-3） | `AdaptiveDetector` 的两段式骨架：逐帧打分 → 滚动均值自适应阈值 → 闪光抑制；`FrameTimecode` | 它的 HSV 特征换成帧向量余弦距离 |
| UVA-CV-Lab/OmniShotCut（MIT） | 切分真值，标定向量阈值 | 不进生产链路 |
| QwenLM/Qwen3-VL-Embedding（Apache-2.0） | vLLM 部署示例；同系列 Reranker 做精排 | — |
| Tencent/WeMM-Embedding | `mmeb_v3_eval/` 批量编码与检索指标，改造成片段向量基准 | — |
| mixpeek/video-embedding-benchmark | IR 指标口径（NDCG/MRR/Recall），选型前在自己语料重跑 | — |
| immich（AGPL） | Postgres 向量落地经验（VectorChord 迁移账） | 只读设计不抄代码 |
| ssrajadh/sentrysearch（Apache-2.0） | 可插拔 embedder / reranker / 失败重试队列 / 按时间码出片的抽象 | 固定窗口切分 |

## 4. 设计

### 4.1 Provider 抽象（业务只认三样东西）

1. **协议** `openai-embeddings-multimodal`：一个端点，输入是内容项列表 `{type: text | image_url | video_url | video_frames}`，可带 `instructions`（文本前缀方式，厂商字段不依赖）。doubao `/embeddings/multimodal` 与 nous-engine 网关 `/v1/embeddings` 都归一到它。现有 `EmbeddingService` 只会发 `input` 字符串，要加多模态适配器。
2. **能力声明**（`mediahub_models` 新增 `capabilities jsonb`，或从引擎 `/v1/models` 自动读）：`modalities: [text, image, video]`，`native_dims`，`matryoshka_dims`，`max_video_frames`，`instruction_style`。某层需要的模态不在声明里 → 该层 `unavailable`，不报错不静默。
3. **向量空间** `embedding_spaces(id, model_name, actual_model, dims, instruction_version, modalities, created_at)`：每张向量表带 `space_id`。换模型 = 新空间 + 全量重嵌；查询只打当前空间；重嵌期间新旧并存，跨空间绝不比分数。

**维度统一到 2048**：pgvector 0.8 的 HNSW 只支持 `vector` ≤ 2000 维、`halfvec` ≤ 4000 维。所以向量列一律 `halfvec(2048)`：doubao、WeMM-2B 原生 2048；4B/9B/Qwen3-VL-Embedding 用 matryoshka 截到 2048 再归一化（引擎侧的 `dimensions` 参数）。现有 `resource_analysis.content_embedding vector(2048)` 正因超过 2000 维而**建不了索引**，迁移到新表时一并解决。

### 4.2 数据表

```
embedding_spaces        见 4.1
resource_embeddings     resource_id, layer('semantic'|'transcript'), space_id, embedding halfvec(2048), source_hash, created_at
                        PK (resource_id, layer, space_id)；HNSW (embedding halfvec_cosine_ops)
video_shots             id, resource_id, shot_index, start_ms, end_ms, rep_frame_ms, cut_score, cluster_id, created_at
                        UNIQUE (resource_id, shot_index)
video_shot_embeddings   shot_id, kind('frame'|'clip'), space_id, embedding halfvec(2048)
                        PK (shot_id, kind, space_id)；HNSW
video_shot_clusters     id, resource_id, cluster_index, shot_count, duration_ms, rep_shot_id, label(可空，二期 VLM 填)
transcript_segments     resource_id, segment_index, start_ms, end_ms, text, space_id, embedding halfvec(2048)
```

- 语义层过渡：PR 1 继续写 `resource_analysis.content_embedding`；PR 2 建 `resource_embeddings` 并把回填改指它，`match_videos_by_embedding` 改读新表，旧列保留一版后删。
- 语义层文档侧扩为：标题 + 简介 + 摘要（`resource_summaries`）+ 转录全文前 N 字。文档不带指令。
- 语义层不依赖 VLM 字段，有什么嵌什么（标题 / 简介 / 标签 / 摘要 / 转录前 2000 字 / 有 L1 分析时再加 VLM 描述），所以回填就地嵌入、不再派 `analyze_l1`（PR 2 实现：`app/services/library/embedding_document.py`）。
- 命中帧缩略图不落库，按时间码用 ffmpeg 现切（`/resources/{id}/frame?ms=`）。

### 4.3 切镜与嵌入管线（DBOS workflow `index_video_shots`）

1. 从对象存储拉流（`ObjectStore` → ffmpeg 读 URL），1 fps 抽帧，短边 448，JPEG 落 NVMe 中转区。
2. 帧向量：批量走 embedding 协议（`image_url` data URI）。这些是**临时向量，不落库**。
3. 切点：相邻帧余弦距离曲线 → PySceneDetect `AdaptiveDetector` 骨架（窗口滚动均值 × 比例阈值 + 闪光抑制）→ 最短镜头 1.5 s，超长镜头按 30 s 硬切，每支视频封顶 600 镜头。
4. 代表帧：离镜头内帧向量均值最近的一帧；相邻镜头代表帧余弦 > 0.95 合并（口播视频去重后通常剩两三成）。
5. 两条检索向量：`frame` = 代表帧当图片嵌；`clip` = 镜头内均匀抽 ≤ 16 帧当 `video_frames` 嵌（provider 声明不含 video 时跳过，写 `unavailable` 原因）。
6. 簇：每支视频对 `clip` 向量做 HDBSCAN（余弦，min_cluster_size 3），噪声点单独成簇；写 `video_shot_clusters`。
7. 写表、删中转帧、`update_progress(force=True)` 收尾；失败 `raise`（路线 C）。
8. **两种触发，同一个 workflow，都进 Task Center**：
   - **单支按需**：Shots tab 的 Index this video（未索引态显示估算：时长 · ≈ 镜头数 · ≈ 向量数 · 本地免费 / doubao ≈ 300 token 每镜头），以及搜索结果卡的 shots queued 入口。走 `POST /ai/analyze/index-shots/{resource_id}`，建一行 `task_tracking`（类型 `index_shots`，标题 "Index shots · <视频标题>"），用户点完可以继续浏览。
   - **全局回填**：Settings → AI → Vectors 的 Retrieval layers 表里 Dry run / Run N，走 `POST /ai/analyze/backfill-shots`（与 backfill-embeddings 同形：dry_run / limit / 类型化 skipped：`no_video_file` / `provider_no_video` / `already_indexed` / `embedder_unconfigured`）。它本身是**一个父任务**（类型 `shot_backfill_batch`，metadata 记 dispatched / done / skipped），派发的每支视频各自一行子任务（metadata.parent_task_id），Task Center 里父任务显示 N/M。
   - **进度按路线 C**：phase / status 由 trigger 同步；业务只写 subtitle 与 metadata：`extract 100% → cut 62 peaks → embed 143/200 → cluster`；失败 `raise`，subtitle 写类型化原因（如 `embedding failed: provider_error`）。
   - **策略（Vectors 子 tab 的 Indexing policy）**：Auto-index new videos = Off / Local provider only（默认）/ Always；On-demand indexing 开关（成员可否为单支视频触发）；估算文案。网络 provider 下全局回填必须先 dry run 看 token 估算再 Run。并发从 provider 配置读（本地 8，网络 3）。

### 4.4 查询

- `hybrid_search` 扩成多腿：文本 ILIKE（已有）+ 语义 + 画面 + 镜头语言 + 转录，每腿一个 outcome 码（沿用 `VECTOR_LEG_OUTCOMES`），`SearchResponse` 逐腿回报。
- 镜头类腿：`DISTINCT ON (resource_id)` 取最佳镜头，结果带 `{shot_id, start_ms, end_ms, score, layer}`。
- 查询指令按层：语义层 `en_keyword`（已定）；画面/镜头层用 "Find an image or video that best matches the following description:" 一族，上线前同法扫描一次。
- 精排：配置了 `rerank` 协议的 provider 时，对合并后的 top 20 做一次重排；没配就跳过，结果标 `reranked=false`。
- 全页筛选、无 scope 等已有的跳过规则不变。

### 4.5 Agent 工具与 skill

- 工具 `library_search(query, layers?, limit?)`：返回类型化结果 `{resource_id, title, layer, score, shot?, matched_text?}`，每条都能回到来源时间码。
- skill `library-search`：改写与扩展（中英互补、纠错、长句拆子查询并行）、意图分流（找词 vs 找画面）、命中解释。改写效果先在基准上量（27B 改写 +x），再定 skill 正文。
- 视频结构摘要：agent 可读某支视频的簇与镜头条，不看完整支就知道由几类画面组成。

### 4.6 UI（独立 PR，画板见 `2026-09-16-video-vector-layers-mockup.html`）

落在**现有骨架**上，不新开页面：模块栏 · `DownloadsView` 卡片网格 · 右侧 info island（`VideoDetailPanel`）；上传资源在 `ResourceDetailPage` 的 inspector 上同构。

屏 1 · My Downloads · 搜索命中（`DownloadsView`，**只在 AI 搜索激活时**出现下列增量，清除搜索即消失）
- 页头与工具栏不变：标题、刷新、搜索框（AI 模式徽标）、筛选按钮、视图切换。
- **不加新的一行**。增量全部塞进现有筛选 chip 行（Tags / Rating / Type / Source / AI / Date 之后）：
  - `Layer · All ⌄`：下拉选 All / Semantic / Visual / Camera / Transcript。
  - `Sort · Similarity ⌄`：AI 搜索激活时排序默认切到**相似度**（rerank 分优先，无 rerank 用向量分），下拉里仍可选 Date / Likes；清除搜索后回到原排序。
  - 一个 **legs chip**：五条腿各一个色点加命中数（Text 12 · Semantic 9 · Visual 14 · Camera 7 · Transcript），色点即状态（ok 绿 / warn 赭 / unavailable 砖红），hover 显示 `vector_leg` 的 outcome 码；末尾标 rerank on/off。
  - 行尾灰字：57 hits · best shot per video · 412 ms。
- 卡片仍是现有 `MediaCard`（平台图标、封面标题叠字、图标行、四个互动数、作者日期）。增量两处：封面右上类型图标下方一个**命中徽标**（`3:41 · Camera · 0.71`，纯标题命中显示 `Title · 0.40`）；封面下方一条 3 px **相似度条**，长度即分数，让"按相似度排序"一眼可读。尚未建镜头索引的在图标行末尾标 shots queued。
- 点选卡片 → info island 的 Overview 顶部多一块 **Search hit** 卡：Shot 54 · 3:41–3:48 · 7.7s · rerank / vector 分 · 一行说明，按钮 Play from 3:41 / Open Shots。Overview 其余内容不动。
- 引擎离线：legs chip 的三个向量点变红，徽标与相似度条消失，文本命中照常，排序回落到 Date，不弹 toast。

屏 2 · info island · 大 tab 收成三个：Overview / AI / Shots
- `VideoDetailPanel` 现在的 Overview / Transcript / Analysis（Analysis 里是 Summary + Visual analysis 两段）改成 **Overview / AI / Shots** 三个大 tab。**AI** 大 tab 内一排小 tab：Transcript / Summary / Visual，各自沿用现在的正文与触发按钮（Summarize / Trigger Visual Analysis），不改逻辑只改挂载位置。音频项仍是 Overview + Lyrics。
- **Shots** tab 有索引时带绿点；**未索引态**只显示估算（7:25 · ≈ 100 shots · ≈ 200 embeddings · local provider: free）和 Index this video 按钮，点击后变成任务进度条，任务跑完自动换成正文。有索引时内容自上而下：播放器缩略（当前搜索命中的位置在进度条上打赭色标记）→ 索引信息行（indexed 时间 · 所在空间）+ Re-index → 2×2 数字（Shots / Vectors frame+clip / Average shot / Clusters）→ **镜头条**（按簇着色，hover 时间段与簇名，点击经 `onSeek` 跳播放器，命中镜头描白边）→ 图例 → **簇列表**（Cluster 01 · Talking head A · 30 shots · 3:11，无预设标签，名字留给二期 VLM 卡）→ **镜头列表**（可滚动，Shot n · 簇 · 时间段，命中行高亮并自动滚到视野内）。
- 面板宽度按 island 的 380 px 设计；classic 分栏模式同一 JSX。
- `ResourceDetailPage` 的 inspector 同样收成 Overview / AI / Shots（Review 保留在 Overview 内或作为第四个，实施时按现有 Review 依赖定）；播放器标记轨（现在放评论标记）加镜头切点标记。

屏 3 · Settings 弹窗 → AI 页 → 顶部子 tab 新增 **Vectors**
- AI 页现有子 tab 是 通用 / 服务商 / 本地 CLI / MCP / 记忆。改成 **General / Providers / Local CLI / Memory / Vectors**：**MCP 移出**这排，成为左栏"应用设置"下的独立导航项（排在 AI 之后），为 Vectors 腾位。其余四个子 tab 正文不动；**模型行仍只在 Providers 登记**。
- **Vectors** 子 tab 内两个 section：
  - **Vector Spaces**：Current space 卡（provider、协议、能力 chip text / image / video / rerank、2048 · halfvec · HNSW、指令版本、延迟 p50）+ Candidate space 卡（重嵌进度 478 / 1,285、token 消耗、Pause；**Switch to this space** 在 100% 前禁用）+ Add space。
  - **Indexing policy**：Auto-index new videos（Off / Local provider only / Always）、On-demand indexing 开关、估算文案、Task Center 呈现说明（父任务 + 子任务、类型化 skipped）。
  - **Retrieval layers**（Backfill 列即全局回填）：四层表（Layer / Status / Coverage 进度 / Source / Backfill：Dry run / Run 20），Transcript 行 not built；底部两条说明：上次回填的 skipped 按 reason code 列出；嵌入失败在任务卡与本页都可见。
- MCP 挪到左栏是导航改动，与向量无依赖，作为 UI PR 里的独立 commit。

### 4.7 推送入口：第四个 AI 意图 `shots`（插件 / 快捷指令 / 播放器按钮）

现有推送链已经把 转录 / 总结 / 解析 做成显式请求字段（`POST /media/fetch` 的 `transcribe / summarize / analyze`，映射 Pipeline 组的系统标签 `transcript / summary / analyze`，见 `2026-09-10-ai-intent-fields-design.md`）。镜头索引加为**第四个意图 `shots`**，同一套约定：

- **字段**：`shots: bool`，标签 slug `shots`（Pipeline 组）。`media_fetch_helpers.intent_tag_slugs` 与 `SELECTION_FIELDS`（快捷指令临时 token）同批加。
- **依赖关系**：`shots` 与 `transcribe` **无依赖**（不用转录）；下载完成后直接派发 `index_video_shots`，与转录并行。`applyIntentDependencies` 不为它加链。
- **入口三处同批改**（它们本来就互为镜像）：Chrome 插件 popup 的 Push tab（`chrome-extension/intents.js` + `popup.html` 意图行）、iOS 快捷指令标签页（`frontend/pages/ShortcutsTagsPage.tsx` 意图行加"镜头 / Shots"）、`frontend/utils/aiIntents.ts` 的 `PIPELINE_TAG_SLUGS`。批量抓取与资源"发给 agent"路径原样透传。
- **成本提示**：意图行下一行小字：本地 provider 免费 / doubao ≈ 300 token 每镜头；与 Indexing policy 联动：Auto-index = Always，或 Local provider only 且当前 provider 是本地时，`shots` 默认勾上，否则默认不勾。
- **播放器按钮（插件 v1.5）**：content script 在 Bilibili / 抖音播放器工具栏注入一个 nous 按钮（对应 Indexed 的绿色小按钮）。点一下 = 用上次的意图与标签直接 Push；hover 出迷你面板可改意图；任务运行中按钮转赭色 busy，完成转绿并链到该视频的 Shots tab。这是插件的独立版本票，不阻塞后端。
- **编排**：下载父任务 → 按意图并行派发子任务（transcript → summary / analyze 链保持不变；shots 独立），全部进 Task Center，与 4.3 第 8 条同一套父子任务呈现。

### 4.8 降级与一致性

- 引擎离线 / key 错 / 超时：对应腿 `unavailable` / `error` / `timeout`，文本腿照常返回。
- provider 缺模态：层 `unavailable`，原因进 `vector_leg` 字段与日志。
- 空间切换：新空间回填完成前，查询仍打旧空间；切换是显式动作（Admin），不是自动。

## 5. 验收（可证伪）

1. 语义层基准（57 条查询）：doubao 与 nous-engine 本地模型 recall@10 都 ≥ 0.65，两者差 ≤ 0.1。
2. 切镜：30 支视频与 OmniShotCut 切点比对，容差 ±0.5 s，F1 ≥ 0.8。
3. 镜头语言：20 条运镜类查询（手持跟拍 / 固定机位 / 横向摇移 / 缓慢推进 …）由本机 27B 判 top-10 相关性，hit@10 ≥ 0.6；同一批查询用帧平均对照，片段向量必须更高。
4. 时间码：任意命中镜头 `start_ms/end_ms` 与播放器跳转一致（抽 20 条人工点）。
5. 延迟：混合搜索开全部腿 p95 ≤ 1.5 s（本地 provider）/ ≤ 2.5 s（doubao）；超时腿不拖累文本腿。
6. 回填：全库 189 h 在 PRO 6000 上 ≤ 12 h 跑完；单支新视频 ≤ 2 min；中转区跑完为空。
7. 降级：停掉引擎容器后搜索仍 200，`vector_leg` 各腿为 `unavailable`，文本命中不变。
8. 空间切换：换 provider 后旧空间不再被查询，重嵌完成前后结果集合可解释。
9. provider 对称：doubao 与本地模型各跑一遍验收 1–5，差异写进 PR。

## 6. 决策记录

- 语义层模型：doubao（已配置）先用；WeMM-2B 是自托管候选，两者带指令后差在噪声内。4B/9B 不进生产。
- 向量列固定 `halfvec(2048)`，更大维度截断。原因：HNSW 维度上限 + 存储减半 + 跨 provider 同列。
- 向量库：pgvector，不引第二种。十万到百万级够用，能 join 权限与回收站。
- 镜头语言用片段向量而不是 VLM 标签做检索；VLM 标签二期做 facet。
- 切镜自研，骨架抄 PySceneDetect，特征用向量距离。
- 查询指令 `en_keyword`，文本前缀而非厂商字段。
- 存储位置：nous-db 数据目录（gpupc NVMe `/media/heygo/program/datahub/nous/data/postgres`）；视频文件在 nas-B，抽帧走对象存储拉流。
- 模型部署归 nous-engine，业务只认协议 + 能力声明 + 空间。

## 7. 分期

| 期 | 内容 | 状态 |
|---|---|---|
| PR 1 | 嵌入失败可见、回填端点、向量腿并入混合搜索、查询指令 | 已完成待推 |
| PR 2 | `embedding_spaces` + `resource_embeddings`（halfvec + HNSW）+ 多模态协议适配器 + 能力声明 + 语义层文档扩写 | 已实现（本 PR，mig 494） |
| PR 3 | 切镜 workflow + `video_shots` + `frame` 向量 + 画面腿 + backfill-shots | 依赖 nous-engine 多模态端点 |
| PR 4 | `clip` 向量 + 镜头语言腿 + 簇 + 精排 | 依赖 nous-engine reranker |
| PR 5 | agent 工具 + `library-search` skill | — |
| PR 5b | 第四个意图 `shots`：fetch 字段 + Pipeline 标签 + 插件 popup + 快捷指令页 + 工具函数镜像 | 依赖 PR 3 |
| 插件 v1.5 | 播放器工具栏 nous 按钮（一键 Push + 迷你面板 + busy/done 态） | 依赖 PR 5b |
| PR 6 | 搜索结果与视频详情 UI | — |
| 二期 | VLM 镜头卡 facet、转录层、非视频资源 | — |

## 8. 开放问题（不阻塞）

- doubao `video_url` 输入对运镜语义的质量未测；若明显弱于本地模型，镜头语言层就是第一个"按能力声明降级"的真实案例。
- 9B 在片段向量上是否值得（MMEB 视频分高 3.5）：PR 4 前用 WeMM 评测栈在 30 支视频上测一次。
- HDBSCAN 参数在口播视频（大量近似镜头）上的行为。

## 9. Known Limitations and Deferred Work

- 长视频（>1 h）按封顶 600 镜头会丢细节；封顶值随体量再调。
- 精排 provider 缺失时只有向量分数排序，不做本地兜底重排。
- 空间并存期间存储翻倍，未做自动清理。
- `source_hash` 变化（文档版本 bump 或输入变动）目前不会触发重嵌：回填只挑「当前空间里没有行」的资源；hash 只在 `analyze_l1` 重跑（upsert 覆盖）与同一次回填内幂等两处生效。「hash 过期即重嵌」留给下一版。

## 附录 A：基准数据（2026-09-15/16）

| 指令措辞 | doubao | WeMM-2B | WeMM-4B | WeMM-9B |
|---|---|---|---|---|
| 无指令 | 0.48 | 0.31 | 0.19 | 0.12 |
| en_web | 0.77 | 0.68 | 0.51 | 0.53 |
| en_keyword（采用） | 0.77 | 0.66 | 0.61 | 0.60 |
| en_video | 0.75 | 0.66 | 0.57 | 0.60 |
| zh_video | 0.70 | 0.69 | 0.58 | 0.44 |
| 只加 "Query: " | 0.48 | 0.42 | 0.43 | 0.20 |

延迟 p50 文本/图片（ms）：doubao 198/504，2B 21/127，4B 27/134，9B 27/223。费用：doubao 一轮回填 28 万 token，本地为零。

存储估算（每镜头两条 halfvec(2048)，平均 4.5 s 一镜）：全库约 15 万镜头、30 万向量，数据 + HNSW 约 5 GB；临时逐秒帧向量 68 万条不落库。
