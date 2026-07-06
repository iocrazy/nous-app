# Phase B / Phase 5 — 多人实时协同 设计稿（brainstorm 产出，待用户评审）

> Spec v3 §6 Phase 5 行原文：「多人实时协同（独立 brainstorm + CRDT 评估）」，依赖 P1-P3。
> 本稿 = 那次独立 brainstorm 的产出：CRDT 适配评估结论 + 三方案对比 + 推荐架构 + 分批切法。
> **状态：DRAFT——未经用户评审，未开工。**

## 0. 一句话推荐

**不引入 CRDT。** 在既有 script_ops 台账 + If-Match 协议之上加「**presence + 实时 op 广播**」（方案 B）：改动几乎全是增量（一条 Realtime 通道 + 客户端合并规则 + presence UI），保留 P1-P4 全部投资（版本管理/copilot dry-run/rollback 都建在台账上），409 UX 从「意外冲突」降级为「网络分区下的兜底」。

## 1. 我们已有什么（协同地基盘点）

Phase 1-4 无意间已把协同的难点做掉了大半：

| 已有资产 | 对协同的意义 |
|---------|-------------|
| `script_ops` 台账：per-scene `op_seq` 单调递增，op_json 含 ops+inverse | 天然的**操作流**——协同=把这条流实时推给其他人 |
| `apply_element_ops` If-Match 乐观并发（428/409 契约） | 写路径已经是**单调串行化**的，服务端永远不会产生分叉历史 |
| 409 冲突 UX（「保留我的/采用对方/对比」，F3 真机验证过） | 分歧兜底已存在且经过实战 |
| 前端 sync 串行队列 + 防抖 + flush-on-unmount | 客户端写通道已经是有序的 |
| Supabase Realtime 基建（task_tracking 监听、team chat epic #893-911 全套频道经验） | 传输层现成，零新组件 |
| 版本管理 commits/diff/rollback（P4） | 协同场景的「谁改了什么」审计与恢复已具备 |
| teams/团队权限 + verify_script_access 全端点守卫 | 协同=同 team 成员，权限面零新增 |

**结论：我们缺的只是「让别人的写入实时出现在我屏幕上」+「知道谁在场」。**

## 2. CRDT 适配评估（spec 要求的裁决项）

**结论：不适配，成本远大于收益。理由：**

1. **数据模型形状不合**。我们的文档是「元素数组 + anchor-op 协议」（insert/update/move/delete 以 element_id 锚定），不是连续文本。Yjs/Loro 的甜区是字符级共编；要用它，得把 content_json 换成 CRDT 内部表示，**script_ops 台账（P4 版本管理、copilot dry-run、rollback、审计的共同地基）整个作废重建**。
2. **收益对不上场景**。剧本创作的协同单位是「场景/元素」（一人写一场戏），不是「两人同时改同一句对白的第 3 个字」。元素级 last-writer + 409 分歧提示已覆盖真实需求；字符级合并是为我们不存在的场景付费。
3. **运维/调试成本**。CRDT 状态是二进制黑盒，出问题没有 `SELECT * FROM script_ops` 可查；与 asyncpg/PostgREST 生态的调试口径（application_logs 漏斗）完全脱节。
4. **保留逃生门**：若未来「单元素内多人同时打字」成为硬需求，可以只对 dialogue/action 的 `text` 字段做**元素内嵌 CRDT**（Yjs subdoc per element），台账记 CRDT update 二进制——局部引入而非全盘重写。此裁决记录在案，届时不必重新论证全盘方案。

## 3. 三方案对比

### 方案 A：CRDT 全文实时（Yjs + y-websocket / Loro）
- 优点：字符级共编、离线合并理论最优。
- 缺点：§2 全部；另需自建/托管 websocket 服务（Supabase Realtime 不是 Yjs provider）。
- **完成度 10/10 的通用协同，但对我们是推倒重来。不选。**

### 方案 B：presence + 实时 op 广播（推荐）
- 写路径**完全不变**：仍是 If-Match → apply_element_ops → 台账。
- 新增读路径：script_ops 插入后实时广播给同 script 的其他在线客户端；收到方按 `op_seq == 本地 content_version + 1` 严格衔接则直接 apply_ops 上屏，不衔接（丢包/乱序/离线过）则整 scene refetch 兜底。
- presence：Supabase Realtime presence channel（team chat 同款），标注「谁在这个 script / 谁聚焦哪个 scene」。
- 409 语义不变，只是发生率骤降（对方的写几秒内就出现在你屏上，你很难再基于旧版本提交）。
- **缺点**：不是字符级；两人同时改同一元素仍走 409 分歧提示（这是设计意图，不是缺陷）。

### 方案 C：scene 级硬锁（check-out/check-in）
- 最简单：编辑即锁场景，他人只读。
- 缺点：锁泄漏处理（断网/关页）永远恶心；阻塞并行创作；与 copilot（也是写者）互卡。**不选，但其「软化版」（presence 里显示"某某正在编辑此场景"的提示，不阻塞）并入方案 B。**

## 4. 推荐架构（方案 B 细化）

```
写者 A ──PATCH ops──▶ backend apply_element_ops ──INSERT──▶ script_ops
                                                            │ (Realtime publication 或
                                                            │  backend 200 后主动 broadcast)
在线读者 B ◀──channel `script:{script_id}` op event──────────┘
  ├─ op_seq == local_version+1 → applyOps(elements, ops) 直接上屏（含光标/滚动保持）
  ├─ 不衔接 → refetch 该 scene（与 409 恢复共用代码路径）
  └─ 该 scene 正被 B 本地编辑且防抖未冲刷 → 先 flush 本地（可能拿 409 走既有分歧 UX）
presence channel：{user, script_id, focused_scene_id, mode: viewing|editing}
  ├─ 编辑器头部 avatar 栈（team chat 同款视觉语言）
  ├─ scene 卡片/节点角标「N 人在看 / 某某正在编辑」（软提示不阻塞）
  └─ 画布 NodesView 节点同款角标（P2 节点视图 tie-in）
```

**关键决策点（评审时请裁）：**
1. **广播源**：~~backend 主动 broadcast~~ → **修正为 DB 表驱动（postgres_changes on script_ops）**。依据 rt-survey 基建盘点（2026-07-06）：仓内 20+ channel 全走 postgres_changes（canvas 协同即此范式），Python 侧主动 `channel.send()` 零先例；且 DB 驱动天然覆盖包括 copilot 在内的一切写路径（都汇于 apply_element_ops → script_ops INSERT）。代价=一个新 migration：script_ops `REPLICA IDENTITY FULL` + 进 supabase_realtime publication（per-table EXISTS DO block，抄 mig 217/296）+ authenticated 成员按 script 归属可 SELECT 的 RLS（当前仅 service_role，JWT 订阅收不到事件）。
2. **scope**：首批只做编辑器 Writing 面板 + NodesView 角标；Outline/Storyboard 视图的实时刷新走「收到任意 op → 失效重拉」粗粒度即可。
3. **presence 隐私**：team 内可见你在编辑哪个 scene——默认开还是给设置项？（涉隐私决策，属用户裁决项。）
4. **在线判定**：presence heartbeat 沿 team chat 的既有参数，不另造。

## 5. 分批切法（各批独立可 ship，flag-dark）

| 批 | 内容 | 依赖 | 成本 |
|----|------|------|------|
| C1 | presence 只读：channel + avatar 栈 + scene/节点角标 | 无 | 小（纯前端+channel） |
| C2 | 实时 op 广播 + 客户端衔接 apply / refetch 兜底 | C1 的 channel | 中（backend broadcast 点 + 前端合并规则 + 双浏览器 E2E） |
| C3 | 编辑中软提示（focused_scene 的 editing 态 + 分歧率埋点） | C1+C2 | 小 |

Flag：`VITE_FEATURE_COLLAB`（前端）+ `FEATURE_COLLAB_BROADCAST`（后端广播点），默认 false，两周内决定去留。
E2E 口径：Playwright 双 context 同 script——A 写 op → B 300ms 内上屏；B 断网重连 → refetch 恢复；A/B 同元素竞写 → 409 分歧 UX 不回退。

## 6. 不做（本 epic 内）
- 字符级共编（§2 裁决，逃生门已记录）
- 跨 script 全局活动流（属 Team Chat/通知域）
- 评论/批注（独立 epic，与协同正交）
- 离线编辑队列（PWA 离线写涉及冲突堆积，另议）

## 7. 评审待决清单（用户）
1. 方案 B 是否认可（vs 仍想要字符级 CRDT）？
2. §4 决策点 1-3（广播源 / 首批 scope / presence 隐私默认值）。
3. C1-C3 排期：紧接着做，还是先还 Vercel 视觉过场 + Seedream 解锁两笔外债？
