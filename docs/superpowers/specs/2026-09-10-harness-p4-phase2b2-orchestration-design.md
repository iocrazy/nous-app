# harness 第四轮 · 二期 2b-2「编排：后台/续聊子代理 + 定时唤醒进收件箱 + 接活 workforce 链」设计

> 前序：P4 总 spec `2026-09-05-harness-p4-task-visibility-control-design.md`（§0 第三行痛点「编不起：多 agent / 定时 / 外部触发」，§6 把 schedule 与 continuable 子代理列在第 2 期）；2a `2026-09-06-harness-p4-phase2a-control-plane-design.md`；2b-1 `2026-09-09-harness-p4-phase2b1-replay-fork-timeout-design.md`（§9 Hand-off 把 2b-2 定义为「schedule → 收件箱 + continuable 子代理，先接活 workforce 链」）。
> **状态（2026-09-10）**：用户认可范围与 UI（画板「Issue Workbench」新页「二期 2b-2 · 编排（浅色）」两块稿）；三张小票并入本期 Task 范围；技术取舍由作者定；**用户只验 UI**（§5）。

## 0. 前提核对（执行前必读）

| 假设 | 实况（2026-09-10 勘察） | 结论 |
|---|---|---|
| workforce 链只差「最后一根线」 | 四处都断：`workflows/workforce_dispatch.py:71-74` 的 step 裸建 `InboxProcessor()`（无 dispatcher）；`dbos_pool.dispatch` 若塞进 step 会在 `@DBOS.step` 内 enqueue（路线 C 禁止）；`inflight_count` 只在 `dbos_pool.py:95` 递增、全仓无递减；`claim_next_queued`（`agent_workforce_repository.py:655`）生产零调用方，重复防护实为 `agent_worker.py:110-117` 的读后判 | 四条都要动；范式照 `scheduled_master.py:88-92 / 711-714`（step 返回 orders，workflow body 真派发） |
| `WorkforceScheduler` 还在跑 | `main.py:78-83` 注明 PR-D8 已移除，生产不启动；`tests/test_workforce_scheduler.py` 8 个用例测的是死组件 | 唯一路径是 DBOS scheduled；死组件与其测试本期删除 |
| workforce 任务的 `task_tracking.phase` 由 trigger 同步 | 不是。workforce workflow id 是 `workforce-<task_id>`，与 `task_tracking.dbos_workflow_id`（应用层 uuid）不同，mirror trigger 对它无效，所以 `agent_worker` 直接 PATCH `phase/status` | 这是路线 C 第 2 条的**刻意例外**，本期在 `agent_worker.py` 模块 docstring 写明，不改行为 |
| 既有 schedule 只有写死的 cron | `user_schedules`（mig 204 + 370）+ `scheduled_master` 每分钟扫；`agent_routine` 已走「建 issue → `issues_router._dispatch_execute_issue`」；API 白名单 7 种 task_type，引擎注册表只认 4 种（`ai_transcription` / `ai_visual_analysis` 永远 skip） | 不造第二套定时器；新增 `issue_wakeup` 一种 task_type；白名单与注册表不一致本期修 |
| `cron_expr` 可空 | `NOT NULL`（mig 204） | mig 461 放开，一次性行 `cron_expr IS NULL` |
| schedule 与 `agent_run_inbox` 已有关联 | 零关联；`runner/` 下没有 schedule 概念 | 到点 = 复用 `issue_messages_router` 的「在跑改投 inbox、空闲派发」路径 |
| 子代理已可续聊 / 异步 | `SubAgentTaskService._spawn` 同步跑一轮 `run_turn`；子 run `conversation_id=NULL`、只以 transcript 事件存在；spawn 不 emit 任何事件；`run_projection.py:44` 的 `children{total,done}` 是死占位 0/0 | 续聊用 2b-1 的 `replay.messages_from_events` 重建；异步骑 workforce 链；`children` fold 从零写 |
| `agent_runs.issue_id` 在 issue run 上有值 | 创建处 `ai_library_chat_service.py:1331` 的 `RunRecorder(...)` 没传 `issue_id`；只有回合结束后 `issue_lifecycle.py:646` 的 `backfill_issue_id` 兜底，崩溃/中断/空产出路径永远补不上 | 创建时透传，backfill 保留；存量经 `_BACKFILLS` 回填 |
| fork / resume 的守卫覆盖派发全程 | `dbos_workflow_id` 在 `start_execute_issue` 返回时已同步写好，`execution_locked_at` 要等 workflow 起来才写；窗口内 `running_root_run_id` 与 `execution_locked_at` 都为空，第二次派发被 `atomic_checkout` 静默 `{"skipped": True}`，而 fork 已把 `ai_session_id` 切走 | 派发前落 `execution_state.dispatching` 标记，`atomic_checkout` 清；三处读方按 TTL 判 busy |
| 前端已有子代理/定时的落点 | `issueBlocks` 的 `timeline` zone 空；`parent_run_id` / `child_runs` 在 `frontend/` 零出现；schedule UI 只在 Agent 工作台 Routines 面板；主页 Quick 无「定时」芯片 | 子代理卡、定时卡、Quick 芯片都是新画；子 run 面板复用 2b-1 `DetachedRunPanel` |
| Vitest 偶发 `EnvironmentTeardownError` 是环境抖动 | `AISettings.tsx:594` 的 `getAIGovernance()` 未 mock，真发 fetch，`.finally(setGovernanceLoaded)` 在 jsdom 拆卸后落定 | 补 mock + `tests/setup.ts` 全局 fetch 桩兜整类 |

> **写 plan 时的偏差（2026-09-10，三段勘察后已定，plan 以此为准）**
> - §1/§6：`agent_tasks` 有 ORM 模型（`models/agents.py:568`），schema-drift 门禁两向零容忍，模型与 `DROP TABLE` 必须同 PR——「迁移与消费代码分 PR」在这一条上让位于门禁；`check-realtime-publication-drift.sh` 没有期望表清单（它 diff 的是前端订阅），DROP 不需要改它。`tests/models/test_transcript_event_types_phase2a.py` 钉着 `LATEST_MIGRATION = 460`，461 要推进；`agent_run_inbox.kind` 此前无 ORM 镜像测试，本期补。
> - §1：`InboxProcessor.tick()` 早就返回 `tasks_created`，而 `inbox_dispatch_workflow` 读的是从不存在的 `tasks_enqueued`——那行日志从没打出过，T3 一并修。`inflight_count` 没有 healthz 消费方；`workforce_router.py:409` 的健康端点读 `app.state.workforce_scheduler`，PR-D8 之后无人设置，至今恒报 down——T3 改读 DBOS pool 与派生计数。
> - §2：`run_turn` 的历史参数叫 `user_messages`（续聊把重建消息传这里）；`create_task()` 把 payload 存在 `task_tracking.metadata.agent_payload` 且需要 `agent_id`（spawn 时按 slug 解析目标子代理 UUID），payload 多带 `caller_agent_id` 供 worker 重建服务；`run_one_task` 的 `agent.persistent` 门会拒掉非持久子代理，`subagent` 分支放在 agent 解析之前；`deliver_or_dispatch` 多一个 `already_enqueued`——worker 已经写过 `subagent_result`，忙态分支不许二次插入，只要空闲派发那一臂。
> - §3：`issue_messages` 没有 `author_kind`，定时发起的行是 `kind='comment'` + `author_user_id`=规则所有者 + `meta.source={kind:"schedule", schedule_id, created_by}`；`ScheduleCreatePayload` 的 `name` / `cron_expr` 改可选、加 `fire_at`，`ScheduleResponse.cron_expr` 可空；400 的 `detail` 必须是 dict（`ErrorResponse` 外壳把字符串 detail 变成 `details: null`）；`agent_routine` payload 没有 issue 目标字段，`GET /issues/{id}/schedules` 按 `payload.last_issue_id` 列出它。
> - §5：主页 Quick 芯片纯前端从列表行算，没有可用字段——issue 列表项加 `pending_wakeups: int`（`list_for_user` 一次聚合），T6 因此是小全栈任务；「由定时唤醒开始」芯片不能从 run 事件推（首条 `user` 事件只是文本），改读紧邻在前的 `meta.source.kind === "schedule"` 线程行；`IssueReplyBox` 现无 `issueId` prop，要从 `IssueDetailView` 传入；`schedulesService.create` 要求 `name`/`cron_expr`，前端另写 `createIssueWakeup`；`subagent_done` 可能落在比派出更晚的 step，折叠要跨所有 step 节点找卡。
> - 本期没有给 `run_turn` 结果加任何标志（派出/完成都是事件），`test_turn_end_reasons.py` 不动。

> **T1 实施记录（2026-09-10，mig 461 已落地）—— 七处与计划不同**
> 1. **`cron_or_once` 的写法按计划是漏的，已修**：`(payload->>'once') = 'true'` 在没有 `once` 键时求值为 NULL，而 **Postgres 把 NULL 的 CHECK 当作满足**——一条 `task_type='issue_wakeup'`、`cron_expr IS NULL`、payload 里没声明一次性的行会被放行，而它永远不会重新上弦。改成 `coalesce(payload->>'once','') = 'true'`。（`task_type='download'` 两种写法都拒绝：`FALSE AND NULL` 是 FALSE，不是 NULL。）真库正反两向都验过，源码守卫钉住这个 `coalesce`。
> 2. **`models/storyboard.py::UserSchedules.cron_expr` 必须同 PR 改成 `Mapped[Optional[str]]`**，计划的文件清单漏了它。schema-drift 的第 3 关逐列比对 nullability 且**没有豁免名单**，`ALTER COLUMN … DROP NOT NULL` 不配模型就是红。突变验过：改回非空 → `test_nullability_matches` 只报这一列。
> 3. **`DROP FUNCTION bump_agent_tasks_updated_at()` 一并做掉**：mig 159 只为 `agent_tasks` 建了这个 trigger 函数，全仓再无第二处挂它，DROP TABLE 之后它是纯孤儿（真库确认表没了函数还在）。
> 4. **`tests/models/test_transcript_event_types_phase2a.py` 新增 `_first_array()`**：既有 `_literals` 断言「恰好一个 `ARRAY[...]`」，而 461 有两个（事件白名单 + inbox kind）。只把 `test_migration_and_orm_event_type_sets_are_identical` 切到取第一个数组，`_literals` 本身不放宽——放宽会污染 459/460 的调用方。本 Task 唯一一处改既有测试助手。
> 5. **`agent_run_inbox.kind` 的 ORM 镜像测试已补**（`tests/db/test_migration_461_orchestration.py::test_inbox_kind_orm_literal_matches_the_migration_exactly`）：schema-drift 只比列不比 CHECK 体，事件类型那个镜像测试只管另一张表，此前这两份清单没有任何东西绑住。
> 6. **还有第三个 pin，计划没点到：`tests/db/test_migration_460_event_type_fork.py::test_460_matches_the_orm_check_literal_exactly`**（全量套件才暴露）。它断言 460 的字面量集**等于** ORM——那句话只在「460 是最新的」时才等价于「ORM 跟得上最新迁移」，461 一来必红，而且每加一次白名单迁移就要再改一次。改成子集断言（460 admitted 的一个都不许从 ORM 掉出去）并改名 `test_460_literals_all_survive_in_the_orm_check`；「等于最新」这件事只留在 `LATEST_MIGRATION` 那一处。突变验过：从 ORM 摘掉 `fork` → 该测试红。**后续白名单迁移照此办理——新迁移的文件里写等号，旧迁移的文件里写包含。**
> 7. **schema-drift 本机真跑过，不是 skip**：照 `schema-drift.yml` 的配方起 `pgvector/pgvector:pg17` → `ci_bootstrap.sql` → `schema_baseline.sql` → watermark 364 之上 102 个迁移（含 461）全部 `ON_ERROR_STOP=1` 通过，`INTEGRATION_DATABASE_URL` 指过去后 8 个 gate 全绿（不是 7 skipped）。三个 CHECK 与 (d) 的自禁 UPDATE 都在真库上做了正反对照。

## 1. 接活 workforce 链（前置，T3）

**原则**：一条链只有一个执行入口（DBOS scheduled tick → enqueue → `agent_workforce_workflow` → `run_one_task`），死组件与假承诺一起清掉。

| # | 待接线（`delegate_feature.py` 文档串） | 做法 |
|---|---|---|
| 1 | scheduled `InboxProcessor` 没有 dispatcher | `inbox_dispatch_tick_step` 只做队列整形，然后**返回**所有「`task_kind='agent_task' AND phase='queued' AND metadata.dispatched_at IS NULL`」的任务（收件箱刚整形出来的 + §2 子代理直接建的）；`inbox_dispatch_workflow` 在 workflow body 逐条 `DbosAgentWorkforcePool.dispatch(task)` 并写 `metadata.dispatched_at`（幂等：DBOS 以 `workforce-<task_id>` 固定 workflow id，重复派发被去重）。不给 step 传 dispatcher |
| 2 | 不在 `@DBOS.step` 内 enqueue | 同上；`dbos_pool.dispatch` 加断言「不在 step 内」（DBOS 提供的上下文判定，若无则源码守卫：`workforce_dispatch.py` 的 step 函数体内不得出现 `dispatch(`） |
| 3 | `inflight_count` 只增不减 | 删进程内估算；`inflight_count()` 改为 `task_tracking` 派生：`task_kind='agent_task' AND phase IN ('queued','in_progress')`（异步方法，`/healthz` 调用点随之 await）。docstring 改真 |
| 4 | `claim_next_queued` 死码 | 删除；新增按 id 的 CAS `claim_task(task_id) -> dict | None`（`UPDATE … SET lifecycle='assigned' WHERE id=:id AND lifecycle='queued' RETURNING`），`run_one_task` 用它替代读后判；三处宣称 CAS 的 docstring（`agent_workforce.py:9-11,39-42`、`worker_pool.py:11`、`agent_worker.py:13`）改成事实 |

- 特性开关：`FEATURE_WORKFORCE_DELEGATE` 从裸 `os.getenv` 迁进 `settings`（`config.yml` 键 `FEATURE_WORKFORCE_DELEGATE: false`），真栈验证链路后在 T7 翻成 `true`（单独 PR）。
- 删除 `services/workforce/scheduler.py`、`worker_pool.py`（in-process pool，生产不用）与 `tests/test_workforce_scheduler.py`；`DbosAgentWorkforcePool` 成为唯一 pool。
- 新增链路测试 `tests/workflows/test_workforce_chain.py`：假 repo 里放一条 unread inbox → 跑 `inbox_dispatch_workflow` 的 body（DBOS 桩）→ 断言 `dispatch` 被以该 task 调用一次、且不在 step 内；再喂 `agent_workforce_workflow` → `run_one_task` 用 `claim_task` 拿到 `assigned` 并写 outbox。
- 顺带：mig 200 承诺的 `DROP TABLE agent_tasks` 在 mig 461 兑现（**已落地**）。全仓对该表名的引用只剩 docstring，零 SQL 命中；两个 FK 都是自引用，随表消失，不需要 CASCADE。ORM 模型 `models/agents.py::AgentTasks` 与 `models/__init__.py` 的导出同 PR 删除（schema-drift 两向零容忍）。⚠️ `check-realtime-publication-drift.sh` **没有期望表清单**——它扫的是前端 `table: '…'` 订阅，而前端从不订阅 `agent_tasks`，所以那个脚本不用改（表从 publication 里自动消失，真库确认）。mig 159 建的 `bump_agent_tasks_updated_at()` 同批 DROP。

## 2. 子代理：后台 + 续聊（T4）

**原则**：一份 `_spawn` 实现，两个驱动；子代理的一切经**父 run 的事件**可见；结果回父 run 走**收件箱**（唯一队列）。

### 2.1 工具参数（`Skill(skill="task")`，schema 在 `prompt_composer.py:547-590`）

| 参数 | 语义 |
|---|---|
| `subagent_type`, `prompt`, `description`, `tasks[]` | 现状 |
| `await: bool = true` | `true` 同步（现状）；`false` 后台：立即返回 `{status:"queued", sub_run_id: null, task_id}`，结果以收件箱条目回来 |
| `child_run_id?: str` | 续聊：以该子 run 的事件重建消息，追加 `prompt` 再跑一轮；可与 `await` 任意组合 |

`tasks[]` 并行形态不支持 `child_run_id`（一次只续一个）；`await=false` 与 `tasks[]` 可同用（逐条入队）。

### 2.2 后台路径

1. `_spawn(await=False)` 不建 runner；写一条 workforce task（`task_tracking`，`task_kind='agent_task'`，payload `{kind:"subagent", parent_run_id, subagent_type, prompt, description, child_run_id?, reply_to:{target_kind, target_id}, user_id}`），`reply_to` 取父 run 的目标（issue 或 conversation；父 run 无目标则拒绝 `await=false`，返回类型化 `status:"failed", error:"no_reply_target"`）。
2. 入队走 §1 接活的链：子代理任务**不经** `agent_inbox`，直接建 `queued` 行；`_spawn` **不**自己 enqueue——issue 的 agent 回合本身跑在 DBOS step（`run_issue_agent_step`）里，在这里 enqueue 就是 step 内派发。下一个 `inbox_dispatch` tick（≤10s）把它和收件箱整形出来的任务一起派发（§1 第 1 条的 step 返回的是「所有 queued 且未派发的任务」，不只是本 tick 新建的）。
3. `run_one_task` 按 `payload.kind == "subagent"` 分支：构造 `SubAgentTaskService(caller=父 run 的上下文)` 调同一个 `_spawn(await=True)`（真正跑一轮），拿到 envelope。
4. 完成后：写 `agent_run_inbox(kind="subagent_result", target=reply_to, content={child_run_id, subagent_type, description, status, summary, cost_cents, tokens_used})`；同时仍写 `agent_outbox`（既有审计）。
5. 父 run 在下一个步边界经既有 `InboxClaimHook` 领取，渲染为 `<inbox_message kind="subagent_result" child_run_id="…" subagent_type="…">…summary…</inbox_message>`（属性过 `escape_frame_attr`，正文过 `escape_frame_prose`；框已在 `OWNED_FRAMES`）。
6. 目标 issue 已空闲（根 run 结束）时收到 `subagent_result` → 与「评论触发新一轮」同一路径派发（`issue_messages` 的改投/派发函数抽成 `services/issues/inbox_or_dispatch.py::deliver_or_dispatch(issue_id, item)`，评论、定时唤醒、子代理结果三处共用）。conversation 目标只挂起，等用户下一句（聊天路径无 pause，一贯口径）。
7. 父 run 自身是子代理（depth ≥ 1）时不许 `await=false`（子 run 没有 target）——返回 `error:"async_not_allowed_for_subagent"`。

### 2.3 续聊路径

- `child_run_id` 必须是当前父 run（或其 `fork_of` 祖先）派出的子 run（`agent_runs.parent_run_id` 链校验），否则 `error:"not_your_child"`。
- 消息 = `replay.messages_from_events(events_upto(child_events, last_seq))` + `{role:user, content: prompt}`；新子 run 行 `fork_of_run_id=child_run_id, fork_at_seq=last_seq, parent_run_id=父 run`，metadata `continued_from=child_run_id, round=n`。不加新列。
- 子代理 system prompt 沿用 `_spawn` 的固定 `request_instructions`；续聊不重发 description。

### 2.4 事件与折叠

| 事件（父 run 上） | payload | 时点 |
|---|---|---|
| `subagent_spawned` | `{child_run_id?, task_id?, mode: "sync"\|"async", subagent_type, description, continued_from?}` | `_spawn` 决定派出时（同步：子 run 行建好后；后台：task 建好后，`child_run_id` 为 null） |
| `subagent_done` | `{child_run_id, task_id?, mode, status, cost_cents, tokens_used, duration_ms}` | 同步：`_spawn` 返回前；后台：`run_one_task` 完成后由 worker 以父 run id emit（`RunEventWriter(parent_run_id).append()`，父 run 已结束也照写——事件比 run 长寿） |

`folds/subagents.py`：`view.children = {total, done, running, async_pending, last:{child_run_id, subagent_type, status}}`（替换死占位）；`view.cost.by_child[child_run_id] = cost_cents`，子代理花费计入父 run 的 `spent_cents`，再由既有 issue 汇总计入 issue。mig 461 放行两个事件类型与 `agent_run_inbox.kind='subagent_result'`。

### 2.5 明确边界

子代理不领取收件箱（`inbox_hook.py:43` 的根 run 判定不变）、不可 steer、不可 pause——控制面在父 run。子 run 的 `conversation_id` 保持 NULL；`issue_id` 继承父 run（§4.2 透传后自然带上）。

## 3. 定时唤醒进收件箱（T5）

**原则**：不造第二套定时器；「到点」只是一条收件箱 steer，源头写在 `content.source`。

### 3.1 `user_schedules.task_type='issue_wakeup'`

- payload：`{issue_id: int, text: str, once: bool, created_by: "user"|"agent", run_id?: int}`。
- 一次性：`cron_expr IS NULL`（mig 461 放开 NOT NULL，CHECK：`cron_expr IS NOT NULL OR (task_type='issue_wakeup' AND (payload->>'once')='true')`），`next_fire_at` 直接给；触发后 `enabled=false`、`pause_reason='fired_once'`。
- `scheduled_master._resolve_workflow_callable` 不认它；`fire_due_schedules_step` 按 task_type 分支到 `_fire_issue_wakeup`（同 `_fire_agent_routine` 的位置）：只做 DB 写与判断、**返回 order**，派发在 workflow body（既有范式）。
- 到点逻辑（`deliver_or_dispatch`，与 §2.2 第 6 条同一函数）：
  - issue 终态 / hidden → 不投，`enabled=false`，`pause_reason='issue_terminal'`，`skipped_count+1`；
  - issue 有运行中根 run 或已暂停 → 写 `agent_run_inbox(kind="steer", content={text, source:{kind:"schedule", schedule_id, created_by}})`；
  - 空闲 → 先落一条 `issue_messages`（body=text，`author_kind='schedule'`，让线程可见「由定时唤醒开始」）再派发；派发失败按 `_fire_agent_routine` 的连击簿记。
- API：`POST /schedules` 接受 `task_type='issue_wakeup'`，校验 issue 归属当前用户可见团队、`text` 非空、一次性必须带 `fire_at`（写成 `next_fire_at`）；`GET /issues/{id}/schedules` 新增（只读，列该 issue 的 `issue_wakeup` 行 + 指向它的 `agent_routine` 行，供右栏「定时」卡）；取消走既有 `DELETE /schedules/{id}`。
- 修白名单：`schedules_router` 的 task_type 白名单改为**从 `scheduled_master` 的注册表导出**（单一来源），`ai_transcription` / `ai_visual_analysis` 因而不再可建；存量行由 mig 461 `UPDATE … SET enabled=false, pause_reason='task_type_unsupported'`。

### 3.2 agent 工具 `ScheduleWakeup`

- 参数 `{at?: ISO-8601, delay_minutes?: int, note: str}`（二选一，上限 30 天）；只在**issue run**（根 run 且 `issue_id` 非空）上向模型公开（同 `ResourceFetch` 的按上下文公开机制），子代理不公开。
- 执行：以 issue owner 的 `user_id` 建 `issue_wakeup` 一次性行（payload `created_by:"agent", run_id`），父 run emit `schedule_set{schedule_id, fire_at, note}`；返回 `{schedule_id, fire_at}`。同一 run 最多 3 条（第 4 条返回 `error:"too_many_wakeups"`）。
- 提示词：工具描述一句「Schedule a one-time wake-up for this issue; when it fires you will receive the note as a message. Use it to wait for long external work instead of polling.」（进 `prompts/README.md` What the model sees）。
- `folds/schedule.py`：`view.wakeups = [{schedule_id, fire_at, note}]`（只记本 run 设的，取消由 §3.1 的 API 读实时表）。

## 4. 三张小票（T2 / T6）

### 4.1 派发窗口守卫（T2）

- `issue_dispatch.start_execute_issue` 在 `_dispatch_execute_issue` **之前** `merge_execution_state(issue_id, {"dispatching": {"workflow_id", "at"}})`；`atomic_checkout` 成功时把它置 null（同一 UPDATE）；派发失败的恢复路径也置 null。
- 读方（`issue_fork.run_live` 判定、`issues_router` resume 判定、`issue_messages` 改投判定）新增：`dispatching` 存在且 `at` 距今 < 60s → 视为 busy（fork 409 `issue_busy`，resume 409，评论改投 inbox）。超过 60s 的残留标记视为过期（workflow 没起来，收割器同款）。
- 突变：去掉 `atomic_checkout` 的清除 → 「派发后 60s 内 fork 永远 busy」的测试红。

### 4.2 `issue_id` 创建时写入 + 回填（T2）

- `issue_agent_executor` → `AILibraryChatService.run_session_turn(..., issue_id=)` → `RunRecorder(issue_id=)`；子代理 `_spawn` 从父 run 继承。`backfill_issue_id` 保留为兜底。
- 存量：`_BACKFILLS` 注册 `agent_runs_issue_id`（`UPDATE agent_runs r SET issue_id = i.id FROM issues i WHERE r.issue_id IS NULL AND r.conversation_id = i.ai_session_id`，分批），T7 在真栈跑一次并记录行数。
- `issue_fork` 的 `conversation_id` 反查（#2202）保留一版，待回填后下一期删。

### 4.3 Vitest 拆卸抖动（T6）

- `AISettings.codexDaemon.test.tsx` 的 `aiService` mock 补 `getAIGovernance`（全 true 的治理对象）。
- `frontend/tests/setup.ts` 加同步拒绝的 `globalThis.fetch` 桩（`vi.fn(() => Promise.reject(new Error('fetch is disabled in unit tests')))`），自己装 fetch mock 的测试不受影响；用一次真跑证明整套 `vitest run` 仍绿。

## 5. UI（画板「二期 2b-2 · 编排（浅色）」两块稿，用户已验）

从本期起画板稿改暖纸浅色，与生产一致；2a 及之前的深色稿不重画。

1. **子代理**（`SubagentCards`）：子代理卡挂在**派出它的那一步**下（`foldEvents` 把 `subagent_spawned/done` 折进对应 step 节点的 `children[]`），三态：`同步 · 等结果 · 12s`（ok，spinner）/ `后台 · 结果将进收件箱`（info）/ `✓ 完成 · 8.4s · ¢0.03` + 一行摘要；续聊卡带 `↻ 续聊自 #… · 第 2 轮`（agent 色）；每张卡 `打开 run #…` → 线程上方出 `DetachedRunPanel`（复用 2b-1，头部写「子 run #… · 来自 run #… 第 N 步 · 后台」+ 「回到父 run」）。后台结果到达 = 收件箱行 `📥 子代理结果 · librarian（后台）· 已在第 4 步前读到`（`inbox_claimed{kind:subagent_result}` 折出）。Cockpit 多一格「子代理 1 / 2 · 1 个后台」，只在 `view.children.total > 0` 时出现（同 Tools 格规则）；run 行头结束后仍从事件推导（2b-1 #2208 的口径）。
2. **定时唤醒**（`ScheduleWakeup`）：作曲区加「⏰ 稍后」按钮 → 弹层（预设 1 小时后 / 今晚 20:00 / 明早 09:00 / 自定义；一句说明「到点时：agent 在跑就插进下一步；空闲就用这句话开始新一轮；issue 已结束则不再发。只发一次。」；「定时发送」）。线程三种到点：运行中 = 收件箱行 `⏰ 定时唤醒 · 你 · 昨天 18:02 设 · 已在第 3 步前读到`；agent 自设 = 系统行 `⏰ Agent 定了唤醒 · 明天 09:00 · 「…」· 取消`（`schedule_set` 折出，取消调 DELETE）；空闲到点 = 新 run 气泡头部芯片 `⏰ 由定时唤醒开始`。Cockpit 副行 `⏰ 明天 09:00 唤醒`；右栏新块「定时」（`context` zone，`GET /issues/{id}/schedules`）列一次性与例行各一种样子、可取消、`+ 稍后` 入口；主页 Quick 加「定时 N」芯片（有待到点唤醒的 issue，一期稿预留的位置）。

配色沿用语义 token：子代理 agent 色、后台/定时 info 色、完成 ok 色。

## 6. 数据与端点汇总

| 项 | 变更 |
|---|---|
| mig 461 ✅ | 事件白名单加 `subagent_spawned / subagent_done / schedule_set`；`agent_run_inbox.kind` 加 `subagent_result`；`user_schedules.cron_expr` 可空 + `user_schedules_cron_or_once` CHECK（`once` 的比较必须 `coalesce`，见 T1 实施记录第 1 条）；存量 `ai_transcription` / `ai_visual_analysis` 自禁并写 `paused_at=now()` + `pause_reason='task_type_unsupported'`（**两个都要写**：Routines UI 的 `isPaused = !!paused_at` 是渲染 reason 的门，只写 reason 的行在界面上就是无声停摆）；`DROP TABLE IF EXISTS agent_tasks` + 孤儿 `bump_agent_tasks_updated_at()`。ORM 侧同 PR：删 `AgentTasks` 模型与导出、两个 CHECK 字面量追加、`UserSchedules.cron_expr` 改可空 |
| `Skill(skill="task")` | `+await`, `+child_run_id` |
| 新工具 `ScheduleWakeup` | issue 根 run 专有 |
| `POST /schedules` | `+task_type=issue_wakeup`（一次性 `fire_at`）；白名单改由引擎注册表导出 |
| `GET /issues/{id}/schedules` | 新（只读） |
| `run.view` | `children{total,done,running,async_pending,last}` 变真；`+wakeups[]`；`cost.by_child` |
| `config.yml` | `FEATURE_WORKFORCE_DELEGATE`（默认 false，T7 翻 true） |
| `services/issues/inbox_or_dispatch.py` | 新（评论 / 定时 / 子代理结果共用） |
| 删除 | `workforce/scheduler.py`、`worker_pool.py`、`tests/test_workforce_scheduler.py`、`claim_next_queued` |

## 7. 测试与验收

- 单测：`InboxProcessor.tick()` 返回 created；workflow body 派发且 step 内无派发（源码守卫）；`claim_task` CAS 二次失败；`inflight_count` 派生；`_spawn` 四条拒绝（`no_reply_target` / `async_not_allowed_for_subagent` / `not_your_child` / `too_many_wakeups`）；后台 task payload 形状；`run_one_task` 的 subagent 分支写 `subagent_result` 并以父 run emit `subagent_done`；`folds/subagents`、`folds/schedule`；`deliver_or_dispatch` 三态；`_fire_issue_wakeup` 终态/在跑/空闲三分支与一次性自禁；白名单从注册表导出（两向对照）；`dispatching` 守卫三读方 + TTL；`issue_id` 透传（含「adapter 无 `stream`」用例——凡给 run 结果加标志都要过缓冲回退分支）。前端：`foldEvents` 子代理三态与续聊、Cockpit 子代理格出现规则、稍后弹层与 `POST /schedules` 形状（**真实 wire 形状**）、定时块、Quick 芯片。
- 突变：body 不派发 → 链路测试红；`claim_task` 不带 `WHERE lifecycle='queued'` → 红；`subagent_done` 不以父 run id 写 → `children.done` 测试红；`deliver_or_dispatch` 空闲不派发 → 红；`dispatching` 不清 → 红；fetch 桩去掉 → 拆卸测试可复现抖动（记录一次真跑）。
- 真栈（T7）：① `FEATURE_WORKFORCE_DELEGATE=true` 后 Delegate(await=false) 一条 → tick 内派发 → `agent_workforce_workflow` 跑完 → outbox；② issue 上让 script_ai 派后台子代理 → 父 run `subagent_spawned{mode:async}` → 子 run 行 `parent_run_id` 正确 → issue 空闲后 `subagent_result` 触发新一轮 → `inbox_claimed{kind:subagent_result}` → `subagent_done` 落在父 run；③ 续聊：`child_run_id` 一次 → 新子 run `fork_of_run_id=child`；④ 作曲区「稍后」2 分钟后到点（issue 空闲）→ `issue_messages(author_kind=schedule)` + 新 run；运行中到点 → `inbox_claimed{steer, source.schedule}`；agent `ScheduleWakeup` → `schedule_set` → 右栏可取消；⑤ 回填 `agent_runs_issue_id` 行数；⑥ fork 在派发后 3 秒内 → 409 `issue_busy`；⑦ 前端 `npm run e2e:prod` + 两页对照截图；⑧ `readyz` 与 `inflight_count` 派生值合理。

## 8. 明确不做

cron 型 agent 自建规则（用户例行仍走 Routines UI）；子代理的 steer / pause / 收件箱；conversation 目标的自动续跑；子代理结果的自动摘要压缩；Delegate 与 SubAgentTask 合并（两条路径继续并存，共享安全网）；`agent_inbox`（workforce 邮箱）与 `agent_run_inbox`（harness 收件箱）合并；`issue_fork` 的 `conversation_id` 反查删除（回填后下一期）。

## 9. 纪律（继承 2b-1 §8）

每 Task 独立 worktree（从 `origin/master` 建）+ PR；TDD + 突变记录；对抗评审（opus）全修；flake8 + isort/black/ruff；CI 绿即合并并盯两条部署链（私有仓库 + 额度未恢复时托管 CI 假红，以本地全量 + actionlint 替代门禁，后端 deploy-gpu 不受影响）；偏离即回写本文与 plan 的实施记录；迁移与消费代码分 PR；同一轮最多一条依赖 cwd 的 Bash；给 run 结果加任何标志必须用「adapter 无 `stream`」用例证明穿过缓冲回退分支。

## 10. Hand-off

1. 画板两块稿已由用户认可（2026-09-10）。
2. `superpowers:writing-plans`，Task 粗切：T1 mig 461 → T2 派发窗口守卫 + `issue_id` 创建时写入 + 回填注册 → T3 workforce 四条接线 + CAS + 删死组件 + 链路测试 + 特性开关迁 settings → T4 后台/续聊子代理 + 事件 + fold + `subagent_result` 投递 + `deliver_or_dispatch` 抽取 → T5 `issue_wakeup` + `_fire_issue_wakeup` + `ScheduleWakeup` 工具 + 白名单单一来源 + `/issues/{id}/schedules` → T6 前端（子代理卡、Cockpit 格、稍后弹层、定时块、Quick 芯片、Vitest 修）→ T7 真栈验收（含翻开关、跑回填）+ 完成账。
3. 开工前复核 §0（另一会话可能改 `IssueChatThread` / `runView.ts` / `issue_messages_router`）。
