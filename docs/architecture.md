# MediaHub 系统架构

## 产品定位

MediaHub = 内容团队的 AI 驱动生产线。以可配置节点流引擎为核心，每个节点可接入 AI Agent 自动执行。

## 核心模块

| 模块 | 状态 | 技术 |
|------|------|------|
| 资源管理 | 现有 | parsed_media, resources, tags |
| 项目管理 | 改造 | projects + workflow_template_id |
| 节点流引擎 | **新增** | React Flow + 6张新表 |
| 能力模块系统 | **新增** | capabilities + Agent调度 |
| 项目内聊天 | **新增** | project_messages + Supabase Realtime |
| 人员排期 | **新增** | FullCalendar + 聚合查询 |
| 积分系统 | 现有 | points, payments, quotas |

## 技术栈

- 前端：React 19 + TypeScript + Vite 7 + TailwindCSS
- 流程编辑器：React Flow
- 日历：FullCalendar
- 图表：Recharts
- 后端：FastAPI + Python 3.11+
- 异步任务：Celery + Redis
- 数据库：Supabase (PostgreSQL + Auth + Realtime + Storage)
- AI Agent（短期）：OpenClaw Gateway API
- AI Agent（长期）：自研 Agent 引擎

## 数据流

```
用户创建项目 → 选择工作流模板
  → 发起内容生产实例（workflow_instance）
  → 按节点推进（node_execution）
    → 人工节点：分配给具体人，手动完成
    → AI节点：检查积分 → 扣费 → 调用Agent → 结果回写 → 人工确认
    → API节点：调用外部API → 自动流转
    → 审批节点：审批人通过/驳回
  → 全程聊天通知（project_messages）
  → 完成后数据回收 → 分析复盘
```

## 关键设计决策

1. **sort_order 用浮点数** — 拖拽排序只更新一条记录（参考Plane）
2. **sequence_id 用 pg_advisory_xact_lock** — 并发安全自增（参考Plane）
3. **node_group 归属分组** — preparing/in_progress/reviewing/completed，方便看板
4. **current_node_ids 用数组** — 为未来并行节点预留
5. **execution_round** — 回退后重新执行可追溯
6. **权限数值化** — admin=20/editor=15/viewer=5
7. **JSONB 灵活存储** — config/output/condition 都用 JSONB，暂不做自定义字段系统
8. **积分与节点流打通** — AI节点执行消耗积分，失败退款
