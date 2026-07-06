# Phase B / Phase 4 — 版本管理 UI + 旧面 cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec v3 §5-1：基于 P1 起持续记录的 `script_ops` 台账做版本管理——手动 commit（打标签）、commit 间 diff（op 重放）、回滚（逆向重放经同一 If-Match 协议）；顺带完成旧 storyboard 面 cutover（数据已核实近空：1 行测试项目/0 frames → rename-deprecate，可逆）。

**Architecture:** `script_commits` 表存**每 scene 的 op_seq 水位图**（spec 原文只写 script_id/message/op_seq——op_seq 是 per-scene 的,故实化为 jsonb 水位 `{scene_id: op_seq}` + scene 集合快照,这是对 spec 的必要精化,记决策）。diff = 逐 scene 取两水位间 ops 重放出两版元素数组 → 前端元素级并排对比。回滚 = 逐 scene 用 `buildInverse` 思路服务端算逆批 → 走既有 `apply_element_ops`（If-Match 协议,并发面零新增,actor 记 user）。UI 参考 laper Writing 面板（Version history / Save version 按钮——用户截图基线）。

**Spec:** §5-1（逐字：commit=打标签;diff=两 commit 间 op 重放;回滚=逆向重放;只允许手动 commit）、§2.3（cutover）
**同构模板:** 迁移/repo/router/守卫=P3 shots 全套;前端面板=WritingPanel 既有结构。

## Global Constraints

P1-P3 全部 Global Constraints 继承（含 **task_type ≤20 字符**（#1103 血泪）、dispatch bundle、真库 round-trip、i18n 键集全等、vitest --no-file-parallelism、**Vercel 限流期间前端 PR 照常合并但生产部署延迟——CI 监控忽略 Vercel check,视觉过场排队到配额恢复**）。版本功能无需 flag（读台账+走既有写协议,无新成本面）。

## File Structure

```
supabase/migrations/34X_script_commits.sql            (取下一空号)
backend/app/models/scripts.py                         (+ScriptCommits)
backend/app/repositories/script_commit_repository.py  (new)
backend/app/services/script/version_service.py        (new: 水位/重放/diff/逆批纯逻辑+编排)
backend/app/api/script_versions_router.py             (new: commits CRUD + diff + rollback)
frontend/editor/versions/VersionPanel.tsx             (new: Writing 面板 Version history 区)
frontend/editor/versions/VersionDiff.tsx              (new: 元素级并排 diff 视图)
frontend/editor/sceneService.ts                       (+versions API)
```

**PR 切分**：**PR-V1** = Task 1-3（表+服务+router 后端全量）；**PR-V2** = Task 4-5（前端面板+diff/rollback UX + 旧面 cutover 迁移）；**PR-V3** = Task 6（终审+ship+真机 E2E+P3 遗留清账）。

---

### Task 1: 迁移 + ORM — `script_commits`

- 列：id BIGINT PK snowflake / script_id BIGINT NOT NULL FK script_projects ON DELETE CASCADE / message VARCHAR(200) NOT NULL / watermarks JSONB NOT NULL（`{scene_id: op_seq}`）/ scene_ids JSONB NOT NULL（有序数组,含 sort_order 快照 `[{id,sort_order,heading...}]` 供 diff 展示场景增删/换序）/ created_by VARCHAR(64) NOT NULL / created_at。索引 (script_id, created_at DESC)。RLS+NOTIFY+幂等（P3 迁移同款）。ORM+导入 pin。dev 库双跑核对。
- Commit `feat(script): migration — script_commits`

### Task 2: `version_service.py` — 重放/diff/逆批核心

**纯逻辑（无 IO,穷举单测,复用 backend scene_ops.apply_ops）：**
```python
def replay_to(ops_rows: list[dict], target_seq: int) -> list[dict]
    # 从空列表按 op_seq ≤ target 依序 apply_ops(elements, row.op_json["ops"]) 重放出元素数组
def diff_scenes(elements_a: list, elements_b: list) -> list[dict]
    # 元素级 diff：按 id 对齐 → [{kind: added|removed|changed|moved, id, before, after}]
def inverse_between(ops_rows: list[dict], from_seq: int, to_seq: int) -> list[dict]
    # (from,to] 区间各行 op_json["inverse"] 逆序串联 → 一个可 apply 的逆批
```
**编排（async,读库）：** `snapshot_watermarks(script_id)`（逐 scene MAX(op_seq)+当前 scene 集）/ `compute_diff(script_id, commit_a, commit_b|None=当前)`（scene 集对比+逐 scene replay+diff）/ `rollback_to(script_id, commit_id, actor)`（逐 scene inverse_between(当前水位→commit 水位) → `apply_element_ops`(expected_version=当前,正常走版本+落账);**commit 后新增的 scene 标记 removed 提示但不自动删,commit 时不存在而已删的 scene 不复活——两类边界在响应里如实列出,UI 展示**;回滚本身产生新 ops（可再回滚,历史不可变——spec 操作历史哲学））。
- 测试：重放确定性（同 ops 两次重放相等）/ diff 四类 kind / inverse_between 与 apply 往返 / 回滚边界两类 / 空台账。
- Commit `feat(script): version service — replay, diff, inverse rollback`

### Task 3: router — commits CRUD + diff + rollback

- `POST /scripts/{script_id}/commits {message}`（verify_script_access;service.snapshot → repo.create）/ `GET /scripts/{script_id}/commits`（列表,新→旧）/ `GET /scripts/{script_id}/commits/{commit_id}/diff?against={commit_id|current}` / `POST /scripts/{script_id}/commits/{commit_id}/rollback`（**同步执行**——逐 scene apply 通常 <2s,不值 workflow;失败中途=部分回滚,响应列出成功/失败 scene,前端提示重试——决策记录）/ `DELETE /commits/{commit_id}`（标签可删,历史 ops 不动）。
- wiring 测试全路由守卫 + 行为测试（diff 形状/rollback 部分失败响应/commit message 长度校验）。**task_type 若引入必 ≤20**。
- lint + `-k "commit or version"` 全绿。Commit `feat(script): version router — commits, diff, rollback`

**→ PR-V1 ship**（opus 终审重点：重放确定性/逆批正确性/回滚部分失败语义/守卫）。

### Task 4: 前端 VersionPanel + Diff + Rollback

- WritingPanel 顶部加 **Version history** 区（laper 基线）：`Save Version` 按钮（弹小输入 message,内联而非 modal）→ createCommit;历史列表（message+相对时间+作者）;条目动作:**Compare**（against current 默认）/ **Roll back**（内联确认,G1 同款）/ Delete。
- `VersionDiff.tsx`：中央纸页替换为并排/单列变更流——按 scene 分节,changed 元素前后文本对照（删除线/高亮,双主题）,added/removed/moved 徽章;顶部返回按钮。rollback 成功 → toast + 重拉 scenes;部分失败 → 明细提示。
- 测试：Save Version 调 API/列表渲染/Compare 渲染四类 kind/Roll back 两击确认+成功重拉/部分失败提示。i18n en-zh 键集全等;零 emoji。
- Commit `feat(editor): version history panel with diff and rollback`

### Task 5: 旧 storyboard 面 cutover（rename-deprecate）

- 已核实 prod：storyboard_projects=1（测试残留）/frames=0。迁移：`ALTER TABLE storyboard_projects RENAME TO zzz_deprecated_storyboard_projects`（同 frames/assets/关联表——先 information_schema 列出 storyboard_* 全家再逐个;`script_storyboard_links` 若被新面引用则保留,先 grep 消费方）+ COMMENT 注明 deprecation 日期与恢复方法。**不 DROP**（可逆;DROP 排未来 housekeeping）。
- 后端：旧 storyboard routers/services 挂 410 Gone（或直接从 api/__init__ 反注册,选影响面小的;先 grep 前端还有谁调）。前端：旧 Workbench 路由改为跳转编辑器 Storyboard 槽位 + toast（只读 banner 升级为迁移完成态）。
- 测试：旧端点 410/重定向;新面不受影响（shots 套件全绿）。
- Commit `feat(script): legacy storyboard cutover — rename-deprecate tables, retire routes`

**→ PR-V2 ship**。

### Task 6: 收口

- 终审 → ship → 真机 E2E：写元素→Save Version A→再改+加 scene→Save Version B→diff A vs B（验证 changed/added）→rollback 到 A→内容回到 A 且产生新 ops→再 diff 确认。**Vercel 限流若未恢复：后端 E2E 照做,前端面板视觉过场进"待 Vercel 窗口"清单（与 P3 分镜视觉过场同队列）**。
- P3 遗留清账检查：Vercel 恢复后补 P3+P4 前端视觉过场一次做完。memory 收官。

## Self-Review 已做

- Spec §5-1 覆盖：commit 打标签→T1/3;diff op 重放→T2/3/4;回滚逆向重放走同一协议→T2/3;只手动 commit→无自动路径。§2.3 cutover→T5（rename 代 DROP 的偏差=可逆性优先,数据近空已核实,决策记录）。
- 关键精化决策：op_seq per-scene→watermarks jsonb;回滚同步执行;回滚产生新 ops 历史不可变;scene 增删边界如实上报不自动删。
- 不做：自动 commit、分支/合并、跨 script diff、diff 的字符级高亮（元素级够用）。
