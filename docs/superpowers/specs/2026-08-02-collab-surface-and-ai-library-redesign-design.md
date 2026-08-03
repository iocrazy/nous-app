# 协作面 + AI Library 管理面 重设计（设计定稿）

> 视觉稿：[`2026-08-02-collab-redesign-mockup.html`](./2026-08-02-collab-redesign-mockup.html)（同目录，浏览器直接打开；
> 线上版 https://claude.ai/code/artifact/d01adb45-3704-4dd6-abce-ba570ae52f33）。
> 本文是文字定稿，视觉细节以 mockup 为准；两者冲突时以本文为准。

## 定位与优先级

用户定调：**核心是协作面**——agent 过程进度可见、人机交接醒目、issue 作为枢纽打通各模块；
AI Library 管理界面（卡片化）是辅助面，可并行、可后置。

设计总原则（经三轮收敛后的铁律）：**最小 diff**。现有 Issues 页 / Todolist 组件 /
AgentEditor 的骨架与交互一律保留，改动以"贴层 / 加 chip / 重组现有内容"表达，
禁止重排既有页面。视觉贴 K1 warm-paper 系统（绿主色 / 李紫 agent / 赭警示 / 砖红危险 / 钢蓝信息）。

## A 协作面（核心）

### A1 Issues 页 —— 3 处微改，零重排

现有 scope 切换 / Quick / 状态管道条 / Group / 列表·看板 / 列配置 / Pipelines 入口全部不动。

| # | 改动 | 位置 | 行为 |
|---|------|------|------|
| 1 | 「等我的」横条（唯一新组件 `AttentionStrip`） | Quick 行与列表之间 | **仅当存在 agent 提问 / 审批请求 / 待验收时渲染**，空时零渲染（页面与现状像素级一致）。横向卡片可滚动、整条可折叠（折叠态记 localStorage）。卡片三类：提问（赭）→ 直达 issue 回复框；审批（钢蓝）→ 批准/拒绝内联；待验收（绿）→ 直达 issue 审阅 |
| 2 | `running` chip 加后缀 | issue 行尾（chip 已存在） | `running` → `running · 第N轮 Xm`。数据：task_tracking Realtime（前端已订阅该表，补字段透传即可） |
| 3 | 新增 `等你回复` chip | needs_followup 行尾，与 running chip 同款同位 | 赭色；hover tooltip 显示 agent 提问原文（issues.outcome_reason） |

数据：横条聚合 = `issues.status IN ('needs_followup','in_review')`（scope 内）+ 审批请求表现有查询。
优先复用现有 issues list 数据流，不新建 endpoint；如需一次取回提问原文，扩展 issues list 投影即可。

### A2 任务详情 —— 人机协作时间线（IssueDetailView 内部增强）

对列表页零影响。借 paperclip IssueRunLedger 模式，把 IssueChatThread 升级为统一时间线：

- **运行边界卡**：agent 的一次 dispatch 多轮运行折叠为一张卡（`▶ Script AI 运行 #1 · N 轮 · X 分钟 · tok · ¥`），
  "展开对话流"复用已上线的会话聚合视图（ConversationThreadPane）。轮次明细不再平铺淹没对话。
- **提问卡（needs_input）**：agent 提问以赭色卡内联在时间线，含"在此回复"，回复后 agent 原地继续
  （依赖 2️⃣ needs_input 实施，见依赖节）。
- **交付物卡**：DeliverablesZone 现有能力，入时间线呈现（绿色卡 + 资源库落点链接）。
- **右栏进度轨道**：状态 / 运行次数·轮次 / 耗时·花费（IssueCostLine 复用）/ 执行者；
  关联卡：项目 / 交付物 / 子任务 / 流水线下一站（PipelineRunStrip 数据复用）。

### A3 模块打通 —— 现状与缺口

| 边 | 机制 | 状态 |
|----|------|------|
| issue → agent | 派发 / 回复续跑 / 流水线接力 | ✓ 已通，A2 只做可视化 |
| agent → 人 | needs_input 挂起 + 等你回复 + 聚合 | ⚠ 缺口 = 2️⃣（spec 已定稿：`2026-07-30-needs-input-first-class-design.md`） |
| issue → 会话 | issue chat 即 conversation | ✓ 已通，运行卡展开直接复用 |
| issue → 资源库 | 交付物落库 | ✓ 已通；"资源反查来自哪个任务"为将来项 |
| issue → 项目 | 归属关系 | ✓ 已通 |
| 运行进度 → UI | task_tracking Realtime | ⚠ 半通：数据在流，"第 N 轮 · 已 X 分钟"级透传未做（A1-2 / A2 右栏共用） |
| 失败 → 人 | 类型化失败回显 | ⚠ 4️⃣ 静默失败契约（CLAUDE.md 已立约，另一 session 推进中） |

## B 管理面（辅助，可并行）

细节以 mockup B 系列为准，要点：

- **B0 侧栏瘦身**：AILibrarySidebar 的 19 行 agent 平铺 + 技能项收敛为一个「AI Library」入口
  （页内 tabs：智能体 | 技能 | 市场·留位）；运行时/聊天/洞察分区不动。
- **B1 智能体总览**：分组卡片（编剧组/美术组/工具组，`agents.group` 新字段 + seed 默认值）+
  筛选行 + 状态徽章（文字徽章：运行中·李紫 / 等回复·赭 / 故障·砖红；空闲=灰点）+
  主按钮随状态（空闲→Chat / 运行中→查看运行 / 等回复→去回复 / 故障→修复指引）+
  故障必须携带一行可操作原因。系统 preset 语义改为「官方模板」：主操作 Chat / Fork 定制。
- **B2 详情三区归并**：8 tab → 工作台（最近对话+等你回复+例行任务+周统计）/
  人格与技能（IDENTITY/SOUL/AGENT 内联编辑 + 属性卡 + 技能绑定开关）/
  档案（版本+成本）。AgentEditor(1487 行) 顺势拆为三个子组件。
- **B3 技能库**：卡片化 + 「被哪些 agent 使用」关系可见 + 孤儿技能赭色提醒。
- **B4 技能编辑**：文件即页签（替代三栏文件树），Fork 横幅保留。
- **B5 新建/Fork modal**：模板优先（选模板 → 命名 → 权限域），空白创建为次入口；Fork 复用同 modal。
- **B6 例行任务新建 modal**：工作台「+ 新建任务」→ 自然语言任务描述 + 频率档
  （每小时/每天/工作日/每周/自定义 + 时间，最小档每小时）+ 结果去向 + 审批策略。复用现有例行调度链路。

## 数据与接口改动清单

| 改动 | 类型 | 服务于 |
|------|------|--------|
| `agents.group TEXT` + seed 默认分组 | migration + seed | B1 |
| agents list 端点补周统计（runs/tok）与健康态聚合 | 端点扩展 | B1 |
| skills 反查「被哪些 agent 使用」 | 新查询（agent_skills 反连接） | B3/B4 |
| issues list 投影补 outcome_reason / 运行轮次·起始时间 | 投影扩展 | A1 |
| task_tracking 轮次/耗时字段前端透传 | 前端订阅字段 | A1-2 / A2 |
| agent Fork 端点（若缺） | 端点 | B5 |
| routine 创建入口参数（描述/频率/去向/审批） | 复用现有，补前端表单 | B6 |

无破坏性 schema 变更；除 `agents.group` 外零 migration。

## 依赖与实施顺序（每批独立 PR 可上线）

1. **第一批 · 协作面地基**：2️⃣ needs_input 实施（spec 已定稿）。4️⃣ 由并行 session 推进，不重复排期。
2. **第二批 · A2 任务协作时间线**：运行边界卡 + 提问卡 + 交付物卡 + 右栏进度轨道。
3. **第三批 · A1 Issues 页 3 处微改**：AttentionStrip + 两个 chip。
4. **第四批 · B 管理面**（B0→B1→B2→B3/B4→B5/B6）：不依赖前三批，可由另一 session 并行承接。

第 2/3 批中"逐轮进度"依赖 task_tracking 透传，属小改，随批实施。

## 明确不做（YAGNI）

- 市场 / 跨用户 agent 共享：只留 UI 位与「模板」语义铺垫，不实现。
- 真人感 AI 头像：用图标+语义色块（AgentIconPicker 现有）。
- 例行任务高频档（每 10 分钟级）：写作场景无意义。
- Issues 第三视图模式：注意力用贴层解决，不加新视图。
- 「员工打卡」类隐喻：工作团队/例行任务已有承载。

## 测试策略

- AttentionStrip：空态零渲染快照 / 三类卡渲染与跳转 / 折叠态持久化。
- chips：running 后缀数据映射、needs_followup tooltip 内容来源。
- A2 时间线：运行卡折叠聚合正确性（复用会话聚合的既有测试模式）、提问卡→回复→时间线追加。
- B 系列：分组归属、状态徽章映射（含故障原因必现）、Fork 流程、技能反查关系。
- E2E：Claude 调试账号跑通「派单 → 运行卡出现 → （2️⃣ 落地后）提问 → 横条出现 → 回复 → 交付物入库」。
