# Module Control Center 全量接入 — 6 个大模块开关设计

**日期**: 2026-08-03
**状态**: 设计已确认
**前置**: Module Control Center 框架已存在（`backend/app/services/modules/registry.py` + Admin Settings → Modules）

## 1. 目标

把 6 个已上线的用户功能模块接入现有的 Module Control Center 注册表，让管理员可以在
Admin → Settings → Modules 即时开/关每个模块的后台处理（`enabled`）与前端可见性
（`visible`），无需重启。基础设施（Auth / Teams / Settings / Search）不上开关。

新接入模块（全部 **默认开、fail-open** —— 已上线功能，配置缺失/读失败时不误伤）：

| module id | system_settings key | label | 后端 gate 范围 | visible 控制的前端面 |
|---|---|---|---|---|
| `media-parser` | `media.module` | Media Parser & Downloads | `media_fetch_router` + `media_batch_router`（发起新解析/下载）+ `retry_failed_downloads_workflow` tick | My Downloads 页面入口（ResourcesSidebar personal mode）+ 解析发起 UI |
| `projects` | `projects.module` | Projects (MediaTrack) | `projects_router` + `project_assets_router` + `canvases_router` | 导航 Projects 入口 + 路由 |
| `shares` | `shares.module` | Shares | `shares_router` 全部 | 导航 Shared 入口 + 路由（分享链接访问也被 503） |
| `todolist` | `todolist.module` | Todolist (Issues) | `issues_router` 全部 | 导航 Todolist 入口 + 路由 |
| `ai-library` | `ai.module` | AI Library & Chat | `conversation_router` + `ai_library_router` | 导航 AI Library + Chat 两个入口 + 路由 |
| `ideation` | `ideation.module` | Ideation Board | `ideation_router` | 项目内 Ideation 画板入口 |

明确**不在**本期范围：
- **Scripts**（script_* 8 个 router）— 核心用途，不上开关
- `ai_settings_router` / `ai_memory_router` / `script_ai_router` — 属设置基础设施或 Scripts 域，不随 `ai-library` 关闭
- 已接入的 3 个模块行为不变：Topic Inspiration（fail-open）、Distribution（404 fail-closed，**不迁**到新 gate）、Unified Storage

## 2. 架构（方案 A：批量状态端点 + 通用 gate 依赖）

### 2.1 复用与新增

**不新建任何类。** `ModuleDef` / `ModuleState` dataclass 与
`read_module_state()` / `write_module_state()` / `list_module_summaries()` 原样复用。
不再新增 per-module shim（`topics/module_config.py` 那种是历史产物，新模块直接调
`read_module_state(MODULES_BY_ID[...])`）。

新增（全项目各一份）：

1. **`backend/app/services/modules/gate.py`** — FastAPI 依赖工厂：

```python
def require_module(module_id: str):
    """模块 enabled=off 时 raise 503，detail 为类型化错误体。fail-open：
    读不到配置走模块默认值（本期 6 个模块默认全开）。"""
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

挂载方式：各 router 定义处加 `dependencies=[Depends(require_module("<id>"))]`
（router 级一行；`media-parser` 例外，只挂 fetch 两个 router，不挂读路径）。

2. **`GET /api/v1/modules/status`**（新 router `modules_router.py`，登录用户可读）——
一次返回全部注册模块的 `[{id, enabled, visible}]`。服务端 60s 内存缓存
（复用现有 cache 基建），admin 写入时不主动失效（最长 60s 收敛，可接受）。

3. **前端**：
   - `services/modulesService.ts` — `fetchModulesStatus()`，模块级内存缓存 + in-flight 去重
   - `hooks/useModuleStatus.ts` — `useModuleStatus(id) → {enabled, visible}`，
     加载中/失败返回 fail-open 默认值（`{enabled: true, visible: true}`），
     Distribution 特例由 hook 内 per-module 默认表给 fail-closed 默认值
   - `components/ModuleDisabledPage.tsx` — 统一的 "This feature is currently
     disabled" 提示页（英文 UI + i18n key）

### 2.2 数据流

```
Admin 切开关 → PUT /admin/settings/modules/{id} → system_settings upsert + 审计日志
                                                        ↓
后端各 router 请求时:  require_module dep → read_module_state → 503 MODULE_DISABLED
DBOS retry tick:       读同一状态 → enabled=off 时本轮 skip（log 一行）
前端加载时:            GET /modules/status（≤60s 旧）→ Sidebar 隐藏入口 / 路由渲染 ModuleDisabledPage
```

### 2.3 净删减（迁移旧模式）

- 删 `topics_router` 与 `distribution_router` 各自的 `/module-status` 端点
- 删 `useTopicModuleStatus` / `useDistributionModuleStatus` 两个 hook 及对应
  service 函数，消费点（Sidebar、router.tsx、TopicInspirationPage）改用
  `useModuleStatus('topic-inspiration' | 'distribution')`
- `topics/module_config.py`、`distribution/module_config.py` shim 保留
  （backend 内部消费点仍在用，属注册表薄封装，无重复逻辑）

## 3. 错误处理

- **503 响应体**：gate 抛 `HTTPException(503, detail={"code": "MODULE_DISABLED",
  "module": "<id>"})`，经全局处理器包装后**上线形状**为
  `{"success": false, "error": "Request failed", "code": "http_503",
  "details": {"code": "MODULE_DISABLED", "module": "<id>"}}` —— 前端判定读
  `body.details.code`，模块名读 `body.details.module`（顶层 `code` 是 envelope 的
  `http_503`，不是模块码）。`_handle_http_exception` 对 ≥500 一律通用化以防泄漏，
  该 body 全为我方构造、无异常字符串，故单独放行。类型化契约，前端 API 层识别
  后展示模块停用提示而非通用报错 toast（符合"触发路径必须类型化失败回显"纪律）
- **fail-open**：`system_settings` 读失败/行缺失 → 6 个新模块按默认值视为开启
  （registry 现有行为，`_read_raw` never raises）
- **交叉影响（预期行为，不做联动）**：`ai-library` 关闭时 Todolist 页的
  "派发给 Agent" 按钮会收到 503 并展示停用提示；`projects` 关闭不联动关闭
  `ideation`（两者独立 gate，均在项目区内）
- **`projects` 开关的爆炸半径（已知，暂不隔离）**：`projects_router` 除项目
  CRUD 外还托管剧本实体数据面 —— `/{project_id}/entities`、`/characters*`、
  `/lib/{entity_type}*`、`/workflow*` 全在同一个 router 下，因此挂的是 router 级
  gate，关闭 `projects` 会一并拦掉这些子路径。可观察到的连带影响有两处：
  Scripts 编辑器的 @-mention 人物候选会静默变空（`EditorShell` 处 catch 仅
  `console.error`，不弹提示）；TodolistPage 的项目列表拉空。**Scripts 自身
  （`script_*` router）不受该开关影响**，正文编辑、AI 续写等仍可用。
  若将来要把剧本数据面与项目开关解绑，需把上述三组子路径从 `projects_router`
  摘出到独立 router 再单独定 gate —— 记为后续决策，不在本 PR 范围。
- **正在跑的任务不中断**：gate 只拦新请求/新 tick，已入队的 DBOS workflow 不受影响

## 4. 测试

- **backend（pytest）**：
  - `gate.py`：enabled=off → 503 且 detail.code 正确；enabled=on → 放行；
    配置读失败 → fail-open 放行
  - `/modules/status`：返回全部注册模块、字段齐全、需认证
  - 各 gated router 冒烟：off 时任一端点 503（参数化 6 个模块）
- **frontend（vitest）**：参照现有 `inspirationFlag.test.tsx` 模式 —
  `useModuleStatus` fail-open 默认、visible=false 时 Sidebar 不渲染对应入口、
  路由命中隐藏模块渲染 ModuleDisabledPage
- **端到端冒烟（部署后）**：Admin 关 `shares` → `/api/v1/shares` 503 →
  重新打开恢复（用 claude-debug 测试账号走真实链路）

## 5. 实施清单（概要）

1. `registry.py` MODULES 追加 6 条 `ModuleDef`
2. 新增 `gate.py` + `modules_router.py`（注册进 `api/__init__.py`）
3. 6 组 router 挂 `Depends(require_module(...))`（media 只挂 fetch 面）
4. `scheduled_recovery.retry_failed_downloads_workflow` 开头加 enabled 检查
5. 前端 service + hook + ModuleDisabledPage；Sidebar / router.tsx /
   ResourcesSidebar（My Downloads）/ 项目内 Ideation 入口接 visible
6. 迁移并删除 topic/distribution 旧 status 端点与旧 hooks
7. i18n：en/zh 加 module disabled 文案 key
8. 测试 + 部署后冒烟
