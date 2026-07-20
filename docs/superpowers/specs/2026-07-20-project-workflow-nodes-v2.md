# Project Workflow 节点管理 v2 — 设计定稿（取代 2026-07-19 v1）

日期：2026-07-20 ｜ 状态：已过用户逐轮评审拍板（设计稿 v9） ｜ Mockup：https://claude.ai/code/artifact/6de6c4ce-d67a-42eb-bf2d-87103a45b76f

> v1（2026-07-19-project-workflow-nodes-design.md）已被本文取代。v1 中与本文冲突的条目一律以本文为准；v1 的工程纪律条目（origin 四面镜、#1400 preview 纪律、best-effort hook、404 范式、snowflake string、schema-drift gate）继续有效。

## 1. 层级结构（本次最重要的拍板）

- **全局层（Projects 列表页左下角两个固定入口，viewer 不渲染）**：
  - **Ideation（选题池）**：选题 = 封面 + 标题 + 参考（灵感库 note / # 话题 / 下载库条目），是"造 project 之前"的立项信息。从选题 Create project。
  - **Workflow Templates（模板编排器）**：团队级流程模板管理。
- **project 层**：workflow 挂 project（不挂 ep）。模板决定 project 内部开哪些**节点功能模块**；工作区侧栏 Stages 区块按实例动态渲染。
- **执行层**：镜像 issue + 子任务在 Todolist 流转（零新 UI）。

## 2. 节点库（11 个预置节点）

`project_stages` 升级为全局节点库（**不退役**，推翻 v1 的种子化退役方案）。加列：`phase`（pre/production/post/wrap）、`default_role_label`、`deliverable_label`、`review_required BOOL`。

| Phase | Node | 工作面 | 默认角色 | 交付物 | 验收 |
|---|---|---|---|---|---|
| Pre | Script | 现有剧本模块 | Writer / Script AI | Final script | ✓ |
| Pre | Storyboard | 现有分镜模块 | Artist / Storyboard AI | Shot list + boards | ✓ |
| Pre | Voiceover | 新（最小面） | VO / TTS AI | VO track | — |
| Production | Canvas (AI Generation) | 现有画布模块，**常驻不可关** | Gen AI agent | Generated clips | ✓ |
| Production | Shooting | **TBD**→M1 最小面 | Camera crew | Raw footage package | — |
| Post | Editing | 现有**上传视频+审阅**（A copy / B copy 版本迭代） | Editor / Edit AI | A/B copy | ✓ |
| Post | Color Grading | 同上（上传+审阅） | Colorist | Graded copy | — |
| Post | VFX | 同上（上传+审阅） | VFX artist | VFX shots | — |
| Post | Post Delivery | 现有成片模块 | Editor | **Final cut upload** | ✓ |
| Wrap | Distribution | 现有发布模块 | Ops | Published links | — |
| Wrap | Retrospective | **TBD**→M1 最小面 | Whole team | Retro report | — |

- 最小面 = 镜像 issue 子任务清单 + 阶段文件夹（Files 下按阶段建文件夹）。
- **选题不是节点**（v1 的 Topic Selection 节点删除）——选题在全局 Ideation，project 出生自带 `topic_id`。

## 3. 模板（团队级，种子两套）

- 种子：**Short-form**（默认）/ **Long-form**。两套都含全部 11 节点，差异靠 `skip_default`（见 mockup §01 第二表）；制作方式（Live/AI/Hybrid）**不是模板身份**，是节点开关（Shooting=real/hybrid 开、Canvas 常驻、VFX=hybrid 开）。
- **工期不预设**：模板 duration 全 NULL，项目里手动填。
- 模板节点字段：name、sort_order、parallel_group、default_owner_user_id XOR default_owner_agent_id、default members（子表）、skip_default、review_required、deliverable_required（源自节点库可覆写）、source_stage_id 溯源。
- 保存 = nodes 全量替换式；改模板不影响已建项目。团队可另存命名模板（如自己的 "AI Short"）；软护栏：每 team ≤20 模板、每模板 ≤30 节点（422）。
- 删除 is_default 模板允许；该 team 暂无默认时新建对话框选列表第一套。

## 4. 编排器（飞书式，M1 线性链 + 受控并行）

左侧流程图（胶囊链 + 拖拽排序 + 并行组 + "+ Add from library"）+ 右侧节点面板三 tab：
- **Node Info**（M1）：name / default owner（人-AI 二合一 picker，新组件）/ default members / duration（占位 "Not set — fill in project"）/ deliverable / skip & review 开关。
- **Flow Rules**（M1 固定约定展示，M2 可配置化）：完成人 = **节点 owner 本人**（manager 仅 override）；条件 = owner assigned + owner review（review_required 时）+ deliverable filed（deliverable_required 时，**强校验**）。
- **Events**（M1 内建两条，M2+ 可配置）：到达→幂等派生镜像 issue；完成→关镜像 issue。M2：通知、Suggest agent run（永不静默 dispatch）。M3：自定义 DBOS hook。

**受控并行**：`parallel_group INT NULL`（同值同组）；组内节点各自独立状态，全组终态才可推进；不做任意 DAG（M3）。

## 5. 实例（project 内）

- 创建 project：选模板（Short-form 默认 / Long-form / No workflow）+ method 快捷键（Live/AI/Hybrid 预设节点开关）+ "Customize nodes…"（改即将生成的实例，不动模板）。可从 Ideation 选题带入（topic_id + 标题/封面/参考）。
- 实例化 = 拷贝模板节点 → `project_stage_nodes`（此后独立）；**排期不自动推算**（v1 的 duration 累加推算删除）——用**日期区间选择器**手动填（react-day-picker range mode 定制：双月日历、拖选区间、实时 "N days"、Cancel/Done）→ planned_start/planned_due。
- 每节点独立 `status`：pending / in_progress / in_review / done / skipped。游标 `projects.current_node_id` = 当前活跃组语义（组任一节点 id，推进按组）。
- 就地微调（owner/members/schedule/skipped）只写实例；实例节点增删 M2（skip 覆盖 M1）。
- 工作区：Overview 顶部 workflow strip（复用 IssuePipeline 胶囊视觉）+ 当前节点卡（owner/members/schedule/deliverable + Open in Todolist + Complete stage）+ 头部 "N agents active" chip + 侧栏 Stages 区块（点节点 M1 落 Overview 对应节点卡；完整 Stage Board M2）。

## 6. Hook 闭环（issue 是执行事实源，节点状态是投影）

1. **节点→issue（到达/完成）**：复用 `set_current_stage` 系 repo 后置回调（三调用方一处 hook）。到达：`ensure_stage_issue` 幂等派生镜像 issue，**继承节点 owner 与 planned_due**（issues 加 `due_date DATE`）；AI owner **只指派不 dispatch**（run-confirm 门不变，测试必须断言未 dispatch）。完成/推进：关旧 issue（开放子任务保持 open + preview 警告）。回退：reopen 镜像 issue，preview 明示。
2. **issue→节点（状态回流）**：`IssueRepository.transition_status` 后置 hook 新增 `_fire_stage_node_sync`（与 `_fire_subissue_barrier` / `_fire_pipeline_relay` 同挂点）：`origin_kind='project_stage'` 的 issue 状态变化单向回写 `project_stage_nodes.status`（todo→pending、in_progress→in_progress、in_review→in_review、done→done、cancelled→pending）。
3. 两条 hook 均 best-effort（swallow + warning 不阻塞）。

**origin 纪律（不变）**：`origin_kind='project_stage'` 复用不新增枚举；`origin_id` 新格式 `project_stage:{project_id}:{node_id}`，读端（前端 parseOriginId / 后端 lookup）兼容旧 `{stage_id}` 格式；存量镜像 issue 不迁移。

## 7. 验收与权限（改 v1-D2）

- **owner 审阅制**：镜像 issue in_review→done 由**该节点 owner 本人**执行（人 owner==当前用户；agent owner 的节点由 manager 审）；project manager 可 override。review_required=false 的节点 owner 直接完成。守卫只对 `origin_kind='project_stage'` 且挂 project 的 issue 生效，散 issue 不上锁。
- **交付物强校验**：deliverable_required 节点，该阶段文件夹 0 files → advance/complete 阻塞（服务端 predicate 复算，绝不信前端）。
- 推进/回退：M1 仅相邻（MiniStepper dot 首次接线即带确认门——现状只读无历史包袱）；`advance-preview`（纯读）与 `advance` 共用同一 predicate（#1400）。
- 角色：模板 CRUD / 推进 / 微调 = manager+editor；viewer 不渲染入口。有效角色解析：`project_members` 显式 > team 兜底（owner/admin→manager、member→editor）——**新函数，成为唯一真源**（现状 team 成员二值放行，需收敛）。404 不泄露存在性。
- agents active = `agent_runs.project_id` 聚合 **`status='running'`**（修正 v1 的 ended_at IS NULL）。

## 8. 已决工程悬点（12 条 + 后补）

回退 reopen；不自动重排（无推算）；改日期不级联；自然日；editor 可编模板；允许 No workflow（Overview 不渲染 workflow 区，随时 Apply template）；软护栏 20/30；多 owner 不做（owner 单选人 XOR agent，members 多选混排）；开工日锚点不再需要（排期手动）；MiniStepper 相邻推进/回退；实例增删节点 M2；Image Post 模板等图文流程明确后单独种。

## 9. 数据模型汇总（新增/变更）

```
project_stages            + phase, default_role_label, deliverable_label, review_required（升级为节点库，只读运营字典）
workflow_templates          id/team_id/name/is_default(partial unique)/created_by/timestamps
workflow_template_nodes     id/template_id FK CASCADE/name/sort_order/parallel_group/
                            default_owner_user_id XOR default_owner_agent_id/skip_default/
                            review_required/deliverable_required/source_stage_id/duration_days NULL
workflow_template_node_members  node_id FK/user_id XOR agent_id
project_stage_nodes         id/project_id FK CASCADE/source_template_node_id/legacy_stage_id/
                            name/sort_order/parallel_group/status(pending|in_progress|in_review|done|skipped)/
                            owner_user_id XOR owner_agent_id/planned_start DATE/planned_due DATE/
                            review_required/deliverable_required/skipped BOOL
project_stage_node_members  node_id FK/user_id XOR agent_id
issues                      + due_date DATE NULL
projects                    + current_node_id BIGINT NULL（current_stage_id 过渡只读，M2 清理）
-- M1.5:
topics                      id/team_id/title/cover_url/excerpt/status(candidate|shortlisted|produced|archived)/
                            note_id NULL/resource_id NULL/media_id NULL/inspiration_topic_id NULL/created_by
projects                    + topic_id BIGINT NULL
```

全部 snowflake id 走 DB `generate_snowflake_id()` server_default；**每张新表同 PR 加 SQLAlchemy model**（schema-drift gate）；repo 层日期字段断言 date 对象（isoformat 坑）。

## 10. API（/api/v1，team 铁边界，id 全程 string）

```
GET/POST           /workflows                       模板列表/新建
GET/PATCH/DELETE   /workflows/{id}                  详情/改名/删（nodes 全量替换）
GET                /workflows/stage-library         节点库列表
GET                /projects/{id}/workflow           实例节点+per-node status+agents active
PATCH              /projects/{id}/workflow/nodes/{node_id}   微调（owner/members/schedule/skipped）
GET                /projects/{id}/advance-preview    纯读裁决
POST               /projects/{id}/advance            推进/回退（direction 参数，服务端复算）
-- M1.5:
GET/POST /topics · GET/PATCH/DELETE /topics/{id} · POST /topics/{id}/create-project
```

## 11. 分期与 PR 划分

- **M1**（本期，三个 PR 走 /ship，CI 绿即合）：
  - **PR-A** 迁移+models+种子（节点库升级、五张新表、issues.due_date、projects.current_node_id、两套种子模板）+ 模板 CRUD 后端 + 节点库端点。
  - **PR-B** 实例化 + 双 hook（ensure 继承 owner/due、_fire_stage_node_sync、origin 双格式）+ advance-preview/advance predicate（owner 审阅、deliverable 强校验、并行组、回退 reopen）+ 有效角色解析 + workflow 读写端点。
  - **PR-C** 前端：左下角模板入口+编排器（chain+Node Info+并行组）、创建项目模板选择（两卡+method 快捷键）、工作区 strip+当前节点卡（owner/members picker + react-day-picker 日期区间）、侧栏 Stages 区块、MiniStepper 确认门、agents active chip、e2e stub 走查 spec+双主题截图。
- **M1.5**：Ideation（topics 表+全局页+Create project from topic）。
- **M2**：Stage Board 完整工作面、实例节点增删、Flow Rules/Events 可配置化、通知事件、current_stage_id 清理、逾期标红。
- **M3**：任意 DAG、自定义 DBOS hook、表单化交付。

## 12. 测试要点（继承 v1 全部 + 新增）

- 每节点独立 status：并行组两节点各自流转互不干扰；组全终态才允许 advance。
- _fire_stage_node_sync：origin 双格式、best-effort（抛错不阻塞 transition）、非 project_stage origin 不触发。
- owner 审阅守卫：owner 本人可过 in_review→done、非 owner 编辑者 403、manager override 放行、散 issue 不受限。
- deliverable 强校验：0 files 阻塞、≥1 放行；preview 与 advance 同 predicate。
- 种子幂等：重跑不双建；No workflow 项目零节点不渲染。
- agent owner 派生 issue 断言未 dispatch；due_date 继承为 date 对象。
