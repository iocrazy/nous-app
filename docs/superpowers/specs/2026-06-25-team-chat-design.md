# Team Chat (类飞书团队沟通 + AI Agent 入驻) — 设计 Spec

> **状态图例**：☐ 未实现 · ◑ 进行中 · ☑ 已实现 · ⊘ 本版不做
>
> **编号规则**：`CHAT-<类>-NN`，编号一旦分配**不复用、不重排**，便于跨 session 追溯实施情况。
>
> **本文档定位**：分类化设计 + 需求追溯矩阵。实施计划（bite-sized TDD 任务）由本 spec 定稿后另出 `docs/superpowers/plans/`。

**Date:** 2026-06-25
**Branch:** `feature/team-chat`（worktree：前端 5176 / 后端 8081 / Redis DB 1）
**调研来源**：Rocket.Chat（`github-repos/Rocket.Chat`）、Mattermost（`github-repos/mattermost`）真实源码 + Zulip/Matrix/Slack/OWASP 公开文档。**借鉴设计思路，不抄代码。**

---

## 0. 目标与核心判断

| ID | 内容 | 状态 |
|----|------|------|
| CHAT-GOAL-01 | 在 MediaHub 内提供团队内嵌的即时沟通（频道 + DM），与现有 team/project/素材深度融合 | ☐ |
| CHAT-GOAL-02 | 让现有 AI agent（`ai_agents`/`AgentRunner`）作为"频道参与者"进驻：@召唤回话、主动播报、能引用素材/任务卡片、专属 DM | ☐ |
| CHAT-GOAL-03 | **不**引入独立 IM 系统（Rocket.Chat/Mattermost 整套）——避免第二套基础设施/双账号/双 DB；在现有 Supabase Realtime + teams/auth 上自建 | ☐ |
| CHAT-GOAL-04 | 安全/权限模型借鉴成熟开源已验证的设计，杜绝越权（尤其 agent 跨 team 泄露素材） | ☐ |

### 0.1 概念心智：采用"群(group)"模型，非"频道(channel)"模型

| ID | 决策 | 依据 | 状态 |
|----|------|------|------|
| CHAT-GOAL-05 | 采用**飞书式"群"模型**：核心单元是「单聊 + 成员制群聊」，而非 Slack 式「公开可发现话题频道」。群默认成员制（拉人进群、非成员不可见其存在） | 飞书/钉钉/企微 = 群派；Slack/MM = 频道派。用户明确要"类飞书" | ☐ |
| CHAT-GOAL-06 | 类型收敛为 `{dm, group, public}`：`group`=成员制群（飞书群，含原 private 语义，主力）；`dm`=单聊；`public`=可选的 team 内可发现频道（如全员 #general），非主力 | 飞书无"公开频道"主概念，故 public 降为可选 | ☐ |

> 术语：本文档"频道"与"群"互指同一实体（`channels` 表），保留 channel 命名只因表/代码用英文。心智上以"群"为准。

---

## 1. 架构（ARCH）

| ID | 设计 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-ARCH-01 | **分两层**：第一层"人的群聊"（新建 `channels`/`channel_members`/`channel_messages`）；第二层"agent 作为频道参与者"（@agent → 复用 `AgentRunner` → 回写一条 message） | 与现有 `agent_runs vs task_tracking 决定不合并` 先例一致 | ☐ |
| CHAT-ARCH-02 | **不**扩展现有单用户 `ai_sessions` 成群聊。agent 在频道里每次回话仍起一个 `ai_session`/`agent_run` 做记录，与人群聊表解耦 | `ai_sessions.user_id` 是单 owner（`backend/app/models/ai.py:267`） | ☐ |
| CHAT-ARCH-03 | 复用现有 agent 运行时（实测 85% 可复用）：`AgentRunner.stream_turn`（已支持 SSE）、`RunRecorder`、Hook 链（Pre/PostToolUse）、`resource_fetch` 工具 | `backend/app/services/ai/runner/agent_runner.py:209` | ☐ |
| CHAT-ARCH-04 | 后端分层遵循 Router→Service→Repository；新增 `chat_router.py` / `chat_service.py` / `chat_repository.py` | CLAUDE.md 分层规范 | ☑ |
| CHAT-ARCH-05 | 所有新表主键用 `generate_snowflake_id()`（BIGINT），与全库一致 | `migrations/050_snowflake_id_infrastructure.sql:21` | ☑ |

---

## 2. 数据模型（DATA）

| ID | 表/字段 | 设计要点 | 来源 | 状态 |
|----|---------|---------|------|------|
| CHAT-DATA-01 | `channels(id snowflake PK, team_id, type, history_mode, last_message_seq int default 0, name, topic, created_by, is_archived, created_at)` | 频道主表 | Mattermost `channel.go:27` | ☑ |
| CHAT-DATA-02 | `channels.type ∈ {dm, group, public}`（采群模型，见 CHAT-GOAL-06）：`group`=成员制群（主力，含原 private 语义）；`dm`=单聊；`public`=可选 team 可发现频道 | 飞书群模型 | MM `O/P/D/G`、RC `c/p/d/l` | ☑ |
| CHAT-DATA-03 | `channels.history_mode ∈ {shared, joined}`，**与 type 正交**、建频道时定死、事后不可改。**默认 `shared`**（决策 CHAT-OPEN-01，先服务小团队） | "加入规则"与"历史可见性"是两个独立维度 | Matrix `join_rules`×`history_visibility` | ☑ |
| CHAT-DATA-04 | `channel_members(channel_id, user_id, last_read_seq int default 0, mention_count int default 0, roles text[], open bool, notify_level, joined_at, PK(channel_id,user_id))` | **Subscription 模式**：一行 = 成员关系 + 未读 + 房内角色 | Rocket.Chat `ISubscription.ts:11-83` | ☑ |
| CHAT-DATA-05 | 索引：`channel_members(user_id, open)`（列我的侧栏）；`channel_messages(channel_id, seq)` UNIQUE（排序/分页/补洞） | 高效 keyset | RC `Subscriptions.ts:44`、MM `post_store.go:670` | ☑ |
| CHAT-DATA-06 | `channel_messages(id snowflake PK, channel_id, seq int, sender_id, sender_type {user,agent}, content_type, body jsonb, reply_to_id, from_bot_agent_id, edited_at, deleted_at, created_at)` | 消息表 | MM Post `post.go:127` | ☑ |
| CHAT-DATA-07 | **每会话自增 `seq`** 为权威顺序（非全局）：`UPDATE channels SET last_message_seq=last_message_seq+1 RETURNING` 与 insert 同事务（行锁，不依赖 Redis） | seq 同时驱动排序/未读/补洞 | Tinode/OpenIM per-channel seq | ☑ |
| CHAT-DATA-08 | `agent_channels(agent_id, channel_id, added_by, created_at, PK(agent_id,channel_id))`：agent 入驻频道白名单 | "成员资格 = 数据边界" | MM outgoing webhook `ChannelId` 约束 | ☑ |
| CHAT-DATA-09 | `content_type ∈ {text, media_card, task_card, system}`；`media_card`/`task_card` 的 `body` 用统一卡片 schema（见 CHAT-MSG-06） | 富消息 | MM `message_attachment.go:20` | ☑ |
| CHAT-DATA-10 | DM 用 `type='dm'` + 固定 2 成员复用同一套表/policy，**不单开 DM 表** | 复用 | RC subscription 统一模型 | ☑ |

---

## 3. 权限与安全（SEC）— 重点

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-SEC-01 | 三表全开 RLS。辅助函数 `is_channel_member(uid,cid) → bool`（`SECURITY DEFINER STABLE`，内部直读 `channel_members` 绕过其 RLS）避免 policy 递归 | 现有 `get_user_team_ids` 范式（`migrations/010`） | ☑ |
| CHAT-SEC-02 | `channels` SELECT policy：`type='public'` 同 team 成员可见；`group/dm` 仅 `is_channel_member()` 为真者可见。**成员制群连"存在/名字"都不暴露给非成员**（飞书群默认行为） | 防 Rocket.Chat #3196 式 private 群泄露 | ☑ |
| CHAT-SEC-03 | `channel_members` SELECT policy：只允许同频道成员看到成员行，禁止任意 `SELECT *` 全表 | RC canAccessRoom 三层防御 | ☑ |
| CHAT-SEC-04 | `channel_messages` SELECT policy（最关键）：`is_channel_member(auth.uid(),channel_id) AND (history_mode='shared' OR created_at >= 成员 joined_at)` | history_mode 落地；joined 模式只看加入后 | ☑ |
| CHAT-SEC-05 | RLS 子查询用 `(SELECT auth.uid())` 包裹，让 Postgres 作 initplan 求值一次而非每行 | Supabase 官方性能建议 | ☑ |
| CHAT-SEC-06 | `joined_at` 一旦写入不随意 UPDATE（会改变 joined 模式下可见历史窗口） | Matrix #13968 历史撤销难教训 | ☑ |
| CHAT-SEC-07 | service_role 仅用于写系统表（task_tracking 镜像、agent 元数据），**绝不用 service_role 读用户素材** | — | ☑ |
| CHAT-SEC-08 | 历史授权一旦给出难撤销：踢人后客户端缓存需主动失效（RLS 只管新查询） | Matrix #13968 | ☑ |

### 3.1 AI Agent 授权（SEC-AGENT）— 最容易出洞，单列

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-SEC-AGENT-01 | **铁律：`agent 可见素材 = RLS(发起人身份) ∩ 当前频道 scope`**。agent 自身身份只用于审计/限流/吊销，**永不作为读素材的授权来源** | Slack「user token 才代人」+ OWASP 最小授权 | ☑ |
| CHAT-SEC-AGENT-02 | @agent 时，`resource_fetch` 以**发起 @ 的用户 user_id** 查询（现状已如此） | `resource_fetch_tool.py:38-72`（已用 user_id + team_members 校验） | ☑ |
| CHAT-SEC-AGENT-03 | **补现有洞**：`resource_fetch` 在频道上下文里要 **AND 上当前频道所属 team 的 scope**，否则用户能让 agent 把他在别的 team/个人私有的素材搬进当前频道（跨 team 泄露） | 现状只校验"该 user 在任何 team 可见"，未限当前频道 | ☑ |
| CHAT-SEC-AGENT-04 | DM/private 频道内 @agent：允许取**发起人个人 scope**（`scope_type='user'`）素材（等于自己调）；team/group 频道内只能取该频道 team scope 素材 | 频道是"输出边界"，发起人是"输入授权" | ☑ #899（agent DM 走 ai_sessions 个人 scope，无 team 输出边界，铁律天然成立）|
| CHAT-SEC-AGENT-05 | **主动播报（无发起人在场）**：只能播报频道 scope 内、明确可共享的内容（team 公共素材/任务状态），**绝不触碰任何 `scope_type='user'` 私有行** | 无人可代时收窄到 broadcast 白名单 | ☑ #902（广播只含聚合计数+task_kind 枚举,无 LLM/无 resource_fetch,结构性零 user-scope) |
| CHAT-SEC-AGENT-06 | agent 只能 post 到 `agent_channels` 白名单内的频道（显式入驻，可审计动作），**无"全局读/发所有频道"后门** | MM webhook 必须指定 ChannelId | ☑ |
| CHAT-SEC-AGENT-07 | 防 prompt injection：频道里他人消息一律当**数据**不当指令；忽略"把所有人素材发出来"类越权指令；输出前自检是否含跨 scope 数据 | OWASP AI Agent Cheat Sheet | ☑ |
| CHAT-SEC-AGENT-08 | agent 读 resource 全程落 `application_logs`（谁的身份/查了哪些 id/在哪个频道），供事后越权审计 | 现有日志体系 | ☑ |
| CHAT-SEC-AGENT-09 | 用 `scope_type`+`scope_id` 判 team 归属（已知坑：`libraries`/scope 无 `team_id` 列） | CLAUDE.md 已知陷阱 | ☑ |

---

## 3.2 Agent 能力/权限治理（PERM）— 在 AI Library 配置

> **需求**：不是所有 agent 都能进群、都能读本 team 文件。Agent 的所有管理都在 **AI Library**（Settings → AI Library → Agents），在那里集中配置聊天相关权限。
>
> **设计基线（复用现有体系，不新建表）**：扩展现有 `ai_agents.capability_profile` JSONB（`backend/app/models/ai.py:126`，Phase 4.5 能力闸，现有 5 个键）——新增一个 `chat` 子对象。**默认全 false = 默认不能进群/不能读 team 文件**（默认拒绝，符合需求）。

### 数据模型

| ID | 内容 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-PERM-01 | 在 `ai_agents.capability_profile` JSONB 加 `chat` 子对象：`{enabled, read_team_resources, auto_broadcast, allowed_team_ids}` | 复用现有 `capability_profile`（`models/ai.py:126`） | ☑ |
| CHAT-PERM-02 | `chat.enabled` (bool)：agent 能否被加进群/被 @召唤。**缺省=false** | 默认拒绝 | ☑ |
| CHAT-PERM-03 | `chat.read_team_resources` (bool)：agent 被召唤时能否读取**当前群所属 team** 的素材。缺省=false | 与 CHAT-SEC-AGENT-03 叠加 | ☑ |
| CHAT-PERM-04 | `chat.auto_broadcast` (bool)：agent 能否主动播报（任务完成等）。缺省=false | 与 CHAT-SEC-AGENT-05 叠加 | ☑ |
| CHAT-PERM-05 | `chat.allowed_team_ids` (bigint[])：**空=可进任何把它加进去的 team**；非空=仅限白名单 team。这就是"不是所有 agent 都能进所有 team 的群"。**注：schema/解析已就绪，team 多选 UI 延后到编辑器能加载 team 列表后**（空白名单=任何 team 是安全默认） | 按 team 收窄 | ◑ (schema/解析/Out 已交付；team 白名单 UI 延后) |
| CHAT-PERM-06 | Migration 给 `capability_profile->chat` 写注释；建 partial index `WHERE (capability_profile->'chat'->>'enabled')='true'`（"可加入群"的 agent 选择器要按此过滤） | jsonb 查询性能 | ☑ |

### 后端能力解析与执行点（4 个闸）

| ID | 规则 | 执行点 | 状态 |
|----|------|--------|------|
| CHAT-PERM-07 | 后端 `agent_chat_caps(agent_dict) -> ChatCaps`：安全解析 `capability_profile.chat`，缺字段一律按 false/空兜底（fail-closed） | 新 helper `services/ai/.../agent_chat_caps.py` | ☑ |
| CHAT-PERM-08 | **进群闸**：把 agent 加进群（`agent_channels` insert）前校验 `caps.enabled` 且（`allowed_team_ids` 空 或 群 team∈白名单），否则 403 | chat_service add-agent endpoint | ☑ |
| CHAT-PERM-09 | **召唤闸**：@agent 解析时若 agent 未入驻该群或 `caps.enabled=false`，**不触发 AgentRunner**，回一条 system 提示"This agent isn't enabled for chat" | @mention resolver（CHAT-AGENT-02） | ☑ |
| CHAT-PERM-10 | **读 team 文件闸**：聊天上下文的 `resource_fetch` 包装层，若 `caps.read_team_resources=false` 直接拒绝（先于 CHAT-SEC-AGENT-03 的 scope 过滤） | resource_fetch 包装（CHAT-SEC-AGENT-03） | ☑ |
| CHAT-PERM-11 | **播报闸**：broadcast 服务发消息前校验 `caps.auto_broadcast`，否则跳过该 agent | broadcast 服务（CHAT-AGENT-05） | ☑ #902（scan_and_broadcast 发消息前校验 caps.auto_broadcast+enabled+allows_team,fail-closed) |
| CHAT-PERM-12 | 所有权限**拒绝**落 `application_logs`（哪个 agent / 哪条规则 / 哪个 team），供审计 | 现有日志 | ☑ |
| CHAT-PERM-19 | **权限编辑要角色校验**（review H1）：PATCH `chat_permissions` 限 **agent 所属 scope 的 team owner/admin 或 agent owner**；system preset 的权限改动限**平台 admin**。现有 `update_agent` 端点**无任何角色校验**（任意登录用户可 PATCH），是提权点必须堵 | `ai_library_router.py` PATCH | ☑ |
| CHAT-PERM-20 | **响应模型暴露**（review C1）：`AgentOut` 现**不含** capability_profile，list/get/patch 全走 AgentOut → 前端收不到权限。需加**派生窄字段** `chat_permissions`（只暴露 chat 子对象，**不吐整个 capability_profile** 以免泄露 tool_blacklist 等内部 gating），router 用 `agent_chat_caps(row)` 填充 | `schemas/ai_library.py` AgentOut + router enrich | ☑ |
| CHAT-PERM-21 | **授予/变更也要审计**（review M3）：PATCH chat_permissions 写 `application_logs`（actor / agent / 前后值），不只记拒绝 | 现有日志 | ☑ |

### AI Library UI

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-PERM-13 | Agent 编辑器加第 N 个 sub-tab **"Permissions"**（与 Overview/Files/Skills 并列） | `AgentEditor.tsx:41` SubTab 联合类型 + nav | ☑ |
| CHAT-PERM-14 | Permissions 页：4 个开关（Enable chat / Read team files / Auto-broadcast / team 白名单多选）+ 文案说明默认拒绝语义 | 沿用 Overview 的 draft+PATCH 保存模式（`AgentEditor.tsx:244`） | ☑ |
| CHAT-PERM-15 | **系统预设 agent 特例**：`ai_library_router.py:481` 现对 `is_system_preset` 一律 403。需放开**仅 `capability_profile`（含 chat 权限）字段**可 PATCH（权限是治理，不是内容编辑），其余字段仍锁 | `ai_library_router.py:481` PATCH 守卫 | ☑ |
| CHAT-PERM-16 | `AgentUpdate` schema（`schemas/ai_library.py:58`）加 `capability_profile`（或细化 `chat_permissions`）可 patch 字段 | 后端 schema | ☑ |
| CHAT-PERM-17 | Agents 列表项 + 群"添加 agent"选择器：只列 `chat.enabled=true` 的 agent；列表给个"Chat"小标记 | `AILibrarySidebar.tsx` + 群 add-agent picker | ☑ |
| CHAT-PERM-18 | 前端 `AILibraryAgent` 类型（`types.ts:1018`）+ `aiLibraryService.updateAgent`（`aiLibraryService.ts:82`）带上 chat 权限字段 | 前端类型/服务 | ☑ |

---

## 4. 未读与已读（UNREAD）

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-UNREAD-01 | **读扩散**：每成员只存 `last_read_seq`，未读数 = `channels.last_message_seq − channel_members.last_read_seq`（读时纯整数运算） | 避开 Rocket.Chat 写扩散规模炸弹 | ☑ |
| CHAT-UNREAD-02 | **禁止**每条消息给全频道成员写扩散 +1（1000 人 = 1000 次写/条） | RC `Subscriptions.ts:537` 反例 | ☑ |
| CHAT-UNREAD-03 | **@提及例外**：仅给被 @ 的少数成员写扩散 `mention_count += 1`（小集合，便宜） | RC `incUserMentionsAndUnread…:1561` | ☑ #895（post_message fan-out 成员过滤+排除自己；sidebar amber badge；channel_members realtime） |
| CHAT-UNREAD-04 | 标记已读 = 客户端 debounce 后 PATCH 自己的 `last_read_seq`（别每条消息都写） | MM typing/已读节流思路 | ☑ |
| CHAT-UNREAD-05 | 侧栏总未读/红点用一个 view 聚合，O(成员数) 而非 O(消息数) | — | ☑ |

---

## 5. 实时投递（RT）

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-RT-01 | `channel_messages` 加入 `supabase_realtime` publication + `REPLICA IDENTITY FULL`，前端订阅 `postgres_changes` | 现有 `task_tracking` 范式（`migrations/180,210`） | ☑ |
| CHAT-RT-02 | **依赖 Realtime 受 RLS 约束**（已验证）：无权订阅者收不到 private 频道事件，不自建应用层 fan-out | `migrations/064` task_tracking RLS + Realtime 链路 | ☑ |
| CHAT-RT-03 | **typing / 在线状态走 `realtime.broadcast()` 内存事件，不落库**（高频，落库撑爆 replication） | MM typing `user.go:2823` 不落库 | ☑ #897（useChannelPresence: presence 在线数 + typing broadcast 4s 自过期，独立 channel，不落库） |
| CHAT-RT-04 | 前端订阅自己的 `channel_members` 行获取未读变化；重连后按 seq range 拉缺口补洞 | seq 补洞 | ☑ #895+#896（mention badge 走 channel_members 订阅；reconnect/online/focus 触发 gapFill 前向补拉 BigInt seq） |
| CHAT-RT-05 | 前端订阅模式复用 `TaskManagerContext` 的 channel/subscribe 写法 | `frontend/contexts/TaskManagerContext.tsx:595` | ☑ |

---

## 6. 消息能力（MSG）

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-MSG-01 | 排序按 `seq DESC`（权威顺序）；keyset 分页 `seq < cursor`，**禁 OFFSET** | MM keyset `post_store.go:1609` | ☑ |
| CHAT-MSG-02 | 同毫秒不乱序：seq 本身单调；若回退到 created_at 排序须带 `id` tie-breaker | MM `OrderBy CreateAt DESC, Id DESC` | ☑ |
| CHAT-MSG-03 | 编辑：软更新，置 `edited_at`，Realtime 推 UPDATE 通知 | MM EditAt / RC editedAt | ☑ #893（PATCH endpoint owner-gated + edited_at；Realtime UPDATE 订阅；inline edit + (edited) 标记） |
| CHAT-MSG-04 | 删除：软删，置 `deleted_at`，前端渲染"已删除"占位 | MM DeleteAt 软删 | ☑ #893（DELETE endpoint 软删 owner-gated；tombstone 占位；Realtime 实时） |
| CHAT-MSG-05 | 线程回复：`reply_to_id` 指向被回复消息（第一版只做"引用单条"，不做完整 thread 树） | MM `RootId` | ◑ (reply_to_id 列就绪，同频道校验+UI 延后) |
| CHAT-MSG-06 | **统一卡片 schema**（`body jsonb`）：`{title, title_link, text, color, image_url, thumb_url, fields:[{title,value,short}], actions:[], footer, ts}` | MM `message_attachment.go:20` / RC attachments | ☑ #890（MessageBubble 渲染 title/image_url/fields；actions 留待 MSG-07） |
| CHAT-MSG-07 | 交互按钮回调上下文（cookie/context）**只存服务端，客户端剥离**；点击走独立 endpoint | MM `integration_action.go:121` 安全做法 | ⊘ 本版延后（决策 OPEN-03） |
| CHAT-MSG-08 | 素材卡片 = 把一个 resource 渲染成 `media_card`（缩略图 + 文件名 + 大小 + 打开链接） | CHAT-GOAL-01 融合点 | ☑ #890（ResourcePicker → media_card 缩略图/类型/大小；"打开链接" 随 MSG-07 延后） |

---

## 7. AI Agent 入驻（AGENT）

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-AGENT-01 | agent 身份独立（用于审计/限流/吊销），不持绕过 RLS 的 service_role 读素材 | MM Bot 独立模型 `bot.go:24` | ☑ |
| CHAT-AGENT-02 | @agent → 后端解析提及 → 以发起人身份起 `ai_session`/`agent_run` → `AgentRunner.stream_turn` → 回复写回一条 `channel_message`（`sender_type='agent'`） | 复用 `agent_runner.py:209` | ☑ |
| CHAT-AGENT-03 | **🔥 防循环**：agent 发的 message 标 `from_bot_agent_id`；入站触发前 `if from_bot_agent_id IS NOT NULL → skip 触发` | Rocket.Chat `triggerHandler` **此处有 bug 会无限循环**，必须主动加这道过滤 | ☑ |
| CHAT-AGENT-04 | agent 发言节流（防刷屏、防自触发链）；触发前校验 `agent_channels` 白名单 | MM/Slack rate limit | ☑ |
| CHAT-AGENT-05 | 主动播报：监听 `task_tracking` 完成事件 → 写 message 到映射频道。第一版映射 = 发起人所属 team 的 `#general`，只播 team scope 可共享内容 | CHAT-SEC-AGENT-05 约束 | ☑ #902（DBOS scanner 每2min,channel-driven,模板化任务完成摘要,watermark 防重） |
| CHAT-AGENT-06 | "Agent 专属 DM" 复用现有 `AIChatPanel`+`ai_sessions`（本就是 1对1 与 agent），接进新聊天 DM 列表即可，不重做 | `frontend/components/AIChatPanel.tsx:108` | ☑ #899（复用 AIChatPanel：加可选 agentSlug，sidebar agent-DM 行，ChatPage 路由；ai_sessions 路径非 channels）|
| CHAT-AGENT-07 | 会话创建时绑定 `agent_id`（现有 ChatPanel 创建会话未绑定，需适配） | 调研适配点 | ☑ |

---

## 8. 前端 UI（UI）

| ID | 规则 | 来源/依据 | 状态 |
|----|------|----------|------|
| CHAT-UI-01 | 全英文 UI + i18n（en/zh），命名遵循 Title Case / camelCase key / kebab-case 文件 | CLAUDE.md UI 规范 | ☑ |
| CHAT-UI-02 | 频道侧栏样式与现有资源库/项目侧栏统一 | feedback_sidebar_consistency | ☑ |
| CHAT-UI-03 | 遵循岛式 UI 铁律：零 emoji、导航不消失、密度不减、新代码禁 zinc | project_island_redesign | ☑ |
| CHAT-UI-04 | 组件 ≤400 行典型，按 feature 组织（channel-list / message-list / composer / message-card） | 全局 coding-style | ☑ |
| CHAT-UI-05 | Toast 用 `useToast()`；BIGINT 经 `bigIntSafeFetch` | CLAUDE.md Key Patterns | ☑ |

---

## 9. 非目标 / 本版不做（OUT）

| ID | 项 | 状态 |
|----|------|------|
| CHAT-OUT-01 | 完整 thread 树（只做引用单条 CHAT-MSG-05） | ⊘ |
| CHAT-OUT-02 | 视频会议 / 语音通话（飞书的会议块） | ⊘ |
| CHAT-OUT-03 | 文档/审批/日历等飞书超级 App 模块 | ⊘ |
| CHAT-OUT-04 | 联邦协议（Matrix 式跨实例） | ⊘ |
| CHAT-OUT-05 | 完整 RBAC 细粒度权限编辑器（第一版用 team role + 频道 roles[] 够用） | ⊘ |
| CHAT-OUT-06 | 消息全文搜索（第一版先不做服务端搜索；若做必须服务端 keyset） | ⊘ |

---

## 10. 决策记录（OPEN）— 已全部拍板（2026-06-25）

| ID | 问题 | 决策 | 状态 |
|----|------|------|------|
| CHAT-OPEN-01 | `history_mode` 默认值 | **全 `shared`**（先服务小团队，新人可看历史，protected 留作未来按群可选项） | ✅ 已定 |
| CHAT-OPEN-02 | 频道自动生成策略 | **team 建立时自动建一个默认群（全员 `#general` 等价，type=`public`）+ 手动拉群**；project 频道暂不自动建（可选，延后） | ✅ 已定 |
| CHAT-OPEN-03 | 第一版是否含素材卡片 | **含 `media_card`**（CHAT-MSG-06/08，差异化核心）；交互按钮（CHAT-MSG-07）延后 | ✅ 已定 |
| CHAT-OPEN-04 | 主动播报触发源范围 | **先只接"AI 任务完成"一个**（转写/总结/分析完成），验证形态后再扩解析完成/新热点 | ✅ 已定 |

---

## 11. 建议实施阶段（PHASE）— 供后续出 plan

| ID | 阶段 | 涵盖编号 | 状态 |
|----|------|---------|------|
| CHAT-PHASE-0 | **Agent 聊天权限治理（AI Library）**：扩 capability_profile + helper + 响应模型暴露 + 角色校验 + Permissions UI + 系统预设特例 + 审计。**可先于聊天表落地**（enabled 仅存储，执行点等聊天表到位再接） | PERM-01~07, PERM-13~21 | ☐ |
| CHAT-PHASE-1 | 人聊天地基：表 + RLS + seq + Realtime + 频道/DM/未读 | DATA-01~10, SEC-01~08, UNREAD-*, RT-*, MSG-01~05 | ☐ |
| CHAT-PHASE-2 | @agent 回话：复用 AgentRunner + 防循环 + 频道授权补洞 + **接 PERM 执行闸** | AGENT-01~04, AGENT-07, SEC-AGENT-01~09, PERM-08~12 | ☐ |
| CHAT-PHASE-3 | 富消息 + 素材卡片 | MSG-06~08 | ☐ |
| CHAT-PHASE-4 | agent 主动播报 + DM 整合 | AGENT-05, AGENT-06 | ☐ |
| CHAT-PHASE-5 | UI 精修 + i18n + E2E | UI-* | ☐ |

---

## 附：实施情况检查口径

复查实施进度时，逐条对照上表勾选状态；新增/变更需求**追加新编号**，不改旧编号语义。后端列名变更前先对 `information_schema` 核列名（CLAUDE.md schema 漂移检查口径）。
