# Agent 权限页梳理（三柱重组 + 假开关下架 + 变更审计 + 拦截可见化）设计

2026-08-10 · 起因：用户指出 Settings → AI Library → Agent 编辑器的「权限」tab 信息架构混乱，
要求「整理丰富完善，检查选项是否可靠有用且没有危险」，并对照 YC 开源的 agent harness
**qm**（quartermaster，github.com/yc-software/qm）找可借鉴项。

## 0. 现状体检结论（侦察实据，file:line 见 §8 对照表）

- **无危险项**：所有权限均 fail-closed（缺省即拒绝，parser 只认字面量 `true`）；改权限有
  角色闸门（owner / 团队 owner / 平台管理员，`_can_edit_chat_permissions`，chat 与
  capabilities 共用同一闸门）；保存后回显运行时真实生效值。
- **两个假开关**：`delete`（删除场景与草稿）与 `external_publish`（对外发布）——UI 可勾、
  后端可存、gate 有检查代码，但 `TOOL_REQUIREMENTS` 中**没有任何工具声明消费这两个
  requirement**，授予与否对系统行为零影响。方向是正的（勾了也不放行任何东西）所以不危险，
  但让用户以为在管理一个不存在的能力。
- **信息架构乱**：聊天权限与能力授权语义混排；`cross_episode_read` 是 scope 维度却放在
  能力组；`max_calls_per_turn` 在未授权任何媒体时悬空显示默认值 4。
- **拦截不可见**：gate 拒绝工具调用时只有模型知道，用户看到的是"agent 好像没干活"。
  生产实况的极端形态：全库无任何 agent 有 `write_level` 授权 → 写作类工具整体不可达，
  且无人可从 UI 感知这一点（2026-08-10 undo E2E 实测发现）。
- **权限变更无审计**：`capability_profile` 的 PATCH 不留痕，无从回答"谁在何时给哪个
  agent 开了什么"。
- 代码注释引用的原始设计文档（`2026-08-04-screenwriting-agent-layer-design.md` 等）在仓库
  中不存在（悬空引用）。**本 spec 即成为权限体系的权威设计文档**。

## 1. 拍板决策

| 决策点 | 结论 |
|--------|------|
| 本期范围 | 三柱重组 + 假开关下架 + 权限变更审计 + 拦截可见化（方案 B + A/C 两个 qm 借鉴项）|
| 假开关处置 | **UI 下架、schema 字段保留**（deprecated 注释）；不接消费者、不建审批流——今天不存在删除/发布类工具，为不存在的能力造机器就是假开关换个形式重演 |
| 审批流 | 不做，写入设计纪律：未来任何删除/对外发布类工具必须与审批流（once/session/always + purpose）**同批落地**，禁止常开布尔开关复活 |
| 审计形态 | 新表 append-only，PATCH 同事务落行，理由字段可选（qm purpose 借鉴）|
| 拦截可见化 | 最小版：gate 拒绝 → transcript 事件 → 聊天流 chip + 深链权限页 |
| TTL 限时授权 | 不做，入路线图 P2（与 keychain 同族："授权的时间维度"）|

## 2. S1 · 权限页信息架构（前端）

`PermissionsSection.tsx` 重组为三组（替换现平铺），组序即页序：

### ① Chat & Scope（聊天与作用范围）——"它能进哪、看哪"
- `chat.enabled` 启用聊天
- `chat.auto_broadcast` 主动播报
- `chat.read_team_resources` 读取团队素材
- `capabilities.cross_episode_read` 跨集读取（**从能力组移入**——它是 scope 维度；存储位置不动，仅 UI 归组）

### ② Creative Capabilities（创作能力授权）——"它能改什么、花什么钱"
- `capabilities.write_level` 写入分级四档（none/read/propose/write，语义不变）
- `capabilities.media.image` / `.video` 生成图片 / 生成视频
- `capabilities.media.max_calls_per_turn` 单次生成上限——**仅当 image 或 video 至少开一个时可编辑**，否则置灰并显示"先开启一种生成能力"（解决悬空的默认值 4）
- 组头保留警示句："高危能力，默认全部拒绝……只授予该 Agent 确实需要的"
- **下架**：`delete`、`external_publish` 两个 Toggle 从 UI 移除

### ③ Change Log（变更记录）——"谁改过它的权限"
- 只读列表：最近 N 条权限变更（谁 / 何时 / 改了哪些项 / 理由），数据来自 §3 新端点
- 保存动作旁新增可选输入框 **"Reason for change"**（写入审计行 `reason`，可留空）

### C · 执行边界诚实化（tooltip 文案纪律）
每个开关的提示文案写清**真实执行点与边界**（qm SECURITY.md 的态度），例如：
- 写入分级："Controls which script tools the agent can call. Tools outside the granted
  tier are hidden from the agent AND blocked at dispatch — hiding alone is not the guard."
- 读取团队素材："When off, ResourceFetch is neither offered nor honored."
其余各项类推；§8 的「开关 → 执行点」对照表是文案的事实来源。
- UI 全英文、lucide 图标、语义色 token；i18n en/zh 同步。

## 3. S2 · 权限变更审计（后端，本期唯一新机制）

### 表 `agent_permission_audits`（migration 取号：写 spec 时水位 419，落地前 fetch 复核）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGINT snowflake PK | `generate_snowflake_id()` |
| `agent_id` | UUID NOT NULL | FK → `ai_agents(id) ON DELETE CASCADE` |
| `changed_by` | UUID NOT NULL | 操作者 user_id |
| `before_json` | JSONB NOT NULL | 变更前的 `{chat, capabilities}` 快照（仅这两个子树）|
| `after_json` | JSONB NOT NULL | 变更后同构快照 |
| `reason` | TEXT NULL | 可选变更理由（qm purpose 借鉴）|
| `created_at` | TIMESTAMPTZ NOT NULL | `now()` |

索引 `(agent_id, created_at DESC)`。**append-only**：不提供任何 UPDATE/DELETE 路径。
不启用 RLS（后端内部表，不经 PostgREST；与 `script_shot_ops` 同口径）。

### 写入
`update_agent` 的权限分支（chat_permissions/capabilities 深合并处）在**同一事务**内写一行；
快照用 parser 解析后的生效值（与 `_with_resolved_permissions` 同源），保证审计记录的是
"运行时会放行什么"而不是原始 JSONB。`AgentUpdate` 增加可选 `permission_change_reason: str`。

### 读取
`GET /api/v1/ai-library/agents/{slug}/permission-audits?limit=20`——可见性走
`_can_edit_chat_permissions` 同款闸门（能改才能看）。返回项含 `changed_by` 的展示名
（join profiles，拿不到就显示 uuid 前八位）。

## 4. S3 · 假开关的 schema 处置（不破坏兼容）

- `CapabilitiesIn.delete` / `.external_publish` 字段**保留**（`extra="forbid"` 下删字段会让
  带旧 payload 的客户端 422），加 deprecated 注释指向本 spec。
- `high_risk_caps.py` parser、gate 中对应检查代码保留原样，注释标注"等待消费者，见
  2026-08-10 spec §1 拍板"。
- 存量库中已存的 `delete:true` 等值无害（无消费者，fail-closed），不做数据清理。

## 5. A · 拦截可见化（gate 拒绝 → 用户可见）

- `HighRiskCapabilityGateHook` 拒绝一个工具调用时，除现有的"给模型的拒绝结果"外，
  **落一条 transcript 事件**（`agent_run_transcript_events`，新 `event_type='capability_denied'`，
  payload：tool 名、缺的维度与当前档位、需要的档位）。
- 前端聊天流（`toolActivity` 管线）把该事件渲染成一条警示 chip：
  "Agent tried CreateShot — blocked: write level is None. → Open permissions"，
  深链到该 agent 的权限 tab（danger/warn 语义色，禁 emoji）。
- issue timeline（非交互 surface）渲染纯文本版，无深链按钮（no affordance beats a dead one）。
- 同一 run 内同一 (tool, dimension) 的重复拒绝只落第一条（防刷屏）。
- 这是"触发路径必须类型化回显"纪律在权限域的落地。

## 6. 设计纪律（本 spec 立约）

1. **Portal-only 三原则**（qm 借鉴）：授权变更、审批决策、身份冒充永远不给 agent 自己的
   API——升级未来权限的决策必须来自 agent 之外。
2. **删除/对外发布类工具与审批流同批落地**：once/session/always 三档 + purpose 逐字记录
   批准原话；禁止复活常开布尔开关。
3. **每个权限开关必须有消费者**：新增任何权限字段的 PR 必须同批接上执行点，否则不许进 UI
   （假开关的根因免疫）。
4. **执行边界必须写进用户可见文案**：只做 prompt 层过滤不算拦截；tooltip 必须如实描述。

## 7. 路线图（各自独立立项，不在本期）

| 优先级 | 项 | 内容 | qm 出处 |
|--------|----|------|---------|
| P1（最急，与权限页无关）| Provider 容错 | fallback_models 暴露到 agent 配置 + 预设 agent 默认 fallback（pro→lite）+ provider 错误类型化透出（429 不再变裸 500）| 「不自带内核」|
| P2 | 授权的时间维度 | 写入分级支持"仅本次会话 / 24h"临时授权（`expires_at`，parser 过期回落 none）；分发线社媒账号的 keychain 式授权（按账号、写用途、限时、Ask 流程）——`external_publish` 的最终归宿 | capability token TTL / keychain |
| P3 | 团队权限地板 | 团队管理员定上限（如"本团队 agent 最多 propose"），成员只能收紧不能放松 | `composeSecurityPosture` 单调组合 |
| P4 | Agent 例行任务 | "每天巡一遍本集剧本"类用户可配置 cron（DBOS scheduled + routine 归因骨架已在）| per-scope cron |

## 8. 开关 → 执行点对照表（tooltip 文案的事实来源）

| 权限键 | 执行点 | 备注 |
|--------|--------|------|
| `chat.enabled` + `allowed_team_ids` | `conversation_agent_turn.py:335`、`conversation_service.py:189,425`（三处召唤路径前置闸门）| `allowed_team_ids` 当前无 UI 编辑器，本期不加（语义留待需要时核实）|
| `chat.read_team_resources` | `conversation_agent_turn.py:261-267`（handler 内二次判）+ `:413`（工具注册门控）| 双层 |
| `chat.auto_broadcast` | `agent_broadcast.py:106` | 叠加 enabled + allows_team |
| `capabilities.write_level` | `high_risk_capability_gate.py:133-140`（强制）+ prompt 通告过滤（展示层）| ListScenes/ReadScene=read；ProposeEdit=propose；CreateShot/UpdateShot/ApplyEdit=write |
| `capabilities.media.*` | gate `:145-166` + `media_kill_switch`（环境变量只关不开）+ 通告过滤 | max_calls_per_turn 默认 4，上限 100 |
| `capabilities.cross_episode_read` | `scope_binding.py:142-154`（独立执行点，不经 gate 表）| 只能扩到 project 级，不突破 project 边界 |
| `capabilities.delete` | **无消费者**（gate `:142-143` 代码存在但永不触发）| 本期 UI 下架 |
| `capabilities.external_publish` | **无消费者**（gate `:171-172` 同上）| 本期 UI 下架 |

## 9. 测试口径

- 前端组件测：三组渲染与归组、上限置灰逻辑、下架项不再渲染、变更记录列表、拦截 chip
  三态（交互/非交互/去授权深链）、i18n key 齐平。
- 后端：审计行同事务性（PATCH 失败不留审计）、快照为生效值、append-only（无更新端点）、
  审计读取的闸门；`capability_denied` 事件落一次不重复；既有 gate/parser 测试不回归。
- 迁移：`agent_permission_audits` 幂等（IF NOT EXISTS）。

## 10. 范围外（YAGNI）

- 审批流本体、TTL 授权、团队地板、provider 容错、例行任务（见 §7 路线图）
- `allowed_team_ids` 编辑器、低风险调优键（tool_blacklist 等）的 UI 暴露
- `agent_runs` 随 project CASCADE 的审计留存问题（记录在案，属审计域后续）
- 分发线 keychain（P2 一并设计）
