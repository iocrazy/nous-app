# Phase B / Phase 3 — Storyboard（shots 层 + Auto Storyboard + Generate）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec v3 §3.3/§6 Phase 3：`script_shots` 表 + Auto Storyboard（scene→shot 列表 AI 拆解）+ 分镜视图（scene 分列 shot 卡）+ shot 图像 Generate（复用既有生成链）+ 旧 storyboard 面转只读。

**Architecture:** shots 挂 scene（CASCADE），词表对齐旧 `storyboard_frames`（shot_type/camera_angle/camera_movement/focal_length/lighting）。Auto Storyboard = DBOS workflow（P1 convert-to-scenes 同构：wf_id 串联 + resolve provider + 落库步）。Generate 复用 `storyboard_ai_service.generate_image` 的 image_provider 链路（读它再决定薄封装还是直调），产物 URL 落 shot 行（spec §4：storage_service 单点，底层以 storage session 进度为准）。前端挂左栏 **Storyboard 槽位**点亮（同 G1 节点视图模式）。旧 storyboard 面 P3 只读 banner（DROP 是 P4）。

**Tech Stack:** 迁移 341+（合并时占用则顺延）；FastAPI+ORM；DBOS workflow；React editor/ 目录既有体系。

**Spec:** `docs/superpowers/specs/2026-07-05-phase-b-script-storyboard-design.md` §2.1（script_shots 列逐字）、§2.3（cutover）、§3.3、§4、§6 P3 行
**参考实现（实现者必读）:** convert-to-scenes 全链（`script_scene_convert.py`/`script_ai_router.py` 端点/pin 测试）＝Auto Storyboard 的同构模板；`storyboard_ai_service.generate_image`（335 行起）＝图像生成模板；`SceneFlowNode/NodesView`＝分镜视图的槽位/双主题/测试模式模板。

## Global Constraints

P1/P2 plan 的全部 Global Constraints 继承（wf_id #1017 / resolve provider #1025/#1030 / 扁平派发信封 #1019 / bigint coerce + str 双侧比较 #1006 / 守卫+wiring 测试 / 真库 round-trip 血泪律 / i18n en-zh 键集全等含复数 / 双主题零 emoji 禁 zinc / NOTIFY pgrst / 每 PR ≤1 天 / vitest `--no-file-parallelism` 为准 / **新 workflow 必须 import 进 `_dispatch_bundle.py`**（#1055 血泪））。分镜视图无需新 flag（挂已开的编辑器面）；**Generate 端点加后端 flag `FEATURE_SHOT_GENERATE`**（图像生成成本面，默认 false）。

## File Structure

```
supabase/migrations/341_script_shots.sql               (new，占用顺延)
backend/app/models/scripts.py                          (modify: +ScriptShots)
backend/app/repositories/script_shot_repository.py     (new: CRUD+reorder+create_many)
backend/app/api/script_shots_router.py                 (new: shots CRUD/reorder + auto-storyboard + generate 端点)
backend/app/workflows/script_shot_breakdown.py         (new: Auto Storyboard workflow)
backend/app/workflows/script_shot_generate.py          (new: 单 shot 图像生成 workflow)
backend/app/services/storyboard/script/script_ai_service.py  (modify: +scene_to_shots prompt 方法)
backend/app/core/scope_guards.py                       (modify: +verify_shot_access 薄封装)
frontend/editor/storyboard/StoryboardView.tsx          (new: scene 分列 + shot 卡列)
frontend/editor/storyboard/ShotCard.tsx                (new: 镜号+参数标签+描述+状态+Generate)
frontend/editor/sceneService.ts                        (modify: +shots API 客户端)
frontend/editor/components/{EditorShell,RailModules}   (modify: Storyboard 槽位点亮)
frontend/features/storyboard 旧面入口                   (modify: 只读 banner——先定位旧路由挂哪)
```

**PR 切分**：**PR-S1** = Task 1-3（迁移+ORM+repo+router+两 workflow，后端全量）；**PR-S2** = Task 4-5（分镜视图+Auto/Generate 接线+旧面只读 banner）；**PR-S3** = Task 6（终审+ship+flag 开+真机 E2E+memory）。

---

### Task 1: 迁移 341 + ORM + dev 库验证

- `341_script_shots.sql`：spec §2.1 shots 行逐字建列（id BIGINT PK snowflake / scene_id BIGINT NOT NULL FK script_scenes ON DELETE CASCADE / shot_number INTEGER / shot_type、camera_angle、camera_movement、focal_length VARCHAR / lighting TEXT / description TEXT / image_url、thumbnail_url、video_url TEXT / status VARCHAR NOT NULL DEFAULT 'empty' / sort_order INTEGER NOT NULL DEFAULT 0 / created_at、updated_at）+ 索引 `(scene_id, sort_order)` + RLS service-role policy（P1 迁移同款）+ `NOTIFY pgrst`。全幂等。
- ORM `ScriptShots` 进 models/scripts.py（邻类风格），导入测试 pin（照 test_scene_models_import.py）。
- dev 库 apply + 幂等重跑 + information_schema 列核对。
- Commit `feat(script): migration 341 + ORM — script_shots`

### Task 2: repo + router（CRUD/reorder + 守卫）

- `ScriptShotRepository`：list_by_scene（sort_order 序）/ get_by_id / create（组内 MAX+1000）/ create_many（Auto Storyboard 落库用,单事务）/ update（参数标签+描述白名单,不触 status）/ update_status（status+image/thumbnail/video_url 专用）/ delete / move_shot（step-1000 稀疏,同 P1 move_scene 语义）。house 惯例：read/write_scope、_bigint、_parity。
- `verify_shot_access`：shot→scene→script 反查复用 `_assert_script_team_access`（scope_guards 既有内核）。
- router：GET/POST `/scenes/{scene_id}/shots`（verify_scene_access）；GET/PATCH/DELETE `/shots/{shot_id}`、POST `/shots/{shot_id}/move`（verify_shot_access）。注册进 api/__init__。
- wiring 测试（参数化全路由声明守卫）+ repo capture-SQL 测试（白名单/排序/_bigint）。
- Commit `feat(script): shot repository + router with authz`

### Task 3: Auto Storyboard + Generate 两个 workflow

- **Auto Storyboard**：`POST /scenes/{scene_id}/auto-storyboard`（verify_scene_access，扁平 task_id 信封）→ `script_shot_breakdown_workflow`（**逐字照 script_scene_convert.py 结构**）：step1 读 scene elements + `resolve_script_provider_config` → 新服务方法 `scene_to_shots(elements, heading)`——完整提示词：把场景元素拆成 3-8 个 shot，每个返回 `{shot_type∈{WIDE,MEDIUM,CLOSE,ECU,OTS,POV,INSERT},camera_angle∈{EYE,LOW,HIGH,DUTCH,TOP},camera_movement∈{STATIC,PAN,TILT,DOLLY,TRACK,HANDHELD},focal_length(如 "16mm"/"35mm"/"85mm"),lighting(一句),description(一句,可含 @实体)}` 严格 JSON（元素文本围栏防注入——G3 硬化同款）；step2 `create_many` 落库（shot_number=序号,status='empty'）。失败 raise。**workflow import 进 `_dispatch_bundle.py`**。pin 测试照 test_scene_convert_dispatch.py（wf_id/provider/信封/围栏）。
- **Generate**：`POST /shots/{shot_id}/generate`（verify_shot_access；flag `FEATURE_SHOT_GENERATE` off→404；扁平 task_id）→ `script_shot_generate_workflow`：step1 读 shot+scene 上下文 → **先读 storyboard_ai_service.generate_image 与其 image_provider 解析**,复用该链生成（prompt=参数标签+描述+场景 heading 合成）；step2 产物 URL 写 shot（update_status done+image_url;失败 raise——trigger 会标 failed,但 status 列是业务字段:dispatch 时端点先置 'generating',workflow 失败路径 except 内置 'failed' 再 raise——注意路线 C:phase 由 trigger 管,shot.status 是业务列可以写）。pin 测试同构。
- dev 库真调 Auto Storyboard 一次（真 LLM）记录输出。
- Commit `feat(script): auto-storyboard + shot-generate workflows (generate flag-dark)`

**→ PR-S1 ship**（后端全量 pytest + lint + opus 终审）。

### Task 4: StoryboardView + ShotCard

- 左栏 Storyboard 槽位点亮（railView 加 'storyboard'，G1 同模式）；中央渲染 StoryboardView：**scene 分列横向布局**（每列头=场景编号+heading 排版态,列内 shot 卡纵排,底部 "+ Add Shot"）。
- ShotCard：镜号徽章 + 参数标签 pills（shot_type/angle/movement/focal——laper 截图的 16mm/WIDE/LOW/STATIC 风格）+ 描述（行内编辑,防抖 PATCH）+ 状态角标（empty/generating spinner/done 缩略图/failed 重试）+ Generate 按钮（flag 探测 404 降级 disabled,G3 同款）。
- 列内拖拽 reorder → move_shot；参数标签点击循环词表值（快速改）或下拉,选实现简单的并说明。
- Auto Storyboard 按钮（列头,内联确认,G1 同款）→ dispatch → useConvertPoll 泛化轮询 shots 出现。
- 测试：分列渲染/卡片标签/描述防抖 PATCH/reorder 锚/Auto 派发+轮询/Generate 降级。i18n en-zh、双主题、零 emoji。
- Commit `feat(editor): storyboard view — scene columns with shot cards`

### Task 5: Generate 接线 + 旧面只读 banner

- ShotCard Generate → generate 端点 → 轮询 shot.status（复用泛化 poll,predicate=status done/failed）→ done 显示 image_url 缩略图（懒加载）,failed 显示重试。
- 旧 storyboard 面：定位现有入口（features/ 或 pages/ 的旧 StoryboardPage/路由）→ 顶部只读 banner `This legacy storyboard is read-only — shots now live in the script editor`（i18n）+ 写操作按钮 disabled（最小改动,不迁数据——backfill 属 P4 决策）。
- 测试：generate 派发+status 轮询上屏/banner 渲染+写钮 disabled。
- Commit `feat(editor): shot generate wiring + legacy storyboard read-only banner`

**→ PR-S2 ship**（前端全量+终审）。

### Task 6: 收口

- opus 整分支终审重点：生成成本控制（flag 前置于任何 provider 调用）/ prompt 注入（元素+描述围栏）/ shot.status 状态机与 task_tracking 纪律边界 / 旧面只读完备性。
- ship 后：NAS 开 `FEATURE_SHOT_GENERATE=true` → 真机 E2E：建 scene→写元素→Auto Storyboard（LLM 拆 shot 上屏）→ 单 shot Generate（真图落库+缩略图上屏）→ 截图 → 数据清零。
- memory 更新（P3 收官+P4 入口）。

## Self-Review 已做

- Spec 覆盖：§2.1 shots 表逐字→T1；§3.3 分镜视图/Auto/shot 卡→T3/4；§4 生成经既有链单点→T3/5；§2.3+§6 旧面只读→T5（backfill/DROP 明确留 P4）。不做：视频生成（列先留 video_url）、shot 级 @实体建模（描述纯文本含 @ 字符即可）、跨 scene 拖 shot。
- 同构复用声明：Auto Storyboard=convert-to-scenes 模板;Generate=storyboard_ai_service.generate_image 链;视图=G1 槽位模式——实现者按"先读模板再写"执行。
- 类型一致性：status 词表 empty/generating/done/failed 贯穿 T1/T2/T3/T5;镜头参数词表 T3 prompt 与 T4 标签循环共用（前端从常量导出）。
