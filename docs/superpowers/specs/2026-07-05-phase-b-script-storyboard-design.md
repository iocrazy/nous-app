# Projects Phase B — Script→Storyboard 主线设计 Spec (v3, autoplan 全阶段修订)

- 日期：2026-07-05（v3：CEO/Design/Eng 三阶段双声审查修订完毕）
- 状态：待用户最终批准（final gate）
- 采集底稿：`docs/superpowers/specs/phase-b-reference-intake.md`
- 范围：Phase B 首个切片 = Script→Storyboard 主线。其余模块在本 spec 定架构、分阶段实现。

## 0. 用户裁决记录（2026-07-05 autoplan 门）

CEO 双声（Claude opus + Codex）一致挑战三个方向，用户裁决（**已定，不再复议**）：
1. **分期维持「编辑器优先」**（模型建议生成优先 wedge；用户否决——写作体验是地基，用户本人是首个重度用户）。
2. **scene 内容维持自造元素格式**（模型建议复用 mig120 TipTap；用户否决）。风险已知晓，缓解见 §2.2。
3. **Supabase Storage 迁移由另一 session 独立推进**——移出本 spec；生成阶段经 storage_service 接口落地（§4）。

CEO 双声一致的 5 项架构修正全部采纳（定日期 cutover、新建 shots 表、Episode 维度、去定稿硬门、版本走操作历史）。Design/Eng 双声（各 Claude+Codex）发现均为**规格完备性**修正，与用户裁决无冲突，**全部采纳**并已织入下文（审计表 11-23 号）。

## 1. 目标与背景

把 MediaHub 项目详情从「阶段 tab 条 + 腐坏的节点画布」重做成创作工作台：文字剧本为写作主视图，节点为随时可用的结构投影，脚本 scene 顺流到分镜 shot。laper.ai 为设计基线，差异化主张 = **媒资采集→剧本→生成的一体化** + Outline↔Beats↔Script 联动（laper 缺失）。

**为什么现在做**：Phase A 后审查发现现有 script/storyboard 前端整链腐坏（六连环 bug 才端到端跑通），且是「旧节点画布优先」的错位设计。后端数据层活着但缺 Scene 层。

## 2. 核心数据模型

```
projects (已存在)
  └─ episodes (★新建)
       └─ script_projects (已存在；加 episode_id 可空)
            └─ script_chapters (已存在：DAG 分支；content_json=TipTap 保留为章节级 prose)
                 └─ script_scenes (★新建：写作+分镜共同单元)
                      └─ script_shots (★新建：镜头)
script_ops (★新建：元素级操作日志，Phase 1 起记录)
```

### 2.1 新表

**`episodes`**（迁移预 claim 337）：id BIGINT PK snowflake / project_id BIGINT NOT NULL FK projects **ON DELETE CASCADE** / title / sort_order / created_at / updated_at。新项目默认建 "Ep 1"。**回填迁移（预 claim 339）**：存量每个含 script_projects 的项目建 "Ep 1" 并 set episode_id。路由：`/team/:t/projects/:p/episodes/:e/script`。一致性规则（应用层校验）：`script.episode.project_id == script.project_id`。

**`script_projects` 加列**：`episode_id BIGINT NULL FK episodes` **ON DELETE RESTRICT**（episode 下有脚本不许删）。

**`script_scenes`**（迁移预 claim 338，含 script_ops；合并时号被占则取下一空号；迁移末尾 NOTIFY pgrst）：
| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK snowflake | |
| script_id | BIGINT NOT NULL FK script_projects | |
| chapter_id | BIGINT NULL FK script_chapters **ON DELETE SET NULL** | **不变量（v3 修正）：scene 归属至多一个 chapter**；NULL=散场景桶（script 内独立排序）。删章节→场景转散，不静默删内容。一致性校验：chapter_id 非空时 `chapter.script_id == scene.script_id` |
| scene_number | INTEGER | **读取时按 sort_order 派生、仅展示**（不落库或落库仅作缓存），避免并发插入撞号 |
| heading_int_ext | VARCHAR(10) | INT/EXT |
| location_text | TEXT | Phase 1 唯一地点形态 |
| location_id | BIGINT NULL | 实体软引用（无 FK；实体模块完整化后启用） |
| time_of_day | VARCHAR(20) | DAY/NIGHT/NOON/DAWN/DUSK/CONTINUOUS/LATER |
| content_json | JSONB | 元素节点树（§2.2） |
| content | TEXT | **派生纯文本**（搜索/AI 上下文），每次内容写同步提取（沿用 chapters `_extract` 思路） |
| content_version | INTEGER NOT NULL DEFAULT 0 | **内容版本号**：每次内容写 +1，作乐观并发 token（与 updated_at/画布坐标解耦，布局写不引发内容假冲突） |
| position_x/y, width, height | DOUBLE | 节点视图坐标 |
| sort_order | INTEGER | **canonical 排序**：step-1000 稀疏，冲突时重排 |
| created_at / updated_at | TIMESTAMPTZ | |

索引：`(script_id, chapter_id, sort_order)`。

**`script_shots`**：id BIGINT PK / scene_id BIGINT NOT NULL FK script_scenes **ON DELETE CASCADE** / shot_number INTEGER / shot_type / camera_angle / camera_movement / focal_length VARCHAR + lighting TEXT（对齐 storyboard_frames 词表）/ description TEXT（散文+@实体）/ image_url / thumbnail_url / video_url TEXT / status VARCHAR(empty/generating/done/failed) / sort_order / created_at / updated_at。索引 `(scene_id, sort_order)`。（替代「往 storyboard_frames 塞 scene_id」——frames 的 node_id/project_id 是 NOT NULL 双亲。）

**`script_ops`**（Phase 1 起记录，v3 提前自 Phase 4）：id BIGINT PK / scene_id BIGINT NOT NULL / op_seq BIGINT（scene 内单调）/ op_json JSONB（op 全文，含逆操作所需信息）/ actor（user_id 或 'copilot'）/ created_at。**undo = 逆 op 经同一协议重放**；Phase 4 的 commit/diff UI 建立其上，无需重构端点。

### 2.2 场景内容 = 自造元素节点树（用户裁决维持）

`content_json` = 有序元素数组：
```json
{ "id": "el_xxx", "type": "action|dialogue|character|paren|transition|comment|subtitle",
  "text": "...", "character_id": 123, "meta": {} }
```

**元素 op 协议（v3 规格化，Eng 双声共识）**：
1. ops 为 **anchor-based**：`{op: insert|update|delete|move, element_id, before_id|after_id, payload}`。**禁 index-based**（并发插入后 index 漂移，重放会写错位置）。
2. 元素 id **客户端生成**（el_xxx）；insert = upsert-by-id（**幂等**，防丢 ACK 双插）。
3. 乐观并发 token = `content_version`（`If-Match: <version>`），冲突返回 409 附当前版本与受影响元素。
4. **禁止整块 content_json replace** 作为常规写路径（AI 也不例外）。
5. 每次成功写落一行 `script_ops`。
6. **copilot 写入协议**：带 read_version + 目标元素 content hash；stale → 返回 **proposal**（不自动写）+ 退避 + 重试上限 + 用户可见状态。Phase 1 copilot **只做结构化元素级编辑**（明确 element_id 的 update/insert/delete）；自由文本→ops 调和器（diff 保 id）为 **Phase 2 交付**。
7. 前端元素 PATCH debounce/batch，减少写风暴。

**与 mig120 TipTap 边界**：`script_chapters.content_json`（TipTap）保留承载章节级 prose（AI Expand 产出）；scene 级用本格式；两格式各管一层不互转。

**存量内容 cutover（v3 新增，Eng H2）**：编辑器遇「chapter 有 prose 且无 scenes」→ **回退渲染章节 prose（只读卡）+ 「Convert to scenes」按钮**（AI 辅助拆场景，用户确认后写入）。绝不静默空白；老 scripts 渐进迁移，无强制一次性转换。

### 2.3 旧 storyboard 面定日期 cutover

Phase 3 起点：新链路（scene→script_shots）为唯一写路径，旧 storyboard_projects/nodes/frames 转只读；Phase 3 内 backfill（可映射的映射，孤儿标 legacy）；Phase 4 DROP 旧表（沿用 Phase A destructive 纪律：先删代码引用再删表）。`script_storyboard_links` 同节奏退役。**无永久并行。**

### 2.4 实体引用

Phase 1：location_text 文本 + character_id 软引用（无 FK）。`@` 在 Phase 1 渲染为**名字 chip + 颜色点**（非富卡——无实体模块，明确降级）；create-on-miss = 登记 script 内本地角色名。完整实体建模（别名/说话人/span 引用）随实体模块阶段。

### 2.5 Beats 锚定（预留）

Beat 可跨 scene：Phase 4 beats 用 **junction 表 `beat_scenes`**（v3 修正，不用 BIGINT[]——#911 array-bind 教训 + 无法 FK 约束）。本期不建表。

## 3. 视图架构

### 3.1 布局与层级（v3 新增，Design D1-D3）

优先级显式：**(1) 中央格式化页面 = 主角**（最大宽度、排版焦点，写作时唯一要紧的东西）；(2) 左栏 = Episodes 选择器 + Scenes 列表，可折叠、安静——**Phase 1 不摆其余模块**（Beats/Storyboard/… 藏于 flag 后，Phase 2+ 出现）；(3) 右 Writing 面板（Info/Statistics/Format）可折叠、按需；(4) AI copilot 由选区召唤、浮层呈现，**非常驻 chrome**。窄屏折叠顺序：右面板先、左栏次。**Scene 块 = 视觉容器**：编号徽章 + 头行（INT/EXT·location·time 三下拉）+ 左侧 `::` 拖拽手柄，视觉包住全部子元素（父子模型与拖拽换序的可视基础）。

### 3.2 文字剧本编辑器（写作主视图 — Phase 1）

- Script / Outline / Cover tab；元素工具条 8 元素；`@` 实体选择器（键盘可导航 combobox）；Scene 头三段下拉。
- **Tab/Enter 状态转移表（v3 提升进正文，D7）**——实现前逐格对照 laper 核准：

| 当前元素 | Enter | Tab | Shift-Tab |
|----------|-------|-----|-----------|
| Scene 头 | 新 Action 行 | 进入 Action（laper 实测） | 实现前对照 laper 核准 |
| Action | 新 Action | 切 Character | 切 Scene 头 |
| Character（选择器开）| New character | Switch to action（laper 实测）| 关闭选择器回 Action |
| Character（已选）| 进入 Dialogue | 切 Paren | 回 Action |
| Dialogue | 新 Dialogue 行 | 切 Paren | 回 Character |
| Paren | 回 Dialogue | 切 Transition | 回 Dialogue |
| Transition（选择器开）| 选中并换行 | Switch to scene header（laper 实测）| 关闭回 Action |
| Comment / Subtitle | 新 Action | 循环回 Scene | 前一元素类型 |
| Scene 头 time 字段 | — | 切 INT/EXT（laper 实测）| — |

  Backspace（行首）/Escape/IME/粘贴行为：实现前对照 laper 核准并写入 plan 用例。
- **双制式渲染表（D8）**——同一 content_json 两套**布局引擎**（非 CSS 主题；工作量在 plan 单列预算，E12）。切换 per-script 持久（右面板 Format）：

| 元素 | Hollywood | Asian |
|------|-----------|-------|
| Scene 头 | 全大写 `INT. LOCATION - NIGHT`，左对齐 | `N. Location 时间 / INT-EXT` 编号行（laper 实测） |
| Action | 左对齐全宽 | 带 `△` 前缀行（laper 实测） |
| Character | 居中偏左大写 | `角色名:` 标签行（laper 实测） |
| Dialogue | 居中窄列 | 缩进于角色标签下（laper 实测） |
| Paren | 居中括号 | 斜体括号（laper 实测） |
| Transition | 右对齐大写 `CUT TO:` | 右对齐（实现前核准） |
| Comment | 侧线批注块 | 竖线引用+斜体（laper 实测） |
| Subtitle | 居中斜体 | 居中斜体（laper 实测） |

- 右侧 Writing 面板：Statistics 实时计数（Words/Scenes/Characters/…；**页数依赖渲染引擎，工作量随 E12 预算**）。
- **AI copilot**：选中→挂对话（Scene N·M elements）→ read focus → **结构化元素级编辑**（§2.2 协议）→ `N edits this turn` + Undo；Polish/Summarize 快捷。

### 3.3 节点视图（随时可用投影 — Phase 2）与 Storyboard（Phase 3）

节点视图：定稿硬门已去除；chapter/scene 节点 + Expand/Branch/Storyboard 动作；canvas-core + @xyflow/react；**智能画布 clean-room**（Infinite-Canvas 仅思路参考，LICENSE 禁商用）。Storyboard：按 scene 分列 → Auto Storyboard → script_shots；shot 卡=镜号+参数标签+描述+@实体+Generate；laper 为能力基线，视觉 design-shotgun 出稿。

### 3.4 状态设计（v3 新增，Design D4-D6 — CRITICAL）

- **409 冲突 UX**：纯排序/元数据竞争（文本 byte-identical）→ 自动重放、用户无感；**文本分歧 → 绝不静默覆盖**，非阻塞提示「此行已在别处更改：保留我的 / 采用对方 / 对比」。Phase 1 的 409 主要来源 = **copilot 与用户写同一 scene**（协同虽是 Phase 5，并发 Phase 1 就存在）。
- **保存信任面**：常驻保存指示器（saved ✓ / saving… / retrying / offline-queued）；离线=本地排队，重连经同一 409 协议重放。
- **copilot 状态机**：idle / 召唤 / 流式（工具步 trace 可见）/ 目标元素锁定+预览态 / 成功（N edits·Undo）/ 失败（消息+重试，元素不动）/ 中止（部分编辑回滚）。注意 SSE done 契约既有坑（bug_stream_turn_tool_trace_lost 的 follow-up）。
- **空态三件**：零 scene 脚本→冷启动屏，主 action「Create Story」（插入首个 Scene 块、光标就位、Tab 就绪）；**Import Script 排除出 Phase 1（不出现）**；空 scene 块→内联提示「Tab 开始 Action，或添加 Character」；@实体缺失→名字 chip 降级（§2.4）。
- **Episode**：Phase 1 = 数据层 + 默认 Ep 1；**多集管理 UI 推 Phase 2**（路由支持，UI 不做添加/切换）。

### 3.5 无障碍（v3 新增，D10）

Tab 被编辑器语义占用 → 焦点逃逸规则（Esc 退出编辑态后 Tab 走正常焦点序）；工具条/选择器键盘导航（combobox ARIA）；可见焦点环；scene 重排提供键盘路径；409/AI 事件 aria-live 通告。

### 3.6 场景重排（D9）

独立 **scene 级端点**（非元素 PATCH）；跨 chapter 拖动=允许（reparent+resequence）；drop indicator；分支切换前 **flush 未决 ops**（守卫）。

### 3.7 编辑器视觉最低基线（D12）

design-shotgun 出稿前：纸页隐喻、等宽 Courier 系、页面最大宽度与行距密度对齐 laper 截图；遵守岛式设计系统铁律（零 emoji / 密度不减 / 禁 zinc）。

## 4. 生成资产存储

Supabase Storage 迁移由独立 session 推进（用户裁决），移出本 spec。约定接口：Phase 3 Generate 产物经 `storage_service.put(...) -> asset_url` 单点落库；底层指向 NAS 或 Supabase Storage 以该 session 进度为准，生成链不感知。

## 5. 超越 laper 的三件

1. **版本管理（Phase 4）**：基于 **Phase 1 起就在记录的 `script_ops`**；commit=打标签（script_commits: script_id, message, op_seq, created_by）；diff=两 commit 间 op 重放；回滚=逆向重放；只允许手动 commit。
2. **Outline↔Script↔Beats 联动（Phase 2 起）**：三层同源；beats 经 junction 表锚 scene。
3. **多人协同（Phase 5，独立 brainstorm）**：CRDT 适配评估（自造格式风险裁决时已知晓）。

## 6. 分阶段实现（先出 Phase 1 plan）

| Phase | 交付 | 依赖 |
|-------|------|------|
| **1（首期）** | 迁移 337-339（episodes / script_scenes+script_ops / 回填）；数据层（scene CRUD + 元素级 anchor-op PATCH + content_version 并发 + op 落账 + 派生 content）；**全部新端点挂 scope 守卫 + wiring 测试**（含顺带修 script_canvas_router 既有缺口）；**文字剧本编辑器**（§3.1-3.7 全部规格：布局层级/8 元素工具条/Tab 状态机/@chip/双制式双引擎（单列任务）/Statistics（单列任务）/copilot 结构化元素编辑/409 UX/保存指示器/空态/a11y）；**存量 chapter prose 回退渲染 + Convert to scenes**；scene 懒加载+长脚本虚拟化（性能预算）；入口 flag `VITE_FEATURE_SCRIPT_V2` | 无 |
| 2 | 节点视图（随时可用投影）+ Outline↔Script 联动起步 + **copilot 自由文本→ops 调和器** + 多集管理 UI | P1 |
| 3 | Storyboard：Auto Storyboard、Generate（经 storage_service）；旧 storyboard 面转只读 + backfill | P1 P2 |
| 4 | 版本 commit/diff UI（基于既有 script_ops）；Beats 表（junction 锚）+ 联动完整；DROP 旧 storyboard 表 | P1 P3 |
| 5 | 多人实时协同（独立 brainstorm + CRDT 评估） | P1-P3 |

## 7. 架构不变量 / 约束

- 内容存结构（元素树），格式是渲染层；用户不手调格式。
- Scene 单一数据源；**scene 归属至多一个 chapter**；sort_order canonical、scene_number 派生展示。
- 元素级 anchor-op 写 + content_version 乐观并发 + 客户端 id 幂等；禁整块 replace；每写落 op。
- **authz 不变量：所有新端点（episodes/scenes/shots/ops）挂 scope 守卫**（Phase A verify_*_access 范式 + 结构化 wiring 测试）。
- 旧 storyboard 面：P3 只读 → P4 DROP。
- Episode 是路由与数据层级；存量回填 Ep 1。
- 智能画布 clean-room；@xyflow/react(MIT) + canvas-core。
- Trunk-based，PR ≤1 天，flag-dark；UI 英文+i18n；PC 优先。
- 后端 ORM 路径；migration 走 PR+CI+NOTIFY pgrst；bigint 写路径 coerce；**path param 与 row 字段比较必 str 双侧 coerce**（#1006 坑）。

## 8. 测试策略（v3 扩充，E5）

Phase 1 必测：双写者竞争 / 409 重放正确性 / 幂等插入（丢 ACK）/ AI+用户同元素 / copilot 重试风暴 / int-vs-str path param 比较 / 存量 TipTap chapter 回退渲染 + Convert / cascade 行为（章节删→scene 转散；scene 删→shots 级联）/ RLS+跨团队 IDOR（403/200 对）/ Tab-Enter-Backspace-IME-粘贴编辑器状态机 / 双制式渲染快照 / 10x scenes 性能冒烟 / 真库集成（asyncpg 类型坑）/ 端到端 E2E（建 episode→scene→写元素→切格式→读回）。每 PR lint 齐。**六连环教训：无人用面必须 E2E 校验后才算 done。**

## 9. 明确不做（YAGNI / 移出）

- Supabase Storage 迁移（另一 session）。
- Phase 1 不做：节点视图、分镜、生成、版本 UI、协同、Beats、实体完整建模、移动端、**Import Script**、多集管理 UI、copilot 自由文本调和器。
- 不移植 Infinite-Canvas 代码；不引入 TipTap 到 scene 层（用户裁决）。

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 1 | CEO | DX 阶段跳过 | Mechanical | P3 | API 均内部契约，无外部开发者面 | 跑 DX 全审 |
| 2 | CEO | 分期维持编辑器优先 | USER CHALLENGE→用户裁决 | — | 用户：写作体验是地基，本人为首个重度用户 | 双声建议生成优先 wedge |
| 3 | CEO | scene 内容自造元素格式 | USER CHALLENGE→用户裁决 | — | 用户否决 TipTap；风险已缓解（anchor-op+content_version） | mig120 TipTap |
| 4 | CEO | 存储迁移移出本 spec | USER CHALLENGE→用户裁决 | — | 另一 session 推进；只约定 storage_service 接口 | spec 内绑定实现 |
| 5 | CEO | 旧表定日期 cutover | Taste→采纳 | P2/P5 | 避免 Phase A 式死表面债 | 永久并行 |
| 6 | CEO | 新建 script_shots 表 | Taste→采纳 | P5 | frames NOT NULL 双亲不可硬塞 | frames 加 scene_id |
| 7 | CEO | 补 episodes 维度 | Taste→采纳 | P1 | 后补代价高 | 后期再加 |
| 8 | CEO | 节点视图去定稿硬门 | Taste→采纳 | P5 | 创作非线性 | 定稿后解锁 |
| 9 | CEO | 版本走操作历史 | Taste→采纳 | P1/P5 | 全量快照 O(size×commits) | snapshot 全量 |
| 10 | CEO | scene 分支本地不变量 | Mechanical | P5 | 消除排序歧义 | 跨分支共享 |
| 11 | Design | 布局层级显式化+Phase1 左栏收敛+scene 视觉容器 (D1-D3) | Taste→采纳 | P5 | 防实现者发明三栏仪表盘 | 留白让实现者定 |
| 12 | Design | 409 UX+保存信任面+copilot 状态机+空态三件 (D4-D6) | Taste→采纳 | P1 | 强制并发协议的用户面不能是空白 | 只定后端协议 |
| 13 | Design | Tab 状态机+双制式渲染表提升进正文 (D7-D8) | Mechanical | P5 | 核心价值主张不能只活在采集稿 | 留在 intake |
| 14 | Design | 场景重排独立端点+键盘路径 (D9) | Mechanical | P5 | scene 级 op 非元素 PATCH | 复用元素 PATCH |
| 15 | Design | a11y 节 (D10) | Taste→采纳 | P1 | Tab 被占用的编辑器必须有焦点逃逸 | 不管 |
| 16 | Design | Import Script 排除 Phase 1；多集 UI 推 P2 (D6/D11) | Taste→采纳 | P3 | 收敛首期 | 塞进 P1 |
| 17 | Design | 编辑器视觉最低基线 (D12) | Taste→采纳 | P5 | 「像 laper」不是 spec | 全交 design-shotgun |
| 18 | Eng | authz 不变量+wiring 测试+顺带修 script_canvas_router (E1) | Mechanical | P1 | Phase A IDOR 同类，不能重演 | 不管存量缺口 |
| 19 | Eng | 存量 prose 回退渲染+Convert to scenes (E2) | Taste→采纳 | P1 | 用户自己的老脚本不能开屏空白 | 静默空白 |
| 20 | Eng | anchor-op+客户端 id 幂等+content_version+copilot proposal 协议 (E3) | Mechanical | P5 | index-op 重放必错；whole-scene token 假冲突 | index op + updated_at |
| 21 | Eng | copilot P1 只做结构化编辑，调和器 P2 (E4) | Taste→采纳 | P3 | 调和器是隐藏大活 | P1 硬啃 |
| 22 | Eng | script_ops 自 P1 记录 (E6) | Taste→采纳 | P1 | undo/版本/copilot 共用，防 P4 重构 | P4 再补 |
| 23 | Eng | 一致性/级联/派生列/排序/索引/迁移号/junction/性能预算 (E7-E15) | Mechanical | P1/P5 | 数据完整性与 2am 面 | 留待实现时踩 |
