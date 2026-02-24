# Task 02: 工作流模板 CRUD API

## 目标

实现工作流模板的创建、读取、更新、删除 API，包括节点和连线的管理。

## 依赖

- Task 01 完成（数据库表已创建）
- 现有代码参考：`app/api/projects_router.py`, `app/services/projects_service.py`, `app/repositories/projects_repository.py`

## 输出文件

```
backend/app/
├── api/workflow_router.py           # API 路由
├── services/workflow_service.py     # 业务逻辑
├── repositories/workflow_repository.py  # 数据访问
└── schemas/workflow.py              # Pydantic 模型
```

## Pydantic Schemas

### workflow.py

```python
class NodeCreate(BaseModel):
    name: str
    node_type: str  # human/ai_agent/api/approval
    node_group: str = "in_progress"  # preparing/in_progress/reviewing/completed
    capability_id: Optional[str] = None
    role_name: Optional[str] = None
    position_x: float = 0
    position_y: float = 0
    config: dict = {}
    sort_order: float = 65535
    auto_advance: bool = False
    timeout_hours: Optional[int] = None

class EdgeCreate(BaseModel):
    source_node_id: str  # 或用 index 引用
    target_node_id: str
    condition: Optional[dict] = None
    label: Optional[str] = None

class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    team_id: Optional[str] = None
    nodes: list[NodeCreate] = []
    edges: list[EdgeCreate] = []

class WorkflowUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    nodes: Optional[list[NodeCreate]] = None
    edges: Optional[list[EdgeCreate]] = None

class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    team_id: Optional[str]
    is_template: bool
    is_active: bool
    node_count: int
    created_at: datetime
    updated_at: datetime

class WorkflowDetailResponse(WorkflowResponse):
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/workflows | 列表（筛选 team_id, is_template） |
| POST | /api/v1/workflows | 创建（含 nodes + edges） |
| GET | /api/v1/workflows/{id} | 详情（含 nodes + edges） |
| PUT | /api/v1/workflows/{id} | 更新（全量替换 nodes + edges） |
| DELETE | /api/v1/workflows/{id} | 删除（检查 active 实例） |
| POST | /api/v1/workflows/{id}/duplicate | 复制 |

## 业务逻辑

### 创建工作流
1. 创建 workflow 记录
2. 批量创建 nodes（生成 Snowflake ID）
3. 批量创建 edges（source/target 引用 node ID）
4. 返回完整 workflow 含 nodes + edges

### 更新工作流
1. 检查是否有 active 实例（有则拒绝更新，需要新建版本）
2. 删除旧的 nodes + edges
3. 重新创建 nodes + edges
4. version + 1

### 删除工作流
1. 检查是否有 active 实例引用
2. 有 → 返回 409 Conflict
3. 无 → 软删除

### 复制工作流
1. 复制 workflow（name 加 "(Copy)"）
2. 复制所有 nodes（新 ID）
3. 复制所有 edges（映射新 node ID）

## 注册路由

在 `app/api/__init__.py` 中注册：
```python
from app.api.workflow_router import router as workflow_router
app.include_router(workflow_router, prefix="/api/v1", tags=["workflows"])
```

## 验收标准

- [ ] GET /workflows 返回列表，支持 team_id 和 is_template 筛选
- [ ] POST /workflows 可创建含 nodes + edges 的完整工作流
- [ ] GET /workflows/{id} 返回完整详情
- [ ] PUT /workflows/{id} 可更新（有 active 实例时拒绝）
- [ ] DELETE /workflows/{id} 软删除（有 active 实例时 409）
- [ ] POST /workflows/{id}/duplicate 正确复制
- [ ] 所有端点需要认证
- [ ] 权限：团队 admin 可写，成员可读
