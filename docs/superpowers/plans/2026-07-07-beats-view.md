# Beats 视图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 点亮编辑器 rail 上唯一 disabled 的「节拍」槽位（Phase B 产品面最后一块空缺）。V1 = 经典 beat sheet：有序节拍卡（标题+概述+可选关联场景），CRUD/拖拽重排/点关联场景跳回剧本——**无 AI、无自动推导**（后续再议）。

**Architecture:** 新表 `script_beats`（P3 shots 迁移同构模板）；REST CRUD + reorder（move 走 step-1000 稀疏 sort_order，照 move_scene 范式）；前端 BeatsView 挂 rail 节拍槽（拖拽重排照 G2 Outline 范式、内联编辑照既有交互密度、场景关联 chips 点击 onOpenScene 跳回）。

**用户裁决：**「挨个」链内站②（顺序此前已确认）。

## Global Constraints

全部既有约束继承（守卫 verify_script_access/#1006/迁移幂等+NOTIFY/i18n 全等/零 emoji/双主题/vitest --no-file-parallelism/lint gate）。**无 workflow 无 task_type**（纯同步 CRUD）。scene 关联用 jsonb 有序数组存 scene id 字符串（轻量；不建 M:N 表——YAGNI，一个 beat 关联少量场景）。

## File Structure

```
supabase/migrations/3XX_script_beats.sql          (取下一空号)
backend/app/models/scripts.py                     (+ScriptBeats)
backend/app/repositories/script_beat_repository.py (new)
backend/app/api/script_beats_router.py            (new + 注册)
backend/tests/test_script_beats.py                (new)
frontend/editor/beats/BeatsView.tsx               (new)
frontend/editor/beats/BeatCard.tsx                (new)
frontend/editor/sceneService.ts                   (+beats API)
frontend/editor/components/EditorShell.tsx        (rail 节拍槽 enable + 挂载)
frontend/public/locales/{en,zh}.json
frontend/editor/__tests__/beatsView.test.tsx      (new)
```

**PR 切分：PR-BT1** = Task 1（后端全量）；**PR-BT2** = Task 2（前端）。

---

### Task 1: 表 + repo + router

- 迁移：`script_beats`（id snowflake PK / script_id BIGINT FK script_projects ON DELETE CASCADE / title VARCHAR(200) NOT NULL / summary TEXT / scene_ids JSONB NOT NULL DEFAULT '[]'（有序 scene id 字符串数组）/ sort_order INT / created_at / updated_at；索引 (script_id, sort_order)；RLS service_role（P3 同款）；幂等+NOTIFY）。dev 双跑。
- ORM + repo：list_by_script（sort_order 序）/create（尾部 sort_order=max+1000）/update（title/summary/scene_ids）/delete/move（step-1000 稀疏重排，照 move_scene）。scene_ids 写入时 String() 收敛。
- router：`GET/POST /scripts/{script_id}/beats`、`PATCH/DELETE /beats/{beat_id}`、`POST /beats/{beat_id}/move {after_beat_id|null}`——全挂 verify_script_access（beat→script 解析后验，404 before 403）。title ≤200 校验。
- 测试：wiring 守卫矩阵 + move 重排 + scene_ids round-trip。lint。
- Commit `feat(script): beats — table, repo, router`

### Task 2: BeatsView 前端

- rail「节拍」槽 enable（EditorShell 的 disabled 拿掉），BeatsView：垂直节拍卡列表 + 顶部 Add Beat；BeatCard=序号徽标+标题（内联编辑，blur/Enter 提交）+概述（可折叠 textarea 内联）+关联场景 chips（点击→onOpenScene 跳回剧本定位；编辑态用简单 scene 多选下拉——数据源=已加载 scenes 投影）+删除（两击确认既有范式）
- 拖拽重排照 G2 Outline 的拖拽范式（同款手柄/落点线），落点调 move API
- 空态照 #1139 口径（No beats yet + Add 按钮）；错误 toast；乐观更新+失败回滚照编辑器既有 sync 风格（beats 无并发协议，直接 REST，失败重拉）
- i18n；测试：CRUD 链/move 调 API/chips 跳转/空态/内联编辑提交
- Commit `feat(editor): beats view — ordered beat cards with scene links`

### Task 3: 收口
- ship 两 PR → 真机过场（加 beat/重排/关联场景跳转）→ memory。

## Self-Review 已做
- 与 laper 基线的节拍语义对齐（beat sheet 卡片流）；scene 关联 jsonb 决策记录（YAGNI 不建 M:N）；无 AI v1 边界明确
- 守卫/迁移/#1006/i18n 全排查；不碰 rail 其他槽位
