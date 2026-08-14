# 模型自检：脱敏的失败原因分类（reason code） — 设计（2026-08-14）

> 承接 `2026-08-14-model-health-surfacing-design.md` §4 backlog 第 2 条（该 PR 已上线，#1838）。
> 用户已批准开工。

## 0. 为什么要做（当天数据实证）

#1838 上线后，前端能看到红灯了，但只知道"失败了"，不知道"为什么"。
2026-08-14 05:00 那轮真实探针结果说明这个差别有多大：

| 模型 | 原因 | 用户该做什么 |
|---|---|---|
| `nous-qwen3-llm` | `ReadTimeout: <no message>` | **多半不用管**（本机引擎在加载/已停） |
| `mediahub-doubao-seed-2-0-pro` | `HTTP 429: SetLimitExceeded` | **要去处理**（配额/换模型） |
| `jimeng-cli-image` / `-seedance` | `UnsupportedProtocol: URL missing 'ht...'` | 探针打错端点的**假红**（已被前端类型过滤挡掉） |

同样是红灯，动作完全相反。而原始 `last_test_detail` **不能直接透给用户**——它常带上游
host、私有 `base_url`、上游内部模型 ID（上表第三行就含 URL 片段）。
`GET /api/v1/ai/mediahub-models` 是面向全体用户的接口，#1838 特意只放了 status/tested_at。

## 1. 方案：服务端算封闭枚举，前端只拿枚举

**枚举（封闭，天生不含自由文本）**：
`timeout` / `unreachable` / `auth` / `rate_limit` / `model_not_found` /
`upstream_error` / `bad_response` / `other`

**判据（探针里全部现成，不猜、不做子串匹配）**：

| 信号 | code |
|---|---|
| `httpx.TimeoutException` 子类（Read/Connect/Write/Pool） | `timeout` |
| `httpx.ConnectError` / `UnsupportedProtocol` / 其它 `TransportError` | `unreachable` |
| HTTP 401 / 403 | `auth` |
| HTTP 429 | `rate_limit` |
| HTTP 404 | `model_not_found` |
| 其它 HTTP >= 400 | `upstream_error` |
| HTTP 200 但响应体缺 `choices` / 缺 embedding 向量 | `bad_response` |
| 其它异常 | `other` |

⚠️ **asr 分支例外（终审明确点名）**：它走 `AIProviderFactory.test_connection`，
**只有自由文本，没有 status_code、没有异常对象**。本期**一律归 `other`**，
**绝不靠子串匹配去猜**。让 `ai_provider.py` 返回结构化 code 是更大的一步，记 backlog。

## 2. 改动清单

### F1 — 探针产出 code

`backend/app/services/ai/mediahub_model_health.py`：
- 新增纯函数 `classify_probe_failure(*, status_code: int | None, exc: BaseException | None,
  bad_response: bool = False) -> str`，按上表映射；**无副作用、可单测**。
- 三个失败出口（chat 非 200 / embedding 非 200 / 顶层 `except`）与两个 `bad_response`
  出口（缺 `choices` / 缺向量）都带上 `code`；返回 dict 增加 `"code"` 键。
- asr 分支显式 `code="other"`（带注释说明为什么不猜）。

### F2 — 落库

- 迁移：`mediahub_models` 增列 `last_test_code TEXT`（可空；旧行为 NULL = 未知）。
  ⚠️ **迁移号 push 前必须 `git fetch` 复核**（当前最大 426，本分支预定 427；
  本仓库撞过两次号）。
- `repositories/mediahub_model_repository.py::record_test_result` 增参 `code`（可选，
  默认 None），写入该列；`_PUBLIC_COLS` 增补 `last_test_code`
  （⚠️ 仍然**严禁**增补 `api_key`/`app_id`/`base_url` —— #1838 已有测试钉死，保持）。
- 两个调用点同步传 code：`api/admin/mediahub_model_router.py`（手动 Test）、
  `workflows/scheduled_health.py`（定时探针）。

### F3 — 前端显示

- `frontend/types.ts` 的模型类型补 `last_test_code?: string | null`。
- `frontend/utils/modelHealth.ts`：`buildModelHealth` 的值增加 `code`；
  新增 `healthReasonKey(code)` → i18n key（未知/缺失 code 回退到通用文案）。
- 显示位置沿用 #1838 的两处（**不新增位置**）：模型下拉的警示标记、
  agent 属性页的内联提示——文案从"上次自检失败"变成
  "上次自检失败：**请求超时** · 20 分钟前"。
- i18n：en/zh 两份各加 8 个 key（`aiSettings.healthReason.*`）。英文为准，中文翻译。
- **仍然不禁选**（#1838 立的规矩，不改）。

## 3. 范围外

- 不改探针的判定逻辑、不改超时值、不改 cron（#1838 已定）。
- 不让 `ai_provider.py` 返回结构化 code（asr 归 `other`，记 backlog）。
- 不透出 `last_test_detail` 原文到公开接口（本设计存在的理由）。
- 不动 admin 端（那里本来就能看原文 detail）。

## 4. 验证

- 后端：`classify_probe_failure` 的**表驱动单测**逐条覆盖上表（含 asr → `other`）；
  钉死"公开列仍不含凭据"（复用 #1838 的既有测试，确认新列不破坏它）；
  记录路径把 code 一路写进库的测试。
- 前端：`code=timeout` 显示"请求超时"文案；`code` 缺失/未知回退通用文案；红灯仍可选。
- 迁移：`run-migration.yml` 绿。
- **生产验收（可证伪）**：部署 + 下一轮整点探针后，
  `SELECT name, last_test_status, last_test_code FROM mediahub_models WHERE last_test_status='fail'`
  ——每一行 `last_test_code` 都必须非空，且 `doubao-seed-2-0-pro` 应为 `rate_limit`、
  `nous-qwen3-llm`（若仍超时）应为 `timeout`、两个 `jimeng-cli-*` 应为 `unreachable`。
  这三条是当天已知真值，构成正向对照。
