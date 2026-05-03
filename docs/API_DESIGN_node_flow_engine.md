# MediaHub API 设计 — 节点流引擎

## 基础约定

- 前缀：`/api/v1/`
- 认证：`Authorization: Bearer <supabase_jwt>`
- 响应格式：JSON
- ID 类型：BIGINT (Snowflake)
- 分页：`?page=1&page_size=20`

---

## 1. 工作流模板 API

### GET /workflows
获取工作流模板列表（含团队模板和系统预设模板）

Query: `?team_id=xxx&is_template=true`

Response:
```json
[{
    "id": "123",
    "name": "抖音内容生产",
    "description": "...",
    "team_id": "456",
    "is_template": true,
    "node_count": 10,
    "created_at": "..."
}]
```

### POST /workflows
创建工作流模板

Body:
```json
{
    "name": "我的工作流",
    "description": "...",
    "team_id": "456",
    "nodes": [
        {"name": "选题", "node_type": "ai_agent", "node_group": "preparing", "role_name": "编导", "capability_id": "1", "position_x": 0, "position_y": 0, "sort_order": 1}
    ],
    "edges": [
        {"source_node_index": 0, "target_node_index": 1}
    ]
}
```

### GET /workflows/{id}
获取模板详情（含完整 nodes + edges）

### PUT /workflows/{id}
更新模板（全量更新 nodes + edges）

### DELETE /workflows/{id}
删除模板（软删除，检查是否有 active 实例引用）

### POST /workflows/{id}/duplicate
复制模板

---

## 2. 工作流实例 API

### GET /projects/{pid}/instances
获取项目下的实例列表

Query: `?status=active&priority=high&assignee_id=xxx`

Response:
```json
[{
    "id": "789",
    "title": "抖音视频 #001",
    "sequence_id": 1,
    "status": "active",
    "priority": "high",
    "current_nodes": [{"id": "...", "name": "剪辑", "node_type": "human"}],
    "progress": {"total": 10, "completed": 5},
    "start_date": "2026-03-01",
    "target_date": "2026-03-07",
    "created_by": "...",
    "created_at": "..."
}]
```

### POST /projects/{pid}/instances
创建新实例（发起一条内容生产）

Body:
```json
{
    "workflow_id": "123",
    "title": "抖音视频 #002",
    "priority": "high",
    "start_date": "2026-03-01",
    "target_date": "2026-03-07"
}
```

逻辑：
1. 基于 workflow_id 复制模板的 nodes 和 edges（快照）
2. 生成 sequence_id（pg_advisory_xact_lock）
3. 创建第一个节点的 node_execution（status=pending）
4. 发送聊天通知

### GET /instances/{id}
获取实例详情（含完整流程图 + 所有执行记录）

Response:
```json
{
    "id": "789",
    "title": "...",
    "status": "active",
    "workflow": {
        "nodes": [...],
        "edges": [...]
    },
    "executions": [
        {"node_id": "1", "node_name": "选题", "status": "completed", "output": {...}},
        {"node_id": "2", "node_name": "脚本", "status": "in_progress", "assignee": {...}}
    ],
    "current_node_ids": ["2"]
}
```

### POST /instances/{id}/advance
推进到下一节点

Body:
```json
{
    "output": {
        "script_url": "https://...",
        "notes": "脚本已完成"
    }
}
```

逻辑：
1. 验证当前用户有权限操作当前节点
2. 检查 required_fields 是否满足
3. 当前 execution → completed
4. 查找 edges，匹配条件分支
5. 创建下一节点的 execution（pending）
6. 如果下一节点是 AI 且 auto_advance → 触发 Agent
7. 发送聊天通知

### POST /instances/{id}/reject
驳回回退

Body:
```json
{
    "reason": "画面质量不达标，请重新剪辑",
    "target_node_id": null  // null = 回退上一步
}
```

### POST /instances/{id}/skip
跳过当前节点（需要 admin 权限）

### PUT /instances/{id}
更新实例信息（标题、优先级、日期等）

### PUT /instances/{id}/status
更新状态（暂停/取消/恢复）

---

## 3. 节点执行 API

### GET /instances/{iid}/executions
获取实例的所有执行记录（按 sort_order 排序）

### PUT /executions/{id}
更新执行记录（填写产出物、分配执行人等）

Body:
```json
{
    "assignee_id": "user_123",
    "output": {"file_ids": ["f1", "f2"]},
    "notes": "已完成拍摄"
}
```

### POST /executions/{id}/trigger-agent
手动触发 AI Agent 执行

逻辑：
1. 检查节点类型是否为 ai_agent
2. 查找绑定的 capability
3. 检查积分余额（QuotaCheck）
4. 扣除积分
5. 调用 Agent → 创建 unified_task
6. 异步等待结果回写

Response:
```json
{
    "task_id": "uuid",
    "points_consumed": 10,
    "status": "running"
}
```

---

## 4. 能力模块 API

### GET /capabilities
获取能力模块列表

Query: `?type=ai_agent&is_active=true`

### GET /capabilities/{id}
获取能力详情

### POST /capabilities（admin only）
注册新能力模块

### PUT /capabilities/{id}（admin only）
更新能力配置

---

## 5. 项目消息 API

### GET /projects/{pid}/messages
获取消息列表（分页，最新在前）

Query: `?page=1&page_size=50&since=<timestamp>`

### POST /projects/{pid}/messages
发送消息

Body:
```json
{
    "content": "这个选题不错，我们推进吧",
    "message_type": "text",
    "reply_to": null,
    "mentions": ["user_id_1"]
}
```

### PUT /messages/{id}/pin
置顶/取消置顶

---

## 6. 排期 API

### GET /schedule/calendar
获取日历事件

Query: `?team_id=xxx&start=2026-03-01&end=2026-03-31`

Response:
```json
[{
    "instance_id": "789",
    "title": "抖音视频 #001",
    "start_date": "2026-03-01",
    "target_date": "2026-03-07",
    "status": "active",
    "current_node": "剪辑",
    "priority": "high"
}]
```

### GET /schedule/workload
获取人员负载

Query: `?team_id=xxx`

Response:
```json
[{
    "user_id": "user_123",
    "user_name": "张三",
    "active_count": 3,
    "pending_count": 2,
    "completed_this_week": 5,
    "load_level": "high"  // low/medium/high/overload
}]
```
