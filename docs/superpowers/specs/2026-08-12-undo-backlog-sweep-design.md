# Undo Backlog 清扫 — 设计（2026-08-12）

> 来源：Agent Run 撤销立项（PR #1767）终审遗留 backlog + 权限立项遗留的同族问题。
> 交接锚点：`docs/superpowers/specs/2026-08-12-agent-line-roadmap-handoff.md` §2-③。
> 用户已批准本设计（2026-08-12，会话内 AskUserQuestion）。

## 0. 范围

五个修复点，一个 PR（`feat/undo-backlog-sweep`），零 migration，纯代码。
不做：扩展 undo 响应体带 scene id 列表（精确刷新）、realtime 链路改造、
`_mark_visual_analysis_failed` 遮蔽问题（不属 undo 域）。

## 1. 修复点

### F1 — `already_undone` 独立文案（前端）

**现状**：重复 Undo 时后端返回 `{"status":"already_undone", 计数全 0, skipped:[]}`
（`backend/app/api/ai_library_router.py:2447-2455`）。
`frontend/components/agentActivity/TurnWriteSummary.tsx:175-199` 不区分 status，
渲染 `agentActivity.undoSummary`（"Undid 0 cards, reverted 0 cards, restored 0
passages"）——看起来像刚执行了一次空撤销，误导。

**修法**：`undoReport.status === 'already_undone'` 时渲染独立文案，不走计数模板：

- 新 i18n key `agentActivity.undoAlreadyUndone`
  - en: `Already undone earlier — nothing changed`
  - zh: `此前已撤销过 — 本次未做任何更改`
- 计数模板与 skipped 列表仅在 `status === 'done'` 时渲染。

### F2 — `list_ops_by_scene` DB 错误上抛（后端）

**现状**：`backend/app/repositories/script_scene_repository.py:266-283` catch 全部
异常 → log → return `[]`。两类消费者都被骗：

- `version_service.py`（4 处调用：297/305/311/376）拿空账本 replay/rollback，
  会**静默重建出错误内容**；
- `run_undo_service.py:187` 拿空账本 → `scene_undo_plan` 空 → 静默"无可撤销"。

**修法**：删掉 try/except，让异常上抛。与同文件 `list_scene_rows_for_project`
（L250-264）的 house convention 一致：primary/aggregate read 失败走类型化 500，
不吞。`run_undo_service` 侧的泛异常 handler 接住后归 `internal_error`（见 F4）。

### F3 — scene 正文撤销后刷新已开 script sheet（前端）

**现状**：`useRunUndo.ts:99` 成功后只发 `requestStoryboardRefresh()`——
订阅者只有 `useSceneShots.ts:166` 和 `EpisodeStoryboardPage.tsx:270`（分镜面）。
剧本纸（`frontend/editor/components/EditorShell.tsx`，script sheet）不刷新，
已打开时显示 stale 正文。

realtime `script_ops` 流理论上能补（undo 走 `apply_element_ops`，确实写
`script_ops` + bump `content_version`，actor=`undo:{run_id}` 不会被 self-echo
过滤），但**不可依赖**：整条链被 `VITE_FEATURE_COLLAB` 开关包住
（`EditorShell.tsx:104`，本地默认关），且 `useSceneSync.ts:152-165` 有
"同 id 不重播种"守卫。

**修法**（用户已拍板，bus + 重挂载）：

1. `frontend/components/agentActivity/shotFocusBus.ts` 加第三通道（与
   storyboard refresh 同形）：`onSceneContentRefresh(listener)` /
   `requestSceneContentRefresh()`。
2. `useRunUndo.ts` 在 undo 成功且 `result.scene_elements_reverted > 0` 时
   触发 `requestSceneContentRefresh()`（skipped-only 不触发——内容没变）。
3. `EditorShell.tsx` 订阅该通道，复用 rollback 现成先例
   `handleRolledBack`（L354-361）：`reload()` + bump `rollbackNonce` →
   `<Fragment key={`${s.id}:${rollbackNonce}`}>`（L1584）强制重挂载
   SceneBlock，绕过同 id 不重播种守卫。

**已知代价**（与版本回滚同语义，rollback 先例已接受）：剧本纸上未保存的
本地输入会被丢弃。undo 意味着服务端内容已变，整卷 reload 是诚实行为。
响应体不含被改 scene 的 id 列表，故不做精确到场景的刷新。

### F4（含原 F5）— `skipped.reason` 加 `internal_error` 档（前后端）

**现状**：`backend/app/services/ai/undo/run_undo_service.py` 两处泛异常
handler 把未知内部错误也归因 `edited_after_run`：

- `_undo_shots` L88-101（per-plan isolation 的 except Exception）
- `_undo_scenes` L206-215（per-scene 的 except Exception）

用户看到的解释（"changed since this run"）是假的——真相是服务端内部错误。
权限立项终审也点名了 `_undo_scenes` 这处（同族问题，一并收）。

**修法**：

1. 两处泛异常的 reason 改为 `"internal_error"`（logger.error + exc_info 保留）。
2. `execute_undo` docstring（L43-45）与 `schemas/agent_runs.py:179` 注释同步。
3. `frontend/types.ts` `AgentRunUndoReport.skipped[].reason` union 加
   `'internal_error'`。
4. i18n `agentActivity.undoReason.internal_error`
   - en: `internal error during undo — left untouched`
   - zh: `撤销时发生内部错误 — 未做更改`

既有归因不动：CAS miss → `edited_after_run`、渲染物存在 → `rendered`、
`VersionConflict` → `version_conflict`。F2 上抛后，`run_undo_service:187` 的
DB 错误自然落入 `_undo_scenes` 泛异常 → `internal_error`（准确归因）。

## 2. 测试（TDD）

后端（pytest）：
- `list_ops_by_scene` DB 错误上抛（mock session 抛错 → pytest.raises，不再 []）。
- `_undo_shots` / `_undo_scenes` 泛异常 → skipped reason == `internal_error`
  （现有 undo service 测试文件内加案例；同时锁死 CAS miss 仍是
  `edited_after_run`，防止归因回归）。

前端（vitest）：
- `TurnWriteSummary`：`status='already_undone'` 渲染独立文案、不渲染计数模板；
  `status='done'` 行为不变。
- `useRunUndo`：`scene_elements_reverted > 0` 触发 `requestSceneContentRefresh`；
  为 0（含 skipped-only）不触发。
- `EditorShell` 订阅：收到 sceneContentRefresh → reload 被调 + nonce 变化
  （或按现有 EditorShell 测试基建的可测粒度锁行为）。
- i18n：en/zh 两份 locale 均含新 key（如仓库有 key 一致性测试则自动覆盖）。

## 3. 验收

- CI 绿（后端 pytest + 前端 vitest/build）。
- 前端部署后走一遍真实链路（调试账号）：undo 一个含 scene 改动的 run，
  已开剧本纸自动刷新；重复 undo 显示 already-undone 独立文案。
  （write_level 已于 2026-08-12 给 storyboard/script_ai 预设开通 `write`，
  真实链路可达。）
