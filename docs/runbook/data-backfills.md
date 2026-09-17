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

## 例外：按用户跑的嵌入补算（2026-09-16）

`POST /api/v1/ai/analyze/backfill-embeddings` **不在** `_BACKFILLS` 注册表里，也不是
admin 端点——它补的是**调用者自己**缺 `content_embedding` 的下载（2026-09-16 查时
`resource_analysis` 只有 2 行、0 条向量，语义层一直是空的；背景见 CLAUDE.md 已知陷阱
「向量链路三处静默」），范围按 JWT 的
user_id 划，不是全表，所以走不了 `/api/v1/admin/backfill` 那条全库通道。

```
POST /api/v1/ai/analyze/backfill-embeddings  {"limit": 20, "dry_run": true}
```

- `limit` 默认 20、上限 200，两条腿合计计数；`dry_run` 的元素形状与真跑一致
- 两条腿：**已有 VLM 分析的行就地重嵌**（一次 embedding 调用，不建 Task Center 行）；
  没有分析的行各派发一个 `analyze_l1`（VLM + 嵌入，有任务行）
- 响应字段刻意正交：`reembedded` 是**已经**落了向量的；`dispatched` 是刚起的 workflow、
  向量**还没**落；`skipped` 带稳定 reason code（`no_cover_url` / `embedder_unconfigured`
  等，绝不回显 provider 原文）；`in_flight` 是已有运行覆盖的行；`remaining` =
  `total_missing` 减去真正落地的那部分
- embedder 没配就 409 `embedder_unconfigured` 直接拒——否则每个派发出去的 `analyze_l1`
  都会花 VLM 的钱再原样撞上同一个失败

⚠️ 与本文开头的规矩有两处偏离，都是刻意的：没有 workflow，且重嵌那条腿不建任务行，
所以 metadata 审计只覆盖派发出去的那一半。**全库范围**的重嵌（换模型、换向量空间）
仍应按开头的规矩做成 DBOS workflow。
