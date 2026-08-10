# 项目工作区 IA 重构（Workspace IA Redesign）设计

2026-08-10 · 起因:三视图主工作面(#1757)把分镜面板叠上 Overview 后,用户以 12 张截图
反馈整套信息层级乱了。经四轮 UI 方案稿迭代(artifact `734ba665-7312-4509-84b5-5a882642ffb7`,
v4.2)定稿。分镜页本身的布局规范以原设计稿 artifact `24b61005-0db2-4fe0-9b2b-7b173be3df47`
视图一为准。

核心一句话:**总览展开看流程,节点信息卡按角色行内改,分镜独立成页,编辑集中到设置。**

## 0. 现状问题(机理已核实)

| 症状 | 机理 |
|------|------|
| 点侧栏「分镜」显示的却是剧本入口+阶段卡堆 | `handleOpenWorkView('storyboard')` 落到 Overview 并试图切 storyboard 视图,但当前节点 surface 是 script 时视图集里没有 storyboard,effect 把视图重置回 script(`ProjectWorkspace.tsx` ~L420/L545) |
| 总览堆满分镜内容 | `showSurfacePanel = showOverview && surfaceViews.length>0` 把三视图面板渲染在 Overview 上方,下面还有 WorkflowSection 全部阶段卡+Continue 卡+汇总行+四格 tile |
| 阶段卡全部铺开 | WorkflowSection 渲染 strip + 所有节点的详情卡 |
| 无处推进流程 | 编辑器内没有「完成阶段」入口 |
| 配置无处集中填 | 项目设置只有名称/公告/类型/分组,节点负责人/排期只能在阶段卡堆里改 |

## 1. 目标 IA(四层)

```
总览(集列表) ──点集行展开──▶ 集内流程条 ──点节点──▶ 单节点信息卡 ──跳转──▶ 工作面
                                                        Script → 剧本编辑器
                                                        Storyboard → 分镜三视图页
                                                        交付物节点 → 阶段板
```

## 2. 总览重构(手风琴)

- 总览 = 集列表头(`剧集 · N` + 「N 集等你回答」)+ 每集一行(EP 号/标题/`SC·SHOTS·CUTS`
  计数/分段进度条/状态)。
- **点集行原地展开**该集的流程条(手风琴,同时只开一个,再点收起);不跳页。
- 展开区 = 流程条(done 绿/当前 ochre 点/skip 划线置灰)+ **单节点信息卡**(流程条上点谁
  显示谁,默认当前节点)。
- **从总览删除**:三视图面板(`showSurfacePanel` 整块)、WorkflowSection 的全部阶段卡堆、
  Continue 卡、四格 SummaryTile(含写死 `—` 的 Canvas tile)、`bg-indigo-500` 按钮
  (K1 违规色)。WorkflowSection 的 strip 逻辑复用到展开区,阶段卡改为单卡。

## 3. 单节点信息卡

- **头部**:节点名 + 状态 + 右侧工作面入口按钮(Script→「打开剧本」/Storyboard→「进入分镜」/
  renders→「打开成片」/交付物节点→「打开阶段板」)。
- **信息行**(一行三项):负责人 / 排期 / 交付物(`名称 · N 个文件 ↗` 链接到阶段板归档)。
- **底部**:「在待办中打开」+ 权限提示灰字 + 「← 回退」「完成阶段 →」
  (推进操作保留在卡上,权限沿用现有 advance 角色闸,用户已拍板)。
- **行内编辑(按角色)**:
  - 有权者(见 §5):负责人/排期是可点控件——平时如文本,hover 出边框与 ⌄;
    空值渲染为虚线圆角「＋ 指派负责人」「＋ 设置排期」引导钮。
  - 无权者:纯文本(空值显示灰色「未指派/未设置」),无任何可点态。
- **排期控件 = 日历区间选择器**(新公共组件):月视图 + ‹ › 翻月 + 起止两格 + 区间高亮 +
  可跨月 + 清除;**React Portal 渲染到 body、fixed 定位**(方案稿实测:挂在卡内会被
  `overflow:hidden` 祖先裁剪);下方空间不足按实测高度翻转到锚点上方;外点/滚动关闭。

## 4. 分镜页独立成页

- 侧栏「分镜」与流程条 Storyboard 节点点击 → **直达分镜页**(新增 workspace module
  `storyboard`),不再借道 Overview。`handleOpenWorkView('storyboard')` 无 sceneId 分支
  改指向该模块;带 sceneId 的深链行为不变(已在 #1767 深链到 scene 级)。
- 页头:标题「分镜」+ 计数 + 分段控件「分镜 | 画布 | 分镜列表」**靠左**紧跟标题
  (与其他页一致),「显示选项/导出」贴右。
- **视图一·分镜按设计稿分列**:每列一场、固定窄列(设计稿 236px),列内 = 场记条 +
  Scene/I-E/Location/D-N 元数据格 + 打开/自动分镜 + 该场镜头卡竖排;列间横向滚动。
  现 EpisodeSceneBoard 的全宽横条布局废弃,组件改造保留其数据逻辑
  (read-only probe / Start Storyboard CTA / autoStoryboard 派发均不变)。
- **镜头卡点击 → 切到画布视图并聚焦该镜头节点**(复用 `shotFocusBus.requestShotFocus`,
  画布已支持)。
- 视图二(画布)/视图三(分镜列表)不动。
- Episode 3 类无剧本剧集:进分镜页显示既有 Start Storyboard 空态,不再出现「剧本入口」错位。

## 5. 权限模型

| 操作 | 谁可以 |
|------|--------|
| 剧集编排(集的增删/排序、节点结构增删改) | 仅**项目负责人**(`projects.owner_id`,resolve_effective_role 已认 owner→manager,#1742) |
| 节点负责人/排期/成员/背景说明 编辑 | 项目负责人 + **该集负责人** |
| 流程推进(完成阶段/回退) | 沿用现有 advance 角色闸,不变 |
| 查看 | 项目成员均可(只读态) |

- **「该集负责人」= 新增 `episodes.owner_id UUID NULL`**(用户拍板方案 A;migration 取号
  落地前 fetch 复核)。在设置 › 节点配置页指派(项目负责人可指派)。
- 后端:节点字段 PATCH 端点加权限检查(项目负责人 or `episodes.owner_id == 当前用户`);
  剧集编排端点(集/节点结构写操作)收紧到项目负责人。返回 403 带类型化错误码,
  前端以此渲染只读态(不是隐藏后靠后端兜底,是双向一致)。

## 6. 编辑器流程胶囊

剧本编辑器(studio 模式)顶栏右侧新增流程胶囊:`● <节点名> · <状态>` + 「完成阶段 →」。
数据来自当前集 workflow;点击走既有 requestAdvance 通道(含确认弹窗/角色闸)。
无 workflow 或交付物型节点当前不在本集游标上时不渲染。解决「写完剧本无处推进」。

## 7. 设置 · 节点配置

- 项目设置新增「节点配置」区(现设置面板只有名称/公告/类型/分组/删除)。
- 形态与工作区一致:顶部**节点条**(点谁配谁,skip 节点置灰不可选)+ 下方该节点表单:
  负责人 / 成员 / 排期(同 §3 日历组件)/ 交付物 / 背景说明。
- 顶部「EP1 ⌄」切集;**集负责人指派**也在此区(集级字段,节点条上方一行)。
- 总览信息卡的权限提示灰字深链到这里(带集+节点定位)。
- 权限同 §5;无权者看到只读表单。

## 8. 统一寻址(ID 全覆盖)

原则:**四层里每一个可点击/可跳转/可聚焦的东西,都必须以后端稳定 ID 寻址,并反映进
URL**——组件间传 ID 不传索引,刷新可恢复,链接可分享,设置/待办/Agent 面板的深链都拼
同一套参数。

### 8.1 实体 ID 底账(全部已存在,已核实)

| 层 | 实体 | ID 来源 | 现状 |
|----|------|---------|------|
| 项目 | project | `projects.id` | 路由级已有 |
| 剧集 | episode | `episodes.id` | ⚠️ 只存 localStorage,不进 URL |
| 流程节点 | node | `project_stage_nodes.id` | 仅 stage 模块带 `?node=` |
| 剧本 | script | `script_projects.id` | 内部解析,不进 URL(每集唯一,由 ep 推导,无需进) |
| 章节(编辑器画布) | chapter | script canvas chapter id(`sync_canvas` 按 id 增删改) | 编辑器内部 |
| 节拍 | beat | `script_beats.id`(含 `scene_ids` 关联) | 编辑器内部 |
| 场次 | scene | `script_scenes.id` | 深链已 id 化(#1767) |
| 剧本内容块(元素) | element | `content_json` 的 `element_id`(script_ops 账本键,undo/归属都靠它) | 编辑器/账本已 id 化 |
| 镜头 | shot | `script_shots.id` | shotFocusBus 已 id 化 |
| 分镜画布节点 | = shot | 画布节点本体就是 shot,无独立实体 | 已 id 化 |
| 成片/归档文件 | resource | `resources.id` | 已有 |
| 待办 | issue | `issues.id` | 已有 |

### 8.2 URL scheme(本重构统一收口)

```
?module=<overview|storyboard|script|...>   现有,保留
&ep=<episodes.id>        当前集(取代 localStorage 作为第一真相;localStorage 降级为无参时的默认值)
&node=<stage_nodes.id>   总览手风琴选中的节点 / stage 板的节点(现有语义合并)
&view=<board|canvas|shotlist>  分镜页三视图
&scene=<script_scenes.id>      分镜页定位场次列 / 编辑器深链场次
&shot=<script_shots.id>        画布聚焦镜头(进页后触发 shotFocusBus)
&tab=nodes                     设置页节点配置区(设置深链:module=settings&tab=nodes&ep=&node=)
```

- 总览手风琴:展开集 = `ep`,选中节点 = `node` → 「在设置中修改」深链、待办回跳、
  分享链接全部可拼。
- 所有 ID 在 URL/props 中一律**字符串**(Snowflake BIGINT 超 2^53,禁 `Number()`;
  fetch 层已有 bigIntSafeFetch,前端新代码不得引入数字转换)。
- 新组件契约:手风琴行、节点卡、场次列、镜头卡的 props/事件载荷全部携带实体 ID,
  禁用数组下标定位(场次重排/镜头增删后索引即失效)。

## 9. 接线点(已核实的代码位置)

| 变更 | 位置 |
|------|------|
| 手风琴总览 | `WorkspaceOverview.tsx` 重写;`EpisodeSummaryRow` 扩展为可展开行 |
| 单节点卡 | 新组件(WorkflowSection 阶段卡逻辑抽取单卡版) |
| 分镜页模块 | `ProjectWorkspace.tsx` 增 `storyboard` module;`handleOpenWorkView`/`handleSelectNode` 改路由;`showSurfacePanel` 块迁出 Overview |
| 场次分列布局 | `EpisodeSceneBoard.tsx` 布局改造(数据逻辑不动) |
| 日历组件 | 新公共组件(Portal + 区间选择),总览卡与设置页共用 |
| 编辑器胶囊 | `WorkspaceTopBar.tsx`(studio 模式已有 slate/workflow props 通道) |
| 设置节点配置 | 设置面板新增区块;`episodes.owner_id` migration + PATCH + 权限检查 |
| URL 寻址 | `ProjectWorkspace.tsx` searchParams 块(~L110-140)扩展 ep/node/view/scene/shot 参数 |
| e2e 跟改 | 现有 workflow/stage-board e2e 依赖 Overview 上的 strip 与阶段卡,需按集内展开跟改 |

## 10. 测试

- 前端组件测:手风琴单开互斥;节点卡三态(有权编辑/无权只读/空值 + 号);日历翻转与跨月;
  分镜页路由(侧栏/节点两个入口直达、无剧本空态);编辑器胶囊显隐。
- 后端:episodes.owner_id 权限矩阵(项目负责人/集负责人/普通成员 × 节点字段 PATCH/
  剧集编排端点)各 403/200;类型化错误码。
- e2e:总览展开→点节点→进工作面全链;设置节点配置改负责人后总览卡即时反映。

## 11. 范围外(YAGNI)

- 画布/分镜列表视图改动;Agent 面板(已收官)
- 排期日历的「具体时间/提醒」(参考截图里有,本期只做日期区间)
- 集负责人之外的更细粒度权限(节点级负责人已有字段,不再加节点级权限层)
- 移动端布局适配
