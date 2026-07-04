# Projects 模块：安全修复 + 智能化重设计（Design Spec）

- 日期：2026-07-04
- 状态：已通过用户设计评审
- 范围决策：安全先行（Phase A），UI 智能化重设计随后（Phase B）；Tasks tab 删除；归档功能补齐；文件评论 500 本次一并修复。

## 背景与问题清单

2026-07-04 对 Projects 模块（`frontend` 项目列表/详情 + `backend/app/api/projects_router.py`）做了全面审查，发现：

### 后端

1. **IDOR 越权（严重）**：`projects_router.py` 共 42 个端点，仅 5 个挂 `verify_project_write_access`（style-profile×2、current_stage、stage_history、upload、version）。后端使用 service-role client 绕过 RLS，因此 `GET /projects/{id}`、files、members、tasks、shares、collections、folders、comments 的读写端点均可被任意登录用户凭 ID 访问。另外 `update_project/delete_project` 仅允许 owner（不含 team 成员），与守卫「owner 或 team 成员」语义不一致。
2. **文件评论 500（parked bug，本次修复）**：`projects_repository.py:684-744` 的 4 个评论方法仍走旧 supabase REST 路径、查询 migration 062 已重建的 `review_comments` 旧列（`file_id/timestamp_seconds/drawing_data`），生产环境稳定 500。
3. **成员操作静默 no-op（parked bug，本次修复）**：`projects_repository.py:1081-1122` 的 `update_member/delete_member` 按不存在的 `id` 列过滤（project_members 是复合主键 `(user_id, project_id)`，无 id 列），返回成功但无任何效果。
4. **Tasks 死表面**：migration 176 已 DROP `project_tasks`（数据 backfill 到 issues 系统），但 router 仍保留 4 个 task 端点、`ProjectTasks` ORM 模型（`models/teams.py:489`）仍在，前端 `KanbanBoard.tsx`（647 行）仍渲染 Tasks tab。
5. **性能**：`projects_service.py:58-61` file_count 为 N+1 查询（每项目一次 DB 往返）；list 的 `project_type/starred` 过滤（router L70-73）与 files 的 folder 过滤（service L437-440）均在 Python 内存做，未下推 SQL。

### 前端

6. **Active/Archived 假过滤器**：`ProjectsPage.tsx:107-108,125-126` 判 `p.status === 'archived'`，但 `Project` 接口与数据库均无 `status` 字段——Active 恒等于全部，Archived 恒为空。
7. **重复请求**：`StageSelector` 与 `StageToolGrid` 各自独立调用 `fetchCurrentStage(projectId)`。
8. **死代码**：`ProjectsSidebar.tsx`（201 行）全仓零引用。
9. **静默吞错**：`ProjectCollectModal`（3 处）、`ProjectShareModal`、`ProjectVersionModal` 的空 catch。
10. **i18n 违规（中英混杂根因）**：`ProjectFilterSidebar`、`ProjectNavSidebar` 整个文件未接 i18n（All/Starred/Recent/Active/Archived、Files/Scripts/Storyboard/Output/Tasks/Trash/Settings 等全部硬编码英文）；`ProjectsPage.filterTitle`、`ProjectCard` 类型 fallback、相对时间函数同样硬编码。中文 locale 下页面出现两种语言。

### 已有但未被 UI 利用的智能化底子

- SOP 阶段状态机：`project_stages` 目录表（migration 295，6 阶段 planning→delivery）+ `projects.current_stage_id` + `project_stage_history`（append-only）+ 每阶段 `tools_recommended` JSONB。
- `project_style_profile`（migration 283）已注入 Storyboard AI 生成 prompt。
- Script AI / Storyboard AI 独立 router 已可用，`script_ai_router` 可 dispatch 到 storyboard。

## Phase A：安全与正确性（3 个 PR，先行合入）

### PR-A1 权限收口

- `backend/app/core/scope_guards.py` 新增 `verify_project_read_access`，与现有 `verify_project_write_access` 成对。访问语义统一：**owner ∨ team 成员（team 项目）∨ project 成员**。
- `projects_router.py` 全部端点挂守卫：读端点 read 守卫、写端点 write 守卫。
  - `DELETE /projects/{id}`：保持仅 owner。
  - `PUT /projects/{id}`：从「仅 owner」放宽为 write 守卫语义（消除语义分裂）。
  - collections/shares 的**公开消费端点**（外部收集链接、分享链接的 token 化访问）保持不变，仅收口项目内 CRUD。
- 测试：每类守卫补 403 越权用例 + 200 归属用例，参照 `rls_behavioral_audit.py` 范式。

### PR-A2 数据正确性

- **评论修复**：4 个 repo 方法迁移到 062 之后的 `review_comments` 新 schema（ORM/SQLAlchemy 路径，不再走旧 REST）。实现前先核 `information_schema` 确认新表列；若新表纯 `resource_id` 导向、project_files 无法直接映射，则新增小 migration 补 file 维度关联（investigation step：先验证再二选一）。
- **成员修复**：`update_member/delete_member` 改按复合主键 `(project_id, user_id)` 过滤；补「写后读回验证」集成测试（no-op 类 bug 逃过 mocked 测试的教训）。
- **归档补齐**：migration 新增 `projects.archived_at timestamptz NULL`；list 端点支持 `archived` 过滤参数且下推 SQL（同 PR 将 `project_type/starred` 过滤一并下推）；`ProjectContextMenu` 增加 Archive/Unarchive；前端 Active/Archived 过滤器改用真实字段；`ProjectResponse` schema 同步补列（顺带处理现有 schema drift：visibility/display_code/modules_enabled/current_stage_id 按需暴露）。
- **N+1 修复**：file_count 改单条 `GROUP BY project_id` 聚合查询。

### PR-A3 死代码与体验债清理

- 删除 Tasks 面：`KanbanBoard.tsx`、4 个 task 端点、`ProjectTasks` ORM 模型、`projectTasksService.ts`、`ProjectNavSidebar` 的 Tasks 入口。
- 删除 `ProjectsSidebar.tsx`。
- current_stage 状态提升到详情页父组件，`StageSelector`/`StageToolGrid` 共享一次请求。
- 3 个 modal 的静默 catch 改为 `console.error` + toast 反馈。
- i18n 收口：`ProjectFilterSidebar`、`ProjectNavSidebar`、`ProjectsPage.filterTitle`、`ProjectCard`、相对时间工具全部接 `t()`，`en.json/zh.json` 补 key。UI 文案保持英文（key 化后由 locale 决定显示语言）。

## Phase B：UI 智能化重设计（Phase A 合入后启动）

### B0 设计探索（先出稿再定深度）

用 design-shotgun 产出 2–3 版视觉方案对比，覆盖两个画面：

1. **项目列表页**：卡片升级——阶段进度环（`current_stage_id` + `sort_order` 推导）、成员头像、封面缩略图（项目内最新媒体推导，不落新列）、最近动态一行；列表支持按阶段/活跃度智能分组；归档项目的视觉处理。
2. **项目详情页「阶段驱动工作台」**：现有顶部 pill 条升级为工作台头部——当前阶段大卡（阶段说明 + `tools_recommended` 引导卡片）、一键推进下一阶段（写 stage_history）、阶段时间线可视化。

### B1–B3 实施（选稿后细化，各自独立 PR、flag-dark）

- **B1 列表页信息升级**：按选定稿实施卡片与分组。
- **B2 阶段驱动工作台**：详情页以当前阶段为中心重排。
- **B3 AI 助手嵌入**：阶段感知建议卡片（如脚本阶段完成后建议「生成分镜？」），串联现有 Script AI / Storyboard AI 端点与 style profile；做成工作台内建议卡片，不新造聊天窗。

## 架构不变量

- 阶段体系沿用 `project_stages` / `current_stage_id` / `project_stage_history`，不新造状态机；阶段仍允许任意跳转（不强制顺序）。
- Phase B 新信息优先从现有表推导（parsed_media/最新文件），能推导的不落新列。
- Trunk-based：每 PR ≤1 天寿命，feature 部分 flag-dark（`VITE_FEATURE_*` 默认 false）。
- UI 全英文 + i18n key；测试数据英文。

## 测试策略

- PR-A1：守卫单测 + 越权集成测试（403/200 对）。
- PR-A2：评论/成员走真库集成测试（写后读回），归档过滤 SQL 下推的端到端用例。
- PR-A3：删除面跑全量前端 build + 后端 pytest 确认无引用残留。
- Phase B：组件单测 + Vercel preview 真实视觉 UX 过场（登录后过一遍，参照 feedback_ui_early_visual_ux_pass）。

## 明确不做（YAGNI）

- 不把 Tasks 接回 issues 系统（重设计时再评估「项目内展示关联 issues」）。
- 不做阶段强制顺序状态机。
- 不在本 epic 内动 script_*/sb_* 路由内部实现。
