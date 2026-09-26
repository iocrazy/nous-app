# 平台模型与 BYOK 同形：一次聚合、纯映射、引擎按需读

日期：2026-09-25 · 状态：已拍板（用户 2026-09-25「好的」）· 取代：#2438 小时就绪探针（nous 行部分）、#2446 自动建行、#2458 每分钟同步 / 撤销禁用 / 401 全禁

## 1. 问题

BYOK 厂商卡（ModelScope、DeepSeek …）的形状是：用户点一次「测试连接」把厂商目录存进自己的设置，从目录里勾「已启用模型」，之后智能体编辑器、任务分配、画布**只读那一个已保存的设置对象**，纯映射，不再请求任何东西。

Nous（平台）不是这个形状：

| | BYOK 卡 | Nous（平台）现状 |
|---|---|---|
| 清单来源 | `providers.<key>.models`（用户设置里） | 管理端目录表 `nous_models`，用户设置只存黑名单 |
| 消费方取值 | 一处：`getAvailableModels(settings)` | 五处各自请求：设置页 `/ai/nous-models`、智能体编辑器 `/ai/nous-models?type=llm`、画布文本节点 `/canvases/text-models`、出图节点与封面工作室 `/canvases/generation-models`，各带一套过滤 |
| 存在与加载状态 | 无 | 后台抄：每分钟 `nous_engine_sync_workflow` + 小时 `nous_model_health_workflow` 写回 `last_test_status` / `is_enabled` |

抄状态的每一层都带自己的失败模式（空列表、401、滞后、要不要自动重新启用），而引擎自己一次查询就能回答"存不存在、加载没加载"。用户原话：「为什么要有这种同步工作流？同一个地方取值不行吗？其他就是映射」「智能体等其他需要使用的地方是一体显示的，不是说这模块要获取下，另一个模块还要获取下」。

## 2. 目标

1. 用户端只有**一个**平台模型清单，随 `GET /api/v1/ai/settings` 一起下发，形状与 BYOK 卡一致；所有消费方走现成的 `getAvailableModels(settings)`。
2. 引擎的"存在 / ready"在服务端**按需读**（30 秒缓存、引擎不通沿用上次），不再写回目录表。
3. 目录表 `nous_models` 退回**纯映射**：名字、类型、价格、base_url/key、窗口覆盖、管理员启停。
4. 删掉每分钟同步工作流、小时探针里对 nous 行的就绪读取、缺席自动禁用、401 全禁。

不变：管理端仍是唯一的平台模型管理入口（key、价格归管理员；用户只做启停）；BYOK 卡的一切不动；`last_test_status` 列与 `idle` 枚举值保留（admin「Test」按钮仍写）。

## 3. 设计

### 3.1 服务端聚合：`app/services/ai/platform_provider.py`

`async def platform_provider_view(user_id) -> PlatformProviderView`：

1. `NousModelRepository.list_enabled(viewer_user_id=user_id)`——管理员启用的行，含 owner 作用域（BYOK 的 nous 行只对 owner 可见，现状）。
2. 治理：`nous_enabled=False` → `enabled=False, models=[]`（现在的 `visiblePlatformModels` 在前端做这件事，搬到服务端做一次）。
3. `actual_provider='nous'` 的行按 base_url 叠引擎快照（§3.2）：
   - 快照有该 `actual_model` → `status = ready ? 'ok' : 'idle'`；
   - 快照**没有**该服务（引擎明确列出了别的、就是没它）→ **不进清单**（授权已撤销或服务已下线；不写库、不禁用，管理端能看到「引擎未列出」）；
   - 引擎不可达且无可用快照 → 行**照常进清单**，`status='not_probed'`，视图带 `engine.reachable=false`（「空输出不是否定结论」：够不着 ≠ 撤销）。
4. 其他平台行（doubao / deepseek …经平台 key）的 `status` 仍来自小时探针写的 `last_test_status`（那些探针是真调厂商，保留）。
5. `status='fail'` 的行不进清单（沿用 2026-09-24 裁定）。
6. `enabled_models = [m.name for m in models if m.name not in disabled_models]`。

响应形状（挂在 `AISettingsResponse`，由 `GET /ai/settings` 一并返回；`PUT` 的回显同样带）：

```
ai_providers.nous = {
  "enabled": bool,                 # 用户主开关（存储值；缺省 true）
  "managed": true,                 # 平台托管标记：无 api_key，卡片不渲染 key 框与测试连接
  "models": [row name, ...],       # 用户可启停的全部行 —— 与 BYOK 的 models 同形（字符串数组）
  "enabled_models": [row name, ...],   # models − disabled_models —— 消费方读这个
  "disabled_models": [row name, ...]   # 存储的黑名单（PUT 仍写它）
}
platform_models = {              # 顶层，name → 映射（BYOK 不需要，因为 BYOK 的 id 就是模型名）
  "<name>": { "actual_model", "type", "status", "is_local", "pricing_type", "pricing_value",
              "context_window_tokens" },      # 不带 base_url / key（2026-08-14 泄露绊线）
  ...
}
platform_engine = { "reachable": bool, "stale": bool, "checked_at": WireDatetime | null } | null
```

- `models` 用字符串数组是刻意的：`getAvailableModels(settings)` 零改动就能读 nous；名字与状态从 `platform_models[name]` 映射（`utils/platformModel.platformModelLabel` 的 primary = `actual_model || name` 规则不变）。
- `PUT /ai/settings` 对 `nous` **只持久化** `enabled` 与 `disabled_models`，其余键剥掉（服务端 `_strip_computed_platform_fields`）；wire 测试钉住：带着 `models` / `enabled_models` PUT 上去，存储里不会出现它们。
- `platform_models[name].status` 取值 `ok | idle | not_probed`（`fail` 行已被过滤掉，不会出现）。

### 3.2 引擎访问：`app/services/ai/engine_catalog.py`

一个函数、一处缓存，取代 `nous_engine_sync._fetch_services` 与 `nous_model_health._probe_nous_engine_readiness`：

```
@dataclass(frozen=True)
class EngineService: id, type, ready: bool, context_window: int|None, capabilities: Mapping|None
@dataclass(frozen=True)
class EngineSnapshot:
    services: Mapping[str, EngineService] | None   # None = 没有可用列表
    fetched_at: datetime | None
    reachable: bool          # 这次（或沿用的那次）是否真读到了
    stale: bool              # True = 这次没读到，沿用的是 ≤10 分钟前的快照
    unauthorized: bool       # 401
    error: str | None

async def engine_snapshot(base_url, api_key) -> EngineSnapshot
```

- `GET {base}/models?include_unready=1`，5 秒超时；`app/core/cache.TTLCache` 30 秒，key = base_url；**keep-last-good**：读失败时若上次成功快照 ≤ 10 分钟，返回它并标 `stale=True`；否则 `services=None, reachable=False`。`TTLCache.get_or_load` 的 load 失败不缓存，所以失败路径每次都会重试，符合预期。
  - 实施注记（P1 #2468，2026-09-25 裁定）：① **失败结果同样缓存 30 秒**（快照本身带 `reachable=false` / `stale`，不是抛异常）。否则引擎假死时每次 `GET /ai/settings` 都要等满超时，且 `TTLCache` 跨 key 共用一把锁会把请求串行化。② 超时 15 秒 → **5 秒**：它挂在登录后的设置加载路径上，引擎同内网，正常几十毫秒。③ 缓存键是 `base_url + key 指纹`，快照按行自己的 key 读（派发用的就是它）；key 相同时一个引擎仍只读一次。
- 401 → `unauthorized=True, services=None`。**不做任何写入**。
- `context_window` / `capabilities` 为 null 原样带出（§3.4 的手动同步与窗口缓存各自决定怎么用；null 永不覆盖已填值）。
- 调用方：§3.1 视图、§3.3 状态端点、§3.5 admin 叠加、§3.6 派发守卫、§3.4 手动同步。

### 3.3 运行态状态：`GET /api/v1/ai/platform-status`

清单（配置，变化慢）和状态（运行态，变化快）分开：清单随设置一次下发；状态由一个轻端点给：

```
{ "models": { "<name>": { "status": "ok|idle|not_probed", "local_ready": bool|null } },
  "engine": { "reachable", "stale", "checked_at" } }
```

- 与 §3.1 同一个函数算出来（同一份缓存），外加本地 daemon 行的 `local_ready`（现 `services/generation/local_readiness` 对 viewer 的判定，`is_local` 行才有）。
- 前端一个 hook `usePlatformStatus()`（复用 `staleCatalog` 的策略：模块级缓存，窗口聚焦刷新，两次至少隔 30 秒），是**唯一**的状态请求点；所有选择器把它叠在 `settings` 的清单上（`platformModelAvailability` 输入从行改成 status）。
- 没拿到状态（首屏、请求失败）时按 `settings` 里下发的初始 status 显示，绝不空白。

### 3.4 目录表退回映射 + 手动同步

- 删除：`nous_engine_sync_workflow`、`sync_engine_catalog_step`、小时探针里的 `_sync_engine_catalog` 前置、`_follow_ready`、`_disable_revoked`、401 全禁、`NousEngineSyncRepository.disable_rows` / `record_ready`、`READY_TRACKED_TYPES`。
- 小时探针 `probe_nous_models_step` 对 `actual_provider='nous'` 的行**跳过**（`not_probed`，detail `live: status comes from nous-engine`），不再逐行 `GET /models/{id}`；其他平台行照旧。
- 保留 admin「Sync from nous-engine」按钮 = `sync_engine_models` 的**建行 + 窗口更新**部分（改用 `engine_snapshot`），语义只剩「把引擎目前列出、目录里还没有的服务建成行」；响应模型去掉 `disabled` / `ready_changed` / `unauthorized`（`unauthorized` 改为 `error` 文案）。不再有任何自动建行。
- `refresh_catalog_windows` 不动（它读 `context_window_tokens`，由手动同步或 admin 编辑写入）。

### 3.5 管理端

- `GET /admin/nous-models` 对 nous-engine 行叠加 `engine_status: "listed" | "missing" | "unreachable"` 与 `engine_ready: bool | null`（同一快照）；admin 页每行状态点对这类行读 `engine_status`/`engine_ready`，不读 `last_test_status`；`missing` 显示「引擎未列出」（管理员据此决定停用或删除，**系统不替他停**）。
- 「Sync from nous-engine」提示改为「新建 N 行」；admin 页 `:413` 那条已过时的注释删掉。
- `is_enabled` 从此只有 admin PUT / 创建写。

### 3.6 派发守卫

`resolve_nous_model`（`ai_provider_helpers.py`）对 `actual_provider='nous'` 的行：取快照，若 `reachable && services 不含 actual_model` → raise 类型化 `engine_service_unavailable`（上层 → 409/503 带 `details.code`）；不可达或 stale → 放行（真正的调用会给出类型化错误，不在这里猜）。`rank_default_candidates` 的输入从 `last_test_status` 改为 §3.1 视图算出的 `status`（`canvas_run_service._pick_default_row`、`resolve_scorer_config` 两处调用方一并改；`ok > not_probed > idle`，`fail` 跳过）。

### 3.7 前端消费方收口

| 现在 | 之后 |
|---|---|
| `aiService.getNousModels()`（设置页、智能体编辑器） | 删除；读 `settings.providers.nous.*` + `settings.platform_models` |
| `canvasGenerationService.listTextModels` / `useTextModels` | 删除；`platform_models` 里 `type='llm'` 且在 `enabled_models` 的行 |
| `listGenerationModels` / `useGenerationModels` | 保留 hook 名，实现改为 `platform_models` 里 image/video 行 + `usePlatformStatus` 的 `local_ready` 叠加（原 `apply_readiness` 的本地 daemon 判定与 jimeng 孪生隐藏搬到 §3.3 状态端点） |
| `listGenerationCapabilities` / `useModelCapabilities` | 不动（能力表是另一件事） |
| `visiblePlatformModels`（前端做治理 + 黑名单） | 删除；服务端已算好 `enabled_models` |
| `utils/platformModel.platformModelAvailability(row)` | 输入改为 `status` 字符串，规则不变 |
| Nous 卡 | 读 `providers.nous.models` / `enabled_models`，写 `disabled_models`；`managed=true` 时不渲染 key 框与测试连接（现状已如此） |

### 3.8 删除的端点（P3，先全仓 grep 零调用方）

`GET /ai/nous-models`、`GET /canvases/text-models`；`GET /canvases/generation-models` 的清单部分并入 §3.1（`generation-capabilities` 保留，其可见性过滤改读同一个视图函数以保持"能力表与清单逐行一致"的既有不变量）。

## 4. 不做

- 不自动建行、不自动禁用、不自动重新启用——目录是管理员的映射表。
- 不把 nous 的存储改成白名单 `enabled_models`（会让新行对老用户默认关闭）；存储仍是 `disabled_models`。
- 不给 BYOK 卡加状态叠加。
- 不做 DB 迁移。

## 5. 分期与验收

| 期 | 内容 | 验收 |
|---|---|---|
| P1 后端（本 PR） | §3.1 §3.2 §3.3 §3.5 §3.6；`GET/PUT /ai/settings` 聚合与剥离；旧端点与定时任务**暂不删**（前端还在用） | wire 测试贴真实响应；`/ai/settings` 里 `ai_providers.nous.models` 与 `/ai/nous-models` 逐行一致；引擎不通时 `platform_engine.reachable=false` 且清单不空；PUT 不把计算字段存进库 |
| P2 前端 | §3.7 | 五处消费方零单独请求（vitest 断言 `getNousModels` 等不再存在）；Nous 卡、智能体编辑器、画布三处显示同一份清单与状态；e2e:prod 走查 |
| P3 删除 | §3.4 §3.8；测试清理；`export_openapi` + 两端 `gen:api` | 棘轮零容忍仍绿；`dbos.workflow_status` 不再出现 `nous_engine_sync_workflow`；小时探针日志里 nous 行 `not_probed` |

上线后真栈：引擎侧暂停一个服务的授权 → 30 秒内它从用户清单消失、admin 显示「引擎未列出」、目录行 `is_enabled` 不变；恢复授权 → 30 秒内回到清单。停掉引擎 → 清单不变、卡片显示「nous-engine 不可达」、派发得到类型化错误。

## 6. 记票

- `resolve_platform_model`（治理 / embedding_config / graph_memory / embedding_spaces 调用）不经用户侧开关，本期不动。
- admin 目录零 CI 覆盖（既有）。
