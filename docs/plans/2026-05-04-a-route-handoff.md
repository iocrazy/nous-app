# A 路线交接 — 2026-05-04 Session

> **作者**: Claude (heygo session, 2026-05-04)
> **目的**: 一天 16 个 PR 的批量交付，下次 session 接手不需要回看会话历史
> **背景**: 用户原话「我要的是系统的最优解」→ 选了 A 路线（保留 DBOS + 系统化补强），不走 B（自建 task engine）

---

## 0. 今天解决的真实痛点

1. **DBOS executor wedge** —— dev 多次 hard kill 累积 sched-* PENDING workflow，DBOS recovery 一次性 burst 把 ThreadPoolExecutor 打满 → 后续所有 workflow 卡 PENDING
2. **system_status 表 43k 写/天** —— cron 30s 写单行 + Realtime 广播，hot row 竞争
3. **WS token 明文 in URL** —— JWT 进 server log / Sentry / 浏览器历史
4. **Cancel 不真停工作** —— 仅改 task_tracking row，subprocess（ffmpeg/whisper）继续烧 CPU
5. **任务集 + 定时任务缺失** —— 用户提的两个核心需求
6. **logger.error 吞 traceback** —— admin 审计 application_logs.exception 列长期 NULL
7. **Workflow lifecycle bug** —— trigger 漏镜像 phase + progress；parse_workflow 静默 return failed dict 导致 DBOS 误标 SUCCESS
8. **抖音 dev 解析挂** —— httpx 缺 brotli + Surge 代理 fake-IP
9. **Lane 优先级缺失** —— 高优 user click 被 background AI flood 阻塞

---

## 1. 16 PR 总览（合并顺序）

### 第一批 — 独立小修（先合，最安全，~1 day 观察）

| PR | 主题 | 风险 |
|----|------|------|
| #149 | logger.error → logger.exception (admin audit traceback) | 极低（mechanical sed） |
| #152 | brotli for httpx (douyin parse) | 极低（加 1 个 dep） |
| #153 | asyncio.run for Py 3.13 scheduled workflows | 极低（3 文件 sed） |
| #150 | trigger phase/progress + parse_workflow raise on save fail | 低（mig 200 trigger 修改） |
| #154 | DBOS pre-launch sweep stale sched-* (修今天 dev 卡死) | 低（dev 已验证 138 stale 清掉） |

### 第二批 — 基础设施新增，无破坏性

| PR | 主题 | 依赖 |
|----|------|------|
| #156 | Lifecycle Bus (in-process + Redis pubsub) | — |
| #157 | kill_tree + AbortRegistry cancel infra | #156 |
| #155 | system_status → Redis (砍 43k 写/天) | — |
| #158 | WS short-lived ticket auth | — |
| #161 | 4-Lane queue + admin /lanes/snapshot | — |
| #151 | workflow health classifier (用户掌控自动 cancel) | mig 200 |
| #148 | rename UnifiedTaskManager → TaskTrackingManager | 21 file 机械改名 |

### 第三批 — 业务架构改动（合完跑 24h 看 prod）

| PR | 主题 | 风险 |
|----|------|------|
| #145 | Phase 1 gateway/worker 物理隔离 | 中（docker-compose 拆服务） |
| #146 | Phase 2 per-run subprocess 隔离 (env toggle) | 中（默认 inprocess，flip env 才生效） |
| #147 | Phase 3 WorkforceScheduler → @DBOS.scheduled | 中（删了 in-process scheduler） |
| #159 | task_flows 表 + cascade trigger (用户提的"任务集") | 低（新表，业务代码尚未接入 flow_id） |
| #160 | user_schedules 表 + master scheduler (用户提的"定时") | 低（新表，前端 UI 没做） |

### 第四批（暂未做） — 留下次 session

| 编号 | 主题 | 工时 |
|------|------|------|
| **A4 修订版** | agent_tasks 合并到 task_tracking（用 task_kind 列区分） | ~6h |
| **A6** | 前端 TaskCenter 重写（以 flow 为单位 + schedules 页 + lanes 看板） | ~6h |

**A4 决定**：只合 agent_tasks，**不动 inbox/outbox**（actor mailbox 模式有意分离，列结构不重叠，特化索引价值高）。

---

## 2. 部署观察清单

### prod 部署后立即看

```bash
# 1. healthz
curl -fs https://mediahubserver.heygo.cn:88/api/v1/healthz

# 2. 查 application_logs 有没有新模式的 ERROR
SELECT level, module, message, count(*)
FROM application_logs
WHERE logged_at >= NOW() - INTERVAL '30 minutes'
  AND level IN ('ERROR', 'WARNING')
GROUP BY level, module, message
ORDER BY count DESC LIMIT 20;

# 3. 验 #154 sweep 工作
grep "pre-launch sweep" backend logs

# 4. 验 #155 Redis HASH 在写
redis-cli -n 0 HGETALL mediahub:system:status

# 5. 验 #157 cancel infra（手动取消一个 task → 看是否 abort_registry 日志）
```

### 跑 24h 后看

```sql
-- DB 写入压力降了多少
SELECT count(*) FROM system_status WHERE updated_at > now() - INTERVAL '24 hours';
-- 之前: ~43,200
-- 期望: 0 (整条 PR #155 后停写)

-- DBOS workflow_status 没堆积
SELECT status, count(*)
FROM dbos.workflow_status
WHERE updated_at/1000.0 > EXTRACT(EPOCH FROM now() - INTERVAL '24 hours')
GROUP BY status;
-- 期望: SUCCESS 占绝大多数

-- WS ticket 流量
redis-cli -n 0 KEYS 'ws_ticket:*' | wc -l
-- 期望: 少量 (30s TTL，频繁刷新)
```

---

## 3. 下次 session 接手清单

### 起步检查
```bash
cd /Volumes/program/project-code/repos/mediahub
gh pr list --search "is:open author:@me created:2026-05-04" --json number,title,state
```

### A4 修订版（agent_tasks 合并）

```bash
git checkout master && git pull
git checkout -b refactor/a4-merge-agent-tasks
```

**Schema 改动**：
```sql
ALTER TABLE public.task_tracking
  ADD COLUMN task_kind         TEXT NOT NULL DEFAULT 'workflow',
  ADD COLUMN agent_id          UUID,
  ADD COLUMN parent_task_id    UUID REFERENCES public.task_tracking(dbos_workflow_id),
  ADD COLUMN root_task_id      UUID,
  ADD COLUMN inbox_message_id  UUID;

-- task_kind enum: 'workflow' (default) | 'agent_task'
CREATE INDEX idx_task_tracking_task_kind ON public.task_tracking(task_kind);
CREATE INDEX idx_task_tracking_agent ON public.task_tracking(agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX idx_task_tracking_parent ON public.task_tracking(parent_task_id) WHERE parent_task_id IS NOT NULL;

-- 数据迁移 agent_tasks → task_tracking
INSERT INTO public.task_tracking (
  dbos_workflow_id, user_id, task_type, status, phase, title,
  metadata, error_code, error_msg, created_at, started_at,
  completed_at, updated_at,
  task_kind, agent_id, parent_task_id, root_task_id, inbox_message_id
)
SELECT
  id::text, user_id, 'agent_task',
  CASE lifecycle_status
    WHEN 'queued'      THEN 'pending'
    WHEN 'assigned'    THEN 'pending'
    WHEN 'in_progress' THEN 'processing'
    WHEN 'done'        THEN 'completed'
    WHEN 'failed'      THEN 'failed'
    ELSE lifecycle_status
  END,
  CASE lifecycle_status
    WHEN 'queued'      THEN 'queued'
    WHEN 'assigned'    THEN 'queued'
    WHEN 'in_progress' THEN 'in_progress'
    WHEN 'done'        THEN 'completed'
    WHEN 'failed'      THEN 'failed'
    ELSE lifecycle_status
  END,
  COALESCE(title, 'Agent task'),
  jsonb_build_object('payload', payload, 'result', result, 'current_run_id', current_run_id),
  error_code, error_message, created_at, started_at,
  ended_at, updated_at,
  'agent_task', agent_id, parent_task_id, root_task_id, inbox_message_id
FROM public.agent_tasks
WHERE id::text NOT IN (SELECT dbos_workflow_id FROM public.task_tracking);

-- inbox/outbox FK target 重指向（task_id 列）
-- 注意: UUID → text 类型变化，需要 cast
ALTER TABLE public.agent_inbox  DROP CONSTRAINT IF EXISTS agent_inbox_task_id_fkey;
ALTER TABLE public.agent_outbox DROP CONSTRAINT IF EXISTS agent_outbox_task_id_fkey;
-- 不强制 FK（task_tracking PK 是 text，inbox.task_id 是 uuid，转换成本高）
-- 应用层改为读 task_tracking WHERE dbos_workflow_id = inbox.task_id::text
```

**代码改动 4 文件**：
- `app/services/workforce/agent_worker.py`：`AgentWorkforceRepository.update_task_status` 改写 task_tracking
- `app/services/workforce/scheduler.py` / `inbox_processor.py` / `outbox_dispatcher.py`：查 task_tracking WHERE task_kind='agent_task'
- `app/repositories/agent_workforce_repository.py`：所有 SELECT/INSERT 切表
- 验证 task_tracking 加 5 列后 #150 trigger / #151 health classifier / #157 cancel registry 自动适用

**回归测试**：
- 触发一个 agent delegate task → 看 task_tracking row 出现 with task_kind='agent_task'
- 看 trigger 自动镜像 status / phase
- 触发 cancel → AbortRegistry signal → agent worker 退出
- 验证 inbox/outbox dispatch 仍正常 → outbox.task_id text 联到 task_tracking

**最后 drop agent_tasks**：
```sql
-- 跑 7 天后
DROP TABLE public.agent_tasks CASCADE;
```

### A6 前端 TaskCenter 重写

```bash
git checkout -b feat/a6-taskcenter-rewrite
cd frontend && npm run dev  # 起 dev server (port 5176)
```

**改动文件**：
- `frontend/components/TaskManager/TaskCenter.tsx` — 顶层组件，按 flow 折叠
- `frontend/components/TaskManager/FlowCard.tsx` (新) — flow 卡片含子 task 折叠
- `frontend/services/flowService.ts` (新) — 调 /api/v1/flows
- `frontend/contexts/TaskManagerContext.tsx` — WS 连接改用 ticket 流程：
  ```ts
  const { ticket } = await api.post('/api/v1/ws/ticket')
  const ws = new WebSocket(`${WS_URL}/ws/task-progress?ticket=${ticket}`)
  ```
- `frontend/pages/Settings/SchedulesPage.tsx` (新) — `/api/v1/schedules` CRUD UI + cron picker (用 react-cron 库)
- `frontend/pages/Admin/LanesSnapshot.tsx` (新) — `/api/v1/lanes/snapshot` 看板
- `frontend/pages/Admin/SystemStatus.tsx` — 改 WS push（订阅 mediahub:system:status:changed pubsub）

**测试场景**：
- 提交一个 douyin URL → TaskCenter 显示 1 个 flow card (parse → download → transcribe 折叠在内)
- Cancel flow → 所有子 task 立即标 cancelled
- 创建 daily-9am 定时任务 → 9am 看是否触发
- Admin lanes 页看到 4 lane 实时饱和

---

## 4. 已知遗留 / 暂不修

| 项 | 描述 | 暂缓原因 |
|----|------|---------|
| Discord MCP `client not logged in` | 整 session Discord 通知都失败 | 你重新登录就好，不需要代码改动 |
| 抖音 cookie 4 天前 | dev 用户 cookie 老了 | 不影响（PR #152 brotli 解决了 dev 解析；prod 用 prod cookie） |
| Surge 代理 fake-IP `198.18.x.x` | dev 测抖音解析靠加 SSRF_DEV_ALLOWLIST | 已在 .env 处理；prod 不涉及 |
| commitment_sweeper / memory_archival 还有零星 ERROR | DBOS executor 还没修干净的偶发 | PR #153 + #154 部分缓解；prod 单进程长跑应不复现 |
| `app/agent_framework/lifecycle_bus.py` 旧版 | A8 引入新 `app/services/lifecycle_bus.py` | 老版还在用，标 deprecated；下个 session 迁移所有 caller 后删 |
| `app/services/unified_task_manager.py` 命名 | 已 rename 到 task_tracking_manager (PR #148) | OK |

---

## 5. 决策记录

| 决策 | 理由 |
|------|------|
| ❌ Sprint R (自建 task engine) | DBOS 没真瓶颈，wedge 是 dev hard kill 累积，prod 不会复现；自建 1500 行只覆盖 DBOS 30% 功能 |
| ✅ A 路线 (修补 + 系统化) | 5 工作日 vs 4 周；保留 DBOS durable execution 价值；解决用户全部痛点 |
| ❌ 合并 inbox/outbox 到 task_tracking | actor mailbox 模式有意分离，列结构不重叠 |
| ✅ 合并 agent_tasks 到 task_tracking | 列重叠大，能复用 #151 health / A3 flows / A8 bus / A10 cancel 全套基础设施 |
| ✅ system_status → Redis | DB 写 -99%；用户已经有 Redis；跨进程 pubsub 比 PG Realtime 快 |
| ✅ DBOS sweep 而非自动 reaper task_tracking | 用户反馈 "不要乱删我的任务"；只清 sched-*（DBOS 内部 cron 生成的，每分钟新一个替代旧的） |

---

## 6. 文档索引（今天产出的）

- 本文档：`docs/plans/2026-05-04-a-route-handoff.md`
- Notion Q&A: [Rust sidecar 是否值得](https://www.notion.so/35675c5fd44f811b9a12d20daf8b2656)
- 已有相关：`docs/plans/2026-05-01-d10-d11-openclaw-borrowables.md`（mediahub 团队 5/1 写的，A 路线大部分思路从这里来）

---

## 7. 联系上下文

PR 队列：https://github.com/iocrazy/mediahub/pulls?q=is%3Aopen+author%3Aheygo+created%3A2026-05-04

任何问题：
- DBOS 行为不正常 → 先看 PR #154 sweep 是否生效
- DB 写入暴增 → 看是不是哪个 PR rollback 没干净，重新 INSERT system_status 表
- 取消任务没真停 → 看 abort_registry 是否注册到 lifecycle bus 订阅
- WS 连不上 → 看 frontend 是否还在用 ?token=（兼容期 6 个月，但日志会有 debug 提示）

— EOF
