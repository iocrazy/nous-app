# Module Control Center 全量接入（6 大模块开关）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Shared / Todolist / Media 解析下载 / Projects / AI Library+Chat / Ideation 六个模块接入现有 Module Control Center，管理员可即时开关后台处理（`enabled`）与前端可见性（`visible`）。

**Architecture:** 注册表加 6 条数据（零新类）；后端一个 `require_module()` 依赖工厂（关闭 → 503 `MODULE_DISABLED` 类型化错误）+ 一个批量 `GET /modules/status` 端点；前端一个 `useModuleStatus(id)` hook + 统一 disabled 提示页。迁移并删除 topic/distribution 两套旧 per-module status 端点与 hook。

**Tech Stack:** FastAPI (依赖注入) / pytest (直接函数调用模式，无 TestClient fixture) / React 19 + vitest / 现有 `TTLCache`。

**Spec:** `docs/superpowers/specs/2026-08-03-module-switches-rollout-design.md`

## Global Constraints

- UI 文案一律英文（Title Case），中文进 i18n zh.json；翻译 key 用 camelCase
- 新前端代码不用旧色相类名（indigo/amber 等），用语义 token（见 `frontend/index.css` @theme）
- 6 个新模块全部 **默认开、fail-open**；Distribution 保持 404 fail-closed，**不迁**到 `require_module`
- 后端业务代码禁止直接 PATCH `task_tracking` 引擎列（本计划不涉及，但别顺手碰）
- 提交信息末尾带 `Claude-Session: https://claude.ai/code/session_012KgGGXVTJwW9vkEqHmB4hH`
- 后端测试跑法：`cd backend && uv run pytest tests/<file> -v`；前端：`cd frontend && npx vitest run <file>`

---

### Task 1: 注册表加 6 条 ModuleDef

**Files:**
- Modify: `backend/app/services/modules/registry.py:36-66`（`MODULES` 列表）
- Modify: `backend/tests/test_admin_modules_endpoint.py:38-43`（ids 断言集合）
- Test: `backend/tests/services/modules/test_registry.py`（已有文件，追加）

**Interfaces:**
- Produces: `MODULES_BY_ID` 新增 key：`"media-parser"`、`"projects"`、`"shares"`、`"todolist"`、`"ai-library"`、`"ideation"`（后续所有 Task 以这些 id 为准）
- 对应 system_settings key：`media.module` / `projects.module` / `shares.module` / `todolist.module` / `ai.module` / `ideation.module`

- [ ] **Step 1: 写失败测试**（追加到 `backend/tests/services/modules/test_registry.py`）

```python
NEW_MODULE_IDS = {
    "media-parser": "media.module",
    "projects": "projects.module",
    "shares": "shares.module",
    "todolist": "todolist.module",
    "ai-library": "ai.module",
    "ideation": "ideation.module",
}


def test_six_rollout_modules_registered_fail_open():
    """2026-08-03 rollout: six live modules, all default-ON / fail-open."""
    from app.services.modules.registry import MODULES_BY_ID

    for module_id, key in NEW_MODULE_IDS.items():
        m = MODULES_BY_ID.get(module_id)
        assert m is not None, f"missing module {module_id}"
        assert m.key == key
        assert m.enabled_default is True
        assert m.visible_default is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/services/modules/test_registry.py::test_six_rollout_modules_registered_fail_open -v`
Expected: FAIL（`missing module media-parser`）

- [ ] **Step 3: 实现** — 在 `registry.py` 的 `MODULES` 列表末尾（unified-storage 之后）追加：

```python
    # ---- 2026-08-03 rollout: six live product modules, all default-ON and
    # fail-OPEN (missing/garbage config must never take a shipped feature
    # down). `enabled` gates the module's API/processing via
    # app.services.modules.gate.require_module; `visible` gates the frontend
    # nav entries + routes via GET /api/v1/modules/status.
    ModuleDef(
        # Gates NEW parse/download initiation (media_fetch_router,
        # media_batch_router) + the hourly retry tick. Reads/playback of
        # already-downloaded media are intentionally NOT gated.
        id="media-parser",
        key="media.module",
        label="Media Parser & Downloads",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        # Gates projects_router + project_assets_router + canvases_router.
        id="projects",
        key="projects.module",
        label="Projects (MediaTrack)",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        id="shares",
        key="shares.module",
        label="Shares",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        # Todolist UI is backed by issues_router.
        id="todolist",
        key="todolist.module",
        label="Todolist (Issues)",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        # Gates conversation_router + ai_library_router. Deliberately NOT
        # ai_settings_router / ai_memory_router / script_ai_router (settings
        # infra & the Scripts domain stay independent of this switch).
        id="ai-library",
        key="ai.module",
        label="AI Library & Chat",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        id="ideation",
        key="ideation.module",
        label="Ideation Board",
        enabled_default=True,
        visible_default=True,
    ),
```

- [ ] **Step 4: 更新受影响的旧断言** — `backend/tests/test_admin_modules_endpoint.py` 中 `test_get_modules_lists_all_registered` 的 `ids == {...}` 集合改为 9 个：

```python
    assert ids == {
        "topic-inspiration",
        "distribution",
        "unified-storage",
        "media-parser",
        "projects",
        "shares",
        "todolist",
        "ai-library",
        "ideation",
    }
```

同文件里若还有按"只有 3 个模块"写死的断言（搜 `unified-storage`），一并改为包含式断言或补全集合。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/services/modules/ tests/test_admin_modules_endpoint.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/modules/registry.py backend/tests/services/modules/test_registry.py backend/tests/test_admin_modules_endpoint.py
git commit -m "feat(modules): 注册表接入 6 大模块（默认开/fail-open），admin 卡片自动出现"
```

---

### Task 2: `require_module()` gate 依赖工厂

**Files:**
- Create: `backend/app/services/modules/gate.py`
- Test: `backend/tests/services/modules/test_gate.py`

**Interfaces:**
- Consumes: Task 1 的 `MODULES_BY_ID`、`read_module_state()`
- Produces: `require_module(module_id: str) -> Callable[[], Awaitable[None]]` — FastAPI 依赖工厂。模块 `enabled=False` 时 raise `HTTPException(503, detail={"code": "MODULE_DISABLED", "module": module_id})`；未注册的 module_id 在 **import 时**（工厂调用时）raise `KeyError`（拼错 id 应当在启动时炸，不是运行时静默放行）

- [ ] **Step 1: 写失败测试**

```python
"""Tests for the module gate dependency factory (spec 2026-08-03 §2.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_gate_disabled_raises_typed_503():
    from app.services.modules.gate import require_module

    check = require_module("shares")
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        with pytest.raises(HTTPException) as exc:
            await check()
    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "MODULE_DISABLED", "module": "shares"}


@pytest.mark.asyncio
async def test_gate_enabled_passes():
    from app.services.modules.gate import require_module

    check = require_module("shares")
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": True, "visible": True}),
    ):
        assert await check() is None


@pytest.mark.asyncio
async def test_gate_fails_open_on_missing_config():
    """No stored row (raw=None) → default-ON module passes (fail-open)."""
    from app.services.modules.gate import require_module

    check = require_module("todolist")
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value=None),
    ):
        assert await check() is None


def test_gate_unknown_module_raises_at_factory_time():
    from app.services.modules.gate import require_module

    with pytest.raises(KeyError):
        require_module("no-such-module")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/services/modules/test_gate.py -v`
Expected: FAIL（`No module named 'app.services.modules.gate'`）

- [ ] **Step 3: 实现 `backend/app/services/modules/gate.py`**

```python
"""FastAPI dependency gate for Module Control Center switches.

One factory for every gated router (spec 2026-08-03 §2.1). Module OFF →
typed 503 so the frontend can show a "feature disabled" notice instead of a
generic error (repo discipline: 触发路径必须类型化失败回显). Fail mode
follows each module's registry default — the six 2026-08 rollout modules
are default-ON / fail-open.

NOT used by Distribution: that unlaunched module keeps its own 404
fail-closed gate (``require_distribution``) so its existence stays hidden.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import HTTPException

from app.services.modules.registry import MODULES_BY_ID, read_module_state


def require_module(module_id: str) -> Callable[[], Awaitable[None]]:
    """Dependency factory: raise typed 503 when ``module_id`` is disabled.

    Unknown ids raise ``KeyError`` here, at router-definition (import) time —
    a typo must crash startup, never silently un-gate an endpoint.
    """
    module = MODULES_BY_ID[module_id]

    async def _check() -> None:
        state = await read_module_state(module)
        if not state.enabled:
            raise HTTPException(
                status_code=503,
                detail={"code": "MODULE_DISABLED", "module": module_id},
            )

    return _check
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/services/modules/test_gate.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/modules/gate.py backend/tests/services/modules/test_gate.py
git commit -m "feat(modules): require_module 依赖工厂 — 关闭时 503 MODULE_DISABLED 类型化错误"
```

---

### Task 3: 批量状态端点 `GET /api/v1/modules/status`

**Files:**
- Create: `backend/app/api/modules_router.py`
- Modify: `backend/app/core/cache.py:74` 附近（追加一个 cache 实例）
- Modify: `backend/app/api/__init__.py`（import + include_router）
- Test: `backend/tests/api/test_modules_status_endpoint.py`

**Interfaces:**
- Consumes: `list_module_summaries()`（Task 1 后返回 9 个模块）
- Produces: `GET /api/v1/modules/status` → `{"modules": [{"id": str, "enabled": bool, "visible": bool}]}`（登录用户可读；60s 服务端缓存）。前端 Task 6 依赖这个响应形状。

- [ ] **Step 1: 写失败测试**

```python
"""Tests for GET /modules/status (batch module switches for the frontend).

Direct-function-call pattern (no TestClient fixture in this codebase),
mirroring tests/test_admin_modules_endpoint.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_status_returns_all_registered_modules():
    from app.api.modules_router import get_modules_status
    from app.core.cache import modules_status_cache

    modules_status_cache.invalidate("all")
    fake_auth = MagicMock()
    fake_auth.user_id = "user-1"

    with patch(
        "app.services.modules.registry._read_raw", new=AsyncMock(return_value=None)
    ):
        resp = await get_modules_status(fake_auth)

    by_id = {m.id: m for m in resp.modules}
    # all nine registered modules present
    assert set(by_id) == {
        "topic-inspiration",
        "distribution",
        "unified-storage",
        "media-parser",
        "projects",
        "shares",
        "todolist",
        "ai-library",
        "ideation",
    }
    # registry defaults flow through untouched
    assert by_id["shares"].enabled is True and by_id["shares"].visible is True
    assert by_id["distribution"].enabled is False


@pytest.mark.asyncio
async def test_status_is_cached_for_60s():
    from app.api.modules_router import get_modules_status
    from app.core.cache import modules_status_cache

    modules_status_cache.invalidate("all")
    fake_auth = MagicMock()
    fake_auth.user_id = "user-1"

    read = AsyncMock(return_value=None)
    with patch("app.services.modules.registry._read_raw", new=read):
        await get_modules_status(fake_auth)
        first_calls = read.await_count
        await get_modules_status(fake_auth)
    # second request served from cache — no extra registry reads
    assert read.await_count == first_calls
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/api/test_modules_status_endpoint.py -v`
Expected: FAIL（`No module named 'app.api.modules_router'`）

- [ ] **Step 3: 实现**

`backend/app/core/cache.py` 在 `points_pricing_cache`（74 行）后追加：

```python
# Batch module-switch snapshot for GET /modules/status — read on every page
# load by every user, so cache it. Admin writes do NOT invalidate (worst-case
# 60s staleness is acceptable for a feature switch).
modules_status_cache: TTLCache[list[dict[str, Any]]] = TTLCache(ttl_seconds=60.0)
```

`backend/app/api/modules_router.py`：

```python
"""Batch module-switch status for the frontend (spec 2026-08-03 §2.1).

One authenticated read returns every registered module's ``enabled`` +
``visible`` so the sidebar/router can gate all entries with a single
request — replaces the retired per-module ``/module-status`` endpoints
(topics, distribution).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.cache import modules_status_cache
from app.core.deps import AuthDep
from app.services.modules.registry import list_module_summaries

router = APIRouter(prefix="/modules", tags=["Modules"])


class ModuleStatusItem(BaseModel):
    id: str
    enabled: bool
    visible: bool


class ModulesStatusResponse(BaseModel):
    modules: list[ModuleStatusItem]


@router.get("/status", response_model=ModulesStatusResponse)
async def get_modules_status(auth: AuthDep) -> ModulesStatusResponse:
    """Every registered module's switches, 60s server-side cache."""

    async def _loader() -> list[dict]:
        return await list_module_summaries()

    summaries = await modules_status_cache.get_or_load("all", _loader)
    return ModulesStatusResponse(
        modules=[
            ModuleStatusItem(
                id=s["id"], enabled=s["enabled"], visible=s["visible"]
            )
            for s in summaries
        ]
    )
```

注意：`AuthDep` 的实际 import 路径以 `topics_router.py:8-9` 的
`from app.core.deps import AuthDep` 为准（若名字不同，跟随现状）。
`TTLCache.get_or_load` / `invalidate` 的方法签名以 `app/core/cache.py` 现有类为准，
若 `invalidate` 名字不同（如 `delete`/`clear`），测试与实现同步用现有名。

`backend/app/api/__init__.py`：字母序附近加 import 并在 include_router 区
（比如 `notifications_router` 附近）加一行：

```python
from app.api.modules_router import router as modules_router
...
api_router.include_router(router=modules_router, tags=["Modules"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/api/test_modules_status_endpoint.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/modules_router.py backend/app/core/cache.py backend/app/api/__init__.py backend/tests/api/test_modules_status_endpoint.py
git commit -m "feat(modules): 批量 GET /modules/status 端点（60s 缓存），前端一次拿全部开关"
```

---

### Task 4: 六组 router 挂 gate + retry tick 检查

**Files:**
- Modify: `backend/app/api/media_fetch_router.py:33`
- Modify: `backend/app/api/media_batch_router.py:27`
- Modify: `backend/app/api/projects_router.py:70`
- Modify: `backend/app/api/project_assets_router.py:23`
- Modify: `backend/app/api/canvases_router.py:46`
- Modify: `backend/app/api/shares_router.py:32`
- Modify: `backend/app/api/issues_router.py:41`
- Modify: `backend/app/api/conversation_router.py:33`
- Modify: `backend/app/api/ai_library_router.py:84`
- Modify: `backend/app/api/ideation_router.py:25`
- Modify: `backend/app/workflows/scheduled_recovery.py:452-470`（retry workflow 开头）
- Test: `backend/tests/api/test_module_gates_mounted.py`

**Interfaces:**
- Consumes: Task 2 的 `require_module()`
- Produces: 十个 router 对象的 `.dependencies` 含 gate；`retry_failed_downloads_workflow` 在 `media-parser` 关闭时本轮 skip

- [ ] **Step 1: 写失败测试**（结构性断言：gate 真的挂上了，避免逐端点冒烟的脆弱性）

```python
"""Asserts the module gate is mounted on every gated router (spec §1 table).

Structural check: FastAPI keeps router-level dependencies in
``router.dependencies``; we match by the dependency's closure cell holding
the right ModuleDef. This fails when someone adds/reworks a router and
forgets the gate wiring.
"""

from __future__ import annotations

import pytest


def _gated_module_ids(router) -> set[str]:
    ids: set[str] = set()
    for dep in router.dependencies:
        fn = dep.dependency
        for cell in getattr(fn, "__closure__", None) or ():
            obj = cell.cell_contents
            if hasattr(obj, "id") and hasattr(obj, "enabled_default"):
                ids.add(obj.id)
    return ids


@pytest.mark.parametrize(
    "module_path,router_name,expected_id",
    [
        ("app.api.media_fetch_router", "router", "media-parser"),
        ("app.api.media_batch_router", "router", "media-parser"),
        ("app.api.projects_router", "router", "projects"),
        ("app.api.project_assets_router", "router", "projects"),
        ("app.api.canvases_router", "router", "projects"),
        ("app.api.shares_router", "router", "shares"),
        ("app.api.issues_router", "router", "todolist"),
        ("app.api.conversation_router", "router", "ai-library"),
        ("app.api.ai_library_router", "router", "ai-library"),
        ("app.api.ideation_router", "router", "ideation"),
    ],
)
def test_router_has_module_gate(module_path, router_name, expected_id):
    import importlib

    router = getattr(importlib.import_module(module_path), router_name)
    assert expected_id in _gated_module_ids(router), (
        f"{module_path}.{router_name} is missing require_module('{expected_id}')"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/api/test_module_gates_mounted.py -v`
Expected: 10 FAIL（missing require_module）

- [ ] **Step 3: 挂 gate** — 每个文件在 `router = APIRouter(...)` 定义处加
`dependencies=[Depends(require_module("<id>"))]`，并补两个 import。以 shares 为例：

```python
from fastapi import APIRouter, Depends

from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/shares",
    dependencies=[Depends(require_module("shares"))],
)
```

对应关系（严格按 Interfaces 表）：
- `media_fetch_router.py:33` / `media_batch_router.py:27` → `"media-parser"`（这两个 router 无 prefix，保持无 prefix，只加 dependencies）
- `projects_router.py:70`（保留 `prefix="/projects"`）/ `project_assets_router.py:23` / `canvases_router.py:46` → `"projects"`
- `shares_router.py:32` → `"shares"`
- `issues_router.py:41`（保留 prefix 和 tags）→ `"todolist"`
- `conversation_router.py:33` / `ai_library_router.py:84` → `"ai-library"`
- `ideation_router.py:25` → `"ideation"`

已有 `dependencies=[...]` 的 router 就追加到列表里，不要覆盖。
**不要**动 `media_router.py` / `media_slides_router.py` / `media_content_router`（读路径不拦）。

- [ ] **Step 4: retry tick 检查** — `scheduled_recovery.py` 的
`retry_failed_downloads_workflow` 函数体开头（`collected = ...` 之前）加：

```python
    # Module Control Center: media-parser OFF pauses the retry tick too —
    # retries are new download dispatches (spec 2026-08-03 §1). Plain skip
    # (not raise): a paused module is not a workflow failure.
    from app.services.modules.registry import MODULES_BY_ID, read_module_state

    state = await read_module_state(MODULES_BY_ID["media-parser"])
    if not state.enabled:
        logger.info("[retry_failed_downloads] media-parser module disabled — skip")
        return
```

再补一个测试（追加到 `test_module_gates_mounted.py`）：

```python
@pytest.mark.asyncio
async def test_retry_tick_skips_when_media_parser_disabled(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.workflows import scheduled_recovery as sr

    collect = AsyncMock()
    monkeypatch.setattr(sr, "collect_retryable_downloads_step", collect)
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        # DBOS decorators wrap the coroutine; call the undecorated original if
        # exposed (``__wrapped__``), else the workflow callable directly.
        fn = getattr(
            sr.retry_failed_downloads_workflow,
            "__wrapped__",
            sr.retry_failed_downloads_workflow,
        )
        await fn(None, None)
    collect.assert_not_awaited()
```

若 DBOS 装饰器包裹导致该测试无法直接调用（import 期要求 DBOS launch），把 skip 逻辑抽成模块级小函数 `async def _media_parser_enabled() -> bool` 并测它 + 用 grep 断言 workflow 体内调用了它（结构测试兜底，不硬啃 DBOS runtime）。

- [ ] **Step 5: 跑测试确认通过 + 全量后端回归**

Run: `cd backend && uv run pytest tests/api/test_module_gates_mounted.py -v && uv run pytest tests/ -x -q --ignore=tests/integration`
Expected: 新测试 PASS；回归无新增失败（integration 目录按仓库惯例跳过）

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/ backend/app/workflows/scheduled_recovery.py backend/tests/api/test_module_gates_mounted.py
git commit -m "feat(modules): 六组 router 挂 require_module gate + retry tick 尊重 media-parser 开关"
```

---

### Task 5: 删旧 per-module status 端点（后端）

**Files:**
- Modify: `backend/app/api/topics_router.py:291-301`（删 `/module-status` 端点）
- Modify: `backend/app/schemas/topics.py:124-130`（删 `ModuleStatusResponse`）
- Modify: `backend/app/api/distribution_router.py:68-83`（删 `/module-status` 端点；**保留** `require_distribution`）
- Modify: `backend/app/schemas/distribution.py:36-43`（删 `ModuleStatusResponse`）
- Test: 现有测试suite（删除后跑回归，含 `tests/topics/`）

**Interfaces:**
- Consumes: Task 3 的 `/modules/status` 已可替代
- Produces: 旧端点不复存在（前端 Task 6/7 之后才部署 —— 同一 PR 内原子替换，无兼容窗口问题）

- [ ] **Step 1: 删除** — 上列四处代码 + 对应 import（`ModuleStatusResponse` 在两个 router 的 import 行）。`distribution_router` 里 `is_module_visible` 若因此不再被引用，连带清掉 shim 里 `is_module_visible` 的 re-export？**不清** —— shim 保持原样（spec §2.3），只删 router 端点本体。

- [ ] **Step 2: 全仓引用扫描**

Run: `grep -rn "module-status\|ModuleStatusResponse" backend/ --include="*.py"`
Expected: 零命中（除测试文件待删的引用；有命中就把那些测试改指向新端点或删除）

- [ ] **Step 3: 回归**

Run: `cd backend && uv run pytest tests/ -x -q --ignore=tests/integration`
Expected: 无新增失败

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/topics_router.py backend/app/api/distribution_router.py backend/app/schemas/topics.py backend/app/schemas/distribution.py
git commit -m "refactor(modules): 删除 topic/distribution 旧 per-module /module-status 端点（被 /modules/status 取代）"
```

---

### Task 6: 前端 service + hook + disabled 页

**Files:**
- Create: `frontend/services/modulesService.ts`
- Create: `frontend/hooks/useModuleStatus.ts`
- Create: `frontend/components/ModuleDisabledPage.tsx`
- Modify: `frontend/public/locales/en.json`、`frontend/public/locales/zh.json`
- Test: `frontend/hooks/useModuleStatus.test.tsx`

**Interfaces:**
- Consumes: Task 3 响应 `{modules: [{id, enabled, visible}]}`
- Produces:
  - `fetchModulesStatus(): Promise<Record<string, ModuleStatus> | null>`（失败返回 null，60s 模块级缓存 + in-flight 去重）
  - `useModuleStatus(id: string): { enabled: boolean; visible: boolean; loading: boolean }`（加载中/失败按 `FAIL_DEFAULTS` 兜底：distribution `false/false`，其余 `true/true`）
  - `<ModuleDisabledPage />` 无 props
  - i18n key：`moduleDisabled.title` / `moduleDisabled.message`

- [ ] **Step 1: 写失败测试** `frontend/hooks/useModuleStatus.test.tsx`

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const fetchModulesStatus = vi.fn();
vi.mock('../services/modulesService', () => ({
  fetchModulesStatus: (...a: unknown[]) => fetchModulesStatus(...a),
}));

import { useModuleStatus } from './useModuleStatus';

describe('useModuleStatus', () => {
  afterEach(() => vi.resetAllMocks());

  it('returns server state once loaded', async () => {
    fetchModulesStatus.mockResolvedValue({
      shares: { enabled: false, visible: false },
    });
    const { result } = renderHook(() => useModuleStatus('shares'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(false);
    expect(result.current.visible).toBe(false);
  });

  it('fails open for rollout modules on fetch error', async () => {
    fetchModulesStatus.mockResolvedValue(null);
    const { result } = renderHook(() => useModuleStatus('todolist'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(true);
    expect(result.current.visible).toBe(true);
  });

  it('fails closed for distribution on fetch error', async () => {
    fetchModulesStatus.mockResolvedValue(null);
    const { result } = renderHook(() => useModuleStatus('distribution'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.visible).toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run hooks/useModuleStatus.test.tsx`
Expected: FAIL（cannot resolve `./useModuleStatus`）

- [ ] **Step 3: 实现三个文件**

`frontend/services/modulesService.ts`：

```typescript
/**
 * Batch module-switch status (Module Control Center, admin-controlled).
 * One request per page lifetime (60s cache + in-flight dedupe) feeds every
 * useModuleStatus() consumer. Returns null on failure — callers apply
 * per-module fail defaults, this layer never guesses.
 */
import { supabase } from '../supabaseClient';

export interface ModuleStatus {
  enabled: boolean;
  visible: boolean;
}

const API_URL = import.meta.env.VITE_API_URL || '';
const TTL_MS = 60_000;

let cache: { at: number; data: Record<string, ModuleStatus> } | null = null;
let inFlight: Promise<Record<string, ModuleStatus> | null> | null = null;

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchModulesStatus(): Promise<Record<string, ModuleStatus> | null> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.data;
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const resp = await fetch(`${API_URL}/api/v1/modules/status`, {
        headers: await authHeaders(),
      });
      if (!resp.ok) throw new Error(`modules/status ${resp.status}`);
      const body = await resp.json();
      const map: Record<string, ModuleStatus> = {};
      for (const m of body.modules ?? []) {
        map[m.id] = { enabled: m.enabled !== false, visible: m.visible !== false };
      }
      cache = { at: Date.now(), data: map };
      return map;
    } catch (err) {
      console.error('modules/status load failed', err);
      return null;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

/** Test seam: reset the module-level cache between test cases. */
export function __resetModulesStatusCache(): void {
  cache = null;
  inFlight = null;
}
```

（认证头的取法以现有 service 为准：若仓库有共享的 `getAuthHeaders` util
（`topicService.ts` 里 import 的那个），直接复用它替代上面 `authHeaders`。）

`frontend/hooks/useModuleStatus.ts`：

```typescript
import { useEffect, useState } from 'react';
import { fetchModulesStatus, type ModuleStatus } from '../services/modulesService';

/**
 * One hook for every Module Control Center switch consumer.
 * Fail defaults are per-module: launched modules fail OPEN (a transient
 * error must never hide a shipped feature); opt-in distribution fails
 * CLOSED (unlaunched surface never flashes into view).
 */
const FAIL_DEFAULTS: Record<string, ModuleStatus> = {
  distribution: { enabled: false, visible: false },
};
const OPEN: ModuleStatus = { enabled: true, visible: true };

export interface ModuleStatusState extends ModuleStatus {
  /** True until the first read resolves — fail-closed guards must wait for
   * this before redirecting (mirrors the old useDistributionModuleStatus). */
  loading: boolean;
}

export function useModuleStatus(id: string): ModuleStatusState {
  const fallback = FAIL_DEFAULTS[id] ?? OPEN;
  const [state, setState] = useState<ModuleStatusState>({ ...fallback, loading: true });
  useEffect(() => {
    let alive = true;
    fetchModulesStatus().then((map) => {
      if (!alive) return;
      setState({ ...(map?.[id] ?? fallback), loading: false });
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  return state;
}
```

`frontend/components/ModuleDisabledPage.tsx`：

```tsx
import { useTranslation } from 'react-i18next';
import { PowerOff } from 'lucide-react';

/** Shown when a route's module is switched off in the admin Module Control
 * Center. Semantic tokens only (no legacy hue classnames). */
export function ModuleDisabledPage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[50vh] gap-3 text-center px-6">
      <PowerOff size={36} className="text-content-3" />
      <h2 className="text-lg font-semibold text-content">
        {t('moduleDisabled.title', 'Feature Unavailable')}
      </h2>
      <p className="text-sm text-content-2 max-w-md">
        {t('moduleDisabled.message', 'This feature is currently disabled by the administrator.')}
      </p>
    </div>
  );
}
```

i18n：`en.json` 顶层加

```json
"moduleDisabled": {
  "title": "Feature Unavailable",
  "message": "This feature is currently disabled by the administrator."
}
```

`zh.json` 对应：

```json
"moduleDisabled": {
  "title": "功能不可用",
  "message": "该功能当前已被管理员停用。"
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run hooks/useModuleStatus.test.tsx`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/services/modulesService.ts frontend/hooks/useModuleStatus.ts frontend/components/ModuleDisabledPage.tsx frontend/hooks/useModuleStatus.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(frontend): useModuleStatus 统一 hook + 批量 modulesService + ModuleDisabledPage"
```

---

### Task 7: 前端消费点接线 + 迁移删除旧 hooks

**Files:**
- Modify: `frontend/components/Sidebar.tsx:223-227` 及导航项（约 353-457 行两段）
- Modify: `frontend/components/ResourcesSidebar.tsx:252-263`（My Downloads 按钮）
- Modify: `frontend/components/ResourcesViewInner.tsx:612-613`（DownloadsView 分支）
- Modify: `frontend/pages/ProjectsPage.tsx:274`（IdeationBoard 分支）
- Modify: `frontend/components/project/ProjectFilterSidebar.tsx:160-170`（Ideation 过滤项）
- Modify: `frontend/router.tsx:113-119`（DistributionModuleGuard 改用新 hook）+ 各模块路由包 guard
- Modify: `frontend/pages/TopicInspirationPage.tsx`（改用 `useModuleStatus('topic-inspiration')`）
- Modify: `frontend/pages/inspirationFlag.test.tsx:7-9`（mock 路径改为新 hook）
- Delete: `frontend/hooks/useTopicModuleEnabled.ts`、`frontend/hooks/useDistributionModuleStatus.ts`
- Modify: `frontend/services/topicService.ts:141-152`、`frontend/services/distributionService.ts`（删各自 `getModuleStatus` + 类型导出）
- Test: 现有 vitest suite + `frontend/router.moduleGuard.test.tsx`（新增）

**Interfaces:**
- Consumes: Task 6 的 `useModuleStatus(id)`（含 `loading`）、`ModuleDisabledPage`
- Produces: `GlobalModuleGuard`（router.tsx 内部组件，不导出）——
  `{ id: string; children: ReactNode }`，`!visible` 时渲染 `<ModuleDisabledPage />`
  （注意与现有**团队级** `components/ModuleGuard.tsx` 是不同概念，命名必须区分，不得复用/覆盖它）

- [ ] **Step 1: 写失败测试** `frontend/router.moduleGuard.test.tsx`（测新 guard 组件的三态；guard 从 router.tsx 提出到 `frontend/components/GlobalModuleGuard.tsx` 以便测试与复用）

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const mockStatus = vi.fn();
vi.mock('./hooks/useModuleStatus', () => ({
  useModuleStatus: (id: string) => mockStatus(id),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fb?: string) => fb ?? _k }),
}));

import { GlobalModuleGuard } from './components/GlobalModuleGuard';

describe('GlobalModuleGuard', () => {
  it('renders children when visible', () => {
    mockStatus.mockReturnValue({ enabled: true, visible: true, loading: false });
    render(<GlobalModuleGuard id="shares"><div data-testid="inner" /></GlobalModuleGuard>);
    expect(screen.getByTestId('inner')).toBeTruthy();
  });

  it('renders nothing while loading', () => {
    mockStatus.mockReturnValue({ enabled: true, visible: true, loading: true });
    const { container } = render(
      <GlobalModuleGuard id="shares"><div data-testid="inner" /></GlobalModuleGuard>,
    );
    expect(container.innerHTML).toBe('');
  });

  it('renders disabled page when hidden', () => {
    mockStatus.mockReturnValue({ enabled: true, visible: false, loading: false });
    render(<GlobalModuleGuard id="shares"><div data-testid="inner" /></GlobalModuleGuard>);
    expect(screen.queryByTestId('inner')).toBeNull();
    expect(screen.getByText('Feature Unavailable')).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run router.moduleGuard.test.tsx`
Expected: FAIL（cannot resolve GlobalModuleGuard）

- [ ] **Step 3: 实现 `frontend/components/GlobalModuleGuard.tsx`**

```tsx
import type { ReactNode } from 'react';
import { useModuleStatus } from '../hooks/useModuleStatus';
import { ModuleDisabledPage } from './ModuleDisabledPage';

/**
 * ADMIN-GLOBAL module guard (Module Control Center `visible` switch) — not to
 * be confused with the TEAM-level ./ModuleGuard (per-team view toggles).
 * Waits for the first status read so fail-closed modules never flash a bogus
 * disabled page; then either renders the route or the disabled notice.
 */
export function GlobalModuleGuard({ id, children }: { id: string; children: ReactNode }) {
  const { visible, loading } = useModuleStatus(id);
  if (loading) return null;
  if (!visible) return <ModuleDisabledPage />;
  return <>{children}</>;
}
```

- [ ] **Step 4: 消费点逐个接线**

1. **router.tsx**：
   - `import { GlobalModuleGuard } from './components/GlobalModuleGuard';`
   - 删除 `DistributionModuleGuard`（113-119 行）及其 `useDistributionModuleStatus` import；distribution 路由（282 行附近的 accounts/publish/records 区）改包 `<GlobalModuleGuard id="distribution">`（disabled 页替代原 redirect —— 模块存在性已由 404 API gate 保护，路由层显示 disabled 页即可，行为统一）
   - `projects`（218 行）与 `resources`（含 file/folder/smart 等 208-215 行的 resources 系不动 —— Resources 不在本期模块表）中，**只**给 `projects` 路由内层加 `<GlobalModuleGuard id="projects">`（保留外层团队级 ModuleGuard）
   - `todolist`（231）、`shared`（235）、`chat`（289）、`ai-library` 子树（246）各包 `<GlobalModuleGuard id="...">`：`todolist`/`shares`/`ai-library`（chat 与 ai-library 同用 `"ai-library"`）
   - `parser`（205，Topic Inspiration）内层加 `<GlobalModuleGuard id="topic-inspiration">`
2. **Sidebar.tsx**：
   - 删 `useTopicModuleStatus` / `useDistributionModuleStatus` 两个 import 与调用（223-227 行），换成：

```tsx
  const { visible: topicModuleVisible } = useModuleStatus('topic-inspiration');
  const { visible: distributionModuleVisible } = useModuleStatus('distribution');
  const { visible: projectsVisible } = useModuleStatus('projects');
  const { visible: sharesVisible } = useModuleStatus('shares');
  const { visible: todolistVisible } = useModuleStatus('todolist');
  const { visible: aiVisible } = useModuleStatus('ai-library');
```

   - 主模式与 team 模式两段导航里（约 353-457 行）：Projects 项包 `projectsVisible &&`、Shared 项包 `sharesVisible &&`、Todolist 项包 `todolistVisible &&`、AI Library 与 Chat 两项包 `aiVisible &&`（原有 `isViewEnabled(...)`/permission 条件保留，用 `&&` 叠加）
3. **ResourcesSidebar.tsx:252**：My Downloads 按钮包 `mediaParserVisible &&`（组件顶部加 `const { visible: mediaParserVisible } = useModuleStatus('media-parser');`）
4. **ResourcesViewInner.tsx:612**：

```tsx
        {isDownloadsView ? (
          mediaParserVisible ? <DownloadsView /> : <ModuleDisabledPage />
        ) : (
```

（同样在组件顶部取 `useModuleStatus('media-parser')`；覆盖直达 URL `/resources/downloads` 的场景）
5. **ProjectsPage.tsx:274** 与 **ProjectFilterSidebar.tsx:160**：Ideation 过滤项与 IdeationBoard 分支都以 `ideationVisible` 条件渲染——ProjectFilterSidebar 新增 prop `ideationVisible: boolean`（由 ProjectsPage 传入，ProjectsPage 顶部取 `useModuleStatus('ideation')`）；IdeationBoard 分支改 `activeFilter === IDEATION_FILTER && ideationVisible ? <IdeationBoard .../> : ...`
6. **TopicInspirationPage.tsx**：`useTopicModuleStatus()` 改 `useModuleStatus('topic-inspiration')`（返回形状兼容，多出的 `loading` 字段不影响现有解构）；**inspirationFlag.test.tsx:7-9** 的 mock 从 `../hooks/useTopicModuleEnabled` 改成：

```tsx
vi.mock('../hooks/useModuleStatus', () => ({
  useModuleStatus: () => ({ visible: true, enabled: true, loading: false }),
}));
```

7. **删除**：`frontend/hooks/useTopicModuleEnabled.ts`、`frontend/hooks/useDistributionModuleStatus.ts`、`topicService.ts` 的 `getModuleStatus`+`TopicModuleStatus`、`distributionService.ts` 的 `getModuleStatus`+`DistributionModuleStatus`（先 grep 确认无其他引用）：

Run: `grep -rn "useTopicModuleStatus\|useDistributionModuleStatus\|TopicModuleStatus\|DistributionModuleStatus" frontend --include="*.ts" --include="*.tsx"`
Expected: 零命中后再删文件

- [ ] **Step 5: 全量前端验证**

Run: `cd frontend && npx vitest run && npm run build`
Expected: 测试全过、build 成功（build 抓 TS 编译错，比如漏删的 import）

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): 6 大模块导航/路由接 visible 开关；迁移删除 topic/distribution 旧 hooks"
```

---

### Task 8: 端到端冒烟（部署后 / 本地栈）

**Files:** 无代码改动 — 验收步骤

**Interfaces:**
- Consumes: 全部前序 Task；claude-debug 测试账号（凭证 `/media/heygo/program/datahub/nous/secrets/claude-debug.env`，用法见 memory `claude-debug-test-account`）

- [ ] **Step 1: 后端 API 冒烟**（gpupc 生产容器内执行，或本地 dev 栈等价端口）

```bash
# 取 token（127.0.0.1:9082 = kong，可走代理）
set -a; . /media/heygo/program/datahub/nous/secrets/claude-debug.env; set +a
ANON=$(docker exec nous-backend printenv SUPABASE_ANON_KEY)
TOK=$(curl -sS -X POST "http://127.0.0.1:9082/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "{\"email\":\"$DEBUG_TEST_EMAIL\",\"password\":\"$DEBUG_TEST_PASSWORD\"}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

# 1) 批量状态端点：应返回 9 个模块
docker exec nous-backend curl -sS -H "Authorization: Bearer $TOK" \
  http://localhost:8080/api/v1/modules/status | python3 -m json.tool

# 2) Admin 关掉 shares（或直接 SQL 写 system_settings）：
docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  "INSERT INTO public.system_settings(key, value) VALUES ('shares.module', '{\"enabled\": false, \"visible\": false}'::jsonb)
   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;"

# 3) 60s 缓存过期后（或重启 backend）应 503 + MODULE_DISABLED
sleep 61
docker exec nous-backend curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOK" http://localhost:8080/api/v1/shares
docker exec nous-backend curl -sS -H "Authorization: Bearer $TOK" \
  http://localhost:8080/api/v1/shares | python3 -m json.tool   # detail.code == MODULE_DISABLED

# 4) 恢复
docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  "UPDATE public.system_settings SET value='{\"enabled\": true, \"visible\": true}'::jsonb WHERE key='shares.module';"
```

Expected: 步骤 1 九个模块；步骤 3 `503` 且 body `{"detail": {"code": "MODULE_DISABLED", "module": "shares"}}`；步骤 4 后 `/api/v1/shares` 恢复 200。
（注意 gate 读的是 DB 实时值、无缓存 —— `sleep 61` 只为 `/modules/status` 的缓存；`/shares` 的 503 应立即生效，可先验这个。）

- [ ] **Step 2: Admin UI 冒烟**：打开 Admin → Settings → Modules，确认出现 9 张卡片、切换任一开关成功（Message 提示 + `admin_audit_logs` 有 `update_module_switches` 记录）。

- [ ] **Step 3: 前端冒烟**：以测试账号登录 app，Admin 关 `todolist.module.visible` 后刷新页面 → 侧栏 Todolist 入口消失、直达 `/todolist` URL 显示 "Feature Unavailable"；恢复后入口回来。

- [ ] **Step 4: 按 CLAUDE.md 规则发 Discord 通知**（若 Discord MCP 可用）：任务完成摘要 + 冒烟结果。

---

## Self-Review 记录

- **Spec coverage**：§1 模块表 → Task 1/4/7；§2.1 四个新增件 → Task 2/3/6/7（guard 提为 `GlobalModuleGuard` 组件文件，比 spec 说的 router 内联更可测）；§2.3 净删减 → Task 5/7；§3 错误处理 → Task 2（类型化 503）+ Task 6（fail 默认表）；§4 测试 → 各 Task 内嵌 + Task 8 冒烟；§5 清单逐项对应。
- **无占位符**：所有代码块给全文；唯二"以现状为准"处（AuthDep import 路径、TTLCache 方法名）已注明核对位置，属防漂移指引而非留白。
- **类型一致性**：`require_module` / `fetchModulesStatus` / `useModuleStatus` / `GlobalModuleGuard` 各 Task 间签名一致；模块 id 六处引用统一走 Task 1 的 Interfaces 表。
