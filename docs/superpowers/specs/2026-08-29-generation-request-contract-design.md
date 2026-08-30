# 出图/出视频生成请求契约 — 设计

**日期**：2026-08-29 · **状态**：待用户评审 · **范围**：`canvas_generation` workflow、provider 协议层、codex/dreamina daemon、画布 footer 旋钮 UI

## 1. 为什么要做（证据，不是感觉）

用户报「点了 16:9 出来不是 16:9」。取证下来这不是一个 provider 的毛病，是**没有契约**：前端有 8 个旋钮，后端有 3 条分支（服务器 / codex-daemon / dreamina-daemon）各自手抄一份 dict 往 5 个 provider 送，每条路各丢各的。

### 1.1 地面真值（量实际像素，不看 `ok:true`）

| 时间 | 路径 | 请求比例 | 实得 | 结论 |
|---|---|---|---|---|
| 08-19 ×3 | codex 服务器 | 16:9 (1.78) | 0.892 / 0.837 / 0.914 | 全错 |
| 08-19 ×1 | codex 服务器 | 1:1 | 0.80 | 错 |
| 08-29 | codex-local daemon | 16:9 | 1199×1312 = 0.914 | 错 |

5/5。且 `aspect_honored` 在库里 **0 行**——校验只进日志，`provider_protocols/codex.py` 把 `result.raw` 丢了（`jimeng.py` 是 `**result.raw`）。

### 1.2 旋钮 × 路径矩阵（2026-08-29 逐文件核实）

✅ 到达并生效 · ⚠️ 到达但 provider 无此能力，UI 照样显示 · ❌ 半路丢，UI 显示、后端不发

| 旋钮 | codex 服务器 | codex-local | jimeng-cli | jimeng-local | doubao/ark |
|---|---|---|---|---|---|
| ratio | ✅（上游不采纳 `--size`，靠 prompt 短语缓解） | ❌ `canvas_generation.py:215` 发 `params.size`，前端只发 `ratio` | ✅ `--ratio` 原生 | ✅ | ⚠️ `_ASPECT_TO_SIZE` 只 5/8 档，`2:3/3:2/21:9` 静默落 1024² |
| 参考图 | ✅ | ✅ | ⚠️ **CLI 图片链路无 i2i**（`build_image_args` 纯 text2image，`--image` 只在视频命令里）；`jimeng.py` 不转发是结果不是原因 | ⚠️ 同左 | ⚠️ `/images/generations` 纯 t2i，"accepted but not sent" |
| model | ✅ | ❌ 发 `params.actual_model`，前端从不设 → 空 → CLI 默认 | ✅ | ✅ | ✅ |
| quality | ✅ | ❌ payload 无此字段 | ⚠️ | ⚠️ | ⚠️ |
| resolution | ⚠️ "尺寸由模型定" | ❌ | ✅ | ✅ | ⚠️ |
| video_mode | — | — | ✅ 首尾帧/多模态 | ❌ `build_video_args` 无此参数 | — |
| negative prompt | ❌ | ❌ | ❌ | ❌ | ❌（三个底层 provider 与 daemon 均无 `negative` 一词；UI 有此框） |
| 产出校验 / 归因 | 只进日志 | **全丢**：入库为 `origin_kind=canvas_upload`，prompt/model/ratio 全空 | 无 | 无 | 无 |

5 个 ❌ 是真丢，9 个 ⚠️ 是假开关（写计划时核实：jimeng 图片 i2i 是 CLI 没有这个能力，归 ⚠️，不是转发漏了）。模型目录（`mediahub_models`）里真实启用的出图 provider 就是这 5 个；nous-engine 尚不存在。

### 1.3 为什么不逐个打补丁

每个 ❌ 一个 patch、每个 ⚠️ 一个 `if`，共 13 处；下一个 provider（nous-engine）接入时同样的洞再开一遍。契约层是唯一让"接一个 provider = 实现一个方法 + 声明一份能力"成立的做法。

## 2. 目标 / 非目标

**目标**
1. 前端到 provider 只有一种请求形状；三条分支从同一对象取值，不再手抄。
2. 每个 provider 声明能力；UI 只展示它做得到的旋钮；做不到的不静默吞。
3. 每次生成落库「要求了什么 / 实际出了什么 / 是否相符」，服务器与 daemon 两个入库口一致。
4. nous-engine 接入零特判。

**非目标**
- 不解决 codex 上游不采纳 `--size` 本身（那是模型侧；契约做到"发对了 + 量出来 + 记下来"为止）。
- 不新增旋钮（seed、多参考角色等仍在 IC 差距清单）。
- 不动 `count`（已经是"派 N 个任务"，各路一致）。

## 3. 契约三件

### 3.1 `GenerationRequest` — 唯一的请求形状

后端 `app/services/generation/request.py`（新）：

```python
@dataclass(frozen=True)
class GenerationRequest:
    kind: Literal["image", "video"]
    prompt: str
    model: str                      # 目录 row 的 name；由 resolver 换成 actual_model
    ratio: str | None               # 具体值如 "16:9"；'auto' 已在前端 dispatch 时解析（PR #2066）
    quality: str | None
    resolution: str | None
    refs: tuple[str, ...]           # 绝对 URL，上限 9（IC）
    negative: str | None
    video_mode: Literal["frames", "multimodal"] | None
    duration: int | None
```

- `canvas_generation.py` 在入口把 `params` **一次性**解析成它（`GenerationRequest.from_params(kind, prompt, params, source_urls)`），之后三条分支只读这个对象。`size`、`actual_model` 这类分支私有键从此不存在。
- daemon payload 就是它的序列化 + `engine` 字段；dreamina 分支的 `build_image_args/build_video_args` 从它取参（含 `video_mode`）。

### 3.2 `ProviderCapabilities` — provider 自报能力

在 `provider_protocols/base.py` 上加：

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    ratios: frozenset[str]          # 能真正生效的档位
    quality: bool
    resolution: bool
    max_refs: int                   # 0 = 纯 t2i
    negative: bool
    video_modes: frozenset[str]
    honours_ratio: Literal["native", "prompt_hint", "none"]
```

每个 protocol 声明一份（daemon 版按 daemon 能力声明）。首批真实值（按 1.2 核实）：

| provider | ratios | quality | resolution | max_refs | negative | video_modes | honours_ratio |
|---|---|---|---|---|---|---|---|
| codex / codex-local | 8 档 | ✅ / ✅（daemon 补发） | ✗ | 9 | ✗ | — | prompt_hint |
| jimeng-cli / jimeng-local | 8 档 | ✗ | ✅ | **0**（图片 CLI 无 i2i；视频有 first/last/multi） | ✗ | frames, multimodal（daemon 补 `video_mode`） | native |
| doubao/ark | **5 档** | ✗ | ✗ | **0** | ✗ | — | native |

用在两处：
- **UI**：`GET /api/v1/models/capabilities` 随模型目录一起下发；`GenFooterControls` 按当前 model 的能力渲染——比例网格只列 `ratios`，`quality/resolution` 不支持则**隐藏**（不是 disabled 灰掉——灰掉仍是假承诺）；`negative` 在所有 provider 都为 ✗ 时整块隐藏。
- **dispatch**：`GenerationRequest.reconcile(caps)` 返回 `(effective_request, dropped: list[str])`。`dropped` 非空 → 写进 `task_tracking.metadata.dropped_knobs`，并在 prompt 节点状态徽章旁显示「已忽略：quality」。**永不静默**。

### 3.3 `GenerationOutcome` — 统一的产出记录

入库只有一个函数（`register_generated_media` 已是咽喉点，服务器 `persist_canvas_generation_step` 与 daemon `codex_daemon_router` 上传都经它）。在它里面：

- **量**：图片用 PIL 读 `measured_size`；视频用 ffprobe 读 `measured_size + duration`（ffprobe 已在镜像里，`safe_popen_kwargs` 走法见 CLAUDE.md）。
- **比**：`requested_ratio` vs 实际，容差 6%（沿用 `codex_cli._ASPECT_TOLERANCE`），写 `aspect_honored`。
- **记**：`generated_media.params` 固定四键 `requested / effective / measured / honored`（`effective` 即 reconcile 后真正发出的），`origin_kind` 生成图一律 `canvas_run`。
- **daemon 归因**：`dispatch_to_daemon` 把 `GenerationRequest` 与 `canvas_id/node_id` 存进 job（现在 claim 里只有 `job_id`），上传时反查带上。`produced_by: codex-daemon` 保留为 params 内的附注，不再是唯一信息。
- `codex.py` 改 `metadata={"mime": ..., **(result.raw or {})}`——与 jimeng 对齐，一行。

不符合时的处置不变：**记录，不拦截**（图已花钱，形状不对但能用 > 没有）。但每张不符的图现在都可查、可统计。

## 4. 数据流（改后）

```
GenFooterControls(按 caps 渲染) → params → generationRunner(auto→具体 ratio)
  → POST /canvases/{id}/generations
  → canvas_generation: req = GenerationRequest.from_params(...)
       caps = protocol.capabilities
       req, dropped = req.reconcile(caps)          # dropped → task metadata + UI 徽章
       ├─ 服务器: protocol.generate(req)
       ├─ codex-local: dispatch_to_daemon(payload=req.to_daemon("codex"))
       └─ dreamina-local: dispatch_to_daemon(payload=req.to_daemon("dreamina"))
  → register_generated_media(..., request=req)   # 量 / 比 / 记，两入口同一函数
```

## 5. 兼容与版本

- **daemon 协议**：codex payload 从 `{prompt,size,model,ref_urls}` 变为 `{prompt,ratio,quality,model,ref_urls}`；dreamina 加 `video_mode`。daemon 客户端 `tools/codex-daemon/index.mjs` 同步改（ratio→`--size` 换算 + 画幅短语并入 prompt，复用 `codex_cli` 的两张表——抽到 `app/services/generation/aspect.py` 让 daemon 构建时能拿到同一份 JSON）。
- **版本闸门**：沿用 `MIN_TEXT_DAEMON_VERSION`（`app/services/ai/adapters/codex_daemon.py:71`）的机制加 `MIN_IMAGE_DAEMON_VERSION`；旧 daemon 收到新 payload 前，服务器**同时发**旧键一版（`size` 由 ratio 换算填上——这本身就把 ❌ 修掉一半），下一版删旧键。
- **既有数据**：不回填；`aspect_honored` 只对新记录有意义，查询时按 `params ? 'honored'` 过滤。
- **UI**：capabilities 未下发（老后端）时按"全支持"渲染 = 今天的行为，不会变差。

## 6. 验收（可证伪，缺一不算完）

1. **矩阵归零**：1.2 的 6 个 ❌ 各有一条单测，用 `GenerationRequest.from_params` + 假 provider 断言参数到达；`jimeng.py` 参考图转发用真 CLI argv 断言。
2. **真栈逐 provider 出图**：5 个 provider 各出一张 16:9，**量像素**，`aspect_honored` 在库里可查；codex 两条允许不符但必须**有记录**，其余三条必须相符。
3. **假开关归零**：切到 ark，UI 不出现 21:9 / quality / resolution；切到 codex，不出现 4K。用 e2e 断言可见性，不用 count。
4. **daemon 归因**：daemon 出的图 `origin_kind=canvas_run`、prompt/model/ratio 非空——用 SQL 断言，对照今天那 3 行 `canvas_upload` 空归因作为反例。
5. **dropped 可见**：给 ark 硬塞 quality，节点徽章出现「已忽略：quality」，`task_tracking.metadata.dropped_knobs=['quality']`。
6. **旧 daemon 不崩**：0.3.0 daemon 对新服务器仍能出图（拿到换算后的 `size`）。

## 7. 分期

| PR | 内容 | 大小 |
|---|---|---|
| **P1 契约 + 后端三分支** | `GenerationRequest` / `Capabilities` / `reconcile`；`canvas_generation` 三分支改读它；修 ❌ 中的 4 个（codex-local ratio/model/quality、dreamina video_mode）+ jimeng 图片 `max_refs=0` 诚实声明；`codex.py` raw 透传 | 中 |
| **P2 产出记录** | `register_generated_media` 量/比/记；daemon job 归因反查；两入口对齐 | 中 |
| **P3 daemon 客户端** | index.mjs 新 payload + 画幅短语 + 版本闸门；服务器双发一版 | 小，但要用户升级 daemon |
| **P4 UI 按能力渲染** | capabilities 端点 + `GenFooterControls` 隐藏逻辑 + 负向提示词框下架 + dropped 徽章 | 中 |

P1→P2 顺序硬依赖；P3、P4 可并行。P1 合并后 codex-local 的 16:9 就应该对了（通过双发的 `size`）——那是第一个可验证的里程碑。

## 8. 决策记录（有备选，写明为什么这么选）

| 决策 | 选了 | 备选 | 理由 |
|---|---|---|---|
| capabilities 放哪 | **代码里，随 protocol 注册** | `mediahub_models` 表可编辑 | 能力是实现的属性不是配置；放表里会出现"表说支持、代码不支持"第三种漂移 |
| 不支持的旋钮 | **UI 隐藏 + dispatch 记 dropped** | 灰掉；或照发让 provider 忽略 | 灰掉仍是承诺；照发就是今天的 ⚠️ |
| ark 的 2:3/3:2/21:9 | **不展示** | 映射到最近档 | 映射是又一层静默；用户要 21:9 就该知道这家做不了 |
| 负向提示词 | **按 capabilities 隐藏（当前 = 全隐藏）** | 删代码 | 将来 nous-engine 大概率支持，留接口不留假框 |
| 不符时 | **记录不拦截** | 拦截重试 | 图已付费；重试是另一个决策，先有数据 |
| 视频测量 | **P2 含 ffprobe** | 只量图片 | 两入口一个函数，不做一半 |

## 9. 开放问题（评审时定）

1. `dropped_knobs` 的 UI 位置：prompt 节点状态徽章旁，还是 output 槽位角标？我倾向前者（决策发生在 dispatch，跟 RUNNING 徽章同一处）。
2. 版本闸门：旧 daemon 是**拒绝派发并提示升级**，还是**降级双发**？我倾向降级一版再拒绝——P3 落地时用户机器上大概率还是 0.3.0。
3. capabilities 端点是并进现有 `GET /models` 响应，还是独立端点？并进去少一次请求，但改了既有响应形状（`reference-backend-returns-frontend-never-reads` 那族风险）。

---

*同族教训*：`reference-codex-size-not-honored`、`reference-healthcheck-must-be-falsifiable`、`project-codex-ratio-dropped-on-daemon-path`。
