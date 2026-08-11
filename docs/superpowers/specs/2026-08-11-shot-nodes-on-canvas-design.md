# 分镜画布（Shot Nodes on Canvas）设计

2026-08-11 · 用户拍板「按 L 立项」:镜头作为节点进**现有智能画布**,双向同步 `script_shots`。
取代此前讨论的 M 方案(StoryboardView 搬家)——发现画布已有 `shot` 节点类型后,正确路线
是接血管而不是搬卡片板。设计稿依据:24b61005 视图二「底层仍是总画布,每个镜头是一个节点」。

## 0. 已核实的现状(canvas-subsystem-map 调查)

- 画布引擎:React Flow(`@xyflow/react`),11 种节点类型注册于
  `frontend/features/canvas-core/smart/nodes/registry.ts:19-31`,**`shot` 类型已存在**
  (`ShotNodeView.tsx`)但是孤立的:`ShotNodeData = {title, reference_resource_ids, notes}`
  (`smart/types.ts:46`),与 `script_shots` 零关联。
- 持久化:一张画布一行,节点存 `canvases.nodes_json` JSONB 数组(mig 280),后端透传不校验;
  debounced autosave + `base_updated_at` 乐观锁 409。
- 外部实体绑定先例:`CharacterNodeData.character_id` / `LibEntityNodeData.entity_id`;
  `canvas_resource_refs`(mig 290)是节点→资源引用的派生索引。
- 生成链路:`POST /canvases/{id}/generations` → DBOS `canvas_generation_workflow` →
  `generated_media` → 前端轮询回填节点 `data`(`patchNode` 按节点 id,无类型门)。
- 画布归属:`canvases.project_id`,无集概念;权限 `_gate_canvas_read/write`(project_members)。
- shot 写入三车道:人 REST(`script_shots_router`,不进账本)/agent
  (`scoped_script_gateway`,同事务进 `script_shot_ops` + 归属列,mig 415)/生成 workflow
  (直写媒体 URL+status)。**415 账本设计刻意排除普通人写**。

## 1. 用户拍板决策

| 决策点 | 结论 |
|--------|------|
| 路线 | L:镜头进现有画布(非 M 卡片板搬家) |
| 画布归属 | **每集一张系统「分镜画布」**,分镜页画布 tab 直达当前集;侧栏画布库也能打开 |
| 删除语义 | **删节点=仅移出画布,镜头保留**;真删去分镜列表(有既有回收路径) |
| 冗余清理 | 「删掉的就弃用」:编辑器分镜视图整体退役,不留冗余代码/模块 |
| 卡顿根因 | 剧本/节拍同壳内切换 vs 分镜跨模块懒加载重挂载 → keep-alive + 预加载 |

设计者定的安全默认(用户未反对):

- **绑定方式**:扩展现有 `ShotNodeData` 加可空 `shot_id: string | null`——null = 旧孤立
  草稿节点(兼容存量),非空 = 绑定 `script_shots` 行。不新增节点类型。
- **顺序语义**:画布位置**不**驱动 `sort_order`——空间摆放自由,镜头顺序仍由分镜列表/镜号
  管理(位置≠顺序,避免误拖改序)。
- **生成回流**:画布上对绑定 shot 节点跑生成 → 结果自动回填 `script_shots.image_url`
  (走生成 workflow 既有 `update_status` 车道,B4 surface 回流纪律一并触发)。

## 2. 每集分镜画布(系统画布)

- `canvases` 加列:`episode_id BIGINT NULL REFERENCES episodes ON DELETE CASCADE` +
  `kind TEXT NOT NULL DEFAULT 'free'` CHECK (`'free'|'storyboard'`);partial unique
  index `(project_id, episode_id) WHERE kind='storyboard'`——每集至多一张分镜画布。
  migration 取号实施时 fetch 复核。
- **惰性创建**:首次进入该集分镜页画布 tab 时后端 `GET /canvases/storyboard?episode_id=`
  get-or-create(幂等);不随建集扇出(空集不欠画布)。
- 分镜画布在侧栏画布库照常可见(名称如 `EP1 · Storyboard`,kind 徽章),从库里打开
  与从分镜页 tab 打开是同一张。
- 权限:沿用画布 project_members 闸;分镜画布不因 kind 特殊化(镜头数据的权限在 shot
  写端点上,见 §4)。

## 3. 镜头节点(绑定态)

- **自动入驻**:打开分镜画布时,前端按当前集拉 scenes+shots,对缺席的镜头自动补节点
  (按场分列的默认布局:场次 x 偏移、场内竖排),已删镜头的悬空节点标记「已失效」并
  在下次保存时清除;位置一经用户拖动即持久(nodes_json)。
- **节点渲染**(升级 `ShotNodeView`):镜号 + 景别/角度/运动/焦段 chips + 描述(可编辑)
  + 画面帧槽(image_url/缩略)+「生成」按钮 + Storyboard/Lens 双区(设计稿视图二形态)。
- **编辑回写**:节点上的参数/描述编辑 → PATCH `script_shots`(REST 车道,复用
  `script_shots_router` 既有端点;不进 agent 账本,与 415 设计一致)。乐观更新,失败
  revert-by-refetch(IA 重构同款纪律),403 类型化回显。
- **读侧同步**:分镜页视图一/列表与画布 tab 共享数据刷新(镜头在别处增删改后,画布
  下次聚焦/挂载时对账:新镜头补节点、消失镜头标失效)。实时双向推送(Realtime)不在
  本期(§9)。
- **删除**:节点删除仅从 nodes_json 移除(拍板);节点卡菜单提供「在分镜列表中删除」
  跳转,不在画布上直接删数据。
- **生成**:绑定节点的「生成」走画布既有 generations 链路,workflow 完成回调在
  `generated_media` 落库同时写 `script_shots.image_url` + `status`(生成 workflow
  车道,`fire_surface_sync_for_shot` 既有回流保持)。
- 孤立 shot 节点(shot_id=null)保留现有行为,节点菜单提供「转正为镜头」(在当前集
  指定场次创建 script_shots 行并绑定)——一行入口,不做批量。

## 4. 后端接口

- `GET /api/v1/canvases/storyboard?episode_id=` — get-or-create 该集分镜画布,返回画布 doc。
- shot 编辑复用既有 `script_shots_router` PATCH(字段白名单/权限不变)。
- 生成回填:`canvas_generation_workflow` 增加「目标节点绑定 shot 时同步写
  `script_shots.image_url/status`」分支(经 repository,禁裸 SQL)。
- 不新增 shot 写车道;`script_shot_ops` 账本不记画布人写(415 设计边界)。

## 5. 分镜页画布 tab 接线

- 画布 tab 由挂 `WorkspaceCanvas`(素材画布库)改为**直达当前集分镜画布**(嵌入式
  React Flow 画布,复用 canvas-core 的画布路由组件,以 canvasId 挂载)。
- 视图一镜头卡点击 / URL `?view=canvas&shot=` / Agent 面板镜头摘要(shotFocusBus)→
  切画布 tab + 画布视口聚焦该 shot 节点(React Flow `fitView`/`setCenter` 到节点)。
  `shotFocusBus` 订阅者从 EditorShell 迁到分镜画布组件。
- 侧栏「画布」模块(素材画布库)不动。

## 6. 编辑器分镜视图退役(冗余清理)

- `EditorShell` 的 `storyboard` rail view 整体删除(`RailView` 类型、RailModules 入口、
  `StoryboardView.tsx` 及其独占子组件、`onShotFocus` 订阅、pendingFocusShotId 逻辑)。
- 场次卡「打开」深链(`handleOpenWorkView('storyboard',{sceneId})` → 编辑器)改落
  分镜页(scene 定位到画布该场次列附近或视图一场次列,实施时按聚焦能力取近者)。
- 编辑器只剩剧本/大纲/封面;所有被替代路由与死代码删净,不留 fixme。
- e2e/单测跟改(EditorShell storyboard 相关用例迁移或删除,保意图)。

## 7. keep-alive + 预加载(卡顿根治)

- 编辑器(studio)与分镜页两大工作面**保持挂载、显隐切换**(`display:none`,不卸载):
  剧本↔分镜切换与剧本↔节拍同级瞬时。实现在 ProjectWorkspace 渲染层:两模块改为
  持久挂载容器,activeModule 只控显隐;切集仍按现逻辑重挂载(数据换血)。
- 分镜页/画布 chunk 在进入工作区后 idle 预加载(`import()` on requestIdleCallback)。
- 内存代价明示:两棵大树常驻,接受(高频核心面)。

## 8. 附带修复(canvas 成为镜头主场的前置卫生)

- **Canvas 侧栏入口回归**(#1680 丢失,`Sidebar.tsx` + `CANVAS_NAV_ENABLED` 孤儿 flag,
  e2e canvas-nav:91 复活)。
- **WorkflowLibraryPicker 裁剪修复**(`CanvasComposer.tsx:612` overflow-x-auto 隐式裁 Y;
  portal 化弹层,DateRangePopover 同款方案;e2e canvas-feel:228 复活)。
- **节点卡交付物链接**(#1787 遗留):EpisodeNodeCard 交付物 fact 变链接 → Stage Board。

## 9. 范围外(YAGNI)

- Realtime 双向推送(画布↔分镜列表实时互推;本期靠挂载/聚焦对账)
- 画布位置驱动 sort_order;节点连线表达镜头顺序
- 素材画布与分镜画布之间的节点互拖
- 旧孤立 shot 节点的批量转正/迁移
- 多集镜头混排画布

## 10. 测试

- 后端:get-or-create 幂等/每集唯一;生成回填 shot 分支;权限(project_members 闸)。
- 前端:自动入驻对账(新增/失效)、节点编辑回写+403 回退、删除仅移出、聚焦深链三入口、
  keep-alive 显隐不卸载(状态保持断言)、预加载触发;e2e:分镜页→画布→改描述→生成占位
  →回分镜列表数据一致;canvas-nav/canvas-feel 两条复活用例。
