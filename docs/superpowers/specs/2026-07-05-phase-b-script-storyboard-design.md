# Projects Phase B — Script→Storyboard 主线设计 Spec

- 日期：2026-07-05
- 状态：设计中（brainstorming 产出，待用户审阅）
- 采集底稿：`docs/superpowers/specs/phase-b-reference-intake.md`
- 范围：Phase B 的**首个切片** = Script→Storyboard 主线。其余模块（Beats 联动细节、Scenes 智能画布生图/生视频、实体卡、多人协同）在本 spec 定架构、分阶段实现。

## 1. 目标与背景

把 MediaHub 项目详情从「阶段 tab 条 + 腐坏的节点画布」重做成一套**创作工作台**：文字剧本为写作主视图，节点为定稿后的结构/分支/推进形态，脚本 scene 顺流到分镜 shot。laper.ai 为设计基线，目标超越它。

**为什么现在做**：Phase A 收尾后审查发现现有 script/storyboard 前端整条链腐坏（一次修了 6 个连环 bug 才端到端跑通），且是「旧节点画布优先」的错位设计，跟用户要的「文字剧本主视图」是两条路。后端数据层活着但需要补关键的 Scene 层。

## 2. 核心数据模型（地基决策）

**三层结构**，chapter → scene → shot，scene 是写作与分镜的**单一共同基本单元**：

```
script_projects (已存在，脚本容器)
  └─ script_chapters (已存在：DAG 分支 parent_chapter_id + branch_label/type + 画布坐标)
       └─ script_scenes (★新建：写作+分镜共同单元)
            └─ shot (镜头：复用 storyboard_frames 的镜头参数+image_url)
```

### 2.1 新建 `script_scenes` 表（Phase 1 核心）

migration `336+`（取合并时的下一个空号）：

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK snowflake | |
| script_id | BIGINT NOT NULL FK script_projects | 归属脚本 |
| chapter_id | BIGINT FK script_chapters | 所属章节（可空——未分章的散场景） |
| scene_number | INTEGER | 场景序号（自动维护） |
| heading_int_ext | VARCHAR(10) | INT / EXT |
| location_id | BIGINT FK storyboard 或 project location | 地点实体引用（见 §2.4） |
| location_text | TEXT | 地点自由文本（实体未建时的回退） |
| time_of_day | VARCHAR(20) | DAY/NIGHT/NOON/DAWN/DUSK/CONTINUOUS/LATER |
| content_json | JSONB | ★场景结构化内容（元素节点树，见 §2.2） |
| position_x/y, width, height | DOUBLE | 节点视图画布坐标（与 script_chapters 同惯例） |
| sort_order | INTEGER | 场景排序（拖动换序作用于此） |
| created_at / updated_at | TIMESTAMPTZ | |

### 2.2 场景内容 = 结构化元素节点树（`content_json`）

**核心价值：格式固定、零手调**。内容存结构，格式（Hollywood/Asian）是渲染层。`content_json` 是有序元素节点数组，每个元素：

```json
{ "id": "el_xxx", "type": "action|dialogue|character|paren|transition|comment|subtitle",
  "text": "...", "character_id": 123, "meta": {} }
```

- **element type** 对应 laper 工具条 8 元素（Scene 头是 scene 本身的字段，不进 content_json）。
- **@实体引用**：dialogue/action 里的 `@角色` 存为 `character_id` 引用 + 渲染时展开角色卡。
- **拖场景换序** = 改 script_scenes.sort_order；**拖元素换序** = 改 content_json 数组顺序。
- **AI node 操作**（Expand/Polish/replace）= 对 content_json 的元素做增删改，**可 undo**（前端维护 edit stack；后端每次 replace 是一次 PATCH）。

> 决策：元素存 JSONB 数组（非独立行）。理由：一个 scene 的元素是强内聚的整体（一起读、一起 AI 改写、一起渲染），JSONB 读写一次到位，避免 N 行 join；scene 才是需要独立 CRUD/排序/引用的单元。

### 2.3 Shot 层：复用并演进 `storyboard_frames`

`storyboard_frames` 已有 shot_type/camera_angle/camera_movement/focal_length/lighting/image_url/thumbnail_url/note——**就是 laper 的 shot**。Phase B 让 shot 直接挂到 `script_scenes`：

- 加列 `storyboard_frames.scene_id BIGINT FK script_scenes`（Phase 2/3，非 Phase 1）。
- 保留 `node_id/project_id`（向后兼容旧 storyboard_projects），新链路以 scene_id 为准。
- Auto Storyboard = 读 scene.content_json + 实体 → AI 生成多个 frame(shot) 行（shot_type/lens/描述），前端展示为该 scene 的镜头列。

### 2.4 实体引用（角色/地点）

现有 `storyboard_characters` + project location 概念。Phase B 统一：scene.location_id / element.character_id 引用实体表。**Phase 1 先做文本回退（location_text）+ character_id 软引用**，实体模块完整化在后续阶段。

### 2.5 与现有表的关系（不破坏）

- `script_chapters` 保留（章节=故事结构+分支，节点视图那层）。新增 chapter→scene 一对多。
- `storyboard_projects/nodes/edges` **暂不删**（旧分镜面）；新链路走 script_scenes + frames.scene_id。迁移策略：Phase 3 把旧 storyboard 数据 backfill 到新模型或标记 legacy。**本 spec 不删旧表**（避免 Phase A 式 destructive 风险）。
- `script_storyboard_links` 旧桥保留；新桥是 frames.scene_id 直连，更强。

## 3. 视图架构（两张脸）

同一份 scene 数据，两种呈现：

### 3.1 文字剧本编辑器（写作主视图 — Phase 1）
- 顶部 tab：Script / Outline / Cover
- Script：scene-based 格式化编辑器。元素工具条（Scene/Action/Character/Paren/Dialogue/Transition/Comment/Subtitle），Tab 循环切格式，`@` 实体选择器，Scene 头三段下拉（INT-EXT / location / time）。
- 渲染层：Hollywood ↔ Asian 双制式（同一 content_json 两种 CSS/布局）。
- 右侧 Writing 面板：Info（Collaborators / Pagination / Format / AI Usage / Statistics 实时计数）+ Collaboration tab。
- 常驻 AI copilot：选中片段→挂进对话（`Scene N · M nodes`）→read focus→replace node→`N edits this turn` + Undo。Polish format / Summarize outline 快捷。
- Outline：富文本（Logline/Synopsis/Beats/角色弧），与 Script 双向联动（Outline 的 beats ↔ scene 区间）。

### 3.2 节点视图（定稿后 — Phase 2）
- chapter/scene 节点在 canvas-core + @xyflow/react(MIT) 上，连线成流。
- 节点动作：Expand（AI 扩写 scene 内容）、Branch（从 chapter 分叉，Character Choice/Condition × 2-4）、Storyboard（scene→分镜桥）。
- **智能画布 = clean-room 重写**：Infinite-Canvas 只作交互/节点类型/多模型编排的**思路参考**，一行代码不抄，建在现有 canvas-core 上。

### 3.3 Storyboard（分镜 — Phase 3）
- 按 scene 分列，每列 Auto Storyboard → shots。
- Shot 卡：镜号 + 镜头参数标签 + 散文描述 + @实体 + Generate（出图/视频）。

## 4. 生成资产存储：迁移到 Supabase 对象存储

**现状**：storyboard/generated media 存 NAS 本地（`NAS_BASE_PATH=/app/downloads`，DB 存相对路径 + `/file` token 端点），无任何 Supabase Storage 封装。

**目标（Phase 3，生成落地时）**：
- 新建 Supabase Storage 客户端封装（`backend/app/services/storage/supabase_storage.py`）：upload / get_signed_url / delete。
- Bucket 结构：`generated-media` bucket，路径 `{project_id}/{scene_id}/{shot_id}/{asset_id}.{ext}`。
- URL 处理：签名 URL（私有 bucket）替代 NAS 相对路径 + mediaToken。
- 前置确认：自建 sb-prod 栈的 storage service 已启用（NAS 上 `mediahub-sb-prod` 栈）。
- **Phase 1/2 不涉及存储**（无生成动作）；本节为 Phase 3 定架构。

## 5. 用户要加的三件（超越 laper，分阶段）
1. **版本管理（类 git commit）**——对 script（scenes+content_json 快照）做 commit/历史/diff/回滚。粒度：整 script commit（含所有 scene 的 content_json 快照）。**Phase 4**（非首期；先让写作+分镜跑通）。设计：新表 `script_commits`（script_id, message, snapshot_json, parent_commit_id, created_by, created_at），snapshot = scenes 全量 JSONB。diff 在应用层按 scene/element 比对。
2. **Outline↔Script↔Beats 联动**——三层同源：Outline 的 Story Beats ↔ Beats 卡 ↔ scene 区间。**Phase 2 起**逐步接。
3. **多人实时协同**——拉团队成员共编。**Phase 5**（最重，独立评估）。技术选型开放：Yjs/CRDT vs Supabase Realtime vs 现有 team chat 栈——**留到 Phase 5 专门 brainstorm**，Phase 1 只把 content_json 设计成 CRDT-friendly（元素带稳定 id）。

## 6. 分阶段实现（本 spec 定架构，先出 Phase 1 plan）

| Phase | 交付 | 依赖 |
|-------|------|------|
| **1（首期，出 plan）** | `script_scenes` 表+migration；Chapter/Scene/Shot 数据层（repo/service/schema/API CRUD + chapter→scene 关系）；**文字剧本编辑器**（scene-based、元素工具条、Tab 循环、@实体、双制式渲染、Statistics）为写作主视图；从旧节点画布迁到文字主视图 | 无（地基） |
| 2 | 节点视图（chapter/scene 节点 + Expand/Branch）；Outline↔Script 联动起步 | P1 |
| 3 | Storyboard（scene→Auto Storyboard→shots）；Supabase Storage 迁移；Generate Still/Video | P1、P2 |
| 4 | 版本管理（script_commits）；Beats↔scene 联动完整 | P1 |
| 5 | 多人实时协同（独立 brainstorm） | P1-P3 |

## 7. 架构不变量 / 约束
- **格式固定**：内容存结构（content_json 元素树），Hollywood/Asian 是渲染层。用户不手调格式。
- **Scene 单一数据源**：写作、分镜、Beats、版本 diff 全部锚定 script_scenes。
- **不删旧表**：storyboard_projects/nodes/script_storyboard_links 保留，新链路并行，迁移在 Phase 3 评估（避免 destructive）。
- **智能画布 clean-room**：Infinite-Canvas 仅思路参考（其 LICENSE 禁商用），代码建在 canvas-core + @xyflow/react(MIT)。
- **生成资产 → Supabase Storage**（Phase 3）。
- Trunk-based，每 PR ≤1 天，feature 部分 flag-dark（`VITE_FEATURE_SCRIPT_V2` 等，默认 false）。
- UI 全英文 + i18n key；PC 优先，移动端后续。
- 后端 ORM 路径（read_scope/write_scope），migration 走 PR+CI，末尾 NOTIFY pgrst。

## 8. 测试策略
- **Phase 1**：script_scenes repo/service 单测（含 content_json 元素增删改、拖拽排序、chapter→scene 关系）；API 集成测试（真库 round-trip，参照本 session E2E recipe，避免 mocked 漏 asyncpg 类型坑）；前端编辑器组件单测（元素工具条行为、Tab 循环、@选择器、双制式渲染快照）；**真库 E2E**（建 script→建 scene→写元素→切格式→读回）。
- 沿用 Phase A/本 session 的教训：DB 端点 done 前跑真库 smoke（mocked 全漏）；无人用面必须端到端 E2E 校（六连环 bug 教训）。

## 9. 明确不做（YAGNI / 后期）
- Phase 1 不做：节点视图、分镜、生成、Supabase 存储、版本管理、协同、实体卡完整化、Beats。
- 不删任何旧表（storyboard 旧面并行保留）。
- 不移植 Infinite-Canvas 任何代码。
- 移动端本期不做。
