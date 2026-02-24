# MediaHub 开发任务清单

## 文档结构

```
docs/
├── architecture.md      # 系统架构概览
├── database-design.md   # 完整数据库设计（建表SQL）
└── api-design.md        # API 端点详细定义

tasks/
├── phase1/              # 节点流引擎（第1个月）
├── phase2/              # AI Agent + 聊天（第2个月）
└── phase3/              # 排期 + 数据闭环（第3个月）
```

## Phase 1：节点流引擎

| # | 任务 | 类型 | 依赖 | 状态 |
|---|------|------|------|------|
| 01 | [数据库迁移](phase1/01-db-migrations.md) | 后端 | 无 | ⬜ |
| 02 | [工作流模板 CRUD API](phase1/02-workflow-crud-api.md) | 后端 | 01 | ⬜ |
| 03 | [实例 + 流转 API](phase1/03-instance-flow-api.md) | 后端 | 01, 02 | ⬜ |
| 04 | [React Flow 编辑器](phase1/04-workflow-editor-ui.md) | 前端 | 02 | ⬜ |
| 05 | [看板视图 + 实例详情](phase1/05-kanban-view.md) | 前端 | 03, 04 | ⬜ |
| 06 | [预设模板 Seed 数据](phase1/06-seed-templates.md) | 数据 | 01 | ⬜ |

**执行顺序**：01 → 02 + 06（并行）→ 03 → 04 → 05

## Phase 2：AI Agent + 聊天（待拆分）

| # | 任务 | 类型 | 状态 |
|---|------|------|------|
| 07 | 能力模块 CRUD API | 后端 | ⬜ |
| 08 | AI Agent 调度服务（OpenClaw 对接） | 后端 | ⬜ |
| 09 | 积分系统与节点流打通 | 后端 | ⬜ |
| 10 | 聊天后端（project_messages） | 后端 | ⬜ |
| 11 | 聊天前端 | 前端 | ⬜ |
| 12 | AI 执行结果展示 | 前端 | ⬜ |

## Phase 3：排期 + 数据闭环（待拆分）

| # | 任务 | 类型 | 状态 |
|---|------|------|------|
| 13 | 排期聚合 API | 后端 | ⬜ |
| 14 | FullCalendar 排期视图 | 前端 | ⬜ |
| 15 | 人员负载看板 | 前端 | ⬜ |
| 16 | 多平台发布对接 | 后端 | ⬜ |
| 17 | 数据回收 + 分析 | 后端+前端 | ⬜ |

---

## 给 AI Coding Agent 的说明

1. 每个 task 文件是独立的开发单元，按顺序执行
2. 参考 `docs/` 下的设计文档获取完整规格
3. 遵循项目现有的代码风格（参考 CLAUDE.md）
4. 所有 UI 文本使用英文（i18n 提供中文翻译）
5. ID 使用 Snowflake BIGINT
6. 新增 API 端点需要 JWT 认证
