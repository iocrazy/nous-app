# P0 · `current_node_id` 读写点全量盘点

日期:2026-08-04 ｜ 分支:`feat/episode-level-workflow` ｜ 阶段:P0(无代码产出,阻塞 B1/B2)
上游:[结构层 spec](../specs/2026-08-04-episode-level-workflow-design.md) §4 / §9 ｜ [实施计划](2026-08-04-episode-workflow-and-agent-layer.md) P0

**本文是 B1/B2 的施工清单。** 每一行都是要么改、要么显式判定"不改"的点。

## 摘要

| 指标 | 数 |
|---|---|
| 生产代码读写点 | **24**(后端 14 / 前端 10),分布在 14 个文件 |
| 测试 / e2e 涉及文件 | **14**(后端 6 + 前端单测 4 + e2e 4) |
| `advance_service.py` 函数总数 | 15 |
| ↳ 需改签名/作用域 | **9** |
| ↳ 已是 node 粒度、可原样复用 | **6** |
| 数据现状 | `project_stage_nodes` 全库 11 行,全属项目 `291022264100262`(「个人项目测试 1」,4 集);`projects.current_node_id` 全库仅 1 行非空 = `334842286815938` |

**三个最容易踩空的点**(详见 §5):

1. **`parallel_group` / `sort_order` 跨集碰撞**(§5.1)—— 模板值原样拷进每集,`_build_groups` 会把不同剧集的节点并成一组,推进一集等于推进所有集。加 `episode_id` 列**解决不了**这个,所有分组/排序代码都得跟着加过滤。
2. **多集共用同一个交付物文件夹**(§5.4)—— `ensure_node_folder` 按名字复用,每集的 "Script" 节点会拿到**同一个** `folder_id`,`_deliverable_present` 的**主路径**因此被击穿:Ep1 交一个文件,Ep2/Ep3 的 gate 2 直接放行。
3. **`instantiate_from_template` 会静默吞掉第 2 集起的实例化**(§5.3)—— 幂等判据是"项目已有任何节点就不动",且 advisory lock 按 project_id 加,给 Ep2 建链会返回 Ep1 的节点且**不报错**。

(次一级但同样值得知道:`projects.current_node_id` **没有 FK**,悬空时 `_active_index` 静默 `return 0` 退回第一组,见 §5.5。)

---

## 1 · `current_node_id` 全部读写点

R = 读,W = 写,D = 定义(DDL/类型/契约)。

### 1.1 后端 — 生产代码(14 处)

| # | file:line | 类型 | 现在做什么 | 下沉后如何改 |
|---|---|---|---|---|
| B1 | `supabase/migrations/380_workflow_nodes_m1.sql:135` | D | `ALTER TABLE projects ADD COLUMN current_node_id BIGINT`(**无 FK、无索引**) | 新 migration 加 `episodes.current_node_id BIGINT`(建议带 `REFERENCES project_stage_nodes(id) ON DELETE SET NULL`,补上 380 欠的约束);`projects.current_node_id` 保留但停写,B6 清理 |
| B2 | `backend/app/models/teams.py:338` | D | `Projects.current_node_id` ORM 列 + comment | 新增 `Episodes.current_node_id`;`Projects.current_node_id` 的 comment 改成"legacy,M5 下沉到 episodes 后停用" |
| B3 | `backend/app/schemas/workflow.py:297` | D | `ProjectWorkflowOut.current_node_id: Optional[str]` | 语义从"项目游标"变为"本次查询的剧集游标";`GET /workflow` 需带 `episode_id` 入参。**保留字段名**可让前端改动最小,但要在 docstring 写清它现在是 episode-scoped |
| B4 | `backend/app/api/projects_router.py:380` | R | `current_node_id = (project or {}).get("current_node_id")` | 改读 `episode.current_node_id`;endpoint 增加 `episode_id` query/path 参数 |
| B5 | `backend/app/api/projects_router.py:405` | R | 塞进 `ProjectWorkflowOut` 响应 | 同上;项目总览需要另一个"多集汇总"endpoint(见 §5.7) |
| B6 | `backend/app/repositories/project_stage_nodes_repository.py:873` | R | `get_active_group()`:`SELECT projects.current_node_id WHERE projects.id = pid` | 改 `SELECT episodes.current_node_id WHERE episodes.id = eid`;**同时**必须给下面的 `parallel_group` 查询加 `episode_id` 过滤(见 §5.1,这是本函数真正的坑) |
| B7 | `backend/app/repositories/project_stage_nodes_repository.py:935-946` | **W** | `set_current_node_id(project_id, node_id)` — 唯一游标写入口 | 签名改 `set_current_node_id(episode_id, node_id)`,`UPDATE episodes`。**唯一写点,改这一个函数就锁住了所有写路径** |
| B8 | `backend/app/repositories/project_stage_nodes_repository.py:1006` | R | `workflow_badges_for_projects()` 批量读游标,算 `current_node_name` / `workflow_position` | 项目列表徽章语义崩了(见 §5.7)。要么改成"N 集 · 最靠前的集在 X",要么按集返回一组徽章 |
| B9 | `backend/app/services/workflow/instantiation.py:87` | **W** | `await repo.set_current_node_id(project_id, group[0]["id"])` — 实例化后落首组游标 | 循环每集调一次;`_first_active_group` 的入参从"项目全部节点"改为"该集节点" |
| B10 | `backend/app/services/workflow/advance_service.py:76-88` | R | `_active_index(groups, current_node_id)` 纯函数,定位游标所在组 | 函数本身不用改(纯);但喂它的 `groups` 必须是**单集**的组列表 |
| B11 | `backend/app/services/workflow/advance_service.py:357-358` | R | `compute_advance_preview` 从 project 行读游标 | 改从 episode 行读;函数签名加 `episode_id` |
| B12 | `backend/app/services/workflow/advance_service.py:520` | R | `execute_advance` 二次读游标(重算 idx) | 同上 |
| B13 | `backend/app/services/workflow/advance_service.py:535` | **W** | 前进:`set_current_node_id(project_id, next_group[0].id)` | 改 `set_current_node_id(episode_id, ...)` |
| B14 | `backend/app/services/workflow/advance_service.py:580` | **W** | 后退:`set_current_node_id(project_id, prev_group[0].id)` | 同上 |

**写点只有 4 个**(B7 是唯一 repo 写入口,B9/B13/B14 是它的 3 个调用方)。读点 10 个。

### 1.2 前端 — 生产代码(10 处)

| # | file:line | 类型 | 现在做什么 | 下沉后如何改 |
|---|---|---|---|---|
| F1 | `frontend/types.ts:1256` | D | `ProjectWorkflow.current_node_id: string \| null` | 保留字段名,注释改成 episode-scoped;若新增总览 endpoint 则加 `EpisodeWorkflowSummary` 类型 |
| F2 | `frontend/components/workspace/WorkspaceTopBar.tsx:92` | R | `wfNodes.findIndex(n => n.id === workflow?.current_node_id)` 算 MiniStepper 当前位;相邻点跑 advance,非相邻跳节点 | `workflow` 变成"当前剧集的工作流";顶部流程条改为该集流程条。**`handleWorkflowJump` 的 advance 调用要带 episode_id** |
| F3 | `frontend/components/workspace/WorkspaceStageBoard.tsx:173` | R | `workflow?.nodes.find(n => n.id === workflow.current_node_id)` → 喂 `isNodeInActiveGroup` 决定"Complete Stage"是否可见 | nodes 列表必须已按集过滤,否则跨集同 `parallel_group` 的节点会被误判进 active group(§5.1) |
| F4 | `frontend/components/workspace/WorkspaceSidebar.tsx:50,87,345` | R | `currentNodeId` prop;`node.id === currentNodeId` 高亮 | spec §6 要求**删掉侧栏的阶段列表**,改成剧集导航 → 本处大概率整段删除 |
| F5 | `frontend/components/workspace/ProjectWorkspace.tsx:478` | R | `currentNodeId={workflow?.current_node_id ?? null}` 传给 Sidebar | 随 F4 处理;若保留则改传当前剧集的游标 |
| F6 | `frontend/components/Todolist/issueFlow.ts:151` | R | `deriveWorkflowFlow()`:`nodes.findIndex(n => n.id === workflow.current_node_id)` → issue 上下文条的"第 N/M 步"环 | issue 的宿主是节点、节点归属剧集 → 环应显示"Ep2 · 第 2/6 步"。`loadProjectFlow(projectId)` 需改为 `loadFlowForNode(nodeId)` 或带 episode |
| F7 | `frontend/components/workflow/nodeStatus.ts:62` | D | `isNodeInActiveGroup` 的契约 docstring 指名 `workflow.current_node_id` | 契约不变(仍是"已解析好的 currentNode"),但注释要写明调用方须保证 nodes 已按集过滤 |
| F8 | `frontend/components/workflow/WorkflowSection.tsx:244` | R | `workflow.nodes.find(n => n.id === workflow.current_node_id)` → active group + `futureEligible`(start-early 候选) | `futureEligible` 会跨集扫出别集的 pending 节点 → 必须按集过滤 |
| F9 | `frontend/components/workflow/WorkflowSection.tsx:261` | R | `currentNodeId={workflow.current_node_id}` 传 `WorkflowStrip` | 传当前剧集游标 |
| F10 | `frontend/components/workflow/WorkflowStrip.tsx:22,45,59,71` | R | `currentNodeId` prop;`nodes.find(...)` + `current={n.id === currentNodeId}` | prop 语义不变;`nodes` 必须是单集节点链。spec §6 还要求交付物型节点画虚线边框 |

### 1.3 测试 / e2e(14 个文件,B2 收尾时同步改)

| file | 用法 | 下沉后如何改 |
|---|---|---|
| `backend/tests/test_advance_predicate.py:70-95,612,637,680` | `_FakeProjectsRepo(current_node_id=...)` + 断言 `current_node_id_calls == [(_PROJECT, "2")]` | fake 改成 `_FakeEpisodesRepo`;断言元组第一位从 project id 改 episode id |
| `backend/tests/test_autopilot_tick.py:73-142,479-868` | fake repo **回写** `_row["current_node_id"]`(级联多步推进依赖它) | 同上;级联断言 `(_PROJECT,"2")` → `(_EPISODE,"2")`;`_MAX_CASCADE_STEPS` 断言不变 |
| `backend/tests/test_deps_predicate.py:180-197,341` | 同 fake 模式 | 同上 |
| `backend/tests/test_form_incomplete_predicate.py:189-206,329` | 同 fake 模式 | 同上 |
| `backend/tests/test_stage_notifications.py:228-238` | 同 fake 模式 | 同上 |
| `backend/tests/test_workflow_flow_rules.py:566,666` | `return {"current_node_id": None}` 桩 | 改为 episode 行桩 |
| `frontend/components/workspace/WorkspaceStageBoard.test.tsx`(13 处) | `workflow({ current_node_id: '1' })` fixture | fixture 保持,补一条"跨集同 parallel_group 不进 active group"的回归 |
| `frontend/components/workflow/WorkflowSection.test.tsx:54` | fixture | 同上 |
| `frontend/components/workflow/WorkflowStrip.test.tsx`(7 处) | `currentNodeId` prop | prop 语义不变,基本不用改 |
| `frontend/components/Todolist/issueFlow.loader.test.ts:50-95` | `deriveWorkflowFlow` 各分支 | 随 F6 改签名 |
| `frontend/e2e/workflow-walkthrough.spec.ts:90,127` | mock `GET /projects/*/workflow` 返回 `current_node_id: 'node-2'` | route glob 要覆盖新的 episode 参数;fixture 加 `episode_id` |
| `frontend/e2e/workflow-deps.spec.ts:76,118` | 同上 | 同上 |
| `frontend/e2e/stage-board.spec.ts:34,75` | 同上 | 同上 |
| `frontend/e2e/autopilot.spec.ts:43,91` | 同上 + 项目列表徽章 fixture | 徽章语义若改(§5.7),fixture 同步 |

---

## 2 · `advance_service.py` 按"项目单游标"建模的函数

文件 631 行,15 个函数。**9 个要改,6 个原样可用。**

### 2.1 需改(9)

| 函数 | 行 | 今天收什么 | 必须变成什么 | 隐藏假设 / 非显然的坑 |
|---|---|---|---|---|
| `_build_groups` | 50 | `nodes: List[Dict]`(调用方喂的是 `list_nodes(project_id)` = **项目全部节点**) | 收单集节点,或加 `episode_id` 过滤 | ⚠️ **本文件最大的坑**。分组键是 `parallel_group`,而模板拷贝会让**每集拿到同一批 `parallel_group` 值**。不过滤 → Ep1 的 Shooting 和 Ep3 的 Shooting 会被并进同一个 group,推进一集等于推进全部集。见 §5.1 |
| `_active_index` | 75 | `(groups, current_node_id)` — 纯函数 | 签名不变 | 找不到游标时 **`return 0`(退回第一组)而不是 -1**。下沉后若误把 Ep2 的游标喂给 Ep1 的 groups,不会报错,会静默把 Ep1 判为"停在第一组"→ 从头重推 |
| `_deliverable_present` | 137 | `(project_id, node)` | `(project_id, episode_id, node)` | ⚠️ **两条路径都会跨集串味**。主路径(:158-168)按 `node.folder_id` 查文件,而 `ensure_node_folder` 会让每集的同名节点共用一个文件夹(§5.4)→ Ep1 交文件解开全部集。fallback(:200)`return len(files) > 0` 更松:项目里任意未回收文件都算数 |
| `_unmet_dependency_names` | 239 | `(target_group, node_by_id, exempt_ids)` | `node_by_id` 必须是单集 map | 依赖边是实例 id(全局唯一),本身不会跨集乱指;但 `node_by_id` 若含全项目节点,一条**误建的跨集依赖**会被当成真依赖判死锁,且 `_build_groups` 的 exempt 逻辑覆盖不到它。另外 `update_node` 的 backward-only 校验按 `sort_order` 比,跨集 sort_order 重复 → 校验会误判(§5.2) |
| `compute_advance_preview` | 333 | `(project_id, user_id, direction)` | `(project_id, episode_id, user_id, direction)` | 角色判定 `resolve_effective_role(user_id, project_id=...)` 保持项目级(权限本就是项目级,不该下沉);只有节点/游标下沉 |
| `_preview_forward` | 373 | `(project_id, groups, idx, node_by_id)` | 加 `episode_id`(为 `_deliverable_present` 透传) | Gate 4「必须有下一组」:每集链独立后,**每集都会各自走到 BLOCK_NO_NEXT**。今天这是"整个项目做完了",下沉后是"这一集做完了",总览的"全剧完成"要另算 |
| `_preview_back` | 477 | `(project_id, groups, idx)` | 加 `episode_id`(仅透传) | `idx <= 0` → BLOCK_NO_NEXT。逻辑不变 |
| `execute_advance` | 495 | `(project_id, user_id, direction)` | `(project_id, episode_id, user_id, direction)` | 6 个副作用点都要跟改:关 mirror issue(:531)、写游标(:535)、`ensure_node_issues`(:536)、`ensure_node_folders`(:537)、通知(:542-565)、`enqueue_stage_hook_dispatch`(:572)。**`ensure_node_folders` 按项目建文件夹,同名节点(每集都有 "Script")会撞名** —— 见 §5.4 |
| `_enqueue_autopilot_tick_best_effort` | 611 | `(project_id)` | `(project_id, episode_id)` 或保持项目级 | 取决于 §3 的计量决策。若 tick 保持项目级,本函数不用改;若 tick 下沉,`cascade_in_progress()` 的 contextvar 重入守卫**从"每项目一个"变成"每任务一个"**,并发多集 tick 时守卫语义要重新确认 |

### 2.2 六道闸门逐条

`compute_advance_preview` + `_preview_forward` 一共 6 道:

| # | 闸门 | 行 | 作用域 | 下沉后 |
|---|---|---|---|---|
| 0 | 角色(`BLOCK_NOT_MANAGER_OR_EDITOR`) | 344-350 | 项目级 | **不变**。权限属于项目,不该下沉 |
| 1 | 审阅(`BLOCK_REVIEW_PENDING`) | 382-393 | 当前组每个 `review_required` 节点的 mirror issue 须 done | 组变成单集组即可,判据本身按 node 走,**无改动** |
| 2 | 交付物(`BLOCK_DELIVERABLE_MISSING`) | 396-408 | `_deliverable_present` | ⚠️ fallback 的"任意项目文件"会跨集串味,见 §2.1 |
| 3 | 表单(`BLOCK_FORM_INCOMPLETE`) | 410-422 | `_form_incomplete` 纯函数,只看节点自己的 `form_schema`/`form_data` | **无改动** |
| 4 | 有下一组(`BLOCK_NO_NEXT`) | 424-431 | `idx + 1 >= len(groups)` | 语义从"项目走完"变"本集走完";总览的完成态要另算 |
| 5 | 依赖(`BLOCK_DEPS_PENDING`) | 433-462 | `exempt_ids = next_group ∪ active` | exempt 集合是**同集内**的 id,跨集依赖不在 exempt 里 → 若误建会永久 DEPS_PENDING。spec §8 明确"不做跨集依赖",B1 应加约束禁止跨集 `depends_on` |

### 2.3 级联 / start-early

| 位置 | 现状 | 下沉后 |
|---|---|---|
| `autopilot._cascade_pass` (`autopilot.py:337-416`) | 循环 `execute_advance(project_id, owner, "forward")` 最多 50 步 | 每集一条链 → 循环要按集跑;`_MAX_CASCADE_STEPS=50` 是**每集**还是**每 tick 全项目**要定案。按集更合理(50 步/集),否则 10 集共享 50 步会被前几集吃光 |
| `autopilot._auto_start_pass` (`autopilot.py:190-249`) | `list_nodes(project_id)` 扫全项目 auto_start 候选,按 `sort_order` 排 | 跨集 `sort_order` 重复 → 排序不稳定,**配额消耗顺序不可预测**。要按 `(episode.sort_order, node.sort_order)` 排,否则"哪集先跑"随机 |
| `POST .../nodes/{node_id}/start-early` (`projects_router.py:767`) | 按 node 找、按项目校验、`dispatch=False` | node id 全局唯一,**endpoint 路径不用改**;但它调 `_unmet_dependency_names` 时的 `node_by_id` 要按集过滤 |
| 前端 `futureEligible` (`WorkflowSection.tsx:246-253`) | 扫 `workflow.nodes` 全部 pending 节点当 start-early 候选 | 不过滤会把别集的节点列成"可提前开工" |
| `advance_service._enqueue_autopilot_tick_best_effort` | 前进/后退尾部各调一次 | 见 §2.1 最后一行 |

### 2.4 原样可用(6)

`_node_ref`(:91)、`_mirror_issues`(:105)、`_first_issue_identifier`(:120)、`_review_satisfied`(:131)、`_form_incomplete`(:203)、`_open_subissue_warnings`(:310)。

共同点:**都只吃 `(project_id, node)` 或纯节点字典**,而 node id 是全局唯一 Snowflake。mirror issue 的 `origin_id` 也因此不用动(见 §4.2)。

---

## 3 · Autopilot 计量单位决策

### 3.1 现状

| 组件 | file:line | 现状 |
|---|---|---|
| 日配额值 | `autopilot.py:72,131-172` | `system_settings['workflow_autopilot'].daily_auto_runs`,默认 20,**全局同一个数**(不是每项目配置),60s 缓存 |
| 配额计数 | `agent_runs_repository.py:762-801` | `count_auto_dispatches_today(project_id)`:`agent_runs WHERE project_id = ? AND trigger='issue_dispatch_auto' AND started_at >= UTC 当日 0 点`。读失败 fail-open 返回 0 |
| 消费点 | `autopilot.py:207-233` | tick 开头查一次 `used`,循环内 `used += 1` 在内存里累加(注释解释:agent_runs 行是 DBOS 异步写的,同 tick 内重查会 race) |
| 超额行为 | `autopilot.py:231-233` | 不 dispatch,改走 M3 confirm gate,标题 `Autopilot paused: daily limit reached — "<node>"` |
| tick 粒度 | `autopilot.py:456,461` | `autopilot_tick(project_id)`,DBOS workflow,**每项目一个** |
| sweep | `autopilot_sweep.py:32-41` | 每 5 分钟扫 `projects JOIN project_stage_nodes` 找有 eligible auto_start 节点的项目,`LIMIT 200`,每项目 enqueue 一个 tick |

`agent_runs` 表**没有 `episode_id`**(有 `project_id` / `issue_id` / `task_id`)。按集计数需要 `agent_runs → issues.origin_id → 解析出 node_id → project_stage_nodes.episode_id` 三跳,或新加一列。

### 3.2 三个选项

| 选项 | 改动量 | 花费上限 | 公平性 | 失败模式 |
|---|---|---|---|---|
| **A 保持每项目** | 0 | 有界(20/项目/天) | ❌ 每集可用额度 = 20/集数。10 集的剧,后面几集常年吃不到额度 | 按 `sort_order` 消耗 → **永远是最后几集被饿死**,且暂停通知不说明"是项目配额被别集吃光了",读起来像"我这集的 agent 坏了" |
| **B 改每剧集** | 中(加 `agent_runs.episode_id` + 改计数函数 + 改 tick 参数) | ❌ 无界:总花费 = 20 × 集数。导入一部 20 集的剧 → 400 runs/天 | ✅ 完全公平 | 成本失控,且没有任何一处能看到"这个项目今天总共烧了多少" |
| **C 混合(推荐)** | 小(A 的计数不动,加一个按集子上限) | ✅ 项目级硬顶不变 | ✅ 单集吃不掉全部预算 | 两个上限都要在暂停文案里说清是哪个 |

### 3.3 建议:C(混合)——项目级硬顶保留,加每集子上限

**保留 `count_auto_dispatches_today(project_id)` 作为花费天花板**(它就是唯一的成本护栏,下沉掉等于取消成本上限),**另加一个每集子上限** `per_episode_cap = max(3, ceil(daily_auto_runs / max(1, 活跃集数)))`,两个都过才 dispatch。

对"单人写手、每部剧约 3 集"的用户,具体体验是:

- **正常情况下两个上限都碰不到。** 3 集 × 每集链上约 3-4 个 agent 节点 ≈ 9-12 次派发,项目顶 20 富余;每集子上限 `max(3, ceil(20/3)) = 7`,单集也够。**所以这个改动今天对用户是零摩擦的。**
- **加子上限的理由不是省钱,是让故障可读。** 只有 A 时,Ep1 若因为重试/循环烧掉 18 次,Ep2、Ep3 会在 `_auto_start_pass` 里静默走进 confirm gate,用户看到的是两条 "Autopilot paused: daily limit reached",而**通知文案不说这个 limit 是项目级的、也不说是谁烧掉的** —— 单人用户的第一反应会是"agent 又坏了",而不是"我该去看 Ep1 为什么在打转"。子上限把爆炸半径关在出问题的那一集里。
- **配套必须改文案**(否则等于没改):暂停通知要写清 `"项目今日 20 次已用 18(Ep1 用了 14)"` 这一级的信息。这条属于 CLAUDE.md 里「触发路径必须类型化失败回显」的同族要求。

**B2 落地时的两个具体注意点:**

1. **`used += 1` 的内存计数在并发多集 tick 下会失效。** 现在 tick 是每项目一个、DBOS 串行,内存累加安全。若把 tick 下沉成每集一个,两集的 tick 并发跑时各自持有一份 `used`,项目级硬顶会被突破到 2×。→ **建议 tick 保持每项目一个**(一个 tick 内循环处理该项目的所有剧集),只把 `execute_advance` 的作用域下沉。这样 `_enqueue_autopilot_tick_best_effort`(`advance_service.py:611`)和 `cascade_in_progress()` 的 contextvar 守卫都不用动。
2. **`autopilot_sweep` 的 SQL(`autopilot_sweep.py:32-41`)不用改** —— 它只找"有 eligible 节点的项目 id",节点加了 `episode_id` 也不影响这个 DISTINCT 查询。`LIMIT 200` 仍是项目数不是剧集数,不会因下沉而爆。

---

## 4 · `events` 拷贝冻结先例 —— 确认属实,可照搬

**结论:妄想没有落空,这个模式确实存在且完整。** `surface` 可以逐字照抄。

### 4.1 完整代码路径

| 步骤 | file:line | 内容 |
|---|---|---|
| 模板列 | `backend/app/models/project_library.py:291-298` | `WorkflowTemplateNodes.events: JSONB NOT NULL`,`server_default '{"notify_on_arrival":true,...}'::jsonb` |
| 实例列 | `backend/app/models/project_library.py:452` | `ProjectStageNodes.events: JSONB`,同 server_default |
| **拷贝点** | `backend/app/repositories/project_stage_nodes_repository.py:393` | `events=tn.events`,就在 `ProjectStageNodes(...)` 构造里,与 `completion_policy=tn.completion_policy`(:392)、`form_schema=tn.form_schema`(:399) 并列。注释(:389-391)写明:"template-layer config, copied verbatim at instantiation; instances don't open these for in-place tweaks (spec §5)" |
| **冻结保证 1** | `backend/app/repositories/project_stage_nodes_repository.py:528-545` | `update_node()` 的关键字参数白名单里**没有 `events`** —— owner / members / schedule / skipped / form_data / depends_on / brief,就这些。实例侧无 events 写入口 |
| **冻结保证 2** | `backend/app/repositories/project_stage_nodes_repository.py:832-836` | `set_node_metadata()` docstring 明写"never touches `status`/`events`/schedule or any other trigger-/template-owned column" |
| 模块级约定 | `backend/app/repositories/project_stage_nodes_repository.py:22` | 文件头 docstring 同样声明这条纪律 |
| 输出 | `backend/app/repositories/project_stage_nodes_repository.py:158` | `_node_row()` 里 `"events": obj.events` —— 读的是**实例列**,不 join 模板 |
| 消费方 | `autopilot.py:186`(`(n.get("events") or {}).get("auto_start")`)、`autopilot_sweep.py:39`(`n.events->>'auto_start' = 'true'`) | 全部只读实例列 |

### 4.2 B1 照搬 `surface` 的清单

1. `workflow_template_nodes.surface TEXT NULL` + `project_stage_nodes.surface TEXT NULL`(两张表各一份)
2. 模型加两个 `Mapped[str | None]`(对齐 `project_library.py:291` / `:452` 的写法)
3. `instantiate_from_template` 的构造块加一行 `surface=tn.surface`,**紧挨 `events=tn.events`(:393)**
4. `_node_row()` 加 `"surface": obj.surface`(:158 附近)
5. **不要**把 `surface` 加进 `update_node` 的参数表 —— 那样就破坏冻结语义了
6. `NodeOut` schema 加字段(`schemas/workflow.py`),默认 `None`;遗留节点 `surface is None` → 按交付物型降级(spec §5)

⚠️ **一处不对称,B1 要注意**:`events` 是 `NOT NULL + server_default`,所以永远读得到 dict;`surface` 按 spec 要 nullable(NULL = 交付物型)。所以**不能**照抄 `(n.get("events") or {})` 那种"空值当空 dict"的写法 —— `surface` 的 NULL 是**有意义的取值**,不是缺省。

### 4.3 顺带确认:`autopilot_sweep` 的 JSONB 比较陷阱

`autopilot_sweep.py:42-51` 有一段很长的注释解释为什么用 `n.events->>'auto_start' = 'true'` 字符串比较而不是 `::boolean` 强转:一行脏数据会让 `::boolean` **整条查询 raise**,拖垮全局 sweep。`surface` 若将来进 SQL 谓词,沿用同样的字符串比较纪律。

---

## 5 · 会让实施者意外的其他发现

### 5.1 ⚠️ `parallel_group` / `sort_order` 跨集碰撞 —— 本次下沉最危险的一处

`instantiate_from_template`(`project_stage_nodes_repository.py:376-377`)把模板的 `sort_order` / `parallel_group` **原值**拷进实例。按集实例化后,每集会拿到**完全相同**的一组值(Ep1 的 Script `sort_order=0`,Ep2 的 Script 也是 `sort_order=0`)。

这会打穿三处**不报错**的地方:

| 位置 | 后果 |
|---|---|
| `advance_service._build_groups:63-71` | 按 `parallel_group` 值聚合。Ep1 和 Ep3 的 Shooting 拿到同一个 `parallel_group=1` → **被并成一个 group**,推进一集等于推进所有集 |
| `project_stage_nodes_repository.get_active_group:895-910` | SQL 只 `WHERE project_id = pid AND parallel_group = X` → **返回全部剧集**同 parallel_group 的节点。`node_mutations.delete_project_node:85` 的"活动节点不可删"守卫因此会误拦别集的节点 |
| `autopilot._eligible_auto_start_candidates:180` / `advance_service._build_groups:57` | `sorted(key=sort_order)` 在重复值上是稳定排序但**跨集顺序取决于 DB 返回顺序** → 配额消耗顺序不可预测 |

**B1 必须定案**:是给每集重排 `sort_order`(如 `episode_index * 1000 + tpl.sort_order`),还是给 `parallel_group` 做每集命名空间(如 `(episode_id, parallel_group)` 复合键)。**加 `episode_id` 列本身解决不了这个问题** —— 所有分组/排序代码都得跟着加过滤。

### 5.2 `update_node` 的 backward-only 依赖校验会跨集误判

`project_stage_nodes_repository.py:619-631`:校验 `depends_on` 时查**全项目**节点的 `sort_order`,要求 `dep_sort < node.sort_order`。跨集 `sort_order` 重复后,"Ep2 的 Script(sort=0)依赖 Ep1 的 Delivery(sort=10)"会因为 `10 >= 0` 被**误判为 backward 违规而拒绝**;反过来"Ep3 的 Delivery(sort=10)依赖 Ep1 的 Script(sort=0)"会被**误判为合法而放行** —— 而 spec §8 明说不做跨集依赖。B1 应在这里加 `episode_id` 相等约束,把跨集依赖直接挡掉。

### 5.3 `instantiate_from_template` 的幂等判据会吞掉第 2 集起的实例化

`project_stage_nodes_repository.py:210-232` docstring:**"A project that already owns any node is left untouched"**,且 advisory lock 的 key 是 `'project_stage_nodes_instantiate:' || project_id`。

按集实例化时,给 Ep1 建完 11 个节点后,给 Ep2 调用会命中"项目已有节点"分支 → **静默返回 Ep1 的节点,不报错**(`expect_fresh=False` 路径)。B3 必须把幂等判据和 advisory lock key 都改成 `(project_id, episode_id)`。

### 5.4 ⚠️ `ensure_node_folders` 会让多集**共用同一个**交付物文件夹 —— gate 2 主路径直接失效

`advance_service.py:537` / `instantiation.py:91` 调 `ensure_node_folders(project_id, group, user_id)`。读 `node_folders.py:29-77` 的实际行为:

1. 节点已有 `folder_id` → 原样返回
2. 否则**按名字(大小写不敏感)复用项目根目录下的同名文件夹**(`node_folders.py:55-63`)
3. 都没有才新建

关键是第 2 步。每集都有叫 "Script" 的节点,Ep1 到达时建了 "Script" 文件夹,**Ep2/Ep3 到达时会命中复用分支,拿到同一个 `folder_id`**。不是"可能撞名",是**确定性地共用一个文件夹**。

后果比 §5.10 的 fallback 严重得多:`_deliverable_present` 的**主路径**(`advance_service.py:158-168`,`list_folder_files(folder_id)` 非空即通过)会因此被击穿 —— **Ep1 往 Script 文件夹交一个文件,Ep2 和 Ep3 的 gate 2 立刻满足**,而这条路径没有任何"遗留行/创建失败"的前提,是正常流程。

B1/B4 必须二选一:文件夹名加集前缀(`Ep2 · Script`),或让阶段文件夹挂到剧集下而不是项目根。**这条是 B2 的阻塞项,不能留到 B4** —— 否则按集推进的第一次真实使用就会连跳三集。

### 5.5 `projects.current_node_id` 没有 FK,是裸 BIGINT

`migrations/380_workflow_nodes_m1.sql:135` 只有 `ADD COLUMN ... BIGINT`,**无 FK、无索引**。所以:

- 删节点不会清游标 → 悬空 id
- 悬空时 `_active_index` 找不到就 **`return 0`**(`advance_service.py:88`),静默退回第一组 —— 不是报错,是"工作流看起来倒回开头了"
- B1 加 `episodes.current_node_id` 时**顺手补上** `REFERENCES project_stage_nodes(id) ON DELETE SET NULL`,把这个坑一起填了

### 5.6 mirror issue 的 `origin_id` 格式**不用改**(好消息)

`project_stage_issues.py:53-64`:`project_stage:{project_id}:{node_id}`,前端 `parseOriginId` 按**第一个冒号**切。node id 是全局唯一 Snowflake,所以剧集信息不需要编进 origin_id,**`build_stage_origin_id` / `parse_stage_origin_id` 零改动**。`issue_repository._fire_stage_node_sync`(:548)的回流投影同样按 node_id 走,也零改动。

代价是:光看 origin_id 判断不出是哪一集 —— 若 B5 的总览要显示"Ep2 有 issue 等你",得走 `issue → node → episode` 的 join,不能从 origin_id 直接解析。

### 5.7 项目列表徽章的语义会崩

`workflow_badges_for_projects`(`project_stage_nodes_repository.py:985-1074`)返回 `{current_node_name, workflow_total, workflow_position, agents_active}`,消费方 `projects_service.py:313` → `types.ts:749-753`。

`workflow_total = len(非skipped节点)`:下沉后 3 集 × 11 节点 = **33**,`workflow_position` 会算成"第 14/33 步"。这个数字对用户毫无意义。B5 要么改成"3 集 · 2 集进行中",要么按集返回一组徽章。e2e fixture(`projects-phase-b.spec.ts:54-56`、`autopilot.spec.ts:48-50`、`workflow-deps.spec.ts:78-80`、`workflow-walkthrough.spec.ts:93-95`、`stage-board.spec.ts:36-37`)全部要跟改。

### 5.8 没有 realtime 订阅绑在工作流上(好消息)

`grep postgres_changes` 全仓只有两处:`TodolistPage.tsx:311`(issues 列表)和 `IssueDetailView.tsx:386-393`(单 issue 消息)。**工作流/游标没有任何 Supabase Realtime 订阅**,全靠 `useProjectWorkflow` 手动 refetch(`hooks/useProjectWorkflow.ts:24,37`)。下沉不会踩到实时订阅的 filter 重写。

### 5.9 `episodes` 表现状极简,`current_node_id` 是纯新增

```
episodes: id, project_id, title, sort_order, created_at, updated_at
```

**没有 status 列** —— 印证 spec §3 的"剧集推导状态是算出来的、不落库"。加 `current_node_id` 是纯新增,不冲突。生产数据:「个人项目测试 1」(`291022264100262`)有 **4 集**,其余项目全是 1 集。所以真正会暴露多集问题的只有这一个项目 —— 也意味着**测试环境很容易假装一切正常**,B2 必须显式造 ≥2 集的场景。

### 5.10 `_deliverable_present` 的 fallback 在多集下从"松"变成"破"

`advance_service.py:200` 的 `return len(files) > 0` —— 没有匹配文件夹时,**项目里任意一个未回收文件**就算交付物齐了。今天单游标下这只是"宽松";多集共用一个项目文件池后,它变成"一个文件解开所有集的交付物闸门"。

触发前提是节点没有 `folder_id`(文件夹创建失败,或 mig 383 之前的遗留行),所以比 §5.4 少见 —— **但 §5.4 说明主路径也已经被击穿了,两条路径都要修**。建议 B4 顺手把这条 fallback 改成 fail-closed:没有 `folder_id` 就判交付物未满足,而不是"随便有个文件就放行"。

---

## 6 · B1 / B2 的最短安全路径(建议顺序)

1. **B1-a** 先解决 §5.1 的排序/分组命名空间,再加 `episode_id` 列 —— 顺序反了会写出一堆"加了列但分组还是串的"代码
2. **B1-b** `surface` 双份 + 拷贝冻结,照 §4.2 清单
3. **B1-c** `episodes.current_node_id` + 补 FK(§5.5)
4. **B2-a** 只改 `set_current_node_id`(B7)一个写入口 + 它的 3 个调用方(B9/B13/B14),让写路径先收口
5. **B2-b** `advance_service` 9 个函数加 `episode_id`(§2.1),先让 `_build_groups` 只收单集节点
6. **B2-c** 三个跨集串味点:文件夹共用(§5.4,**优先级最高,会连跳三集**)、实例化幂等(§5.3)、依赖校验(§5.2)
7. **B2-d** autopilot 按 §3.3 落地(tick 保持项目级,只下沉 `execute_advance` 作用域 + 加每集子上限 + 改暂停文案)
8. **B2-e** 前端 10 处 + 14 个测试/e2e 文件

**验收必须造 ≥2 集的项目**(§5.9)—— 单集环境下以上所有跨集串味 bug 都测不出来。
