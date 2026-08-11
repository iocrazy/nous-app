# 画布子系统底账(2026-08-11 只读调查,分镜画布项目的事实依据)

1. **数据模型**:一张画布一行,节点存 `canvases.nodes_json` JSONB 数组(无逐节点行)。
   画布 id Snowflake BIGINT;节点 `id/type/position/data` 前端生成,后端透传不校验。
   → `supabase/migrations/280_canvas_core_schema.sql:45-59`, `backend/app/schemas/canvas.py:1-7,39`
2. **节点类型**(11 种):shot, media, llm, prompt, output, loop, timeline, group,
   character, location, prop。前端注册表 `SMART_NODE_TYPES`
   → `frontend/features/canvas-core/smart/nodes/registry.ts:19-31`。加/改类型要触碰:
   `SmartNodeType` 联合与 `*NodeData` 接口、`SMART_NODE_DEFAULT_WIDTH`、`canConnectSmart`
   (均在 `smart/types.ts`)、创建工厂、`CanvasComposer.tsx` 的 addNode switch/工具条、
   可能还有 `canvas_refs_repository.py`(资源引用派生索引)。
3. **前端架构**:React Flow(`@xyflow/react`);节点组件 `smart/nodes/*NodeView.tsx`;
   debounced autosave + `base_updated_at` 乐观锁(409 冲突);画布按项目归属,侧栏
   「画布」模块是画布文档的**图库列表**(`WorkspaceCanvas.tsx:1-14`,非嵌入画布),
   每张画布有自己的路由。store → `frontend/features/canvas-core/store/canvasCoreStore.ts`
4. **生成链路**:`POST /canvases/{id}/generations` → DBOS `canvas_generation_workflow`
   → 结果登记 `generated_media` → 前端轮询 `GET /canvases/generations/{task_id}`,
   经 `patchNode(id,{data})` 回填(按节点 id,无类型门——任意节点类型可挂生成结果)。
   `canvas_resource_refs`(mig 290)是节点→资源引用派生索引。
   → `backend/app/api/canvases_router.py:543-597`,
   `frontend/features/canvas-core/smart/genResume.ts:50,127,213`
5. **归属/权限**:`canvases.project_id` FK,无 team_id、无集概念;RLS 按 project_members,
   路由层 `_gate_canvas_read/write` 双保险 → `280_canvas_core_schema.sql:116-163`,
   `backend/app/models/canvas.py:9-10`
6. **两张已知票**:①Canvas 侧栏入口在 #1680 Module Control Center 重写时丢失,
   `CANVAS_NAV_ENABLED` flag 成孤儿 → `frontend/components/Sidebar.tsx`(对照旧 commit
   `c5b6e18e`),flag 在 `frontend/features/canvas-core/flags.ts`,e2e `canvas-nav.spec.ts:91`;
   ②`WorkflowLibraryPicker` 的 `absolute bottom-full` 弹层被 composer 工具条
   `overflow-x-auto`(隐式 overflow-y:auto)裁剪 → `CanvasComposer.tsx:612`,
   `WorkflowLibraryPicker.tsx:30`,e2e `canvas-feel.spec.ts:228`。
   外部实体绑定先例:`CharacterNodeData.character_id` / `LibEntityNodeData.entity_id`。
7. **shot 三写车道**:(a)人 REST `script_shots_router.py`(经 `script_shot_repository`,
   不进账本);(b)agent `scoped_script_gateway.py:806-949`(同事务进 `script_shot_ops`
   mig 415+归属列);(c)生成 workflow 直写媒体 URL/status。**415 账本刻意排除普通人写**
   ——画布人写走 (a) 车道;画布生成回填走 (c) 车道。
8. **现有 ShotNodeData 是孤立的**:`{title, reference_resource_ids, notes}`
   (`smart/types.ts:46`),无 shot_id;`ShotNodeView.tsx` 已存在。
9. **EditorShell 分镜接线**(将退役):`frontend/editor/components/EditorShell.tsx:534`
   `onShotFocus` 订阅(selectRailView('storyboard')+pendingFocusShotId);Generate 镜头板
   = `frontend/editor/storyboard/StoryboardView.tsx`,仅作为 editor storyboard rail 挂载。
