# 裸 SQL 全量 ORM 化 — 决策与分期（2026-08-04）

> 用户指令：**系统表也要 ORM**——dbos/settings/logs/task_tracking 都非常重要，
> 不接受"系统表裸 SQL 无所谓"的立场。本文件取代
> `2026-06-04-orm-2-architecture-decisions.md` 中"裸 SQL 治理而非消灭"的口径：
> **目标是消灭，治理（scoped_sql）只是迁移期与结构性例外的护栏。**

## 底数（2026-08-04 实测）

- 66 个文件在用 `db_engine.fetch_all/fetch_one/execute`（text() 裸 SQL）
- 其中 20 个碰租户表（13 个 workflows + ~7 个用户请求路径）
- 46 个只碰系统表，高频：issues(24)/system_settings(19)/task_tracking(15)/
  user_schedules(10)/conversations(9)/agent_runs(8)/user_settings(7)/dbos(7)
- ORM 模型层已覆盖上述全部 public 表（107 个生成模型）——迁移是改写查询，不是建模

## 为什么系统表也要 ORM（用户理由 + 技术印证）

1. 这些表是核心基础设施（任务系统/配置/日志/调度），出错影响全局
2. 42703 列漂移事故反复发生（CLAUDE.md 记录多起）——字符串 SQL 只在运行时炸，
   ORM 引用在导入/测试期就炸
3. 单一数据访问模式 = 更少的"第四条路"，review 面收窄

## 分期

| Phase | 范围 | 收益 | 规模 |
|---|---|---|---|
| **A（先行）** | ~7 个用户请求路径租户文件 → ORM；建 `scoped_sql` 护栏 + CLAUDE.md 规则 | 越权风险归零（结构保障） | 1 个 PR |
| **B** | 46 个系统表文件，按表域分批：issues → settings/logs → task_tracking → schedules/conversations → 其余 | 漂移早炸 + 模式统一 | 每批 1 PR，多 session |
| **C** | 13 个 workflows 租户文件 → ORM + 显式 system_session | 同 B | 分批 |

task_tracking 批注意路线 C：phase 列仍归 trigger/manager API 管，ORM 化只覆盖
被允许的读与 metadata 写——迁移不得引入对 phase 列的直接 ORM UPDATE。

## 结构性例外（不 ORM，需用户确认）

1. **`dbos.*` schema**——DBOS 引擎私有表，结构随 DBOS 版本变化，路线 C 本就禁止
   app 直查（仅有两个已文档化的 psycopg 例外：startup reaper 与 pre-launch sweep）。
   给它建 app 侧模型 = 与引擎升级赛跑。**处理：保持禁查纪律 + 两个例外原样。**
2. **PG 系统目录**（information_schema/pg_stat_activity/pg_proc 等，用于 schema
   探针、连接诊断、迁移自检）——没有也不该有 app 模型。**处理：保留 text()，
   集中到少数 diagnostics 模块。**

## 迁移纪律（每批适用）

- 改写前后行为等价：复杂查询（UNION/LATERAL/窗口）用 SQLAlchemy Core 表达式
  （union_all/lateral/over），不为"更像 ORM"牺牲执行计划
- 每批：测试先行 + 对抗式审查 + 部署验证（本会话既有流程）
- 新增代码即日起禁止新的 text() 裸 SQL（除结构性例外），CLAUDE.md 落一条
