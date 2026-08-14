# 探针按类型分派：不会验的类型要说"没验"，不是"失败" — 设计（2026-08-14）

> 承接 `2026-08-14-model-health-surfacing-design.md` §4 backlog 第 1 条（前置 #1838/#1844 已上线）。
> 用户把优先级交给我判断，我选这条：它仍在真实造成伤害。

## 0. 缺陷（生产实证，2026-08-14）

`mediahub_model_health.py:108-160` 只对 `asr` / `embedding` 分派专用协议，
**其余一切**（含 `image` / `video` / `tts`）都 POST 到 `{base_url}/chat/completions`。
这几类**永远探不通**，与模型能不能用无关：

| type | 启用数 | 红灯数 | 实际 detail |
|---|---|---|---|
| image | 2 | **2** | `UnsupportedProtocol: URL missing 'ht...'`（CLI 模型 base_url 为空）/ `HTTP 404 InvalidEndpointOrModel`（文生图打 chat 端点） |
| video | 1 | **1** | 同上 |
| llm | 5 | 1 | 真红（doubao-pro 限流） |
| embedding / asr | 3 | 0 | — |

**当前 4 个红灯里 3 个是构造性假红。** #1838 只在**面向用户的前端**加了
`PROBED_TYPES` 过滤（该 spec 明写"缓解不是修复"），所以：

- **admin 页面仍然常亮 3 个假红灯** —— 运维视角的信号仍然是脏的；
- 每小时白打 3 次注定失败的请求（72 次/天）+ 对应的 WARNING 日志；
- 前端那层类型过滤是**兜底而非治本**，且它有明确约束"在探针真会说那门协议之前不许放宽"。

## 1. 方案：不会验的类型，显式记成"未验证"

**不做**"给 image/video 也写一个真探针"：文生图/视频的真实调用**要花钱、要生成产物**，
每小时跑一次是不可接受的；CLI 类模型（`jimeng-cli-*`）`base_url` 为空，
根本不存在 HTTP 端点可探。

**做**：让探针诚实声明能力边界——**能验的验，不能验的说"没验"，而不是判"失败"**。
这与仓库既有纪律「探针必须可证伪」同源：一个探不了某协议的探针，
应当回答"我验不了"，而不是给出一个必然为假的"失败"。

### 状态语义

`last_test_status` 现有两值 `ok` / `fail`（DB CHECK 约束在 `models/ai.py`）。
新增第三值 **`not_probed`**：

- 含义："本探针不具备验证该类型的能力"，**不是**故障，**不是**未知；
- `last_test_detail` 写明原因（如 `no protocol probe for type=image`）——
  这句话不含上游 host / base_url / 凭据，可安全展示；
- `last_test_code` 保持 NULL（它是**失败**原因的枚举，`not_probed` 不是失败）。

### 判据

`PROBEABLE_TYPES = {"llm", "embedding", "asr"}` —— 与 #1838 前端 `PROBED_TYPES`
**同一份语义**，但**权威侧移到后端**（前端不该猜后端能验什么）。

## 2. 改动清单

### F1 — 探针提前返回 not_probed

`backend/app/services/ai/mediahub_model_health.py`：
- 新增 `PROBEABLE_TYPES` 常量 + 模块级注释（说明为什么不给 image/video 写真探针：
  花钱、生成产物、CLI 模型无端点）。
- `probe_mediahub_model` 在 try 之前判断：`typ not in PROBEABLE_TYPES` →
  直接返回 `{"ok": False, "not_probed": True, "detail": f"no protocol probe for type={typ}", "error": None, "code": None, "dims": None}`。
  ⚠️ **`ok` 必须是 False**（它没成功），靠新键 `not_probed` 区分"失败"与"没验"，
  避免任何调用方把它当成功计数。

### F2 — 落库与统计

- 迁移：放宽 `last_test_status` 的 CHECK 到 `('ok','fail','not_probed')`，
  并把现存 image/video/tts 的 `fail` 行**一次性改写**为 `not_probed` + 对应 detail
  （否则要等下一轮探针才自愈，而 admin 上的假红会继续误导）。
  ⚠️ 迁移号 push 前 `git fetch` 复核（当前最大 427；本仓库撞过两次号）。
- `models/ai.py` 的 CHECK 同步。
- `workflows/scheduled_health.py`：`not_probed` **不计入 failed**，
  汇总日志区分三档（`ok / failed / not_probed`）；`not_probed` 不落 WARNING
  （它不是故障，每小时刷日志正是要消灭的噪音）。
- `repositories/mediahub_model_repository.py::record_test_result`：接受新状态值
  （若有硬编码校验则同步）。

### F3 — 前端与 admin

- **admin**（`admin/src/pages/ai/index.tsx`）：`not_probed` 渲染为中性徽标
  （非红非绿，如"not probed"），**不再计入失败**。这是本次的主要收益面。
- **用户前端**：`frontend/utils/modelHealth.ts` 的 `PROBED_TYPES` 过滤
  **保留不动**（防御性；后端已不再产出这些类型的 fail，过滤成为冗余但无害）。
  ⚠️ 不在本期删除它——删除属于"放宽"，而 spec §4 立了"在探针真会说那门协议之前
  不许放宽"的约束；等真按类型探针落地再撤。

## 3. 范围外

- 不给 image/video/tts 写真实探针（理由见 §1）。
- 不改分类器 `classify_probe_failure`、不改超时、不改 cron（#1838/#1844 已定）。
- 不动 asr 归 `other` 的现状（另一条 backlog）。
- 不删前端 `PROBED_TYPES`（见 F3）。

## 4. 验证

- 后端：`probe_mediahub_model` 对 `type=image/video/tts` **不发任何 HTTP 请求**
  就返回 `not_probed`（用会炸的 fake transport 钉死"真的没发请求"）；
  对 llm/embedding/asr 行为不变（既有测试保持绿）。
- scheduled step：`not_probed` 不计 failed、不落 WARNING。
- 迁移：幂等；CHECK 接受三值、拒绝第四值；回填后 image/video 行不再是 fail。
- admin：`not_probed` 显中性、不计失败。
- **生产验收（可证伪）**：部署 + 迁移 + 下一轮整点探针后
  ```sql
  SELECT type, last_test_status, count(*) FROM mediahub_models
  WHERE is_enabled GROUP BY 1,2 ORDER BY 1;
  ```
  期望：**image/video 行全部 `not_probed`，零 `fail`**；llm 的 `doubao-seed-2-0-pro`
  仍应是 `fail`+`rate_limit`（正向对照，证明没把真红灯一起吞掉）。
  另查 `application_logs` 近 1 小时无 image/video 的探针 WARNING。
