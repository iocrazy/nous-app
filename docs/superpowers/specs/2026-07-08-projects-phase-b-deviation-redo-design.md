# Projects Phase B — 偏差重做设计（对齐 A 稿 Stage Ring）

**日期:** 2026-07-08
**状态:** 设计（待用户 review → writing-plans）
**作者:** 与用户 brainstorm

## 关系与背景

本文是 [`2026-07-04-projects-module-security-and-redesign-design.md`](./2026-07-04-projects-module-security-and-redesign-design.md)
的 **Phase B 实施级续写**。原 spec 的 Phase B 节（B1–B3）是 B0 出稿前的**占位级**
（B3 只有一句「阶段感知建议卡片…串联现有 Script AI / Storyboard AI 端点与 style profile」），
没有数据模型分析、没有端点设计、没有「一键 vs 导航」的判断依据。选稿（A · Stage Ring）后
直接实现，导致 B3 就地降级成导航版——这是本次重做的根因。

设计参考：
- **mockup（仓库）:** `docs/superpowers/specs/2026-07-08-projects-phase-b-mockups.html`（A/B/C 三版，A 选定）
- **artifact:** https://claude.ai/code/artifact/f1d342c2-dadf-4786-9d1d-3011d6b8abed
- **偏差清单来源:** memory `project_projects_phase_a.md`「实现与 A 稿的偏差」节

## 已上线现状（重做的起点）

| 组件 | 现状 | 文件 |
|---|---|---|
| B1 列表卡片 | Stage Ring 布局已 ship（#1142）；动态行只显示阶段跃迁 `Entered {stage}·{actor}·{time}`，永远绿点 | `frontend/components/ProjectCard.tsx` |
| B2 工作台 | 阶段大卡 + Advance 已 ship（#1144/1146）；**缺 `Stage history` 按钮** | `frontend/components/project/StageWorkbench.tsx` |
| B3 建议卡 | **导航版**（#1156/1158）：CTA 仅切 tab，仅 `script` 阶段显 script 数，其余静态 i18n 文案 | `frontend/components/project/StageSuggestion.tsx` |

`StageWorkbench` 是详情页**头部常驻**（`ProjectsPage.tsx:258`，在 tab 内容之上，跨 tab 可见），
B3 卡挂其内始终可见。

## 四个偏差（对齐 A 稿）

1. **B3 建议卡被降级（核心）**：A 稿 = 数据感知 + 一键触发（`9 of 12 shots have frames → [Generate 3 frames]`）；实现 = 导航版（`[去脚本]`）。
2. **B2 缺 `Stage history` 按钮**：A 稿 = `[Stage history][Advance to Production →]`；实现只有 Advance。
3. **B1 动态行语义降级**：A 稿 = 工作事件 + 阻塞态色（琥珀=停滞）；实现 = 阶段跃迁绿点。
4. **视觉像素级偏差未验证**。

---

## 数据模型现实 vs mockup（重做前必读，降级根因）

探查（`backend` 全量）确认 mockup 的部分前提在当前数据模型里**不存在**，重做必须以真实数据为准、
以设计判断跨越，而非照抄 mockup 文案：

| mockup 前提 | 真实数据 | 处理 |
|---|---|---|
| 「Script v4 is **locked**」 | `script_projects` 无 lock/version 列（仅 `status` = active/deleted；`ScriptProjects` @ `models/scripts.py:227`） | **不硬造 lock**；文案由 shot 真实状态驱动 |
| 「**Generate 3 frames**」一键 | 无批量端点，仅单发 `POST /shots/{id}/generate`（`script_shots_router.py:188`），gate `FEATURE_SHOT_GENERATE` | **新建项目级批量端点**，server 端 fan-out |
| 「convertToStoryboard」project 级一键 | script 级 convert **已 410 Gone**（`script_ai_router.py:204`）；live 版是 scene 级 `POST /scenes/{id}/auto-storyboard`（`script_shots_router.py:145`） | 「拆分镜」引导指向 scripts tab（导航），不接死端点 |
| 「Storyboard v3 approved」工作事件 | 无 approval / 版本语义；`latest_activity_for_projects` 只查 stage_history（阶段跃迁） | **混合**：文件级最新事件（resources）优先，阶段事件回退 |
| 「Waiting on client review」阻塞态 | 无 client-review / 阻塞状态 | 用**驻留超阈值**推导阻塞态色（琥珀） |

**朗报**：mockup 核心「9/12 分镜已生成」的**数字可算**。链路
`script_projects → script_scenes(FK script_id) → script_shots(FK scene_id)`；
`script_shots.status` 状态机 `empty → generating → done/failed`（`models/scripts.py:495`；
workflow `workflows/script_shot_generate.py`）。完成度 = `count(status='done') / count(all)`。
仅缺 project 级聚合查询（现有只有 per-scene `list_by_scene`），需新建 JOIN。

**storyboard tab 是死面**：`ProjectStoryboardTab.tsx` 渲染「storyboard moved」提示 + go-to-scripts，
真正的分镜活在 scripts tab 的 script 编辑器内。故一键生成的**结果去 script 编辑器看**；
生成本身走后端批量 dispatch + 任务系统（异步），前端 toast + 卡片轮询刷新 done/total。

**SOP 阶段目录**（`project_stages`，migration 295，固定 6 slug）：
`planning(10) → script(20) → storyboard(30) → generation(40) → review(50) → delivery(60)`。

---

## 设计决策（brainstorm 已定）

- **D1 · B3 一键实现** = 新建后端批量端点（还原 mockup 一键语义；非前端循环、非纯导航）。
- **D2 · B1 动态行** = 混合：文件事件优先 + 阶段回退 + 停滞色。
- **D3 · B3 「locked」** = 不硬造，shot 真实状态驱动文案。
- **D4 · B3 完成度聚合** = 跨项目下**所有非删除 script** 的 shots 求和。
- **D5 · 非 storyboard 阶段** = 数据感知（真实数字）+ 导航 CTA，**不硬造一键**（只有 storyboard 有真实批量动作）。
- **D6 · 停滞阈值** = review 阶段 ≥3d、其他阶段 ≥7d 变琥珀（抽成常量，可调）。
- **D7 · 视觉校验** = Playwright MCP 路线 1a（e2e stub 免登录）为主，留 CI 回归；1b canary 验一键端到端。

---

## ① B3 — 数据感知建议卡（核心重做）

### 后端（零 migration）

**1a. 完成度聚合查询** — `backend/app/repositories/script_shot_repository.py` 加
`storyboard_progress_for_project(project_id) -> dict`：一条 JOIN
`script_projects → script_scenes → script_shots`，`WHERE script_projects.project_id=:pid
AND script_projects.status != 'deleted'`，`GROUP BY` 聚合返回：

```python
{
  "total": int, "done": int, "empty": int, "generating": int, "failed": int,
  "script_count": int, "scene_count": int,
}
```

Best-effort：查询失败返回全 0（建议卡降级为无 progress 的导航文案，不 500）。

**1b. 建议读端点** — `GET /projects/{id}/stage-suggestion`（`projects_router.py`，挂 read 守卫
`verify_project_read_access`）。按 `current_stage.slug` 返回 typed 载荷：

```python
{
  "stage_slug": str,                 # 当前阶段；无 current_stage → null（前端不渲染）
  "kind": str,                       # 见下方 kind 枚举
  "progress": {done,total,empty,...} | None,   # 仅 storyboard 阶段填
  "action": {
    "type": "generate_missing_frames" | "navigate",
    "tab": "scripts" | "output" | "files" | None,   # navigate 用
    "label_key": str,                # i18n CTA 文案 key
    "count": int | None,             # generate_missing_frames = empty 数
  },
}
```

**kind 决策表**（storyboard 阶段专属，D3 shot 状态驱动）：

| 条件 | kind | action |
|---|---|---|
| storyboard 阶段 · 无 script | `storyboard_no_script` | navigate→scripts（去写脚本） |
| storyboard 阶段 · 有 script 无 shots | `storyboard_no_shots` | navigate→scripts（去拆分镜；接 scene 级 auto-storyboard，前端引导，不在此端点触发） |
| storyboard 阶段 · 有空 shots | `storyboard_generate` | **generate_missing_frames**（count = empty） |
| storyboard 阶段 · 全 done | `storyboard_ready` | navigate→advance 提示（就绪可推进） |
| 其他阶段（D5） | `<slug>_nav` | navigate（数字若便宜则带上，如 script 阶段带 script_count） |
| 未知/无阶段 | — | 前端不渲染 |

**1c. 一键批量端点** — `POST /projects/{id}/storyboard/generate-missing`（挂 write 守卫；
gate `FEATURE_SHOT_GENERATE`，off → 404）。流程：

1. 读 `project_style_profile.get(project_id)`（`style_md` / `visual_style`）。
2. 聚合空 shots（`status='empty'`，跨所有非删除 script）。
3. 建一个**父 `task_tracking` 行**（subtitle `Generating N frames`），遵循任务系统纪律
   （manager.create / start；失败 raise 不 return failed dict；业务字段写 metadata）。
4. 逐个 dispatch `script_shot_generate_workflow`（task_type `shot_generate`），透传 style profile。
5. 返回 `{parent_task_id, dispatched_count}`。

并发/防重：dispatch 前对每个 shot 走现有 `update_status('empty'→'generating')` 的乐观流程
（复用 `script_shots_router.py:188` 单发路径的写法，避免重复 dispatch）。

### 前端（重做 `StageSuggestion.tsx`）

- 从 `GET /projects/{id}/stage-suggestion` 取 typed 载荷渲染，替换现有「slug→静态文案」逻辑。
- **storyboard 阶段 = 旗舰**：`{done} of {total} shots have frames — generate the {count} missing frames
  [Generate {count} frames]`。按钮 → `POST …/generate-missing` → toast（`Generating N frames…`）→
  轮询 `/stage-suggestion`（或订阅任务）刷新 done/total，按钮进入 disabled/loading 态直到无空 shot。
- **文案不提「locked」**（D3）；按 kind 决策表渲染。
- **其他阶段**：数据感知（真实数字）+ 导航 CTA（D5）。
- 服务层：`frontend/services/projectsService.ts` 加 `fetchStageSuggestion(projectId)` +
  `generateMissingFrames(projectId)`；类型进 `types.ts`。
- 移除 `StageSuggestion` 现有的 `fetchScriptProjects` 直取 script 数逻辑（改由端点统一给数字）。

---

## ② B1 — 动态行：文件事件优先 + 阶段回退 + 停滞色（D2）

### 后端

`backend/app/repositories/project_stages_repository.py`（或新建 activity repo）加
`latest_file_activity_for_projects(pids) -> {str(pid): {actor, created_at, kind}}`：
一条 `DISTINCT ON (project_id)` 查 `resources` 最新 `created_at` + 创建者，best-effort 降级
（同现有 `latest_activity_for_projects` 范式，enrichment 失败不沉列表）。

`backend/app/services/library/projects_service.py`（现 `:113-114` 并发 gather stage/activity）
加第三个并发查询，合并规则：动态行取「文件事件 vs 阶段事件」**较新**的一条，透传到列表 dict：

```python
latest_activity = {
  "kind": "file" | "stage",
  "label": ...,          # file: "Added N files" 类；stage: stage_name
  "actor": ..., "at": ISO,
  "stalled": bool,       # 当前阶段驻留超阈值（D6）
}
```

停滞判定：用当前 open stage_history 的 `entered_at`（= 当前阶段进入时间）；
`review` 阶段驻留 ≥3d 或其他阶段 ≥7d → `stalled=True`（常量 `STAGE_STALL_THRESHOLDS`）。

### 前端（`ProjectCard.tsx`）

- 动态行渲染 `latest_activity.label`；`kind='file'` 与 `'stage'` 用不同 i18n 模板。
- `stalled=True` → 点从 `bg-emerald-400` 换 `bg-amber-400`，文案前缀 `Stalled/Waiting`（i18n）。
- 无 activity 回退现有 `updated_at + Clock` 分支不变。

---

## ③ B2 — Stage history 按钮

`StageWorkbench.tsx` 阶段卡右上，在 `Advance` **左侧**补 ghost 按钮 `[Stage history]`
（对齐 mockup `[Stage history][Advance to Production →]`）。点击打开 drawer/modal，
复用现有 `GET /projects/{id}/stage_history`（`projects_router.py:292`，repo `history()` 已 newest-first），
渲染进出时间线（stage_name / entered_at / exited_at / actor）。纯前端 + 复用端点，零后端改动。
新组件 `frontend/components/project/StageHistoryDrawer.tsx`。

---

## ④ 视觉像素校验（D7）

runbook：`docs/runbook/real-machine-walkthrough.md`（memory `reference_real_machine_walkthrough`）。

- **主力 · 路线 1a（e2e stub 免登录）**：`frontend/e2e/projects-phase-b.spec.ts`。用
  `e2e/helpers/stubs.ts` 塞假 session + `page.route` 拦截 mock `/stage-suggestion`（各 kind）和
  `/projects` 列表（storyboard 9/12 / review 阻塞琥珀 / archived 满环 / 无 stage 回退）。
  `browser_take_screenshot` 逐像素比对 A 稿 → **同时是永久 CI 回归**（现有例 `e2e/storyboard.spec.ts`）。
- **路线 1b（canary 真库）**：建 `qa_projphaseb_<ts>@example.com`（db `email_confirmed_at=now()`），
  验一键批量端点真发火（shots empty→generating→done）+ B1 真数据动态行端到端。**用完删号**。
- **路线二（扩展）**：线上部署后终验，可选（需用户连 claude.ai/chrome）。

---

## Flag 策略

- 批量端点 `POST …/generate-missing` gate 现有 `FEATURE_SHOT_GENERATE`（生成能力总闸）。
- B3 前端复用现有 `VITE_FEATURE_PROJECT_AI_SUGGEST`（已 go-live=true）；数据感知卡是对已上线卡片的
  **原地升级**，升级期若需灰度可临时置 false 再 go-live（trunk-based ≤1 天）。
- B1/B2 为增量 + 优雅降级，不新 flag（与 B1 已 ship 一致；真机视觉铁律需可见）。

## 测试策略

- **后端单测**：`storyboard_progress_for_project`（空 / 单 script / 多 script / 混合状态 / 全 done / 查询失败降级）；
  `/stage-suggestion` 各 kind 决策；`latest_file_activity_for_projects`（有/无文件、best-effort 降级）。
- **后端集成（真库）**：`/generate-missing` fan-out 计数正确、style profile 透传、父 task_tracking 建行、
  flag off → 404、无空 shot → dispatched=0、越权 403。
- **前端 vitest**：`StageSuggestion` 各 stage kind 文案 + 一键 disabled/loading 态 + 生成后刷新；
  `ProjectCard` 混合动态行（file/stage 模板 + 停滞琥珀）；`StageHistoryDrawer` 时间线渲染。
- **e2e**：`projects-phase-b.spec.ts` 视觉比对（见 ④）。

## 错误处理

- 聚合查询 / 文件活动查询失败 → best-effort 降级（0 / 空），建议卡回退无 progress 导航文案，列表回退阶段/updated_at 动态行，**不 500**。
- `/generate-missing` 部分 shot dispatch 失败 → 已成功的照常 generating，失败的回滚 `empty`，父任务记 metadata，端点返回 dispatched_count（不整体失败）。
- 任务系统纪律：短路失败 raise 不 return failed dict；phase/status/progress 由 trigger 同步，业务字段（subtitle/metadata）由业务代码 PATCH。

## PR 拆分（各自独立可 ship，trunk-based ≤1 天）

1. **PR-1 · B3 后端**：聚合查询 + `/stage-suggestion` + `/generate-missing` + 后端测试。
2. **PR-2 · B3 前端**：重做 `StageSuggestion` 数据感知卡 + service + vitest（依赖 PR-1）。
3. **PR-3 · B1 动态行**：文件活动查询 + service 合并 + `ProjectCard` 混合渲染 + 停滞色 + 测试。
4. **PR-4 · B2 按钮**：`Stage history` 按钮 + `StageHistoryDrawer`。
5. **视觉校验**：`projects-phase-b.spec.ts`（可并入 PR-2，或独立）。

## 文件清单

**后端 新增/改**
- `backend/app/repositories/script_shot_repository.py`（+ `storyboard_progress_for_project`）
- `backend/app/repositories/project_stages_repository.py`（+ `latest_file_activity_for_projects`）
- `backend/app/api/projects_router.py`（+ `GET /stage-suggestion` + `POST /storyboard/generate-missing`）
- `backend/app/services/library/projects_service.py`（合并文件活动进列表 enrichment）
- `backend/app/schemas/projects.py`（StageSuggestion / GenerateMissing 响应模型）
- `backend/tests/…`（单测 + 集成）

**前端 新增/改**
- `frontend/components/project/StageSuggestion.tsx`（重做）
- `frontend/components/project/StageWorkbench.tsx`（+ Stage history 按钮）
- `frontend/components/project/StageHistoryDrawer.tsx`（新）
- `frontend/components/ProjectCard.tsx`（混合动态行 + 停滞色）
- `frontend/services/projectsService.ts`（+ fetchStageSuggestion / generateMissingFrames）
- `frontend/types.ts`（StageSuggestion / activity 类型）
- `frontend/public/locales/{en,zh}.json`（新文案 key）
- `frontend/e2e/projects-phase-b.spec.ts`（新）
- 对应 `*.test.tsx`

## 明确不做（YAGNI）

- 不给 script_projects 加 lock/version 列（D3 用 shot 状态代替）。
- 不复活 storyboard 独立面（410 保留；分镜留在 script 编辑器）。
- 不为非 storyboard 阶段造一键触发（D5）。
- 不改阶段体系 / 不做强制顺序状态机（沿用原 spec 不变量）。
- 不新造聊天窗（B3 是建议卡）。
