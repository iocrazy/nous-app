# harness 第五轮 · 三期 3a「产出与血缘：登记口 + 版本链 + 三处消费面」设计

> 前序：P4 总 spec `2026-09-05-harness-p4-task-visibility-control-design.md`（§0 第四行痛点「用不上：产出消费与回看」，§1 原语 ④「产出必须登记且带血缘」，§6 把本期列为第 3 期）；2a `2026-09-06-...-phase2a-control-plane-design.md`；2b-1 `2026-09-09-...-phase2b1-replay-fork-timeout-design.md`；2b-2 `2026-09-10-...-phase2b2-orchestration-design.md`（完成账在其 plan Task 7）。
> **状态（2026-09-10）**：用户认可范围与 UI（画板「Issue Workbench」新页「三期 3a · 产出与血缘（浅色）」两块稿）。三张小票并入本期 Task 范围；技术取舍由作者定；**用户只验 UI**（§5）。
> **分期**：总 spec 第 3 期内容一分为二。**3a（本期）= 地基 + 三处消费面**：登记口、血缘与版本链、线程产出卡 / @引用产出 / 对象页反查。**3b（下期）= 消费与账**：检索、日报、效率账、消息反馈、制片人视图（另立产品 spec）。

## 0. 前提核对（执行前必读，2026-09-10 两路勘察，master 1550b965）

| 假设（多来自总 spec） | 实况 | 结论 |
|---|---|---|
| 第 3 期要新建 `run_deliverables` 表（总 spec §3） | **453 已建好**（`supabase/migrations/453_harness_p4_phase1_schema.sql:109-129`）：`id/run_id/seq/kind/ref_id/version/parent_version/created_at` + 两个索引 + service_role RLS；ORM `RunDeliverables`（`backend/app/models/agents.py:656`）。`deliverable` 也早在事件类型 CHECK 内（453 → 459 → 460 → 461） | 本期**不建表**；只加列与唯一索引（mig 462） |
| `register_deliverable` 已有白名单实现（总 spec §1④「第 1 期一次放行」） | 只存在于散文：`models/agents.py:658` docstring 与 spec 本身。**零调用方、零 emitter、零 fold**；`lineage` 在前后端零命中 | 登记口本期从零写 |
| 咽喉点覆盖「写资源 / 写画布 / 写分镜 / 发布」四臂 | **两臂没有调用方**：agent 工具集（`services/ai/runner/agent_runner.py:119` `SUPPORTED_TOOLS` = Skill / Delegate / ResourceFetch / FinishIssue / GenerateImage / GenerateVideo / AskUser + screenwriting）里**没有任何写画布的工具**（画布是前端保存路径在写），也**没有发布工具**。第三臂「写资源」也不成立：agent 不直接写 `resources`（该表只有 `prompt_origin`，无 run 引用），它写的是 `generated_media` | 本期只接**三类**：`generated_media`（图/视频）、`script_shot`（分镜）、`script_scene` / `script_chapter`（场景与章节）。画布与发布**明确不做**（§6） |
| 「唯一入口」要新造 | `register_generated_media()`（`services/library/generated_media_service.py:281`）已经是 `generated_media` 的**唯一插入口**，其 `GenerationOrigin`（`:253-278`）已带 `run_id / agent_id / model / provider / cost_cents / parent_resource_id / derivation_kind / conversation_id` | 登记口**叠在它上面**（不是并列），五个调用点零改动——与 #2009 骑 `safe_popen_kwargs()` 同族 |
| run 上下文一路都在 | **分镜两条 DBOS 路径丢 run**：`workflows/script_shot_generate.py:227`、`script_shot_video.py:164` 构造 `GenerationOrigin` 时不传 `run_id`/`agent_id`，上下文在派发时就没了 | 本期最具体的缺口：派发 payload 带 `run_id`/`turn`/`step`，workflow 内回填 origin |
| 血缘字段（模型 / 花费 / 指纹）在表里 | `run_deliverables` 现有列**没有** `model` / `cost_cents` / `turn` / `step` / `title`；总 spec §2 的 `run.lineage` 却要这些 | mig 462 补列（见 §3）；**不写 `metadata_json.lineage`**（偏差，理由见 §2.4） |
| UI 可以叫「交付物」 | 「交付物」已被占用两处，其一就在**同一个详情页**：`frontend/components/Todolist/blocks/DeliverablesBlock.tsx:29-35`（`zone:'context', order:30`，项目阶段文件夹拖放区），另一处是 `WorkspaceStageBoard` | 界面统一叫「产出 / Outputs」，两块并存；事件与表沿用 `deliverable` / `run_deliverables` |
| `step.summary.outputs` 可以复用成产出计数 | 它今天数的是**assistant 文本消息条数**（`TrajectoryRenderer/foldEvents.ts:334-345`，detail 是 `{chars}`） | 产出计数另起字段，不碰它 |
| Generated 收件箱能反查 run | `GeneratedSource`（`services/library/generated_source.py:23-65`）对 `agent_run` 只给平文本「Chat generation」且 `deep_link=None`；行上无 issue_id | 来源行改成「谁 · 哪个 issue · 哪个 run · 第几步」且可点（§4.4） |
| @引用要新做选择器 | 回复框已是 tiptap + 完整 @ 管线（`Todolist/IssueReplyBox.tsx`，共享件在 `components/chat/`：`ChatInputResourceMention.ts` / `ResourcePickerSuggestion.tsx` / `ResourceChipNode.tsx` / `useMentionAssetsTab.ts`），附件统一经 `Todolist/composerAttachmentPayload.ts` 映射成 `resource_ref` / `asset_ref` / 文件三种 | 只加**一个页签 + 一种附件类型 + 一个映射分支**。⚠️ 该映射器头注释写明：漏一个分支**不会抛错**（`AttachmentRequest` 全字段 Optional），是静默失败 |
| 事件与折叠有现成搭车口 | 后端 17 个 fold 一族一文件（`services/ai/runner/folds/`），注册表 `run_projection.register()`（`:108`）+ 文件末显式 import（`:150`，无目录扫描）；落事件唯一入口 `services/ai/runner/events.py:22 emit()`；前端 18 个 case（`foldEvents.ts`）+ 节点注册表 `nodes/registry.ts:26-40`（有测试钉住每个 kind 都有渲染器） | 照 2b-2 加 `subagent_spawned` 的整条线复刻（迁移 → fold → recorder 认族 → 前端 case → 渲染器） |
| 跨 DBOS 边界的事件顺序可靠 | 不可靠且已有先例：`foldEvents.ts:569-585` 的「第二遍」就是为了 `subagent_result` 认领比 `subagent_done` 晚到 | 产出登记同样跨 DBOS（分镜出图），fold 必须**顺序无关**（§2.3） |
| 前端可以直接读 `cost.by_child` | 它只在后端存在（`folds/subagents.py:71-73`），前端 `RunCost` 接口里没有、也没有 selector | 与本期无关，记小票（§7 已有清单） |

> **写 plan 时的偏差（本节以 spec 为准，plan 不得自行调整）**
> - 总 spec §3 把 `run_deliverables` 列为第 3 期新建表 —— 实际 453 已建，本期只有 `ALTER` + 唯一索引。
> - 总 spec §2 要 `agent_runs.metadata_json.lineage` —— 本期**不写**，理由见 §2.4；视图侧只落计数 `view.outputs`。
> - 总 spec §1④ 的四臂收缩为三类（画布 / 发布无调用方）。

## 1. 登记口（咽喉点）

### 1.1 唯一入口

```python
# backend/app/services/deliverables/registry.py  （新模块）
async def register_deliverable(
    *,
    run_id: int | str | None,          # None ⇒ 非 agent 产出，直接 no-op 返回 None
    kind: str,                          # generated_media | script_shot | script_scene | script_chapter
    ref_id: str,
    title: str | None = None,
    model: str | None = None,
    cost_cents: float | None = None,
    turn: int | None = None,
    step: int | None = None,
    recorder: Any | None = None,        # 给得出就用；给不出按 run_id 取 RunEventWriter
) -> DeliverableRow | None: ...
```

一次调用做三件事，缺一不可：① 算版本（§2.1）并插 `run_deliverables` 行；② 经 `events.emit(recorder, "deliverable", payload, turn=, step=)` 落一条 transcript 事件；③ 返回该行（调用方一般丢弃）。**没登记 = 不存在**（与「DBOS 失败必须 raise」同族纪律）。

模块放在 `services/deliverables/` 而不是 `services/ai/runner/` 下：调用方在 library / script 两个域，反向依赖 runner 会让 `generated_media_service` 拖进整个 runner 包。

### 1.2 三个接线点

| 类 | 挂在哪 | 说明 |
|---|---|---|
| `generated_media` | `register_generated_media()` 插行成功之后（`generated_media_service.py:281`） | `origin.run_id` 为空即 no-op，所以画布 / 上传 / 前端生成路径零影响；`model` / `cost_cents` 直接取 `origin` |
| `script_shot` | `services/ai/scope/scoped_script_gateway.py` 写 `script_shots` + `script_shot_ops` 的四处（`:845,865,911,929`） | 该网关已带 `created_by_agent_run_id` / ops 的 `run_id`，登记与 ops 账本同一事务边界 |
| `script_scene` / `script_chapter` | `repositories/script_scene_repository.py:552,577`（场景 `content_json` + `script_ops`）、`workflows/script_ai_workflows.py:67,136`（章节改写 / 分支） | 场景侧今天只有 `actor='agent:<run_id>'` 字符串里藏着 run id；章节侧连这个都没有，需从调用方透传 |

### 1.3 丢 run 上下文的那条路（本期必修）

`GenerateShotImage` / 出视频经 DBOS 派发到 `workflows/script_shot_generate.py` 与 `script_shot_video.py`，两者构造 `GenerationOrigin` 时不带 `run_id`/`agent_id`。修法与 2b-2 让 workforce 任务带 `issue_id` 同形：**派发时把 `{run_id, turn, step}` 放进 workflow payload**，workflow 内构造 origin 时回填。这样登记口不必知道自己在不在 DBOS 里。

事件写在**已经结束的父 run** 上是允许的，且有先例：workforce worker 用 `RunEventWriter.for_run(parent_run_id)` 在父 run 结束后写 `subagent_done`。`register_deliverable` 在 `recorder=None` 时走同一条路。

### 1.4 覆盖守卫

「唯一入口」靠三样东西防退化，缺一条就会悄悄漏登记：
1. **源码扫描测试**：`generated_media` 的 INSERT 只允许出现在 `register_generated_media` 内（已有同族守卫的写法可抄）；`script_shots` / `script_scenes` 的 agent 写入只允许经 `scoped_script_gateway` / `script_scene_repository` 的登记版本。
2. **契约测试**：三个接线点各一条「写出去 ⇒ 恰好一行 `run_deliverables` + 恰好一条 `deliverable` 事件」。
3. **负向**：`run_id=None`（人手改 / 前端生成）⇒ 零行零事件。

MCP 外发工具（`agent_framework/mcp_outbound_registry.py:110`）是**开放集合、静态不可枚举**，本期不纳入覆盖承诺，写进 §6。

## 2. 血缘与版本

### 2.1 版本链

同一对象（`kind` + `ref_id`）被 agent 再次改动即 `version = max(version)+1`，`parent_version` 指向上一版；人手改动**不登记、不占号**。首次登记 `version=1, parent_version=NULL`。

并发两次登记同一对象会算出同一个版本号——本期用 `UNIQUE (kind, ref_id, version)`（mig 462）把它变成 `IntegrityError`，登记口捕获后**重算一次**（一次重试足够：冲突只发生在并发窗口内，第二次读到的 max 已是对方的值）。二次冲突照抛，宁可失败也不写两个 v2。

「回到 v1」不是回滚：它以 v1 的内容新建 v3，血缘照记。

### 2.2 事件载荷

```
deliverable {kind, ref_id, version, parent_version, title, model, cost_cents, turn, step}
```
`title` 进事件与列，**有界**：复用 2b-2 的 `clip_claimed_text`（≤500）同族做法，产出标题截 120。模型可见面不受影响（`deliverable` 不进 `replay.messages_from_events` 读的那三类）。

### 2.3 折叠（顺序无关）

新 fold `backend/app/services/ai/runner/folds/deliverables.py`：

```
view.outputs = {total, revised, last:{kind, ref_id, version, title}}
```
`revised` = `version > 1` 的条数。**同一 (kind, ref_id, version) 重复到达只记一次**——fold 保留一个已见集合（有界：只留最近 50 个键），因为跨 DBOS 的事件可能重放。计数只增不减，不依赖到达顺序。

前端 `foldEvents.ts` 新增 `deliverable` case：把产出卡挂到**它所属的那一步**（`turn`/`step` 坐标），照 `subagent_done` 的「跨所有 step 节点找卡」写法，晚到的事件也能落到正确的步上；步不存在（事件比 `step_start` 早到或 run 已结束）则挂到最后一个 step，永不丢弃。

### 2.4 为什么不写 `metadata_json.lineage`（对总 spec §2 的偏差）

`run_deliverables` 每行已经是「一次产出」的完整记录（含 run_id、seq、版本链，mig 462 后含 model / cost / 坐标）。再往 `agent_runs.metadata_json.lineage` 复制一份，等于对同一事实立两个来源——正是路线 C 第 1 条要消灭的东西，而且 JSON 侧无法加唯一约束。视图里只保留**计数**（UI 要的 Cockpit 格），清单一律走表。

## 3. 数据模型（迁移先行，单独 PR）

**mig 462**（取号执行时重扫，461 是当前最后一个）：

| 变更 | 说明 |
|---|---|
| `run_deliverables` 加列 `title TEXT`、`model TEXT`、`cost_cents NUMERIC(12,4)`、`turn INT`、`step INT` | 喂 §2.2 的血缘；全部可空（存量零行，但按可空写才允许将来补登记） |
| `CREATE UNIQUE INDEX run_deliverables_kind_ref_version_key ON run_deliverables (kind, ref_id, version)` | §2.1 的并发闸门 |
| `CREATE INDEX idx_run_deliverables_ref_latest ON run_deliverables (kind, ref_id, version DESC)` | 「取最新版」与版本链读取 |
| **小票**：`agent_run_inbox` 的 dedupe 并发唯一索引（2b-2 遗留） | 与本期同批，理由见 §7 |

ORM 镜像 `backend/app/models/agents.py::RunDeliverables` 同 PR 改（schema-drift 门禁两向零容忍）。`tests/models/test_transcript_event_types_phase2a.py` 的 `LATEST_MIGRATION` 推到 462。

**不加 `issue_id` 列**：`run_deliverables.run_id → agent_runs.issue_id` 已是可靠路径（2b-2 起 `issue_id` 在创建时写入 + 存量已回填），按 issue 列清单走这一跳 JOIN，避免第二份真相。

## 4. 接口

| 端点 | 作用 | 失败语义 |
|---|---|---|
| `GET /api/v1/issues/{id}/outputs` | 本 issue 全部产出，按 `(kind, ref_id)` 分组、组内按版本倒序；每项带 `latest_version / versions[] / run_id / turn / step / model / cost_cents / title` | 非 owner 404（照既有 issue 可见性） |
| `GET /api/v1/outputs/{kind}/{ref_id}` | 一个对象的血缘：版本链 + 每版的 run / issue / 坐标 / 模型 / 花费 | 无登记 404 `not_registered`（**不是**空数组：没登记与没产出是两件事） |
| `GET /api/v1/outputs/{kind}/{ref_id}/diff?from=&to=` | 文本类（`script_*`）返回两版可渲染文本；媒体类返回两版的 `generated_media` 行与 URL | 版本不存在 404 `version_not_found`；跨对象 400 |
| `POST /api/v1/issues/{id}/messages` 的 `attachments[]` 新增 `output_ref` | `{kind:"output_ref", ref_kind, ref_id, version}` | 引用未登记 / 不属于本 issue → 400 `output_ref_unresolvable`（**类型化回显**，不静默丢） |

`GET /api/v1/generated` 的 `source` 对象：`agent_run` 分支补 `issue_id` / `run_id` / `step` / `deep_link`（指向 issue 详情页并锚到那一步）。

## 5. UI（画板「三期 3a · 产出与血缘（浅色）」两块稿，用户只验这一节）

**稿一「详情页 · 产出」**
- 线程：产出卡挂在产出它的那一步下面，形状抄 2b-2 子代理卡（同一个 `.sub` 缩进区）。两态：`v1 · 新建`（ok 色）/ `vN ← vN-1 · 修订`（warn 色）。卡上一行元信息：模型 · 花费 · 第几步；右侧「打开」＋（修订才有）「看差异」。媒体类带缩略图。
- Cockpit 新增第 5 格「产出 4 · 2 修订」，读 `view.outputs`，经 `runView.ts` 新 selector `outputsState`（组件不摸 JSON）。
- 右栏新块「产出」（`zone:'context'`, order **25**，排在既有「交付物」块之前且两块并存）：按对象分组的只读清单，悬停高亮线程里对应卡，点版本号开差异弹层。
- 差异弹层：文本类并排两栏、新增段落底色高亮；媒体类左右两版缩略图。底部「打开 run #…」与「回到 vN（新建 vN+1）」（后者本期只画，不做，见 §6）。
- Generated 卡补来源行：`Script Ai · MH-91 · run #913402 · 第 3 步`，可点。

**稿二「作曲区 · @引用产出 + 对象页反查」**
- @ 弹层新增第三个页签「产出 · 本 issue」，默认只列每个对象的最新版，旧版折在下方（引用旧版 = 「以那一版为准」）。选中插入 chip，形状与既有资源 chip 一致。
- 发出后：线程里人的消息显示 chip；下一轮 run 的第 1 步下出现一行「引用 N 件」，注明**按版本锁定，不随后续修订漂移**。
- 对象页（分镜 / 场景 / 资源信息面板）通用一小块「来源 / 版本链 / 花费」，三个按钮：打开 issue、打开 run、看差异。人手改的对象没有这块。
- 模型可见面：新增 `<referenced_outputs>` 框，**只给 id + 版本 + 标题**，内容由 agent 用既有工具自取。新框必须同时登记进 `OWNED_FRAMES`（否则 `test_frame_escape_wiring.py` 拒绝），标题走 `escape_frame_attr`。`prompts/README.md` 的三问补一段。

## 6. 明确不做

写画布 / 发布两臂的登记（**今天没有 agent 写入口**，要做得先补工具，另立）；MCP 外发工具的产出登记（开放集合，不可枚举）；「回到 vN」的真实回写（弹层里画了按钮，本期 disabled + title 说明，与资产库 P2 的 `Send To Canvas` 同族做法）；`resources` 表加 run 引用；产出的自动摘要 / 去重；检索、日报、效率账、消息反馈、制片人视图（全部 3b）；`metadata_json.lineage`（§2.4）。

## 7. 并入本期的三张小票（2b-2 遗留）

1. **`tests/workflows` 与 `test_issue_reply_resume` 同跑的顺序依赖假红** —— 每个 PR 都在干扰判读，本期查清并钉住（隔离 fixture，不是加 `-p no:randomly`）。
2. **`agent_run_inbox` dedupe 并发唯一索引** —— 与 mig 462 同批；3a 的引用投递会更依赖收件箱幂等。
3. **`GET /ai-library/agents?slug=` 忽略过滤** —— 验收脚本已踩到（拿到的是 `analyze` 而不是 `script_ai`），是真缺陷，顺手修并补测试。

## 8. 验收（真栈，照 2b-2 口径）

① agent 生成一张图 → `run_deliverables` 恰一行 v1 + `deliverable` 事件 + 线程卡 + Cockpit 计数；② 同一对象二次改动 → v2 且 `parent_version=1`，卡显示修订、差异弹层两栏；③ 分镜出图（DBOS 路径）→ 登记行的 `run_id` 非空（本期缺口的正向证明）；④ 人手改同一对象 → 零新行（负向对照）；⑤ @引用产出发一条 → `output_ref` 落库、下一轮 run 的提示词里有 `<referenced_outputs>`、引用行显示；⑥ Generated 卡来源行可点且落到正确的 issue 与步；⑦ 未登记对象访问血缘端点 → 404 `not_registered`；⑧ `npm run e2e:prod` 绿。

## 9. 纪律（继承 2b-2 §9）

每 Task 独立 worktree（从 `origin/master` 建）+ PR；TDD + 突变记录；对抗评审（opus）全修；flake8 + isort/black/ruff；CI 绿即合并并盯两条部署链；偏离即回写本文与 plan；迁移与消费代码分 PR（ORM 镜像除外，门禁不许拆）；同一轮最多一条依赖 cwd 的 Bash；新增模型可见框必须同时登记 `OWNED_FRAMES`；前端读 `metadata_json.*` 一律经 `runView.ts` selector。

## 10. Hand-off

1. 画板两块稿已由用户认可（2026-09-10）。
2. `superpowers:writing-plans`，Task 粗切：T1 mig 462 + ORM + 收件箱唯一索引 → T2 登记口 + 三类接线 + DBOS 带 run_id + 事件 + fold + `view.outputs` → T3 三个端点 + Generated 来源行 → T4 `output_ref` 附件 + `<referenced_outputs>` 框 → T5 前端线程卡 + Cockpit 格 + 右栏块 + 差异弹层 → T6 前端 @引用页签 + 对象页来源块 + Generated 卡 → T7 两张小票（顺序假红、agents slug 过滤）→ T8 真栈验收 + 完成账。
3. 开工前复核 §0（另一会话在动画布与快捷指令，`generated_source.py` / `IssueReplyBox` 可能又变）。
