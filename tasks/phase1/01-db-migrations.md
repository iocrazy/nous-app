# Task 01: 数据库迁移 — 节点流引擎建表

## 目标

创建节点流引擎所需的所有数据库表和索引。

## 依赖

- 现有 Supabase 数据库（migrations 001-079 已执行）
- 现有表：projects, teams, auth.users, unified_tasks

## 输出文件

```
supabase/migrations/
├── 080_create_capabilities.sql
├── 081_create_workflow_tables.sql
├── 082_create_instance_tables.sql
├── 083_create_project_messages.sql
├── 084_alter_existing_tables.sql
└── 086_enable_realtime.sql
```

## 详细规格

### migration_080_create_capabilities.sql

创建 capability_type ENUM 和 capabilities 表。参考 `docs/database-design.md` 中的完整 DDL。

字段清单：id(BIGINT PK), name, slug(UNIQUE), type(capability_type), description, config(JSONB), icon, action_type, is_builtin, is_active, created_at, updated_at

### migration_081_create_workflow_tables.sql

1. 创建 node_type ENUM: human, ai_agent, api, approval
2. 创建 node_group ENUM: preparing, in_progress, reviewing, completed
3. 创建 workflows 表
4. 创建 workflow_nodes 表（外键 → workflows, capabilities）
5. 创建 workflow_edges 表（外键 → workflows, workflow_nodes × 2）
6. 创建所有索引

### migration_082_create_instance_tables.sql

1. 创建 instance_status ENUM: active, completed, cancelled, paused
2. 创建 priority_level ENUM: urgent, high, medium, low, none
3. 创建 execution_status ENUM: pending, in_progress, completed, skipped, rejected
4. 创建 workflow_instances 表（外键 → workflows, projects）
5. 创建 node_executions 表（外键 → workflow_instances, workflow_nodes, unified_tasks）
6. 创建所有索引

### migration_083_create_project_messages.sql

1. 创建 message_sender_type ENUM: user, system, agent
2. 创建 message_type ENUM: text, file, node_update, agent_output
3. 创建 project_messages 表
4. 创建索引

### migration_084_alter_existing_tables.sql

```sql
ALTER TABLE projects ADD COLUMN workflow_template_id BIGINT REFERENCES workflows(id);
ALTER TABLE project_files ADD COLUMN node_execution_id BIGINT REFERENCES node_executions(id);
```

### migration_086_enable_realtime.sql

```sql
ALTER PUBLICATION supabase_realtime ADD TABLE project_messages;
ALTER PUBLICATION supabase_realtime ADD TABLE node_executions;
ALTER PUBLICATION supabase_realtime ADD TABLE workflow_instances;
```

## RLS 策略

每张新表都需要 Row Level Security：
- workflows: 团队成员可读，admin 可写
- workflow_nodes/edges: 跟随 workflow 权限
- workflow_instances: 项目成员可读，项目 admin/editor 可写
- node_executions: 项目成员可读，assignee 和 admin 可写
- capabilities: 所有认证用户可读，只有系统管理员可写
- project_messages: 项目成员可读写

## 验收标准

- [ ] 所有 migration 可顺序执行，无报错
- [ ] 所有外键关系正确
- [ ] 所有索引已创建
- [ ] RLS 策略已设置
- [ ] Realtime 已启用
- [ ] 可在 Supabase Dashboard 中看到新表
