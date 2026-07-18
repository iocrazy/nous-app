# Data Backfills — 规矩与操作手册

**规矩（2026-07-17 立）：数据回填一律做成 DBOS workflow，不跑手工 SQL。**

理由：DBOS 任务经 `mirror_dbos_lifecycle_to_tracking` trigger 自动进
`task_tracking` → Task Center 可见、phase/progress 可追踪、metadata 里留下
"改了哪些行"的审计、失败有明确的 failed 态而不是"改了一半没人知道"。

## 模板

- Workflow：`backend/app/workflows/backfill_issue_scope.py`（首个实例，抄它）
  - 行级幂等：每条 UPDATE 自带前置条件（重放安全）
  - `dry_run=True` 默认：只统计报告，零写入
  - 失败 `raise`（路线 C rule 4），部分计数先 `patch_metadata` 再抛
  - 决策逻辑抽纯函数 → `backend/tests/test_backfill_issue_scope.py`
- Dispatch：`backend/app/api/admin/backfill_router.py`，新回填加进 `_BACKFILLS`
  注册表即可，不再开新端点

## 操作流程

1. **Dry run**（默认）：
   ```
   POST /api/v1/admin/backfill  {"name": "issue_scope"}
   ```
2. 在 Task Center（或 `task_tracking` 表）找到该任务，**review metadata 里的
   would_fix / ambiguous / orphan 计数**
3. 数字符合预期 → 真跑：
   ```
   POST /api/v1/admin/backfill  {"name": "issue_scope", "dry_run": false}
   ```
4. 完成后任务 metadata 即审计记录（修复的行 id 列表，cap 200）

## 现有回填

| name | 修什么 | 引入原因 |
|------|--------|----------|
| `issue_scope` | ① canvas 转的 issue 补 team/project（原 NULL）② 修 float64 舍入的 team_id/project_id（仅唯一候选才改，歧义/孤儿只报告） | PR #1423 之前的两个前端洞 |
