# Phase B / Phase 5 — 多人实时协同 C1-C3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地设计稿 `docs/superpowers/specs/2026-07-06-phase-b-p5-collaboration-design.md` 方案 B：presence（谁在场/谁在编辑哪个 scene）+ 实时 op 广播（写路径零改动，DB 表驱动），409 从常态降级为兜底。

**Architecture:** 三层全部复用仓内已验证范式（rt-survey 盘点 2026-07-06，报告在 scratchpad `rt-survey.md`）：
- **presence** = `useChannelPresence.ts` 同款 ephemeral channel（track/presenceState + broadcast 附带 focused_scene）
- **op 广播** = postgres_changes on `script_ops`（canvas 协同同款 DB 表驱动；新 mig 开 publication+RLS）
- **op 合并** = `useSceneSync` 新增 `applyRemoteOps`，guard 三件套抄 `canvasCoreStore.ts:569-586`（单调版本去 self-echo / 本地脏→既有 ConflictBar 分歧 UX / 干净则直接 apply 上屏）

**Flags：** `VITE_FEATURE_COLLAB`（前端总闸，默认 false）。后端无新行为（RLS/publication 是数据面，无 flag；广播不经业务代码）。

## Global Constraints

P1-P4 全部继承（i18n 键集全等/零 emoji/双主题禁 zinc/#1006 String 收敛/flush-on-unmount/`npx vitest run editor/ --no-file-parallelism` 门禁/CI 忽略 Vercel checks）。**Realtime 专属**：所有 `.on()` 必须在 `.subscribe()` 前挂完（useNavigation.ts:68-70 坑）；presence/broadcast channel 与 postgres_changes channel 分开建（useChannelPresence 惯例）；订阅 SUBSCRIBED 后必做一次全量对账拉取（TaskManagerContext 惯例）；migration 动 publication 用 per-table EXISTS DO block（抄 mig 217/296，**绝不用 ALTER PUBLICATION DROP IF EXISTS**——非法语法血泪）。协同验证压 vitest fake-channel 层（双 context E2E 无先例且 e2e stubs 屏蔽 Realtime WS）；真机 canary 用「浏览器观察者 + API 第二写者」模式。

## File Structure

```
supabase/migrations/34X_script_ops_realtime.sql        (取下一空号: REPLICA IDENTITY FULL + publication + RLS)
frontend/editor/collab/useScriptPresence.ts            (new: presence channel — 在场者 + focused_scene)
frontend/editor/collab/useScriptOpsRealtime.ts         (new: script_ops INSERT 订阅 → per-scene 分发)
frontend/editor/collab/PresenceAvatars.tsx             (new: 编辑器头部 avatar 栈)
frontend/editor/useSceneSync.ts                        (modify: +applyRemoteOps)
frontend/editor/components/EditorShell.tsx             (modify: flag 内挂两 hook + 分发接缝)
frontend/editor/components/SceneBlock.tsx              (modify: 场景头「N 人在看/正在编辑」软角标)
frontend/editor/nodes/SceneFlowNode.tsx                (modify: 节点同款角标)
frontend/editor/__tests__/{useScriptPresence,useScriptOpsRealtime,applyRemoteOps}.test.*(new)
frontend/public/locales/{en,zh}.json                   (modify)
```

**PR 切分：单 PR（PR-C1）**，三个 Task 对应设计稿 C1/C2/C3。flag-dark 合并，真机 canary 后开闸。

---

### Task 1（=C1）: presence——在场者与 scene 聚焦

**行为契约：**
- `useScriptPresence(scriptId, {userId, name, focusedSceneId})`：channel `script-presence-${scriptId}`，presence key=userId，`track({user_id, name, focused_scene_id, mode})`；focusedSceneId 变化时重 track（节流 2s，useChannelPresence 同参）。返回 `{onlineUsers: Array<{user_id,name,focused_scene_id,mode}>}`，从 `presenceState()` 权威快照重算（sync/join/leave 三事件都只重算不增量）。
- `mode`: 该用户本地有脏编辑（sync 队列非空）报 `'editing'`，否则 `'viewing'`。
- `PresenceAvatars`：编辑器头部（状态条旁）avatar 圈叠栈（首字母，双主题描边），>4 人折叠 `+N`；自己不显示。
- SceneBlock 场景头 + SceneFlowNode 节点：`focused_scene_id === scene.id` 的他人 → 小角标「⋯ 正在编辑」（editing）或人数点（viewing）。软提示，不阻塞任何操作。
- i18n：`editor.collab.editingBadge` / `viewersCount`（含复数 en/zh 全等）。
- 测试：fake channel（useChannelPresence.test.ts 范式）——track 参数/快照重算/self 过滤/节流/unmount removeChannel。

Commit `feat(editor): script presence — avatars + scene focus badges (flag-dark)`

### Task 2（=C2）: 实时 op 广播 + 客户端衔接

**迁移（`34X_script_ops_realtime.sql`）：**
```sql
ALTER TABLE script_ops REPLICA IDENTITY FULL;
-- publication: per-table EXISTS DO block（抄 296）
-- RLS: authenticated 可 SELECT 自己可访问 script 的 ops：
--   scene_id IN (SELECT s.id FROM script_scenes s JOIN script_projects sp ON s.script_id=sp.id
--                JOIN projects p ON sp.project_id=p.id
--                JOIN team_members tm ON tm.team_id=p.team_id WHERE tm.user_id=auth.uid())
-- （以 script_projects 实际归属列为准，实现者先 information_schema 核列名——team_id 直挂还是经 projects）
```
幂等 + NOTIFY pgrst。dev 库双跑。

**前端：**
- `useScriptOpsRealtime(scriptId, {getSceneIds, dispatchToScene})`：channel `script-ops-${scriptId}`，订 `postgres_changes INSERT ON script_ops`（**filter 只能单列**——scene_id 属多值，按 scene 逐一 filter 会爆 channel 数；改为不 filter scene、收到后本地按 `getSceneIds()` 集合过滤丢弃非本 script 的行。若 Realtime RLS 已把可见性缩到成员，本地过滤只是二道闸）。SUBSCRIBED 后触发一次全量 scenes 重拉对账。
- `useSceneSync.applyRemoteOps(row)`（核心，三道 guard）：
  1. `row.actor === 自己 userId` 或 `row.op_seq <= versionRef.current` → self-echo/旧事件，忽略
  2. `row.op_seq === versionRef.current + 1` 且本地队列空 → `applyOps(elements, row.op_json.ops)` 直接上屏 + versionRef 推进（scene_ops 前端纯函数已有）
  3. 不衔接（跳号）或本地队列非空 → 整 scene refetch（与 409 恢复共用路径）；refetch 后若本地有脏编辑 → 走既有 conflict 分歧（绝不 clobber，canvasCoreStore guard ③ 语义）
- EditorShell flag 内挂接：`sceneId → applyRemoteOps` 映射经 SceneBlock 注册（ref map，SceneBlock.tsx:178 绑定点旁）。
- 测试：applyRemoteOps 三分支穷举（self-echo/衔接 apply/跳号 refetch/脏编辑 conflict）+ hook fake-channel（INSERT payload 分发/scriptId 过滤/对账触发）。

Commit `feat(editor): live op streaming — script_ops realtime apply (flag-dark)` +迁移单独 commit 在前。

### Task 3（=C3）: 打磨——editing 软提示闭环 + 分歧率埋点

- presence `mode:'editing'` 与 sync 队列真实联动（Task 1 只有静态接口，此处接 `syncStates` 真值）；他人 editing 徽标在其 track 更新 4s 内自过期（useChannelPresence TYPING_EXPIRY 同款）。
- 埋点：conflict 发生时 `console.info('[collab] divergence', {sceneId})` + 现有 frontend log 管道一条（低频，不建新表）。
- vitest 全绿 + build + i18n 守卫。
- Commit `feat(editor): collab polish — live editing state + divergence beacon`

### Task 4: 收口

- opus 全分支终审（重点：RLS 策略正确性与性能面/applyRemoteOps 与乐观队列竞态/channel 生命周期泄漏/flag 关闭时零挂载零成本/迁移幂等）。
- ship 链（后端 pytest -k "scene or ops or realtime" + 前端门禁 + build + bump from master + PR + CI 忽略 Vercel + merge）。
- **真机 canary（flag 关，用 Vercel preview 或本地 flag 开）**：浏览器观察者（登录用户 A 开编辑器）+ API 第二写者（python 脚本以用户 B 对同 script 打 ops）→ A 屏 300ms 级上屏；B 停写 → A 编辑同元素提交 → 正常走版本推进；A/B 竞写同元素 → ConflictBar 分歧 UX。
- canary 过 → `vercel env add VITE_FEATURE_COLLAB`（**printf 不 echo**）+ redeploy → 生产同款 canary → memory 收官。presence 隐私默认值（team 内可见）在开闸前向用户做最终确认。

## Self-Review 已做

- 设计稿 C1/C2/C3 → Task 1/2/3 一一对应；§4 决策点 1 已按 rt-survey 修正为 DB 表驱动（决策记录在 spec）。
- 不做清单继承 spec §6；双 context E2E 按盘点结论替换为 fake-channel + 观察者/API 写者 canary。
- 类型一致性：applyRemoteOps(row) 消费 script_ops 行 {op_seq, op_json{ops,inverse}, actor}——与 mig 339 列一致；versionRef 语义 = content_version（useSceneSync 既有）。
- 风险记录：postgres_changes 无法按 scene_id 多值 filter → 本地二道闸；RLS join 链以 information_schema 实核为准（team_id 直挂 vs 经 projects——#1006/42703 双坑口径）。
