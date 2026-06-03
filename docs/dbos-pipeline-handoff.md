# MediaHub DBOS 任务流水线 — 已做与计划(供二审 / 跨线冲突检查)

> 写于 2026-06-03。基线:`master @ 60d103c1`(PR #478 已合并并部署到生产)。
> 目的:把 DBOS 这条线"已做 / 计划做 / 涉及文件"讲清,供另一个 agent 二审 +
> 检查是否与并行的另一条 DBOS 改动冲突。

## 背景

后端用 **DBOS 2.19.0**(基于 Postgres 的持久化 workflow 引擎)。生产是
**gateway / worker 分体**(env `MEDIAHUB_ROLE`):gateway 服务 HTTP + 只入队,
worker 执行 workflow。**DBOS 队列消费端是纯轮询**(库内无 LISTEN/NOTIFY,
`_queue.py` 的消费线程是 `stop_event.wait(timeout=polling_interval)`)。

前期已立(更早的 PR,不在本次范围,但二审需知道现状):
- `executor_id` pin 成角色名(`'gateway'` / `'worker'`),见 `app/startup/dbos_init.py`
  → `dbos_orchestrator.init_dbos(executor_id=role.value)`。
- `application_version` pin 成 build 的 `commit_sha`(让 worker 能 dequeue gateway
  入队的活),见 `dbos_orchestrator._resolve_pinned_app_version`。
- gateway `launch_dbos(consume_queues=False)` → `DBOS.listen_queues([])`(不消费用户队列)。

## ✅ 已合并并部署(PR #478,master `60d103c1`,线上已生效)

修复"推 210 首歌单后任务卡在 0 运行 / N 排队"的三个根因(全部 file:line 证据级定位):

- **R1 — `.single()` 崩溃杀 workflow**:`unified_task_manager._get_phase` 用 `.single()`,
  task_tracking 行缺失时抛 `PGRST116`(0 rows)杀掉 soda workflow(日志
  "Future exception never retrieved")。根因:`parse.py::dispatch_soda_download_step`
  吞掉 `create()` 失败却仍 enqueue。
  → 改 `.maybe_single()`,缺行当 `QUEUED`;`start()` 缺行**自愈补建**(真 `user_id`
  从 soda workflow 穿透 —— `task_tracking.user_id` 是 `UUID NOT NULL`,空串会 22P02);
  dispatch 把吞错改成 ERROR 日志。

- **R2 — 全局 `concurrency` → NOWAIT 退避停摆**:DBOS 在队列设了全局 `concurrency`
  时用 `FOR UPDATE NOWAIT` + REPEATABLE READ(`_sys_db.py:3010/3031`),撞锁抛
  `LockNotAvailable` → 轮询间隔 ×2 退到 **120s 上限**(写死)→ 空槽也不取 = 停摆。
  → 分区队列 `parse_user` / `soda_download` / `download_user` / `agent_workforce`
  改成 `worker_concurrency`(已核实在 partition_queue 上是 **per-partition**,每用户并发
  语义不变)→ `FOR UPDATE SKIP LOCKED` + READ COMMITTED,无退避。

- **R3 — gateway 偷调度孤儿撑大表放大锁冲突**:gateway 被 DBOS **强制**消费
  `_dbos_internal_queue`(`_queue.py:225`,即便 `listen_queues([])`),抢到 worker 派的
  `@DBOS.scheduled` tick 却跑不了(没 import `_scheduled_bundle`,函数没注册),
  executor 隔离的 recovery 不回收 → 永久孤儿(曾积 487)。
  → `bootstrap.py::_bg_reap_internal_queue` 从"仅启动"改成**周期(env
  `DBOS_REAP_INTERVAL_SECONDS` 默认 120s,`asyncio.to_thread` 跑同步 psycopg)+
  按 executor 精确清**:
  - `dead_gateway`(任何年龄):`queue_name='_dbos_internal_queue' AND status='PENDING'
    AND executor_id='gateway'`(gateway 跑不了任何东西 = 铁定死);
  - `stale_sched`(老化):`queue_name='_dbos_internal_queue' AND status='ENQUEUED'
    AND workflow_uuid LIKE 'sched-%' AND created_at < now-5min`。
  - **绝不按年龄清任意 PENDING**(测试里加了禁止断言,防误杀跑很久的真任务)。

**#478 改动文件**:
- `backend/app/services/infra/unified_task_manager.py`
- `backend/app/startup/bootstrap.py`
- `backend/app/workflows/agent_workforce.py`
- `backend/app/workflows/download.py`
- `backend/app/workflows/parse.py`
- `backend/app/workflows/soda_download.py`
- `backend/app/workflows/soda_ugc_download.py`
- 新增/改测试:`tests/test_unified_task_manager_missing_row.py`、`tests/test_queue_lock_mode.py`、
  `tests/test_internal_queue_reaper_filter.py`,以及 4 个 `tests/test_*_queue_concurrency.py` /
  `tests/soda/test_soda_ugc_dispatch.py`(`.concurrency`→`.worker_concurrency`)。

后端全套 **2702 passed**;CI(black/isort/flake8/pytest + 前端 + Rust + Vercel)全绿。

## 🟡 计划做、已完整调研、可行性零缺口、尚未写任何代码

**目标**:让 gateway **完全不执行任何 workflow**,根治两件事 ——(a) gateway 偶尔
本地跑用户 workflow(post-#466 14 天 6 次,低频);(b) gateway 偷调度 tick(对每周
一次的 memory archival / consolidation 有"漏跑一周"的小概率风险;#478 的 reaper 只
防孤儿堆积,没阻止"偷")。

**方案**:gateway 的 `DBOS.launch(consume_queues=False)` → 换成官方 **`DBOSClient`**
(`from dbos import DBOSClient`)。已逐一核实:DBOSClient `__init__` 只开连接池、
**零后台线程**(无队列消费 / 无调度 / 无执行池);gateway 需要的全部操作都是
client-native 的纯 DB 操作,**零 API 缺口**:

| 操作 | client API | 底层 | 是否需要 executor |
|------|-----------|------|-------------------|
| enqueue | `client.enqueue(EnqueueOptions(workflow_name, queue_name, app_version), *args)` | 一条 INSERT | 否 |
| cancel | `client.cancel_workflow` | UPDATE→CANCELLED | 否 |
| resume | `client.resume_workflow` | UPDATE→ENQUEUED(**worker 重跑**) | 否 |
| fork | `client.fork_workflow` | 复制+ENQUEUED(**worker 重跑**) | 否 |
| send(审批门) | `client.send` | INSERT notifications | 否 |
| 状态/步骤读 | `client.list_workflows` / `list_workflow_steps` / `retrieve_workflow().get_status()` | SELECT | 否 |

注意:必须调 **`DBOSClient` 实例方法**,不能再调 `DBOS.<x>_async`(未 launch 会抛
"System database accessed before DBOS was launched")。resume/fork 写 `status=ENQUEUED`
让 worker 重跑 —— 正是想要的"gateway 入队 / worker 执行"。

**改动面(尚未动)**:
- gateway 生命周期:`app/startup/dbos_init.py` 不再对 gateway `launch()`,改建一个
  module 级 `DBOSClient`(启动建、关闭 close)。
- **26 处派发**(`start_workflow_routed` / `DBOS.start_workflow`)→ `client.enqueue`。
- **10 处状态/控制/send**(`DBOS.*_async`)→ `client.*`。
- worker 完全不动(照常 `DBOS.launch(consume_queues=True)` + 消费队列/内部队列)。

**计划会触及的文件**(二审重点 + 查重叠):
- `app/services/infra/dbos_orchestrator.py`(`start_workflow_routed` + `launch_dbos`)
- `app/startup/dbos_init.py`
- ~16 个 router:`api/media_fetch_helpers.py`、`api/ai_router.py`、`api/analysis_router.py`、
  `api/sb_ai_router.py`、`api/resources_crud_router.py`、`api/resources_versions_router.py`、
  `api/script_ai_router.py`、`api/task_manager_router.py`、`api/issues_router.py`、
  `api/issue_messages_router.py`、`api/workflows_router.py`、`api/admin/celery_router.py`
- `app/agent_framework/approval_gate.py`(send)
- 可能含 `app/workflows/*` 的队列定义(若把派发改走命名队列而非 `_dbos_internal_queue`)

## ⚠️ 跨线冲突风险(请二审重点判断)

DBOS 的核心面是这几个文件:
- `services/infra/dbos_orchestrator.py`
- `startup/dbos_init.py`
- `startup/bootstrap.py`
- `services/infra/unified_task_manager.py`
- `workflows/*.py`(队列定义 + workflow 本体)

**#478 已经改过**:`parse.py` / `soda_download.py` / `soda_ugc_download.py` /
`download.py` / `agent_workforce.py` / `bootstrap.py` / `unified_task_manager.py`。
**计划中的 DBOSClient 改造**又会大改 `dbos_orchestrator.py` / `dbos_init.py` + 一堆 router。

如果**另一条线也在动这些文件**(尤其 `dbos_orchestrator.py` / `dbos_init.py` /
队列定义 / workflow 本体 / reaper),会和 #478(已合)以及计划中的改造**强冲突**。

## 给二审 agent 的问题

1. 用 `DBOSClient` 替代 gateway 的 `DBOS.launch()`,有没有漏掉的坑?尤其:
   - `app_version` 经 `EnqueueOptions` 传给 worker 才能被对的 executor dequeue(#466 的 pin);
   - `_bounds_registry` 的 dispatch gate(现在在 `start_workflow_routed` 里)往哪放;
   - gateway 关闭时 `DBOSClient` 的 close / 连接池泄漏。
2. 26 处派发改 `client.enqueue` 时,enqueue 到**命名队列**(worker 要注册消费)vs
   直接 enqueue 到 **`_dbos_internal_queue`**(worker 已消费),哪个更对?各有什么坑?
3. 这套改造和"另一条线"在 DBOS 上的改动有没有冲突?该谁先合、怎么排序避免互相覆盖?
4. 严重性判断:已知 gateway 跑用户 workflow 仅 6/14d、调度 tick 被抢是概率事件且
   reaper 已防孤儿堆积 —— 这个大改(碰流水线核心)现在值得做,还是该排后面?

---

## 二审结果(独立 agent 审计,2026-06-03)

**#478(已合):SAFE。** 逐条库验证 R1/R2/R3,无 P0/P1。仅两点无害:R1 自愈行
`task_type='download'` 对非下载恢复错标(纯 UI);R2 多-worker 时全局帽变每-worker
帽(docstring 已写明,当前单 worker 无碍,去重靠 PG CAS 不靠队列)。

**跨线冲突:不冲突。** 另一条线 = `origin/fix/worker-version-sync-hardening`(基于含
#478 的 master):CI 校验 worker==gateway 镜像 + 新 `app/startup/stall_detector.py`
(纯 asyncio+psycopg 查 task_tracking,不碰 DBOS API)+ `bootstrap.py` 9 行注册。
**不碰** dbos_orchestrator/dbos_init/队列定义/reaper/unified_task_manager。与 DBOSClient
计划唯一共享 `bootstrap.py` 但区域不重叠。**建议:先合 worker-version-sync-hardening,
再把 DBOSClient 基于其后 master。**

**DBOSClient 计划:GO-WITH-CHANGES。** 架构成立、动机真实(`start_workflow_routed`
确实在 gateway 进程内跑,`dbos/_core.py:920`),但计划必须补 3 个修正:
1. **enqueue 必须传 `queue_partition_key=str(user_id)`** —— `DBOSClient._enqueue`
   不校验分区键(不同于 `Queue.enqueue`),漏传 → NULL → 分区队列永不 dequeue =
   静默孤儿(`_sys_db.py:2911` 过滤 `queue_partition_key IS NOT NULL`)。
2. **enqueue 必须传 `app_version=_resolve_pinned_app_version()`** —— 不传是 NULL,
   稳态碰巧能跑但回归 #466 的版本 pin(滚动部署可能被旧版 worker 抢)。
3. **必须决定 `is_enabled()` / `_dbos` 单例**在 gateway 不 launch 后的行为
   (`start_workflow_routed` 调 `is_enabled()`)。

修正后的 enqueue 形态:
```
client.enqueue(EnqueueOptions(
    workflow_name=<get_dbos_func_name 注册名,非 fn.__name__>,
    queue_name="parse_user",              # 命名队列(别用 _dbos_internal_queue)
    queue_partition_key=str(user_id),     # 分区队列必传
    app_version=_resolve_pinned_app_version(),  # 保 #466 pin 必传
    workflow_id=workflow_id,              # 替代 SetWorkflowID
    authenticated_user=user_id,           # 替代 DBOSContextSetAuth
), *args)
```
保留 `start_workflow_routed` 外壳(含 `_bounds_registry` dispatch gate,与执行方式无关),
只换尾部的 `DBOS.start_workflow(...)` 为 `client.enqueue(...)`。`DBOS.recv` 留在 worker 侧。
