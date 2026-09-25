# 前端类型从 Pydantic 派生（OpenAPI → TypeScript）设计

日期：2026-09-24　状态：已拍板，分期实施

## 1. 问题

后端用 Pydantic 2 做契约（请求校验、响应序列化），前端却在 `frontend/types.ts`
**手写**了 194 个 interface，与 Pydantic 没有任何派生关系。后果：

- 后端改了 schema，前端副本不动，漂移只在 `tsc` 里以噪音形式出现（2026-09-24 计
  62 条，其中 `FileVersion.resource_id`、`ParsedMedia.notes`、migration 066 之前的
  `Video` 类型名都是这种）。
- CI 的 `TypeScript check` 步骤是 `continue-on-error: true`，`npm run build` 只是
  `vite build`，所以这道门从未拦过任何人；新漂移淹在旧噪音里。

摸底（2026-09-24，离线 `app.openapi()`）：

| 指标 | 数 |
|---|---|
| OpenAPI 路径 / 操作 | 695 / 886 |
| `components.schemas` | 663 |
| 200 响应有明确 schema 的操作 | **300** |
| 200 响应是裸 `dict`（无 `response_model`）的操作 | **586**，其中 191 处是 `return {"success": True, "data": ...}` 信封 |
| `types.ts` 里名字与 schema 精确重合 | 18；再加 `*Response/*Out` 后缀匹配 14 |
| 重复 `operationId` | 4（两个 legacy 重定向路由各注册两次） |

结论：**只做 codegen 覆盖不到三分之二的接口**。真正的工作是把后端裸 `dict` 响应
补上 `response_model`，让 Pydantic 成为唯一来源；codegen 只是把它搬到前端的管道。

## 2. 目标 / 非目标

目标：
1. 前端所有 **API 形状**的类型由后端 OpenAPI 生成，禁止手写副本。
2. 漂移在 PR 里被拦：生成物过期 → CI 红；后端裸 `dict` 响应数量只降不升（棘轮）。
3. 清零现有 62 条 `tsc` 错误后，`TypeScript check` 改为阻塞步骤。

非目标：
- 不改 API 的 wire 形状（信封 `{"success","data"}`、Snowflake id 的 number/string
  差异等**照旧**——生成的类型必须反映真实形状，这正是「边界 mock 必须用真实 JSON
  形状」纪律的类型版）。
- 不引入运行时校验库（zod 等）；运行时约束仍由 Pydantic 在后端承担。
- 不重写 `apiClient`；只是给它的调用方换类型。

## 3. 设计

### 3.1 生成管道

- 导出：`backend/scripts/export_openapi.py` —— `from app.main import app; app.openapi()`
  离线导出到 `backend/openapi.json`（**提交进仓库**，作为可 diff 的契约快照；
  `SKIP_DB=1` 之类的免 DB 启动开关按现有 `app.main` 的能力确定）。
- 生成：`frontend` 加 dev 依赖 `openapi-typescript`，脚本
  `npm run gen:api` → `frontend/types/api.generated.d.ts`（提交进仓库；文件头写
  "GENERATED — do not edit"）。
- 门禁：`ci.yml` 的 backend job 加 `Export OpenAPI & diff`（导出后
  `git diff --exit-code backend/openapi.json`），frontend job 加
  `Generate API types & diff`（生成后 `git diff --exit-code frontend/types/api.generated.d.ts`）。
  两步都是**阻塞**的：改了 Pydantic 没重新生成 = 红。
- 重复 `operationId` 先修（4 处 legacy 重定向路由），否则生成器会告警且 key 撞名。

### 3.2 前端接入方式

- `frontend/types/api.ts`（手写、很薄）：从生成物**重导出**带业务名的别名，
  `export type ProjectFile = components['schemas']['ProjectFileResponse']`。业务代码
  只 import 这一层，不直接碰 `api.generated.d.ts`，这样后端改 schema 名只动一处。
- `apiFetch<T>` 系列的泛型参数改为引用生成类型；对信封接口用
  `Envelope<T> = { success: boolean; data: T }` 这一个前端别名（后端 3.3 收口后由
  生成物替代）。
- `frontend/types.ts` 逐步只剩**前端自有**类型（ViewState、SidebarMode、UI 状态、
  草稿态）；每迁走一个 API interface 就删掉手写版，不允许两份并存。

### 3.3 后端补 `response_model`（棘轮）

- 通用信封：`backend/app/schemas/envelope.py` 定义
  `class Envelope(BaseModel, Generic[T]): success: bool = True; data: T`，
  路由写 `response_model=Envelope[ProjectFileResponse]`，返回体不变。
- 棘轮门禁：`backend/tests/test_openapi_untyped_ratchet.py` 读离线 OpenAPI，统计
  200 响应无 schema 的操作数，与 `backend/tests/snapshots/openapi_untyped_count.txt`
  比：**只许减少**；减少时要同步改快照（与 ORM 索引棘轮同一做法）。
- **P9（2026-09-24）清零后升级为零容忍**：快照文件删除，测试改名
  `backend/tests/test_openapi_all_responses_typed.py`，任何未类型化的 JSON 成功响应
  即失败；`export_openapi.py --write-ratchet` 随之移除。
- 分批按路由域推进，每批一个 PR：projects / resources / canvases / ai / admin …
  优先做前端真正消费、且 `types.ts` 有手写副本的那些。

### 3.4 清零 62 条与翻门

- 3.2 迁移会自然消掉「声明漂移」一类；剩余的 `unknown` 未收窄、测试 mock 形状、
  死代码（`awemeType.ts` 重复键、`constants.ts` 的 `MOCK_*` 与 `Video`）单独一个 PR 清。
- `tsc` 为 0 后，`ci.yml` 把 `TypeScript check` 的 `continue-on-error` 去掉，并修正
  `Build (includes TypeScript check)` 这句失实的步骤名。

## 4. 分期

| 期 | 内容 | 门禁产出 |
|---|---|---|
| P0 | 修 4 个重复 operationId；导出脚本 + `openapi.json`；`openapi-typescript` + `api.generated.d.ts`；两条 diff 门禁；`types/api.ts` 薄层 | 生成物过期即红 |
| P1 | 前端把已有 schema 的 32 个手写 interface（18 精确 + 14 后缀匹配）换成生成类型并删手写版；顺手清 `constants.ts` 死物与 `awemeType.ts` 重复键 | `types.ts` 减少 ≥32 个 interface |
| P2 | 后端 `Envelope[T]` + 棘轮测试 + 首批路由域补 `response_model`（projects、resources、generated-media） | 棘轮快照落地，未类型化数只降不升 |
| P3…Pn | 逐域补 `response_model`，前端同步换类型 | 每批快照下降 |
| P-final | 清零 `tsc`，`TypeScript check` 改阻塞 | typecheck 成为必需步骤 |

P0+P1 一个 PR，P2 一个 PR，之后每域一个。

## 5. 风险与裁定

- **生成类型会把 Snowflake BIGINT 暴露为 `number`**（beats / commits 等原样返回 ORM
  dict 的 router；`scenes`/`shots` 自 #1809 起已在边界转成 string，照实声明为 `str`）。这是真实 wire 形状，**要保留**；前端已有 `bigIntSafeFetch` 处理精度。
  别在生成后手工改成 `string`。
- **`dict` 响应补 `response_model` 可能改变序列化**（Pydantic 会丢掉 model 未声明
  的字段）。每个路由补类型时必须对照真实响应（`api_request_logs` 或本地跑一次）
  确认字段全覆盖；这正是把隐式契约变显式的过程，掉字段 = 之前就没人声明它。
- **生成物提交进仓库**而不是构建时生成：CI 才能 diff，reviewer 才能在 PR 里看到
  契约变化；代价是每次改 schema 要跑一次 `gen:api`（脚本一条命令）。
- 离线导出要能不连 DB 启动 `app`；若 `app.main` 的 lifespan 强依赖 DB，导出脚本
  只构造 FastAPI 实例 + 路由注册，不进 lifespan。
