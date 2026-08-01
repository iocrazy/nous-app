# Workflow M4 — Autopilot(流水线自动化)设计定稿

日期:2026-07-30 ｜ 状态:已过用户逐轮评审拍板 ｜ 上游:M3 设计稿(hook confirm 门、依赖门、表单门均为本期地基)

## 0. 目标与拍板

用户目标:「**整个流程高效化,不会因为哪个卡住就不动了**」。拍板链:

| 决策 | 结论 |
|---|---|
| 自动化档位 | **B 档:自动开工,人守关口**——auto_start 节点依赖满足即自动开工+agent 自动执行;`review_required` 审阅永远等人,绝不自动过审;游标自动级联 |
| 提前嘱托 | 每实例节点 `brief`:开工前随时可写;agent 自动开工时注入上下文;审阅节点进入 in_review 时置顶展示 |
| 预算护栏 | **A 档:项目级日额度**——每项目每日 20 次**自动** dispatch(手动不计),超限降级回"上膛等确认"+通知;失败不自动重试 |
| 先行开工 | 任何未来节点依赖满足即可手动「Start early」(auto_start 的手动版) |

**不做**:审阅超时自动放行、成本金额计费(按次数)、节点级预算、自动重试。

## 1. 数据模型(migration 395,号以现状为准顺延)

```
project_stage_nodes  + brief TEXT NOT NULL DEFAULT ''      -- 提前嘱托(运行时数据,实例 PATCH 可写)
projects             + autopilot_enabled BOOL NOT NULL DEFAULT true   -- 项目级总开关
events JSONB         + auto_start: bool(默认 false)        -- 模板节点配置,实例化拷贝(沿 M3:schema 加 key 即可,无需迁移)
system_settings      'workflow_autopilot' 行:{"daily_auto_runs": 20}  -- env→DB 约定,60s 缓存,随时可调
agent_runs 侧        自动派发需可识别(dispatch 元数据标 auto;具体列/JSONB 依现表结构定,配额只数 auto)
```

## 2. 引擎(全部 best-effort,绝不阻塞既有主操作)

**触发点 → `autopilot_tick(project_id)`(DBOS workflow,幂等)**:
1. 节点状态回流为 done(`_fire_stage_node_sync` 尾部入队)
2. advance / 实例化到达后
3. 兜底:scheduled sweep(每 5 分钟,扫 autopilot_enabled 且有 auto_start 节点的活跃项目)

**tick 逻辑(幂等,每步复查现状)**:
1. 项目 `autopilot_enabled` 关 → return
2. **自动开工**:遍历非终态节点,`events.auto_start && deps 满足(复用 DEPS_PENDING 谓词逻辑) && 未开工` → 置 in_progress + `ensure_node_issues`;owner 为 agent 且未超额度 → dispatch(注入 brief);超额度 → 走 M3 的"上膛"路径(run_prepared + 通知)
3. **级联推进**:当前组全部 done → 内部执行 advance(同一 predicate;REVIEW_PENDING/FORM_INCOMPLETE/DELIVERABLE_MISSING 拦住 → 停 + 单次通知,不重复骚扰);推进成功则新组进入下一轮评估(循环至无可推进)
4. 额度:`当日该项目 auto dispatch 次数 >= daily_auto_runs` → 本 tick 只上膛不派发

**纪律**:审阅门语义零改动(in_review→done 只有人/manager);失败 run 通知 + 节点停 in_progress;所有通知走 M3 `stage_notifications` 同族(kind=workflow_stage)。

**零门槛节点瞬时推进是预期语义**(2026-07-31 实测探针确认):一个组若 review/deliverable/form/deps 全未配置,advance predicate 没有任何东西可拦,autopilot 会在到达当轮 tick 内直接级联跨过它——包括项目创建那一刻。门槛定义"何时算完成",没配门槛=无需等待;不存在隐式的"至少等自己镜像 issue 做完"门。想让节点停下来等,至少配一个门(最轻的是 review_required)。

**tick 内 (auto-start → cascade) 循环到不动点**(同日探针修正):级联打开新组后,`execute_advance` 的尾部 enqueue 被防重入护栏抑制,故 tick 自身负责重跑 auto-start pass 直至一轮无变化(上限 10 轮,超限告警),否则新组的 auto_start 节点会滞留 pending 等外部触发。

## 3. API 与前端

- `POST /projects/{id}/workflow/nodes/{node_id}/start-early` —— 手动先行开工:deps 满足才放行(否则 422 DEPS_PENDING + waiting_on),行为=tick 第 2 步的单节点手动版(agent owner 仍走 confirm 门,手动路径不自动 dispatch)
- 节点 PATCH 增 `brief`(运行时字段,同 form_data 待遇)
- `PATCH /projects/{id}` 增 `autopilot_enabled`
- 前端:
  - 模板编辑器 Events tab 第 5 开关「Auto-start when ready」
  - 工作区头部 Autopilot 开关(chip,开/停一键)
  - 未来节点(deps 满足)显示「Start early」;Stage Board/节点卡 `brief` 输入(textarea,失焦保存,节点 done 后只读)
  - in_review 节点:brief 置顶展示于 Stage Board 审阅区与镜像 issue
  - 通知文案区分 auto 事件("Auto-started ...","Autopilot paused: daily limit reached")

## 4. 测试要点

- tick 幂等(重复入队不重复开工/派发);autopilot_enabled=false 全静默;审阅门永不被自动跨越(专项断言)
- 额度:第 20 次派发后第 21 个候选走上膛;手动 dispatch 不计数;跨天重置(按项目时区?——按 UTC 日,简单一致)
- 级联:全 done 连锁推进到被门拦住;拦住只通知一次
- brief:开工前可写、注入 dispatch payload、in_review 置顶、done 后只读
- start-early:deps 未满足 422;成功路径与 auto 同构;全量回归(advance 22 / deps / form 套件)
- 默认全关(auto_start=false)→ 合并后零行为变化,开关驱动

## 5. 里程碑边界

单 PR 交付(默认关,行为开关驱动,风险可控);后续按真实使用再议:审阅超时放行、金额计费、真 DAG。
