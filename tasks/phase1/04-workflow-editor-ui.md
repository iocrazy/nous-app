# Task 04: React Flow 工作流编辑器（前端）

## 目标

实现基于 React Flow 的可视化工作流编辑器，用户可拖拽创建节点、连线、配置节点属性。

## 依赖

- Task 02 完成（工作流 CRUD API 可用）
- 安装：`npm install @xyflow/react`

## 输出文件

```
frontend/
├── components/workflow/
│   ├── WorkflowEditor.tsx          # 主编辑器页面
│   ├── WorkflowCanvas.tsx          # React Flow 画布
│   ├── nodes/
│   │   ├── WorkflowNode.tsx        # 自定义节点组件
│   │   └── NodeConfigPanel.tsx     # 节点配置侧面板
│   ├── edges/
│   │   └── ConditionalEdge.tsx     # 条件连线组件
│   ├── toolbar/
│   │   ├── NodePalette.tsx         # 节点类型面板（拖拽源）
│   │   └── EditorToolbar.tsx       # 保存/撤销/模板选择
│   └── TemplateSelector.tsx        # 模板选择弹窗
├── services/workflowService.ts     # API 调用
└── types/workflow.ts               # TypeScript 类型
```

## 页面布局

```
┌──────────────────────────────────────────────┐
│ EditorToolbar [模板选择] [保存] [预览]          │
├────────┬─────────────────────────┬───────────┤
│ Node   │                         │ Node      │
│ Palette│     React Flow Canvas   │ Config    │
│        │                         │ Panel     │
│ [人工]  │  [选题] ──→ [脚本] ──→  │           │
│ [AI]   │     [素材] ──→ [剪辑]   │ 名称:___  │
│ [API]  │                         │ 类型:___  │
│ [审批]  │                         │ 角色:___  │
│        │                         │ 能力:___  │
└────────┴─────────────────────────┴───────────┘
```

## 核心交互

### 1. 添加节点
- 从左侧 NodePalette 拖拽节点类型到画布
- 或双击画布空白处弹出添加菜单
- 节点默认名称："新节点"，类型取决于拖拽的类型

### 2. 自定义节点样式
每种 node_type 不同颜色/图标：
- human → 蓝色 👤
- ai_agent → 紫色 🤖
- api → 绿色 🔌
- approval → 橙色 ✅

### 3. 连线
- 从节点右侧 handle 拖拽到另一个节点左侧 handle
- 可选添加条件标签

### 4. 节点配置面板
点击节点后右侧显示配置面板：
- 名称（文本输入）
- 类型（下拉：human/ai_agent/api/approval）
- 所属分组（下拉：preparing/in_progress/reviewing/completed）
- 负责角色（文本输入）
- 绑定能力模块（下拉，从 GET /capabilities 获取）
- 自动流转（开关）
- 超时时间（数字输入，小时）
- 必填字段（标签输入）

### 5. 保存
- 点击保存按钮 → POST/PUT /workflows
- 将 React Flow 的 nodes + edges 序列化为 API 格式
- position_x/y 从 React Flow 的 node.position 获取

### 6. 模板选择
- 创建工作流时可选择从预设模板开始
- GET /workflows?is_template=true 获取模板列表
- 选择后 POST /workflows/{id}/duplicate 复制

## 数据转换

### React Flow → API
```typescript
function serializeWorkflow(rfNodes: Node[], rfEdges: Edge[]): WorkflowCreate {
    return {
        nodes: rfNodes.map(n => ({
            name: n.data.name,
            node_type: n.data.nodeType,
            node_group: n.data.nodeGroup,
            capability_id: n.data.capabilityId,
            role_name: n.data.roleName,
            position_x: n.position.x,
            position_y: n.position.y,
            config: n.data.config,
            sort_order: n.data.sortOrder,
            auto_advance: n.data.autoAdvance,
            timeout_hours: n.data.timeoutHours,
        })),
        edges: rfEdges.map(e => ({
            source_node_id: e.source,
            target_node_id: e.target,
            condition: e.data?.condition,
            label: e.label,
        }))
    }
}
```

### API → React Flow
```typescript
function deserializeWorkflow(wf: WorkflowDetail): { nodes: Node[], edges: Edge[] } {
    return {
        nodes: wf.nodes.map(n => ({
            id: n.id,
            type: 'workflowNode',
            position: { x: n.position_x, y: n.position_y },
            data: {
                name: n.name,
                nodeType: n.node_type,
                nodeGroup: n.node_group,
                capabilityId: n.capability_id,
                roleName: n.role_name,
                config: n.config,
                sortOrder: n.sort_order,
                autoAdvance: n.auto_advance,
                timeoutHours: n.timeout_hours,
            }
        })),
        edges: wf.edges.map(e => ({
            id: e.id,
            source: e.source_node_id,
            target: e.target_node_id,
            label: e.label,
            data: { condition: e.condition }
        }))
    }
}
```

## 验收标准

- [ ] 可从左侧面板拖拽添加 4 种类型节点
- [ ] 节点可拖拽移动
- [ ] 节点间可连线
- [ ] 点击节点展示配置面板
- [ ] 可修改节点所有属性
- [ ] 可保存工作流到后端
- [ ] 可从模板创建
- [ ] 可删除节点和连线
- [ ] 不同类型节点有不同颜色/图标
