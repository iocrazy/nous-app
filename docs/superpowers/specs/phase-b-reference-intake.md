# Projects Phase B — 参考采集笔记（进行中）

> 状态：参考采集中（用户「先收齐参考再拆」）。首个深挖模块 = **Script 剧本编辑器**。
> 这份是活文档，收集参考 + 建立 mental model，收齐后长成「总体架构 + 模块拆分」spec。

## 核心范式

项目详情左栏每个模块 = 一个完整功能 mini-app，不是 tab。Episode 维度组织；跨模块常驻右侧 AI copilot；大量用无限画布做空间化布局。阶段（规划→脚本→分镜→生成→审核→交付）串起这些模块 = 之前定的「阶段驱动工作台」。

## 参考来源

- **laper.ai**（截图 9 张，2026-07-05 收）— Script/Beats/Storyboard/Scenes/Characters/Locations/Props 的成熟形态
- **Infinite-Canvas**（`/Volumes/program/project-code/github-repos/Infinite-Canvas`）— 节点式智能画布：ComfyUI/即梦CLI/多模型（OpenAI/Gemini/方舟/Modelscope/火山）文生图·图生图·文生视频·图生视频；扩图/全景/抽帧/循环节点。**注：该仓 LICENSE 禁商用**，只作交互/能力参考，不移植代码。

## MediaHub 现有地基（Phase B 是提升不是从零）

后端：`script_*`（ai / canvas / outline·projects / import / export / assets）、`sb_*`（分镜 ai / canvas / characters / projects / export）、`canvases_router`、`generated_media_router`。CLAUDE.md：script_ai agent（script-outline/expand/branch skills）、Storyboard AI、Canvas 系统（Infinite-Canvas port，见 project_infinite_canvas_port）、project_style_profile 注入 AI。

## 模块形态（laper 观察）

### Script 剧本编辑器【首个深挖】

**⭐ 架构定调（用户明确）**：laper 的 "screenwriting/project" **就等于 MediaHub 的 project**。整个 laper 结构（Episodes → Script/Beats/Storyboard/Scenes/Characters/Props/Locations/Assets）直接映射到 MediaHub 项目详情。脚本创作与 storyboard 必须有关联性（scene 流向 storyboard 列）。

- 顶部 tab：**Script / Outline / Cover**
- 左栏：Episodes（Ep 1）→ Script/Beats/Storyboard/Scenes/Characters/Props/Locations/Assets；下方 Scenes 列表（1. INT Blank Studio NIGHT …）

**编辑器元素工具条**（好莱坞格式，Tab 循环切格式，实测行为）：
- **Scene**：插入场景标题段落（固定格式 `INT/EXT LOCATION - DAY/NIGHT`），插入后 **Tab 自动切到 Action**
- **Action**：自由动作描述行
- **Character**：弹出「Search characters…」选择器 — Tab=Switch to action / Enter=New character；列已有角色（CLIENT/DEV/VOICE(V.O.)/CEO）→ 引用或新建
- **Paren**：括号提示（parenthetical）
- **Dialogue**：内联输入「Enter dialogue… e.g. I'm back…」
- **Transition**：弹出 TRANS 选择器 — Tab=Switch to scene header；列 CUT TO / FADE TO / DISSOLVE TO / FADE IN / FADE OUT
- **Comment**：内联批注行（高亮框）
- **Subtitle**：居中斜体字幕行
- **`@` 提及**：输入 @ → 弹「a character to render their card in the script」，列角色带**色点**（CLIENT红/DEV绿/VOICE紫/CEO橙）→ 在剧本里内联渲染角色卡

**⭐ AI copilot 流程（核心"智能"，实测一次完整来回）**：
1. 选中段落 → 选区旁浮出「Chat」按钮
2. 点 Chat → 选区作为附件挂进对话窗：`Attached selection · Scene 4 · 2 nodes`（**剧本按 node/段落结构化**）
3. 用户下指令（例：「这个场景写的太单一的，丰富下」）
4. AI：「Let me read the context around this scene…」→ 工具步 `Reading current script focus — done` → 说明理解 → `Replacing script node — done` → **编辑器里的 node 被真实替换**（"Black. One pool of light…" → 拆成 5 节拍的丰富版）
5. AI 回复给出结构化拆解（绝对的黑/光切割进来/光像手术刀/停顿/四秒）
6. 编辑后显示 **`1 edits this turn` + `Undo edit`**（AI 编辑可追踪可撤销）；消息带 copy/👍/👎 反馈
- → AI 把剧本当**结构化 node 集**操作：能 read focus、replace node、edit 可撤销。这是 Script 模块"智能化"的核心机制。
- 快捷：**Polish format**（格式化整理）/ **Summarize outline**（总结成 outline，应与 Outline tab 关联）
- copilot 文案：「Your screenwriting copilot」「handle the tedious formatting work for you」「At the artist's command」

- Outline：文档式 — Logline / Synopsis（Act I/II/III）/ Story Beats / Character Arcs；H1/H2/H3/Quote/Bold/Italic/Rule 工具条
- Cover：封面
- 右侧 Writing 面板：**Info / Collaboration 两 tab**；Collaborators（头像 + `+` 邀请）/ Pagination（Minimal）/ Format（Hollywood ↔ Asian）/ AI Usage（This month·Plan）/ Statistics（Scenes/Words/Characters/Locations/Beats/Shots/Relations 实时计数）

**⭐ 用户明确要加的新需求（超越 laper）**：
1. **版本管理 = 类 git**：Script 内容支持 commit 等操作、版本历史。laper 无此功能，用户要加。（MediaHub 已有 file_versions/resource_versions 可借鉴，但"git 式 commit 剧本内容"是独立特性，需定义 commit 粒度/diff/回滚/分支？）
2. **Outline ↔ Script 联动**：laper **缺失**这个（用户吐槽「Beats 和 Script 没有对应，Outline↔Script 联动性有点缺乏」）。用户要做得比 laper 好——改 Outline 回写 Script、Beats↔Script scene 对应、右下 Summarize outline 双向关联。
3. **多人共同编辑**：拉团队成员进来一起编辑（Collaboration tab + Collaborators）。实时协同。
4. **Script ↔ Storyboard 关联**：scene 流向 storyboard 分列（Auto Storyboard 按 scene 生成）。

**Storyboard 补充观察**（图 26/27）：按 scene 分列（Scene 01–06，I/E·Location·D/N），每列 Open + Auto Storyboard；Storyboard/Shot List 切换。scene 无内容时列头 D/N 为 `-`。

**编辑器结构机制补充（第二批，实测）**：
- **核心价值 = 格式自动固定**：用户明确「格式是固定的，不需要额外花时间调整剧本」。系统自动排版（好莱坞/亚洲），用户只填内容，不手动调格式。
- **Scene 是容器块**：Scene 元素插入编号块 `N. Location DAY/NIGHT / INT/EXT`，三个字段都是**可选下拉**：
  - Location（引用地点实体）
  - 时间下拉：DAY / NIGHT / NOON / DAWN / DUSK / CONTINUOUS / LATER（Tab=切到 INT/EXT）
  - INT/EXT
- **⭐ 拖动场景换序**：scene 块可拖（左侧 `::` 手柄），**连同该场景下的所有子内容（action/dialogue/character/comment/paren…）一起移动**。→ 数据模型：scene 是父节点，其余元素是子节点，排序作用于 scene 块。
- **Format 双制式**：Hollywood ↔ Asian 切换，**同一份结构化内容两种渲染**（Asian：角色名「角色:」标签 + 缩进对白 + 斜体括号 + comment 竖线引用）。→ 内容存结构，格式是渲染层。
- Outline 也是富文本：Body/H1/H2/H3/Quote/Bold/Italic/Rule 工具条。
- Statistics 面板实时计数：Words / Scenes / Characters / Locations / Beats / Shots / Relations。

### Beats 时间轴编排板
- Arrangement / Beats 切换；0'–50' 时间标尺；节拍卡（The Problem/Enter VOID/…）带时长(10m) + Edit beat；可拖拽编排

### Storyboard / Scenes 智能画布
- Storyboard：Storyboard / Shot List 切换；按场景分列（Scene 01–06，带 I/E·Location·D/N）；每列 Open + Auto Storyboard；镜头卡 SHOT 01-01
- Scenes（Scene Board）：Cards / Scene List 切换；场景卡（#1–#6）在无限画布上连线成流；每卡 INT/EXT·location·D/N·chars/lines 计数 + **Generate Still / Edit / Generate Video**

### Characters / Locations / Props 实体模块
- Characters：Overview / Relationships / Casting；卡片（VOICE V.O./CLIENT/CEO/DEV）Portrait/Advanced，色卡渐变，scenes/lines 计数，描述，Generate/Edit（AI 立绘）
- Locations：Overview / Relationships / Scout Sheet；卡片带 scenes/chars 计数、描述、Generate（AI 场景图）
- Props：Overview / List；空态 Prop Sheet 引导；手动登记

## 第三批参考 + 用户对现有实现的批评（2026-07-05）

### laper 新观察
- **段落块悬浮**（图 37）：hover 到段落块显示段落编号（左侧 `16 ::` 手柄样式）。
- **写作热度图**（图 38）：右上角图标弹「Writing」面板 = 类 GitHub 贡献热度图（Activity 按月网格）+ 顶部数字卡（Chats 21 / Token Value $0.09 / Total Tokens 94K / Member Days）+ Tokens Last-30-days 折线。**用户要这个**（写作/AI 用量可视化）。
- **Beats ↔ Script 关联悬浮**（图 39）：Beats 卡（Enter VOID · 10'-20' · 描述 + ✎）悬浮在 script 左侧空白处——但**用户说「目前没看到怎么关联」，让我思考**。→ 设计空间：Beats 应锚定到 script 的 scene/节点上（时间轴节拍 ↔ 剧本场景双向定位）。
- **Ep 多集**（图 45）：Episodes 可 `+` 加 Ep（Ep1/2/3），路由 `laper.ai/app/project/<projectId>/EPISODE_<id>/script`。→ **Episode 是路由层级**，切 Ep 换整套 Script/Beats/Storyboard/Scenes。**关注路由设计**。

### ⭐ 用户对 MediaHub 现有实现的批评（Phase B 要改的）
1. **阶段条做成了 tab（图 42）——错**。「上面应该是流程节点，但做成了 tab，应该类似节点样式的上下游关联」。参考 laper Production 画布（图 40）：Scene → Performance Description → Text Storyboard → Storyboard Frame 是**画布上左→右连线的上下游流程节点**，不是顶部 tab 条。→ 阶段/流程要节点化、有连线、体现上下游。
2. **现有 script 模块很乱（图 43）+ 报错（图 44 已修，见下）**。Script Assets 侧栏（Story Outline/Worldview/Characters/Locations/Props/Plot Points）+ Import Script / Create Story 空态 + AI Chat 抽屉（Execute/Plan First/Dry Run 模式）——用户觉得整体乱，让我看怎么调整。
3. **⭐ 核心设计澄清（节点式 vs 文字式）**：用户原话「我记得之前是节点式的剧本，应该是显示文字样式的剧本，可以节点化，对吧（节点就方便预览和调整顺序）」。
   → **主视图 = laper 那种文字剧本样式**（好莱坞/亚洲格式化文本，图 6/32），**不是** MediaHub 现在的纯节点画布。
   → **但底层节点化保留**，用于「预览 + 调顺序」（拖场景换序、AI 按 node 操作）。
   → 结论：**文字为主视图、节点为结构与操作层**。二者是同一份数据的两种呈现（就像 Hollywood/Asian 是渲染层）。这统一了之前所有观察：scene 容器块、拖动换序、AI replace node、版本 diff 到 node。

### 已修的现有 bug（本轮连带）
- 创建脚本/分镜 500（str team_id 未 coerce bigint）→ #1011 v0.25.102
- 打开脚本编辑器崩溃（缺 TaskManagerProvider）→ #1012 v0.25.105
- Generate Story Outline 500 + expand/branches/storyboard 同病（4 端点 task_tracking.dbos_workflow_id NOT NULL 未串 wf_id，自 23ca9d28 一直坏）→ #1017 v0.25.106
- 教训：这几个都是「无人使用面的连环隐藏 bug」，和 Phase A 救活 project-files 面同一模式。现有 script/storyboard 后端虽在，但前端链路多处腐坏，Phase B 要连地基一起校。

## 已定（用户拍板）
- **PC 端优先**，移动端以后再说
- laper screenwriting = MediaHub project（结构直接映射）
- 首个深挖模块 = Script
- **主视图文字剧本 + 节点化底层**（不是纯节点画布）
- **阶段/流程节点化**（画布上下游连线，不是 tab 条）
- Episode 是路由层级（切 Ep 换整套模块）

## 待收集
- Beats 时间轴编排的交互细节 + 与 Script scene 的对应关系（用户要补的联动）
- Storyboard「Auto Storyboard」生成流程、Shot List 形态
- 智能画布（Scenes）节点交互：连线语义、Generate Still/Video 参数面板、模型选择
- 实体卡（Character/Location）Generate 的参数/结果形态、Relationships/Casting/Scout 视图
- 多人协同的期望形态（实时光标？评论？权限？）
- 版本管理的期望粒度（commit 什么？整剧本 or 单 scene？diff/回滚/分支？）

## 开放问题（收齐后一起定，需用户意图非 laper 截图）
- **版本管理**：commit 粒度（整 script / 单 scene node）、是否要 branch、diff 展示、与现有 file_versions 的关系
- **多人协同**：实时协同技术选型（Yjs/CRDT? Supabase Realtime? 现有 team chat 栈？）、冲突处理、权限（谁能编辑）
- **AI node 操作**：现有 script_ai（outline/expand/branch）能否支撑"read focus + replace node + undo"，还是要扩
- 复用现有 script_*/sb_*/canvas 后端到什么程度 vs 重做
- 8 模块实现顺序与 flag-dark 策略
