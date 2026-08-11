# Provider 容错（类型化错误面 + request-id + 存量 fallback 配置）设计

2026-08-11 · 权限页梳理 spec §7 路线图的 P1 项。起因：doubao-seed-2-0-pro 自 2026-08-08 起持续
429，命中它的 agent（summarize/analyze/coordinator 为系统预设主模型）直接对用户裸 500
`{"code":"internal_error","request_id":null}`；qwen 引擎此前也静默停摆过三周。两次事故的共同点
不是缺容错机制，而是**机制建好了没接完**。

## 0. 现状盘点（侦察实据）

**已存在且在跑的**（本期零改动）：
- `LLMFallbackChain`（`backend/app/services/ai/llm/llm_fallback_chain.py`）：主模型重试 3 次
  （`LLMRetryMiddleware`，429/5xx 可重试 + 指数退避）→ 逐个换 `fallback_models` → 全败抛
  `AllModelsFailed("primary + N fallback(s) exhausted")`。
- `ModelHealthRegistry`（进程内每模型冷却：429→60s / 401、403→3600s / 5xx→30s），启动时挂
  `app.state.model_health`，链路已读。
- `ai_agents.fallback_models TEXT[] DEFAULT '{}'`（mig 155）+ admin 后台编辑框
  （`admin/src/pages/agents/index.tsx`）。
- `error_catalog.classify_ai_error()`：走异常链的类型分类器，已有 `PROVIDER_RATE_LIMIT` /
  `PROVIDER_UNREACHABLE` / `PROVIDER_AUTH` / `PROVIDER_BAD_MODEL` / `TASK_TIMEOUT` /
  `OUTPUT_PARSE` / `INTERNAL` 七码——**只接了 DBOS workflow 路径，从没接 chat/agent-runner**。

**三个真缺口**（本期各补一个）：
1. `AllModelsFailed` / `LLMCallError` 是裸 `Exception`，落进 FastAPI 兜底 handler → 500
   `internal_error`，与真 bug 无法区分。
2. 无 request-id 中间件，`ErrorResponse.request_id` 恒 null，报错无法关联日志。
3. 所有 agent 的 `fallback_models` 都是空数组（"primary + 0 fallback(s)" 的真意）——链路建好了
   但没人配过，`summarize`/`analyze`/`coordinator` 三个预设裸奔在 429 的 doubao-pro 上。

## 1. 拍板决策

| 决策点 | 结论 |
|--------|------|
| 本期范围 | 档 1：类型化错误 + request-id 中间件 + 存量预设配 fallback（Redis 冷却接线 / 主动探针 / 用户配置面留路线图）|
| 错误面实现位置 | `core/exceptions.py` 注册**全局 exception handler**（非逐调用点 try/except）——一个 handler 覆盖 chat/issue/未来所有端点 |
| 分类器 | 复用既有 `classify_ai_error()`，不新写分类逻辑 |
| fallback 链内容 | `doubao-seed-2-0-pro-260215 → doubao-seed-2-0-lite-260428`（lite 有 14% 空产出史但 pro 当前 0% 可用；**qwen 不进救援链**——死过三周的引擎不进急救箱）|
| migration 范围 | 只动 `is_system_preset=true` 且 `fallback_models='{}'` 的行——不覆盖任何已手配值、不动用户自建 agent（他们模型自选，admin 后台可自配）|
| 前端 | 只做 `friendlyError` 的 code 映射文案（en/zh），不做重试按钮等 UI（YAGNI）|

## 2. S1 · 类型化错误面

### Handler（`backend/app/core/exceptions.py`）

注册针对 `AllModelsFailed` 与 `LLMCallError` 的 exception handler（在既有
`BoundaryError → AppError → HTTPException → RequestValidationError → Exception` 链中、
兜底 `Exception` 之前生效——FastAPI 按异常类精确匹配，注册即优先）：

1. `classify_ai_error(exc)` 得类型码；
2. 映射 HTTP 状态：

| error_catalog 码 | HTTP | `ErrorResponse.code`（小写）|
|------------------|------|------------------------------|
| `PROVIDER_RATE_LIMIT` | 503 | `provider_rate_limit` |
| `PROVIDER_UNREACHABLE` | 502 | `provider_unreachable` |
| `PROVIDER_AUTH` | 502 | `provider_auth` |
| `PROVIDER_BAD_MODEL` | 502 | `provider_bad_model` |
| `TASK_TIMEOUT` | 504 | `task_timeout` |
| 其他（含 `INTERNAL`）| 500 | `internal_error` |

3. 响应体沿用既有 `ErrorResponse{error, code, request_id}`；`error` 用面向用户的一句话
   （如 "The model provider is rate-limiting requests. Try again shortly."），不透出内部异常串；
   日志侧 `logger.error` 记完整异常链 + request_id。
- 503 响应带 `Retry-After: 60`（与 ModelHealthRegistry 的 429 冷却一致）。
- import 方向：`core/exceptions.py` 需要引用 `llm` 层异常类与 `classify_ai_error`——为避免
  core→services 的硬依赖，handler 注册放在**独立小模块** `backend/app/core/provider_errors.py`
  （from services import 异常类 + 分类器，向 app 注册 handler），`main.py` 里与既有
  `register_exception_handlers` 相邻调用。core/exceptions.py 本体不 import services。

### chat-stream 的 error event

`ai_library_router.py` 的 SSE 路径已有 `except Exception` → `event: error`，现在 payload 是
裸 `f"{type(exc).__name__}: {exc}"`。改为：命中 provider 异常时
`{"error": <同上用户文案>, "code": <同上小写码>}`；其他异常维持现状 shape 但补 `"code":
"internal_error"`。前端 SSE 消费处按 code 展示（见 S4）。

## 3. S2 · request-id 中间件

`backend/app/main.py` 注册轻量 middleware（http 层最外圈）：
- 入站有 `x-request-id` 头 → 沿用（截断至 64 字符）；无 → `uuid4().hex[:16]`；
- 写 `request.state.request_id`（`_request_id()` 现有读取逻辑无需改）+ 响应头 `X-Request-Id`；
- 与 `api_request_logs` 既有日志管道的关联字段对齐（实现时核对该管道是否已有自己的 id 字段，
  有则沿用其字段名读写，避免两套 id）。

## 4. S3 · 存量预设 fallback 配置（migration，幂等）

```sql
UPDATE public.ai_agents
SET fallback_models = ARRAY['doubao-seed-2-0-lite-260428']
WHERE model = 'doubao-seed-2-0-pro-260215'
  AND fallback_models = '{}'
  AND is_system_preset = true;
```

- 幂等：重跑无副作用（空数组条件不再命中）。
- 生效即刻：`build_agent_runner_stack` 每 turn 读 `agent.fallback_models`，无需重启。
- 平台目录（`mediahub_models`）需含 lite 的凭证——与 pro 同 provider 同 key，天然满足
  （实现时用 SELECT 复核目录里有 lite 行）。

**澄清（2026-08-11 终审 Important 2）：这条 fallback 链只覆盖 `build_agent_runner_stack`
路径**（chat panel 的 1:1 会话 / Delegate 工具 / issue 派发触发的 subagent 执行）——这些
都经过 `ai_library_chat_service.py` 的 `_run_session_turn_inner`，那里会 `await
build_agent_runner_stack(...)` 拿到接了 `LLMFallbackChain` 的 runner。

`SummarizeService`（`backend/app/services/ai/summarize/summarize_service.py`）与
`llm_analysis_service`（`backend/app/services/ai/llm/llm_analysis_service.py`）**不在覆盖面
内**：两者都是裸 `AgentRunner(adapter=...)`，adapter 由各自的 `_build_adapter` /
`_build_adapter_from_provider_config` 直接从**调用者的 per-user provider 配置**解析，从不
读 `agent.fallback_models`；而且它们的实际执行模型来自这份 per-user 配置，与
`ai_agents.model` 本就是解耦的两条数据。所以 mig 423 往 `summarize` / `analyze` 两个预设
agent 行上写的 `fallback_models`，对这两个 service 的直接调用路径（Celery 任务触发的批量
summarize/analyze）是死数据——它只在这两个 agent 被**当作 chat 里的 delegate 目标**时才会
被读到。给这两个 service 接上 `LLMFallbackChain` 属于后续工作，不在本分支范围内，已加入
§7「范围外」清单。

## 5. S4 · 前端最小回显

- `friendlyError`（实现时定位其文件）增加 code → 文案映射：`provider_rate_limit` /
  `provider_unreachable` / `provider_auth` / `provider_bad_model` / `task_timeout`；
  未知 code 落现有通用路径。i18n en/zh 同步（`errors.provider.*` 键族）。
- chat SSE 消费处（AIChatPanel 的 error event 分支）同样过 friendlyError。

## 6. 测试口径

- handler：五个类型码 × 状态码/响应体/Retry-After；非 provider 异常仍走兜底 500；
  异常链嵌套（httpx.HTTPStatusError 包在 AllModelsFailed.__cause__ 深处）分类正确。
- middleware：无头生成/有头沿用/响应头回写；`_request_id()` 读到值。
- migration：幂等 + 不覆盖已配值 + 不动非预设行（SQL 语义即测试断言，ephemeral 库跑）。
- stream error event：provider 异常带 code；普通异常 code=internal_error。
- 回归：`llm_fallback_chain` / `llm_retry_middleware` / `error_catalog` 既有测试零改动零回归。
- 生产验收：mig 生效后 `SELECT slug, fallback_models FROM ai_agents WHERE is_system_preset`
  确认三行已配；用调试账号触发一次 summarize（若 pro 仍 429，应自动落 lite 成功，run 的
  model 字段显示 lite）。

## 7. 范围外（YAGNI，留路线图）

- Redis 冷却注册表接线（`model_health_redis.py` 已写好未挂 `app.state`——跨 worker 冷却共享）
- 主动 provider 探针（进 readyz；要对齐「探针必须可证伪」纪律）
- 用户侧 fallback / `budget_per_run_cents` 配置面（后者今天连 admin PATCH 面都没有，仅 DB 默认——记录在案）
- doubao-pro 429 的根因追击（配额/计费侧，属运营）
- `SummarizeService` / `llm_analysis_service` 接入 `LLMFallbackChain`（见 §4 澄清）——两者
  当前是裸 `AgentRunner(adapter=...)`，adapter 从 per-user provider 配置解析，不读
  `agent.fallback_models`，mig 423 对它们的直接调用路径（Celery 触发的批量
  summarize/analyze）不生效
- `2026-08-07-ai-summary-bug-fixes.md` 里 DBOS workflow 侧的 return-failed-dict 修复（独立既有计划，别混入）
