# Provider 容错 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** provider 故障不再裸 500：类型化错误面（复用 error_catalog）+ request-id 贯通 + 三个 doubao 预设 agent 配上 fallback 链。

**Architecture:** 新小模块 `core/provider_errors.py` 向 FastAPI 注册 `AllModelsFailed`/`LLMCallError` 的专属 handler（走既有 `classify_ai_error`，core/exceptions.py 本体不 import services）；`RequestLoggingMiddleware` 已生成 request_id，只补"写 request.state + 沿用入站头"两件事；SSE error event 带 code，前端 toast 按 code 出 i18n 文案；migration 421 幂等 UPDATE 预设行。

**Tech Stack:** FastAPI exception handlers、既有 `error_catalog.classify_ai_error`、PostgreSQL migration、React i18n。

**Spec:** `docs/superpowers/specs/2026-08-11-provider-resilience-design.md`（已获批）。

## Global Constraints

- 禁裸 SQL（migration SQL 文件本身除外）；后端 isort/black clean；前端 tsc 不新增错误（有既有基线）。
- i18n en/zh key 集合一致；UI 文案英文。
- **不改动**：`LLMFallbackChain` / `LLMRetryMiddleware` / `ModelHealthRegistry` / `error_catalog.classify_ai_error` 的现有逻辑与测试；`RequestLoggingMiddleware` 的日志/排除路径行为（只加 id 贯通两处）。
- code 映射表（spec §2，各处必须一致）：`PROVIDER_RATE_LIMIT→503/provider_rate_limit`（带 `Retry-After: 60`）、`PROVIDER_UNREACHABLE→502/provider_unreachable`、`PROVIDER_AUTH→502/provider_auth`、`PROVIDER_BAD_MODEL→502/provider_bad_model`、`TASK_TIMEOUT→504/task_timeout`、其他→500/internal_error。
- 每 task：RED 测试先行 → 实现 → GREEN → commit（中文 conventional commit）。
- 工作区：worktree `.worktrees/feat-provider-resilience`，分支 `feat/provider-resilience`（基于 22f87ca4 后的 origin/master）。测试：`cd backend && uv run pytest ...`；`cd frontend && npx vitest run ...`。

---

### Task 1: 类型化错误 handler（core/provider_errors.py）

**Files:**
- Create: `backend/app/core/provider_errors.py`
- Modify: `backend/app/main.py`（`register_exception_handlers(app)` 之后一行注册）
- Test: `backend/tests/test_provider_error_handler.py`（新建）

**Interfaces:**
- Consumes: `AllModelsFailed`（`app.services.ai.llm.llm_fallback_chain:46`）、`LLMCallError`（`app.services.ai.llm.llm_retry_middleware:64`）、`classify_ai_error`（`app.services.ai.error_catalog:199`，签名 `(exc_or_message) -> Optional[str]`）、`ErrorResponse` 与 `_request_id`（`app.core.exceptions`，`ErrorResponse{success,error,code,request_id,details}`）。
- Produces: `def register_provider_error_handlers(app: FastAPI) -> None`——Task 3 的 SSE 改动 import 本模块的 `provider_error_payload(exc) -> tuple[int, str, str]`（status, code, user_message）做同一映射。

- [ ] **Step 1: 写 RED 测试**

```python
"""Provider 异常的类型化 HTTP 面（spec §2）——handler 单测,直调不起 TestClient 也可,
但状态码/头/体要真实验证,故用 fastapi.testclient 起最小 app。"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.provider_errors import (
    provider_error_payload,
    register_provider_error_handlers,
)
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError


def _app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    register_provider_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def _rate_limited_all_failed() -> AllModelsFailed:
    # 真实形态:httpx 429 埋在 __cause__ 链深处(fallback 链 raise ... from last_exc)
    request = httpx.Request("POST", "https://ark.example/api")
    response = httpx.Response(429, request=request)
    inner = httpx.HTTPStatusError("429", request=request, response=response)
    failed = AllModelsFailed("primary + 0 fallback(s) exhausted")
    failed.__cause__ = inner
    return failed


def test_rate_limit_maps_to_503_with_retry_after():
    client = _app_raising(_rate_limited_all_failed())
    resp = client.get("/boom")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "provider_rate_limit"
    assert "rate-limiting" in body["error"]
    assert resp.headers.get("retry-after") == "60"


def test_unclassifiable_provider_exception_maps_to_500_internal():
    client = _app_raising(AllModelsFailed("primary + 0 fallback(s) exhausted"))
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"


def test_llm_call_error_auth_maps_to_502():
    request = httpx.Request("POST", "https://ark.example/api")
    response = httpx.Response(401, request=request)
    inner = httpx.HTTPStatusError("401", request=request, response=response)
    exc = LLMCallError("auth failed")
    exc.__cause__ = inner
    client = _app_raising(exc)
    resp = client.get("/boom")
    assert resp.status_code == 502
    assert resp.json()["code"] == "provider_auth"


def test_payload_helper_matches_handler_mapping():
    status, code, message = provider_error_payload(_rate_limited_all_failed())
    assert (status, code) == (503, "provider_rate_limit")
    assert message  # 面向用户的一句话,非异常串


def test_non_provider_exception_untouched():
    # 未注册类型仍走 FastAPI 默认(本测试 app 无兜底 handler → 500 纯文本),
    # 证明 handler 只精确命中两类异常
    client = _app_raising(RuntimeError("boom"))
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert "provider" not in resp.text
```

- [ ] **Step 2: RED 确认** `cd backend && uv run pytest tests/test_provider_error_handler.py -v`（ModuleNotFoundError）

- [ ] **Step 3: 实现 `core/provider_errors.py`**

```python
"""Provider 故障的类型化 HTTP 面（spec 2026-08-11 §2）。

为什么是独立模块而不是 core/exceptions.py：handler 需要 import services 层的
异常类与分类器，而 core/exceptions.py 是全应用的底座，不该反向依赖 services。
main.py 在 register_exception_handlers(app) 之后调用本模块注册——FastAPI 按
异常类精确匹配，这两类异常从此不再落进兜底的 internal_error。

分类复用 error_catalog.classify_ai_error（走整条异常链，含 __cause__/__context__），
不新写分类逻辑——DBOS workflow 路径与 HTTP 路径从此共用同一套码。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import ErrorResponse, _request_id
from app.services.ai import error_catalog
from app.services.ai.error_catalog import classify_ai_error
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError

logger = logging.getLogger(__name__)

# error_catalog 码 → (HTTP 状态, 响应 code, 用户文案)。用户文案不透出内部异常串。
_MAPPING: dict[str, tuple[int, str, str]] = {
    error_catalog.PROVIDER_RATE_LIMIT: (
        503,
        "provider_rate_limit",
        "The model provider is rate-limiting requests. Try again shortly.",
    ),
    error_catalog.PROVIDER_UNREACHABLE: (
        502,
        "provider_unreachable",
        "The model provider is unreachable right now. Try again shortly.",
    ),
    error_catalog.PROVIDER_AUTH: (
        502,
        "provider_auth",
        "The model provider rejected our credentials. The team has been notified.",
    ),
    error_catalog.PROVIDER_BAD_MODEL: (
        502,
        "provider_bad_model",
        "The configured model is not available at the provider.",
    ),
    error_catalog.TASK_TIMEOUT: (
        504,
        "task_timeout",
        "The model took too long to respond. Try again.",
    ),
}

_FALLBACK = (500, "internal_error", "Internal server error")


def provider_error_payload(exc: BaseException) -> tuple[int, str, str]:
    """(status, code, user_message)。SSE error event（Task 3）与 handler 共用。"""
    ai_code = classify_ai_error(exc)
    return _MAPPING.get(ai_code or "", _FALLBACK)


def register_provider_error_handlers(app: FastAPI) -> None:
    async def _handle(request: Request, exc: Exception) -> JSONResponse:
        status, code, message = provider_error_payload(exc)
        rid = _request_id(request)
        logger.error(
            "[provider_errors] %s -> %s %s (request_id=%s): %r",
            type(exc).__name__,
            status,
            code,
            rid,
            exc,
        )
        headers = {"Retry-After": "60"} if code == "provider_rate_limit" else None
        return JSONResponse(
            status_code=status,
            content=ErrorResponse(
                error=message, code=code, request_id=rid
            ).model_dump(),
            headers=headers,
        )

    app.add_exception_handler(AllModelsFailed, _handle)
    app.add_exception_handler(LLMCallError, _handle)
```

`main.py`：`register_exception_handlers(app)`（现 :278）之后加：

```python
from app.core.provider_errors import register_provider_error_handlers

register_provider_error_handlers(app)
```

（import 放文件顶部 import 区，注册调用放 278 行旁；若 CORS 头逻辑在既有 handler 里对错误响应有特殊处理，读 `register_exception_handlers` 的 CORS 注释后照抄同样的头处理——实现时核对，错误响应绕过中间件栈的问题该文件头注释有说明。）

- [ ] **Step 4: GREEN + 回归** `uv run pytest tests/test_provider_error_handler.py tests/ -q -k "error_catalog or fallback_chain or retry_middleware"`
- [ ] **Step 5: Commit** `git commit -m "feat(ai): provider 异常类型化 HTTP 面 — AllModelsFailed/LLMCallError 接 error_catalog,不再裸 500"`

---

### Task 2: request-id 贯通（middleware 补两件事）

**Files:**
- Modify: `backend/app/middleware/request_logging.py`（:131 附近）
- Test: `backend/tests/test_request_id_propagation.py`（新建；先 `ls backend/tests | grep -i request_logging` 看有无既有 middleware 测试可挂靠，有则追加进去并说明）

**Interfaces:**
- Consumes: 既有 `RequestLoggingMiddleware.dispatch`（已生成 `request_id = str(uuid.uuid4())` 于 :131、已回写 `X-Request-ID` 响应头于 :180、已 `logger.contextualize`）。
- Produces: `request.state.request_id` 在请求处理全程可读——`core/exceptions._request_id()`（读 `x-request-id` 头或 `request.state.request_id`）从此在服务端生成场景也拿得到值。

- [ ] **Step 1: 写 RED 测试**

```python
"""request-id 贯通:middleware 生成的 id 必须进 request.state,
且入站 x-request-id 优先沿用(截断 64)——spec §3。"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.request_logging import RequestLoggingMiddleware


def _app() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return {"rid": getattr(request.state, "request_id", None)}

    return TestClient(app)


def test_generated_id_lands_in_request_state_and_header():
    resp = _app().get("/echo")
    rid = resp.json()["rid"]
    assert rid
    assert resp.headers["x-request-id"] == rid


def test_inbound_header_is_honored_and_truncated():
    resp = _app().get("/echo", headers={"x-request-id": "client-abc-" + "x" * 100})
    rid = resp.json()["rid"]
    assert rid.startswith("client-abc-")
    assert len(rid) <= 64
    assert resp.headers["x-request-id"] == rid
```

- [ ] **Step 2: RED 确认**（`rid` 为 None / 入站头被忽略）

- [ ] **Step 3: 实现**（`request_logging.py` :131 处）

```python
        inbound = request.headers.get("x-request-id")
        request_id = (inbound or str(uuid.uuid4()))[:64]
        request.state.request_id = request_id
```

（替换原 `request_id = str(uuid.uuid4())` 一行；:180 的响应头回写与 contextualize 沿用同一变量，零其他改动。注意 `_should_exclude` 的路径仍然直接放行不带 id——健康探针/静态类路径，维持现状。）

- [ ] **Step 4: GREEN + 回归** `uv run pytest tests/test_request_id_propagation.py tests/ -q -k "request_logging or middleware"`
- [ ] **Step 5: Commit** `git commit -m "feat(api): request-id 贯通 — middleware 写 request.state + 沿用入站头,错误响应不再 request_id:null"`

---

### Task 3: SSE error event 带 code + 前端 toast 映射

**Files:**
- Modify: `backend/app/api/ai_library_router.py`（chat-stream 的 `except Exception` 块，~:2945）
- Modify: `frontend/components/AIChatPanel.tsx`（error event 分支 ~:697）
- Modify: `frontend/public/locales/en.json` + `zh.json`（`errors.provider.*` 键族）
- Test: `backend/tests/test_chat_stream_error_code.py`（新建）+ 前端在既有 AIChatPanel 相关测试处追加纯函数测试

**Interfaces:**
- Consumes: Task 1 的 `provider_error_payload(exc) -> (status, code, user_message)`。
- Produces: SSE `event: error` 的 data 变为 `{"error": <用户文案或原样串>, "code": <小写码>}`；前端新纯函数 `providerErrorMessage(code: string, t) -> string | null`（导出供测试）。

- [ ] **Step 1: 后端 RED 测试**

```python
"""chat-stream 的 error event 必须带 code(spec §2)——provider 异常给类型码,
其他异常 code=internal_error 且 shape 兼容旧消费者。"""

import json

import httpx


def test_error_event_payload_for_provider_exception():
    from app.api.ai_library_router import _stream_error_payload
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed

    request = httpx.Request("POST", "https://ark.example/api")
    inner = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    exc = AllModelsFailed("primary + 0 fallback(s) exhausted")
    exc.__cause__ = inner
    payload = json.loads(_stream_error_payload(exc))
    assert payload["code"] == "provider_rate_limit"
    assert "rate-limiting" in payload["error"]


def test_error_event_payload_for_plain_exception():
    from app.api.ai_library_router import _stream_error_payload

    payload = json.loads(_stream_error_payload(RuntimeError("boom")))
    assert payload["code"] == "internal_error"
    assert "RuntimeError" in payload["error"]
```

- [ ] **Step 2: RED 确认**（`_stream_error_payload` 不存在）

- [ ] **Step 3: 后端实现**

`ai_library_router.py` 模块级私有函数（放 chat-stream 端点上方）：

```python
def _stream_error_payload(exc: BaseException) -> str:
    """SSE error event 的 data。provider 异常给类型码与用户文案(与 HTTP 面
    同一映射,provider_errors.provider_error_payload);其他异常维持旧 shape
    (类型名+串)并补 code=internal_error——「触发路径必须类型化回显」。"""
    from app.core.provider_errors import provider_error_payload
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
    from app.services.ai.llm.llm_retry_middleware import LLMCallError

    if isinstance(exc, (AllModelsFailed, LLMCallError)):
        _status, code, message = provider_error_payload(exc)
        return json.dumps({"error": message, "code": code})
    return json.dumps({"error": f"{type(exc).__name__}: {exc}", "code": "internal_error"})
```

except 块里 `data = json.dumps({"error": f"{type(exc).__name__}: {exc}"})` 一行替换为 `data = _stream_error_payload(exc)`（logger.exception 保留原样）。

- [ ] **Step 4: 前端实现 + 测试**

`AIChatPanel.tsx`：新导出纯函数（放 `extractToolCalls` 附近）：

```tsx
/** 已知 provider 错误码 → i18n 文案;未知码返回 null 走原有兜底。 */
export function providerErrorMessage(
  code: unknown,
  t: (key: string, fallback: string) => string,
): string | null {
  const KNOWN = [
    'provider_rate_limit',
    'provider_unreachable',
    'provider_auth',
    'provider_bad_model',
    'task_timeout',
  ];
  if (typeof code !== 'string' || !KNOWN.includes(code)) return null;
  const key = code
    .split('_')
    .map((w, i) => (i === 0 ? w : w[0].toUpperCase() + w.slice(1)))
    .join('');
  return t(`errors.provider.${key}`, code);
}
```

error event 分支（:697）改为：

```tsx
          } else if (evt.type === 'error') {
            const mapped = providerErrorMessage(evt.data?.code, (k, f) => t(k, f));
            throw new Error(
              mapped ??
                (typeof evt.data?.error === 'string' ? evt.data.error : 'stream error'),
            );
          }
```

i18n（en，zh 同步中文值；key 族 `errors.provider.*`，若 en.json 无顶层 `errors` 对象则新建）：

```json
"errors": {
  "provider": {
    "providerRateLimit": "The AI model provider is rate-limiting requests. Try again in a minute.",
    "providerUnreachable": "The AI model provider is unreachable. Try again shortly.",
    "providerAuth": "The AI provider rejected the platform credentials. The team has been notified.",
    "providerBadModel": "The configured AI model is unavailable.",
    "taskTimeout": "The AI model took too long to respond. Try again."
  }
}
```

前端测试（`providerErrorMessage` 纯函数，新文件或挂靠既有 AIChatPanel 测试）：已知 5 码返回 i18n 值、未知码/非字符串返回 null。

- [ ] **Step 5: GREEN**

```bash
cd backend && uv run pytest tests/test_chat_stream_error_code.py -q
cd ../frontend && npx vitest run components/ -t providerErrorMessage 2>/dev/null || npx vitest run components/AILibrary/ components/agentActivity/ && npx tsc --noEmit
node -e "const e=require('./public/locales/en.json').errors.provider,z=require('./public/locales/zh.json').errors.provider;const ek=Object.keys(e).sort(),zk=Object.keys(z).sort();if(JSON.stringify(ek)!==JSON.stringify(zk))process.exit(1);console.log('aligned',ek.length)"
```

- [ ] **Step 6: Commit** `git commit -m "feat(ai): chat-stream error event 带类型码 + 前端 provider 错误文案映射"`

---

### Task 4: 存量预设 fallback migration

**Files:**
- Create: `supabase/migrations/421_preset_doubao_fallback.sql`（号以 fetch 复核为准；写计划时 master 水位 420）

**Interfaces:**
- Consumes: `ai_agents.model` / `.fallback_models`（mig 155）/ `.is_system_preset`。
- Produces: 三个预设 agent（summarize/analyze/coordinator）的 `fallback_models=['doubao-seed-2-0-lite-260428']`，生效无需重启（每 turn 读库）。

- [ ] **Step 1: fetch 复核取号** `git fetch origin master && ls supabase/migrations | sort | tail -3`（被占则顺延并同步改名/引用）

- [ ] **Step 2: 写 migration**

```sql
-- 421_preset_doubao_fallback.sql
--
-- Provider 容错 P1（spec: docs/superpowers/specs/2026-08-11-provider-resilience-design.md §4）。
-- doubao-seed-2-0-pro 自 2026-08-08 持续 429,而所有 agent 的 fallback_models
-- 都是空数组("primary + 0 fallback(s)" 的真意)——链路机制(LLMFallbackChain,
-- mig 155)早就建好,只是没人配过。
--
-- 只动系统预设(summarize/analyze/coordinator 用此主模型)+ 只填空数组:
-- 不覆盖任何已手配值,不动用户自建 agent(他们模型自选,admin 后台可配)。
-- lite 有 ~14% 空产出史,但 pro 当前 0% 可用——降级明确优于不可用。
-- qwen 不进救援链:死过三周的引擎不进急救箱。
-- 幂等:重跑时空数组条件不再命中。

UPDATE public.ai_agents
SET fallback_models = ARRAY['doubao-seed-2-0-lite-260428']
WHERE model = 'doubao-seed-2-0-pro-260215'
  AND fallback_models = '{}'
  AND is_system_preset = true;
```

- [ ] **Step 3: 验证语义**（无 DB 可跑时静态自查：幂等/不覆盖/只预设三条件都在 WHERE 里；有 ephemeral 库则 psql 跑两遍确认第二遍 UPDATE 0）
- [ ] **Step 4: Commit** `git commit -m "chore(migrations): 三个 doubao 预设 agent 配 fallback — pro→lite,只填空数组 (mig 421)"`

---

## 收尾

- [ ] 全量基线：`cd backend && uv run pytest -q`；`cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
- [ ] 推分支开 PR（base master）。PR 描述：spec 路径 + 六拍板 + 部署提示（本期无窗口问题：migration 只 UPDATE 数据、新 handler 随后端一起上，互不依赖）+ 验收步骤。
- [ ] merge 后生产验收：① `SELECT slug, fallback_models FROM ai_agents WHERE is_system_preset AND model LIKE 'doubao%'` 三行已配；② 调试账号触发一次 summarize——若 pro 仍 429 应自动落 lite 成功（run 的 model 字段=lite）；若 pro 已恢复则确认正常走 pro；③ 人为探针：对一个不存在的会话发 chat 之类拿到的错误响应应带 `request_id` 非 null。

## Self-Review 记录

- spec §2（handler/映射/Retry-After/SSE code）→ Task 1+3；§3（request-id，实际比 spec 预估更小——middleware 已存在，只补 state 写入与入站头沿用，spec 的"新建中间件"落地为"改造既有中间件"，语义一致）→ Task 2；§4（migration 三条件幂等）→ Task 4；§5（前端映射）→ Task 3；§6 测试口径分布各 task；§7 范围外未混入。
- 类型一致性：`provider_error_payload` 的 `(status, code, message)` 在 Task 1 定义、Task 3 消费；code 字符串集合与前端 `KNOWN` 数组、i18n key 族一一对应（snake→camel 转换函数已给全）。
- 无占位：所有代码块可直接落地；唯一的实现时核对点（main.py 注册处的 CORS 头处理）已写明去哪读、抄什么。
