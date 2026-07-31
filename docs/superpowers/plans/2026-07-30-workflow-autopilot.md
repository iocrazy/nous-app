# Workflow M4 Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Spec = `docs/superpowers/specs/2026-07-30-workflow-autopilot-design.md`(一切以 spec 为准,含「不做」边界)。

**Goal:** 依赖驱动的流水线自动化——auto_start 自动开工/派发、游标级联推进、提前嘱托、项目日额度护栏、手动 Start early。默认全关,合并零行为变化。

**Architecture:** DBOS workflow `autopilot_tick` 幂等驱动(状态回流/到达/定时三触发);复用 M3 的 DEPS_PENDING 谓词、stage_notifications、run 上膛路径;审阅门语义零改动。

**Tech Stack:** FastAPI + DBOS / React 19。

## Global Constraints

- 迁移号从 **395** 起(`ls supabase/migrations | tail` 核现状顺延);新列同 PR 改 model(drift gate)
- **审阅门绝不被自动跨越**(专项测试断言);失败不自动重试;全部 best-effort swallow
- 额度只数 auto dispatch,手动不计;UTC 日重置;超限走 M3 上膛路径(run_prepared)
- brief 是运行时字段(同 form_data 待遇:实例 PATCH 白名单、模板不可配);events.auto_start 是配置(模板层编辑、实例化拷贝、实例 PATCH 拒改)
- preview/execute 同 predicate(#1400);级联推进走 execute_advance 本体,不旁路
- 默认关:events.auto_start 默认 false;autopilot_enabled 默认 true 但无 auto_start 节点时 tick 无事可做
- UI 英文 + i18n en/zh;测试清代理 env -u ALL_PROXY...;black/lint/tsc 惯例;commit 尾行 `Claude-Session: https://claude.ai/code/session_01KvXWh2z8qRAE3yUgUDq4sW`
- 单 PR,base master,CI 真绿(核对 0 fail 后分步合并)

---

### Task O1: 数据层 — mig 395 + schemas + settings

**Files:**
- Create: `supabase/migrations/395_workflow_autopilot.sql`(brief 列 + autopilot_enabled 列 + system_settings 种子行 `workflow_autopilot`,全部 IF NOT EXISTS/ON CONFLICT 幂等,NOTIFY 结尾)
- Modify: `backend/app/models/project_library.py`(ProjectStageNodes.brief)+ `models/teams.py`(Projects.autopilot_enabled)
- Modify: `backend/app/schemas/workflow.py`(WorkflowNodeEvents 加 `auto_start: bool=False`;NodeOut 加 brief;NodePatch 加 brief;拒 auto_start 于实例 PATCH——events 整体本就不在 NodePatch)
- Modify: `backend/app/schemas/projects.py`(project PATCH 面加 autopilot_enabled——找到现有 ProjectUpdate schema)
- Test: 扩 `tests/test_workflow_flow_rules.py`(auto_start round-trip)+ drift gate

**Steps:** 失败测试 → DDL/models/schemas → 绿 + drift gate → commit `feat(workflow): migration 395 — autopilot columns, brief, settings seed`

### Task O2: 引擎 — autopilot_tick + 额度 + 级联 + start-early

**Files:**
- Create: `backend/app/workflows/autopilot.py` — `autopilot_tick(project_id)`(thin @DBOS.workflow over `_autopilot_tick_impl`,照 stage_hook 范式):spec §2 四步;额度查询按 agent_runs 当日 auto 计数(先读 agent_runs 表结构定标记方式:优先复用现有 metadata/jsonb,没有再加列并入 mig 395)
- Modify: `backend/app/repositories/issue_repository.py`(`_fire_stage_node_sync` 尾部 best-effort 入队 tick——status 变 done 时)
- Modify: `backend/app/services/workflow/advance_service.py` + `instantiation.py`(到达后入队 tick;级联推进=tick 内调 execute_advance,幂等防重入)
- Create: scheduled sweep(照 `scheduled_master.py`/现有 @DBOS.scheduled 范式,5 分钟,过滤 autopilot_enabled 且存在 auto_start 节点的活跃项目)
- Modify: `backend/app/api/projects_router.py` — `POST /{id}/workflow/nodes/{node_id}/start-early`(deps 校验借 DEPS_PENDING 谓词;agent owner 走上膛不 dispatch;route uniqueness)
- Modify: dispatch 注入 brief(读 agent dispatch payload 组装处,brief 非空则附加为任务上下文段)
- Test: `tests/test_autopilot_tick.py`(幂等/开关静默/审阅门专项/额度 20→21 上膛/手动不计数/UTC 重置/级联到门停+单次通知/失败不重试)+ `tests/test_start_early.py` + 全量回归

**Steps:** 失败测试先行 → 实现 → 目标+advance/deps/form/notifications 回归 → 全量绿 → commit `feat(workflow): autopilot engine — auto-start, quota, cascade, start-early`

### Task O3: 前端 — 开关/嘱托/Start early/审阅置顶

**Files:**
- Modify: `frontend/types.ts` + `workflowService.ts`(events.auto_start 默认 false 进 DEFAULT_EVENTS;node.brief normalize '';autopilot_enabled;startEarly API;brief PATCH)
- Modify: `WorkflowTemplateEditor.tsx` Events tab 第 5 开关「Auto-start when ready」
- Modify: `WorkspaceTopBar.tsx`(Autopilot chip 开关,PATCH autopilot_enabled,即时反馈)
- Modify: `WorkspaceStageBoard.tsx` + `CurrentNodeCard.tsx`(brief textarea 失焦保存、done 后只读;未来节点 deps 满足显示 Start early;in_review 时 brief 置顶展示)
- Modify: 镜像 issue 侧 brief 展示(IssueDetailView 或既有 Deliverables 区旁,轻量只读块——读 M2 DeliverablesZone 挂载方式同法)
- i18n `projects.workflow.autopilot.*` / `brief` keys en/zh
- Test: CurrentNodeCard/StageBoard vitest 扩展(brief 保存回调、Start early 条件渲染、Autopilot chip);tsc/lint

**Steps:** 测试先行 → 实现 → vitest+tsc+lint 绿 → commit `feat(workflow): autopilot UI — toggles, brief, start-early`

### Task O4: e2e + ship

- 扩 `stage-board.spec.ts` 或新 `autopilot.spec.ts`:嘱托填写→PATCH 断言;Start early(stub deps 满足)→节点进行中;Autopilot chip 切换→PATCH 断言;双主题截图
- playwright 相关 spec 全绿 → commit `test(workflow): autopilot e2e` → PR title `feat(workflow): autopilot — dependency-driven pipeline automation (M4)`

---

## Self-Review 摘要

- spec §1→O1、§2→O2、§3→O3、§4 测试要点分布于 O2/O3/O4;§0 不做清单未入任务。
- 类型一致:events.auto_start / node.brief / autopilot_enabled / start-early 端点在 O1-O4 同名同型;额度 key `workflow_autopilot.daily_auto_runs`。
- 风险:agent_runs 的 auto 标记方式留给 O2 实施者按现表定(brief 已授权回填进 mig 395);级联重入防护(tick 幂等 + advance 自身互斥)在 O2 测试要点内。
