# Project Workflow M3 — 设计定稿

日期:2026-07-27 ｜ 状态:已过用户逐段评审拍板 ｜ 上游:`2026-07-20-project-workflow-nodes-v2.md`(M3 行原文:"任意 DAG、自定义 DBOS hook、表单化交付")

## 0. 总拍板(与 v2 spec M3 一句话的对应关系)

| v2 spec M3 项 | 本稿落地形态 | 拍板理由 |
|---|---|---|
| 自定义 DBOS hook | **W1:Stage Hook(confirm 门语义)**——到达时自动"上膛"agent run,人点 confirm 才执行 | 保全 M1 铁律"永不静默 dispatch";单人场景的真痛点是缩短手动路径,不是无人值守 |
| 表单化交付 | **W2:表单构建器**——模板节点自定义字段,Stage Board 填写,推进门校验 | 用户明确选完整构建器(否决了预置字段/纯 checklist 两个轻量方案) |
| 任意 DAG | **W3:依赖门(轻 DAG)**——保留游标,加 backward-only 依赖边 | 真 DAG 需重写游标引擎、推翻 M1/M2 全部推进语义;现有流程本质是阶段推进,依赖门覆盖"跨线等待"这一真实缺口;edges 表向前兼容真 DAG |

沿用 M1/M2 全部既有纪律:origin 四面镜、#1400 preview 纪律、best-effort hook、404 先于 403、snowflake string、schema-drift gate、配置模板层编辑+实例化拷贝、UI 英文+i18n en/zh。

## 1. W1 — Stage Hook(confirm 门)

### 配置

`events` JSONB(mig 386 已建)新增 key,模板节点 Events tab 增开关,实例化拷贝:

- `prepare_agent_run: bool`(默认 `false`)——到达时上膛 agent run
- `on_complete_workflow: string | null`(默认 `null`)——**只建结构不做实现**(白名单系统 workflow 触发留 M3.5+;schema 校验只允许 null)

### 数据(migration 389)

`project_stage_nodes` 加 `metadata JSONB NOT NULL DEFAULT '{}'`(2026-07-27 核实:mig 380 未建此列)。业务侧写入走独立 helper,不与 status 等 trigger 管辖列混写。

### 执行(全部 best-effort,不阻塞 advance/创建)

节点到达且 `prepare_agent_run && owner_agent_id` 时,入队 DBOS workflow `stage_hook_dispatch`(用 DBOS 保 worker 重启不丢):

1. 确认镜像 issue 存在(`ensure_node_issues` 已跑过,幂等复查)
2. 发 inbox 通知(kind 沿用 `workflow_stage`,文案 "Agent run ready — review & run",`link_kind='issue'` 深链镜像 issue)
3. 节点 `metadata` JSONB 记 `run_prepared_at`(ISO 时间戳)

**绝不调用 dispatch。** 执行只发生在人点 confirm 之后。同节点重复到达(回退再前进)不重复上膛:`run_prepared_at` 已存在则跳过通知。

### 前端

CurrentNodeCard 与 WorkspaceStageBoard 的 suggest chip 升级:节点 `metadata.run_prepared_at` 存在时,由"Suggested: run {agent}"文字 chip 变为实心 **"Run now"** 按钮 → 弹既有 `DispatchConfirmDialog`(预填镜像 issue + agent)→ confirm 走既有 dispatch 链。路径从四步(开 Todolist→找 issue→点 run→confirm)缩为两步。

### 测试要点

prepare 绝不 dispatch(monkeypatch 断言);无 agent owner 不入队;DBOS 入队失败不影响 advance 返回值;重复到达不重复通知;`on_complete_workflow` 非 null 被 schema 拒绝。

## 2. W2 — 表单化交付(form builder)

### 数据模型(migration 390)

- `workflow_template_nodes.form_schema JSONB NOT NULL DEFAULT '[]'`
- `project_stage_nodes.form_schema JSONB NOT NULL DEFAULT '[]'`(实例化拷贝,改模板不影响存量)
- `project_stage_nodes.form_data JSONB NOT NULL DEFAULT '{}'`

字段定义:`{key: string, label: string, type: enum, required: bool, options?: string[]}`

- `type` 限 6 种:`text / textarea / number / select / checkbox / date`
- `key` 由 label slug 化自动生成(服务端保证节点内唯一,冲突加序号)
- 护栏:每节点 ≤20 字段(422);`options` 仅 select 有效;`form_data` 未知 key 服务端丢弃

### 编排器

节点面板第 4 个 tab **Form**:行式字段编辑器(添加/删除/上下移;label 文本框、type 下拉、required 开关、select options 逗号分隔输入)。不做拖拽画布。走既有 updateNode 本地 state → Save 全量替换,零新保存路径。

### 填写面

- Stage Board Deliverables 区上方渲染表单(`form_schema` 非空才渲染);manager/editor 可填;失焦保存
- 保存走既有 `PATCH /projects/{id}/workflow/nodes/{node_id}` 新增 `form_data` 字段——**form_data 是运行时数据不是配置**,不违反"配置只在模板层编辑"拍板;`form_schema` 仍不可从实例 PATCH 修改
- CurrentNodeCard 显示完成度 "Form 3/5",点击跳 Stage Board

### 推进门

advance predicate 新增独立阻断码 **`FORM_INCOMPLETE`**:节点存在 `required` 字段且未全填 → 阻断;preview 列出缺失字段 label。判定规则:`text/textarea/select/date` 空串或缺失=未填;`number` 缺失=未填(0 算已填);`checkbox` required 语义=必须勾选(false=未过)。与 DELIVERABLE_MISSING 并列不混淆;同 predicate 复算(#1400)。

### 测试要点

schema 拷贝独立性;六类字段 required 判定逐条(含 checkbox false、number 0);未知 key 丢弃;FORM_INCOMPLETE 进 preview 与 advance 同源;无 schema 节点全量零回归;字段 ≥21 → 422。

## 3. W3 — 依赖门(轻 DAG)

### 数据模型(migration 391)

```
workflow_template_node_deps  (node_id FK CASCADE, depends_on_node_id FK CASCADE, UNIQUE(node_id, depends_on_node_id))
project_stage_node_deps      (同构,实例化拷贝)
```

**核心约束:依赖只能指向 `sort_order` 更小的节点。** 服务端保存/PATCH 时校验,违反 → 422 `DEP_BACKWARD_ONLY`(自依赖同罪)。该约束天然免除环检测,且保证游标推进永不死锁。

### 语义

- advance forward 进入目标组时:目标组每个非 skipped 节点的**所有依赖**必须已 `done` 或 `skipped`(skipped 视为满足),否则阻断码 **`DEPS_PENDING`**,preview 列出等待中的节点名
- 与 REVIEW_PENDING / DELIVERABLE_MISSING / FORM_INCOMPLETE 并列,同 predicate 复算
- 回退(back)不受依赖限制
- 实例删除被依赖节点(受 M2-W3 既有 409 保护的删除路径):边随 FK CASCADE 消失=依赖自动解除

### 编排器与实例

- 模板:Node Info tab 加 "Depends on" 多选(候选=排序在前的节点)
- 实例:节点 PATCH 接受 `depends_on: string[]`(依赖属实例结构,与 M2-W3 实例节点增删同级,非"配置");同样 backward-only 校验

### 展示(刻意轻,不画连线)

- Stage Board 节点头:依赖未满足时 rose 色一行 "Waiting on: X, Y"
- WorkflowStrip:当前组被依赖阻塞时胶囊加小锁标
- AdvanceConfirmDialog:渲染服务端阻塞清单(前端零推导)

### 测试要点

backward-only(自依赖/前向依赖 422);skipped 依赖满足;DEPS_PENDING 与 preview 同源;拷贝独立性;删除节点 CASCADE 清边后 preview 实时解除;无依赖项目零回归。

## 4. 分期与 PR

- **M3-W1** Stage Hook:后端(events 扩展 + stage_hook_dispatch workflow + metadata)→ 前端(Run now chip + DispatchConfirmDialog 接线)→ e2e
- **M3-W2** 表单:mig 389 + models/schemas → 编排器 Form tab → Stage Board 填写 + FORM_INCOMPLETE → e2e
- **M3-W3** 依赖门:mig 390 + 边表 repo → predicate DEPS_PENDING + 编排器/实例编辑 → 展示三点 → e2e

每波一个 PR,base master 顺序合并(**不做堆叠**——M2 收尾实测堆叠 squash 链的 retarget 坑,见 memory `project-stacked-pr-squash-merge-trap`);CI 绿即合;migration 号以 `ls supabase/migrations | tail` 现状为准顺延。

## 5. 明确不做(YAGNI 边界)

- `on_complete_workflow` 的实现(只留结构)
- 真 DAG / 去游标 / 画布连线
- 表单字段的文件类型、跨节点引用、条件显隐
- hook 的无人值守自动执行(将来若做,以 per-hook `auto_execute` 开关 + 预算护栏另立设计)
