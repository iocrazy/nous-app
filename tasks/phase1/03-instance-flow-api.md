# Task 03: 工作流实例 + 流转 API

## 目标

实现工作流实例的创建、查询和节点流转（前进/回退/跳过）API。这是节点流引擎的核心运行时逻辑。

## 依赖

- Task 01（数据库表）
- Task 02（工作流模板 CRUD）

## 输出文件

```
backend/app/
├── api/instance_router.py
├── services/instance_service.py
├── repositories/instance_repository.py
└── schemas/instance.py
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/projects/{pid}/instances | 项目下实例列表 |
| POST | /api/v1/projects/{pid}/instances | 创建实例 |
| GET | /api/v1/instances/{id} | 实例详情（含流程图+执行记录） |
| PUT | /api/v1/instances/{id} | 更新实例信息 |
| PUT | /api/v1/instances/{id}/status | 更新状态（暂停/取消/恢复） |
| POST | /api/v1/instances/{id}/advance | 推进到下一节点 |
| POST | /api/v1/instances/{id}/reject | 驳回回退 |
| POST | /api/v1/instances/{id}/skip | 跳过（需 admin） |
| GET | /api/v1/instances/{iid}/executions | 获取执行记录 |
| PUT | /api/v1/executions/{id} | 更新执行记录 |

## 核心业务逻辑

### 创建实例 (POST /projects/{pid}/instances)

```python
async def create_instance(project_id, workflow_id, title, priority, ...):
    # 1. 获取工作流模板（含 nodes + edges）
    workflow = await workflow_repo.get_with_details(workflow_id)
    
    # 2. 生成 sequence_id（并发安全）
    async with advisory_lock(project_id):
        last_seq = await instance_repo.get_max_sequence(project_id)
        sequence_id = (last_seq or 0) + 1
    
    # 3. 创建 instance
    instance = await instance_repo.create({
        "workflow_id": workflow_id,
        "project_id": project_id,
        "title": title,
        "sequence_id": sequence_id,
        "priority": priority,
        "current_node_ids": [first_node.id],
        ...
    })
    
    # 4. 为第一个节点创建 execution（status=pending）
    await execution_repo.create({
        "instance_id": instance.id,
        "node_id": first_node.id,
        "status": "pending"
    })
    
    # 5. 发送聊天通知
    await send_system_message(project_id, f"📋 新内容生产启动: {title}")
    
    return instance
```

### 推进节点 (POST /instances/{id}/advance)

```python
async def advance_instance(instance_id, user_id, output):
    # 1. 获取实例和当前节点
    instance = await instance_repo.get(instance_id)
    current_execution = await execution_repo.get_current(instance_id)
    current_node = await node_repo.get(current_execution.node_id)
    
    # 2. 权限检查：当前用户是否有权操作
    assert user_id == current_execution.assignee_id or is_admin(user_id)
    
    # 3. 检查 required_fields
    required = current_node.config.get("required_fields", [])
    for field in required:
        assert field in output, f"Missing required field: {field}"
    
    # 4. 更新当前 execution → completed
    await execution_repo.update(current_execution.id, {
        "status": "completed",
        "completed_at": now(),
        "output": output
    })
    
    # 5. 查找下一个节点（匹配 edge 条件）
    edges = await edge_repo.get_outgoing(current_node.id)
    next_node = None
    for edge in edges:
        if edge.condition is None or evaluate_condition(edge.condition, output):
            next_node = await node_repo.get(edge.target_node_id)
            break
    
    # 6. 如果没有下一节点 → 实例完成
    if next_node is None:
        await instance_repo.update(instance_id, {
            "status": "completed",
            "completed_at": now(),
            "current_node_ids": []
        })
        await send_system_message(project_id, f"🎉 {instance.title} 已全部完成")
        return
    
    # 7. 创建下一节点的 execution
    next_execution = await execution_repo.create({
        "instance_id": instance_id,
        "node_id": next_node.id,
        "status": "pending"
    })
    
    # 8. 更新 instance.current_node_ids
    await instance_repo.update(instance_id, {
        "current_node_ids": [next_node.id]
    })
    
    # 9. 发送通知
    await send_system_message(project_id,
        f"✅ [{current_node.name}] 完成，流转到 [{next_node.name}]",
        mentions=[next_execution.assignee_id]
    )
    
    # 10. 如果下一节点是 AI 且 auto_advance → 触发 Agent
    if next_node.node_type == "ai_agent" and next_node.auto_advance:
        await trigger_agent_execution(next_execution.id)
```

### 驳回回退 (POST /instances/{id}/reject)

```python
async def reject_instance(instance_id, user_id, reason, target_node_id=None):
    # 1. 当前 execution → rejected
    current_execution = await execution_repo.get_current(instance_id)
    await execution_repo.update(current_execution.id, {
        "status": "rejected",
        "reject_reason": reason,
        "reviewed_by": user_id
    })
    
    # 2. 找到上一个节点（如果 target_node_id 为空）
    if target_node_id is None:
        edges = await edge_repo.get_incoming(current_execution.node_id)
        prev_node_id = edges[0].source_node_id
    else:
        prev_node_id = target_node_id
    
    # 3. 创建新的 execution（round + 1）
    last_round = await execution_repo.get_max_round(instance_id, prev_node_id)
    await execution_repo.create({
        "instance_id": instance_id,
        "node_id": prev_node_id,
        "execution_round": last_round + 1,
        "status": "pending"
    })
    
    # 4. 更新 current_node_ids
    await instance_repo.update(instance_id, {
        "current_node_ids": [prev_node_id]
    })
    
    # 5. 通知
    prev_node = await node_repo.get(prev_node_id)
    await send_system_message(project_id,
        f"↩️ [{current_node.name}] 被驳回: {reason}",
        mentions=[prev_execution.assignee_id]
    )
```

### 条件评估

```python
def evaluate_condition(condition: dict, output: dict) -> bool:
    field = condition["field"]
    operator = condition["operator"]
    value = condition["value"]
    actual = output.get(field)
    
    if operator == "eq": return actual == value
    if operator == "neq": return actual != value
    if operator == "gt": return actual > value
    if operator == "lt": return actual < value
    if operator == "in": return actual in value
    if operator == "contains": return value in str(actual)
    return False
```

## 验收标准

- [ ] 创建实例后，第一个节点自动创建 pending execution
- [ ] advance 正确检查 required_fields
- [ ] advance 正确匹配条件分支
- [ ] 最后一个节点完成后，实例状态变为 completed
- [ ] reject 正确创建新 execution（round + 1）
- [ ] skip 需要 admin 权限
- [ ] 所有操作发送聊天通知
- [ ] AI 节点 auto_advance 可自动触发
- [ ] sequence_id 并发安全（advisory lock）
