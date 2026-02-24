# MediaHub 数据库设计 — 节点流引擎

## 新增表（6张）

### 1. workflows（工作流模板）

```sql
CREATE TABLE workflows (
    id BIGINT PRIMARY KEY,  -- Snowflake
    name VARCHAR(100) NOT NULL,
    description TEXT,
    team_id BIGINT REFERENCES teams(id) ON DELETE SET NULL,
    created_by UUID NOT NULL REFERENCES auth.users(id),
    is_template BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    version INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_workflows_team ON workflows(team_id);
CREATE INDEX idx_workflows_template ON workflows(is_template) WHERE is_template = true;
```

### 2. workflow_nodes（节点定义）

```sql
CREATE TYPE node_type AS ENUM ('human', 'ai_agent', 'api', 'approval');
CREATE TYPE node_group AS ENUM ('preparing', 'in_progress', 'reviewing', 'completed');

CREATE TABLE workflow_nodes (
    id BIGINT PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    node_type node_type NOT NULL DEFAULT 'human',
    node_group node_group NOT NULL DEFAULT 'in_progress',
    capability_id BIGINT REFERENCES capabilities(id) ON DELETE SET NULL,
    role_name VARCHAR(50),
    position_x FLOAT DEFAULT 0,
    position_y FLOAT DEFAULT 0,
    config JSONB DEFAULT '{}',
    sort_order FLOAT DEFAULT 65535,
    auto_advance BOOLEAN DEFAULT false,
    timeout_hours INT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_workflow_nodes_workflow ON workflow_nodes(workflow_id);
```

**config JSONB 结构：**
```json
{
    "required_fields": ["script_url", "priority"],
    "approval_count": 1,
    "ai_prompt_template": "根据以下选题生成脚本...",
    "output_schema": {
        "script_url": "string",
        "word_count": "number"
    }
}
```

### 3. workflow_edges（节点连线）

```sql
CREATE TABLE workflow_edges (
    id BIGINT PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    source_node_id BIGINT NOT NULL REFERENCES workflow_nodes(id) ON DELETE CASCADE,
    target_node_id BIGINT NOT NULL REFERENCES workflow_nodes(id) ON DELETE CASCADE,
    condition JSONB,  -- NULL = 无条件直接流转
    label VARCHAR(50),
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_workflow_edges_workflow ON workflow_edges(workflow_id);
CREATE INDEX idx_workflow_edges_source ON workflow_edges(source_node_id);
```

**condition JSONB 结构：**
```json
{
    "field": "priority",
    "operator": "eq",
    "value": "urgent"
}
```

### 4. workflow_instances（工作流实例）

```sql
CREATE TYPE instance_status AS ENUM ('active', 'completed', 'cancelled', 'paused');
CREATE TYPE priority_level AS ENUM ('urgent', 'high', 'medium', 'low', 'none');

CREATE TABLE workflow_instances (
    id BIGINT PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflows(id),
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    sequence_id INT NOT NULL DEFAULT 1,
    status instance_status DEFAULT 'active',
    current_node_ids BIGINT[] DEFAULT '{}',
    priority priority_level DEFAULT 'medium',
    start_date DATE,
    target_date DATE,
    completed_at TIMESTAMPTZ,
    created_by UUID NOT NULL REFERENCES auth.users(id),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_instances_project ON workflow_instances(project_id);
CREATE INDEX idx_instances_status ON workflow_instances(status);
CREATE UNIQUE INDEX idx_instances_sequence ON workflow_instances(project_id, sequence_id);
```

**sequence_id 生成逻辑（参考Plane）：**
```sql
-- 在 INSERT 触发器或应用层使用 advisory lock
SELECT pg_advisory_xact_lock(hashtext(project_id::text));
SELECT COALESCE(MAX(sequence_id), 0) + 1 FROM workflow_instances WHERE project_id = $1;
```

### 5. node_executions（节点执行记录）

```sql
CREATE TYPE execution_status AS ENUM ('pending', 'in_progress', 'completed', 'skipped', 'rejected');

CREATE TABLE node_executions (
    id BIGINT PRIMARY KEY,
    instance_id BIGINT NOT NULL REFERENCES workflow_instances(id) ON DELETE CASCADE,
    node_id BIGINT NOT NULL REFERENCES workflow_nodes(id),
    execution_round INT DEFAULT 1,
    assignee_id UUID REFERENCES auth.users(id),
    status execution_status DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    output JSONB DEFAULT '{}',
    agent_task_id UUID REFERENCES unified_tasks(id),
    points_consumed INT DEFAULT 0,
    reviewed_by UUID REFERENCES auth.users(id),
    reject_reason TEXT,
    notes TEXT,
    sort_order FLOAT DEFAULT 65535,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_executions_instance ON node_executions(instance_id);
CREATE INDEX idx_executions_assignee ON node_executions(assignee_id);
CREATE INDEX idx_executions_status ON node_executions(status);
```

### 6. capabilities（能力模块注册）

```sql
CREATE TYPE capability_type AS ENUM ('ai_agent', 'api', 'internal');

CREATE TABLE capabilities (
    id BIGINT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(50) UNIQUE NOT NULL,
    type capability_type NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}',
    icon VARCHAR(50),
    action_type VARCHAR(50),  -- 关联 point_pricing 的定价
    is_builtin BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX idx_capabilities_slug ON capabilities(slug);
```

## 现有表改造

### projects 表

```sql
ALTER TABLE projects ADD COLUMN workflow_template_id BIGINT REFERENCES workflows(id);
```

### project_files 表

```sql
ALTER TABLE project_files ADD COLUMN node_execution_id BIGINT REFERENCES node_executions(id);
```

## 新增聊天表

### project_messages

```sql
CREATE TYPE message_sender_type AS ENUM ('user', 'system', 'agent');
CREATE TYPE message_type AS ENUM ('text', 'file', 'node_update', 'agent_output');

CREATE TABLE project_messages (
    id BIGINT PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sender_id UUID REFERENCES auth.users(id),
    sender_type message_sender_type DEFAULT 'user',
    content TEXT NOT NULL,
    message_type message_type DEFAULT 'text',
    metadata JSONB DEFAULT '{}',
    reply_to BIGINT REFERENCES project_messages(id),
    mentions UUID[] DEFAULT '{}',
    is_pinned BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_messages_project ON project_messages(project_id);
CREATE INDEX idx_messages_created ON project_messages(project_id, created_at DESC);
```

## 启用 Realtime

```sql
-- 为聊天和执行状态启用 Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE project_messages;
ALTER PUBLICATION supabase_realtime ADD TABLE node_executions;
ALTER PUBLICATION supabase_realtime ADD TABLE workflow_instances;
```

## Migration 顺序

```
080 → capabilities（被 workflow_nodes 引用，必须先建）
081 → workflows + workflow_nodes + workflow_edges
082 → workflow_instances + node_executions
083 → project_messages
084 → ALTER projects + ALTER project_files
085 → seed 预设模板和内置能力数据
086 → 启用 Realtime
087 → 迁移现有 project_tasks 数据（如有）
```
