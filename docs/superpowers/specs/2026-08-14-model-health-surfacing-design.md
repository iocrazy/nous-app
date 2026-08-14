# 模型自检：失败可诊断 + 健康状态透出前端 — 设计（2026-08-14）

> 用户报障链的延伸：Analyze 打不通 → 查出模型目录里的自检"有灯但没人看、且灯本身会说谎"。
> 用户已批准范围（下拉 + agent 属性页提示；自检频率 6h → 1h；**不禁选模型**）。

## 0. 缺陷（生产实证）

自检本身是**真探针**（`mediahub_model_health.py:89-110` 真发 `POST /chat/completions` 并检查
`choices`，不是 ping `/health`），doubao-pro 那条如实记下了 `HTTP 429: SetLimitExceeded`。
但它有三个洞：

**洞一：失败原因会变成空字符串，事后无法回溯。**
`mediahub_model_health.py:114` 的兜底是 `error: str(e)[:200]`，而 httpx 的
`ReadTimeout` / `ConnectTimeout` / `ReadError` 的 `str()` **实测全是 `''`**（在
`nous-backend` 容器内验证）。经 `mediahub_model_router.py:219` 与
`scheduled_health.py:183` 的 `detail = result.get("detail") or (result.get("error") or "")`
落库，就成了「红灯 + 原因栏空白」。

生产实例：`mediahub-deepseek-v4-flash` 在 2026-08-14 00:00:32 被判 fail、detail 为空，
而同一轮里同 base_url / 同凭据的 `deepseek-v4-pro` 在 20 秒前刚探成功；随后端到端实测
该模型**完全可用**（走完整应用链路 HTTP 200）。**即：这是一次误判，且原因已丢失，
无法区分「模型真坏」与「探针误判」。** 20s 超时是首要嫌疑（deepseek 冷启动、
本机引擎加载模型都可能超过）。

**洞二：定时探针只记汇总，逐模型原因不落日志。**
`scheduled_health.py:195-199` 只写一句 `5/11 platform models unreachable`，
所以 detail 为空时连日志都救不回来。

**洞三（用户真正在意的）：自检结果不参与任何用户可见的反馈。**
`record_test_result` 只写 `last_test_status`；面向用户的模型列表
`GET /api/v1/ai/mediahub-models` → `repo.list_enabled()` 的 `_PUBLIC_COLS`
（`mediahub_model_repository.py:73-81`）**只有 id/name/display_name/type/pricing/sort_order，
不含任何健康字段**。前端不是"没显示"，是**拿不到**。
后果：用户给 agent 选到一个红灯模型，系统不提示、不降级，直到聊天静默失败——
本次 Analyze 事件正是如此。

## 1. 改动清单

### F1 — 失败必须可诊断

1. `backend/app/services/ai/mediahub_model_health.py:114`
   兜底改为 `f"{type(e).__name__}: {str(e) or '<no message>'}"`，**永不为空**
   （超时会明确落成 `ReadTimeout: <no message>`）。
2. 同文件的 httpx 超时 `20.0` → `60.0`（三处：chat / embeddings / multimodal embeddings；
   以实际存在的调用点为准）。理由：误判成本（红灯 + 用户困惑 + 无法回溯）远高于多等 40 秒；
   探针本身是 6→1 小时一轮的轻 ping。
3. `backend/app/workflows/scheduled_health.py:180-185`
   逐模型失败落一条 WARNING（模型 name + status + 原因），不再只报汇总。

### F2 — 健康状态透出到前端

1. **后端**：`_PUBLIC_COLS`（`mediahub_model_repository.py:73-81`）增补
   `last_test_status` + `last_tested_at`。⚠️ 不得增补 `api_key` / `app_id` / `base_url`
   （该列表是**面向普通用户**的公开接口，这是它当前不返回凭据的原因，见函数 docstring）。
2. **前端**：
   - `frontend/services/aiService.ts:477` 的返回类型补这两个字段；
   - **模型下拉**：`last_test_status='fail'` 的选项加警示标记（语义色 `warn`/`danger` token，
     lucide 图标，**不禁用该选项**——用户明确要求"提示而非停用"）；
   - **agent 属性页**：当前选中模型为红灯时，模型框下方内联一条提示，含
     **失败原因**与**检查时间**（"Last health check failed at HH:MM — chats may fail"）；
   - 两处都显示相对时间（"checked 20m ago"）——1 小时一轮，陈旧的绿灯不应被当作当前正常。

### F3 — 自检频率 6h → 1h

`scheduled_health.py:189` 的 `@DBOS.scheduled("0 */6 * * *")` → `"0 * * * *"`。
探针是每模型一次 `max_tokens=8` 的 ping，成本可忽略。

## 2. 范围外

- **不禁选、不自动停用**红灯模型（用户明确要求）。
- 不改探针的判定逻辑本身（真发 chat completion + 查 `choices` 是对的，保留）。
- 不做"失败后加速重探"（用户在两个方案间选了纯提频；加速重探记 backlog）。
- 不动 admin 端已有的展示（admin 已能看到 status/detail）。

## 3. 验证

- 后端：新增测试钉住 ①超时异常落非空原因（构造 `httpx.ReadTimeout` 断言 detail 非空且含类型名）；
  ②`_PUBLIC_COLS` 含健康两列且**不含**凭据列（防未来误加）；③scheduled step 失败时逐模型 WARNING。
- 前端：模型下拉红灯标记可见；agent 属性页在选中模型为 fail 时显示提示（含时间）；
  绿灯时不显示。
- 既有测试保持绿；全量 collect 无 import 错。
- 生产验收：部署后查 `mediahub_models.last_test_detail`——下一轮探针后，
  **任何 fail 行的 detail 都不得为空**（这是本次修复唯一可证伪的信号）。
