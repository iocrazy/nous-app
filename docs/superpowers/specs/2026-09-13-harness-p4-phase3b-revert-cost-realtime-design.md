# harness 第六轮 · 三期 3b「回退 / 花费 / 实时 / 资源来源」设计

> 前序：3a spec `2026-09-10-harness-p4-phase3a-outputs-lineage-design.md`（登记口、版本链、三处消费面；§5 后实施记录）；3a 收尾分诊 `docs/superpowers/plans/2026-09-13-harness-p4-phase3a-followups.md`（40 条小票三桶，本文吃掉桶 C 里与四个主题同根的那些）。P4 总 spec §6 把第 3 期定为「产出与账」；**用户于 2026-09-13 把 3b 收成四条**：Revert To vN、`cost_cents` 真正填值、实时刷新（`invalidateOutputLineage` 接线到回合完成事件）、资源信息面板来源块反查。检索 / 日报 / 效率账 / 消息反馈 / 制片人视图另立（3c）。
> **状态（2026-09-13）**：草案。技术取舍由作者定（用户明示）；**用户只验画板「Issue Workbench」页「三期 3b · 回退 / 花费 / 实时 / 资源来源（浅色）」**（§5）。

## 0. 前提核对（2026-09-13 两路勘察，master 4be9bcf4；报告在 `.superpowers/sdd/2026-09-13-harness-p4-phase3a-followups/recon-3b-*.md`）

| 假设 | 实况 | 结论 |
|---|---|---|
| 「回到 v1 = 以 v1 内容新建 v3，血缘照记」（3a §2.1）可以直接做 | `run_deliverables.run_id` **NOT NULL**（mig 453:112），`register_deliverable(run_id=None)` 显式 no-op（`registry.py:67`）；`script_shot_ops.run_id` 也 NOT NULL（mig 415:24），人手改分镜**结构上不能写账本**；场次账本 `script_ops.actor` 接受人手 actor（router 传 `auth.user_id`，undo 传 `undo:<run>`） | 回退是**人手发起但要占号**的唯一动作 → mig 466 放宽两张表的 `run_id`，加 `actor_user_id`（§2.1） |
| 版本内容可精确重建 | `diff.py` 按 `created_at`（事务开始时间）把版本映射到账本行，不是外键（:97-109）；解析失败取整本账本 | 回退要写回的是**某一版的内容**，错位比显示错更糟 → mig 466 加 `ledger_ref`，登记时填，diff/revert 优先用它（§2.2） |
| 场次回退要「整体替换」op | 没有 replace-all op；但 `version_service.inverse_between(ledger, from_seq, to_seq)` 生成逆操作批，undo 服务就是这么回退场次的（`run_undo_service.py:190`） | 场次回退 = 逆操作批（与 undo 同机制），不造新 op |
| 分镜回退只改 6 个字段 | `_SHOT_FIELDS` = shot_type/camera_angle/camera_movement/focal_length/lighting/description；`ScriptShotRepository.update` 只写 `_UPDATE_FIELDS`，**从不碰 image_url** | 3b 的分镜回退只回文本 6 字段；分镜图回退不在本期（§6） |
| `generated_media` 有版本链可回 | 恒 v1，重新生成 = 新 ref_id，两代只共享 `node_id`（3a 实施记录） | 媒体类不可回退（400 `kind_not_revertible`）；媒体版本链另立 |
| LLM 花费按 step 可归因 | `cost.by_step` 每步 `{turn, step, cost_cents, prompt, completion}`（`folds/step.py:15-40`）；出生点 `AgentRunner._step_ended`（`agent_runner.py:1152-1200`）；`turn` 在 `_step_started/_step_ended` **硬编码 1** | 文本类 `cost_cents` 在 `step_end` 时回填（§3.1）；`(turn,step)` 键沿用，turn=1 的既有假设写进注释，不在本期修 |
| 媒体 provider 回花费 | Codex 只回 tokens；Ark/jimeng 零字段；`generated_media.cost_cents` 17 个调用点零填写；`mediahub_models.pricing_*` 单位是**积分**，仓内无积分→分换算；`ai_model_prices` 只有每千 token 价 | 媒体花费 = **管理员配置的每次调用价**（`ai_model_prices.per_call_cents`，mig 466），在 `register_generated_media` 咽喉点统一填；无价即 NULL、UI `—`（§3.2） |
| 回合完成有恰一次的前端信号 | 三条信号都不是恰一次：`useIssueProgress` 轮询 + `agent_runs` Realtime nudge（每回合多次）；WS `status{phase:'done'}` 最接近但 best-effort；`issue_messages` Realtime 按行 | 边沿由 `useIssueProgress` 推导（`current_run.id` 从非空变空/变化）+ WS done 双源，按 run id 去重（§4） |
| 右栏产出块会随回合刷新 | `OutputsBlock` effect 键 `[issueId, refreshKey]`，`refreshKey` 只在 pipeline 启动时 bump；`useMentionOutputsTab` 有 `requested` 硬守卫从不重拉；`invalidateOutputLineage` 零调用方 | §4 接线 |
| 画布分镜节点能知道后端变了 | `script_shots`/`script_scenes` 零 Realtime 订阅，无 refetch-on-focus | 3b 只做「页面级信号 + 缓存 TTL + 回到前台重拉」，不开新频道（§6） |
| 资源信息面板能问「谁做的我」 | `resources` 无反向列；链接在 `generated_media.promoted_resource_id`（mig 307，456 partial unique，**无 FK、删资源不清**）；`outputs_router` 有 `todolist` 模块门 | 资源臂挂在 `resources_router`（资源 ACL），内部解析 `promoted_resource_id → generated_media.id → lineage_for`（§2.4） |

### 0.1 对照 deepseek-harness（2026-09-14 补做；侦察报告在 ledger）

它是编码 agent 的 harness，四题里三题没有直接对应物：**只有 token 没有钱**（`token-meter` 折叠事件流，明言不当计费输入）、**没有 undo/revert**（只有 append-only 的 `replace` 遮蔽与 `fork`）、**附件无 producer**（内容寻址，靠事件 `sourceEventSeqs`/`derivedEventSeqs` 双向反查）。能搬的是四条纪律，已折进下文：花费 = 事件流的折叠、不存第二份（§3.1）；回退 = 遮蔽不改写、旧内容永不丢（§2.3）；刷新 = seq 水位高者胜、低于等于即丢（§4）；反查必须物化索引（§2.4）。

## 1. 范围（四条，各一小节；每条都有「不做」）

| # | 做 | 不做（记票） |
|---|---|---|
| R 回退 | `script_shot` / `script_scene` 回到任一旧版：新建 v(latest+1)，内容 = 目标版，血缘记 `reverted_from_version` 与 `actor_user_id`；有未登记人手编辑时先把当前内容登记成一版再回退（回退永不销毁内容） | 媒体回退；章节；「回退整个 run」；分镜图回退 |
| C 花费 | 文本类 = 产出它那一步的 LLM 花费按该步产出数均摊，**读时**从 `step_end` 事件折出（`cost_kind=allocated`，不回写）；媒体类 = 目录每次调用价，登记时填（`exact`）；媒体花费计入 run `spent_cents`（预算钩子随之生效）；三处 UI 显示 | 积分→分换算；provider 实报价；花费历史重算（存量行保持 NULL） |
| L 实时 | 回合结束信号 → 右栏产出块 / @页签 / 血缘缓存 / 来源块 / 回退后自刷新；血缘缓存 TTL + 回前台重拉 | `script_shots` 新 Realtime 频道；画布节点跨页推送 |
| P 资源来源 | `ResourceInfoPanel` 挂来源块，走 `GET /resources/{id}/provenance` | `FileInfoPanel`（项目文件/工作区）——同一块，下一批 |

## 2. 数据与写路径

### 2.1 mig 466（取号时重扫，465 是当前最后一个）

| 表 | 变更 | 为什么 |
|---|---|---|
| `run_deliverables` | `run_id` → NULLable；加 `actor_user_id UUID NULL`、`reverted_from_version INT NULL`、`ledger_ref TEXT NULL`；`CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL)`；`idx_run_deliverables_run` 保留（NULL 不进） | 回退是人手占号；不允许「既无 run 也无人」的行 |
| `script_shot_ops` | `run_id` → NULLable；加 `actor TEXT NULL`（同 `script_ops.actor` 形状，`revert:<user_uuid>`）；`CHECK (run_id IS NOT NULL OR actor IS NOT NULL)` | 分镜回退要有账本行，diff 才能继续重建；undo 按 `run_id` 读，NULL 行天然不入 undo |
| `ai_model_prices` | 加 `per_call_cents NUMERIC(12,4) NULL` | 图片/视频模型按次计价；沿用同一张「按 effective_at 版本化、admin 只追加不改」的表，不造第二张价格表 |

ORM 三处同 PR 镜像（`models/agents.py::RunDeliverables`、`models/script.py::ScriptShotOps`、`models/ai.py::AiModelPrices`）；`LATEST_MIGRATION` → 466。

**登记口签名**（`services/deliverables/registry.py`）：

```python
async def register_deliverable(*, run_id, kind, ref_id, title=None, model=None, cost_cents=None,
                               turn=None, step=None, recorder=None,
                               actor_user_id: str | None = None,          # 3b
                               reverted_from_version: int | None = None,  # 3b
                               ledger_ref: str | None = None)             # 3b
```
- `run_id` 空 **且** `actor_user_id` 空 → 仍 no-op（3a 不变量：普通人手改动不占号）。
- `actor_user_id` 有值 → 登记，不落 transcript 事件（没有 run 可挂），返回行。
- `reverted_from_version` 只允许与 `actor_user_id` 同时出现（`ValueError`）。

### 2.2 `ledger_ref`（精确映射，C6 真修）

| kind | 值 | 谁填 |
|---|---|---|
| `script_shot` | `script_shot_ops.id`（登记那次写入的账本行） | `scoped_script_gateway` 四处写点（RETURNING id 后传给 `_register_write`）；回退路径 |
| `script_scene` | `script_ops.op_seq` 水位（该次写入后的最大 op_seq） | 网关 `apply_element_edit`（`apply_element_ops` 已返回新 `content_version`；水位 = 返回的 `op_seq`）；回退路径 |
| `generated_media` / `script_chapter` | NULL | — |

`diff.py` 与回退：有 `ledger_ref` 用它（shot：`id <= ledger_ref` 的前缀折叠；scene：`replay_to(ledger, watermark=ledger_ref)`）；无则退回 `created_at`（存量行）。`OutputVersion` wire 不暴露 `ledger_ref`。

### 2.3 回退写路径

`POST /api/v1/outputs/{kind}/{ref_id}/revert`，body `{to_version: int, expected_latest: int}`，响应 `{version: OutputVersion}`（新版）。

| 步 | 做什么 | 失败 |
|---|---|---|
| 0 | kind ∈ {script_shot, script_scene} 否则 400 `kind_not_revertible`；`_visible_chain`（同 lineage 可见性）；写权限：分镜走 `script_shots_router` PATCH 同一守卫，场次走 `verify_scene_access` | 404 / 403 |
| 1 | `to_version` 存在且 ≠ latest；`expected_latest == latest` | 404 `version_not_found`；409 `version_conflict`（响应带 `latest_version`） |
| 2 | 重建目标版内容（`diff.py` 的 side 重建，优先 `ledger_ref`）；不可重建 → 409 `content_unavailable`（reason 同 diff 的 `unavailable_reason`） | |
| 3a shot | 一个事务：`SELECT … FOR UPDATE` → `UPDATE script_shots` 六字段 → `INSERT script_shot_ops(run_id=NULL, actor='revert:<uid>', action='update', before_json, after_json)` RETURNING id | |
| 3b scene | `inverse_between(ledger, from_seq=ledger_ref(to_version), to_seq=current_max_seq)` → `apply_element_ops(scene_id, inverse, expected_version=content_version, actor='revert:<uid>')`；`VersionConflict` → 409 `version_conflict` | 逆操作批为空（目标版内容 == 当前）→ 仍登记新版（用户明确要求「以 vN 为准」，血缘要记这一笔） |
| 4 | `register_deliverable(run_id=None, actor_user_id=uid, kind, ref_id, title, reverted_from_version=to_version, ledger_ref=新账本位置)`；此处**不是** best-effort：登记失败 → 整个事务回滚（内容与账本已在同一事务里，登记在事务外则先提交内容再登记会出现「内容回了、版本没记」——所以 3b 把回退的三步放进**同一个 postgres 事务**，登记口用 `session=` 参数加入） | 500 `revert_failed`，内容未改 |
| 5 | 返回新版 `OutputVersion`（含 `actor_user_id`、`reverted_from_version`、`run_id: null`） | |

并发：`expected_latest` 是乐观锁；两人同时回退同一对象，后者 409。

**回退永不销毁内容（借 deepseek-harness 的 append-only replace 语义：旧内容只被遮蔽，不被改写）**：步 2 之后、步 3 之前，比对当前内容与 latest 版重建内容；若不同（存在未登记的人手编辑），先在同一事务里把**当前内容**登记成一版 `v(latest+1)`（`actor_user_id=uid`，`reverted_from_version=NULL`，`ledger_ref`=当前账本位置，`title` 沿用），回退版再占 `v(latest+2)`。于是回退前的人手状态永远可回；UI 不再需要「unsaved manual edits are overwritten」的警告，改为「Your current edits are kept as v4; v1 becomes v5」。代价：一次回退最多两行；不同才写，多数情况仍是一行。

### 2.4 资源来源反查

`GET /api/v1/resources/{resource_id}/provenance` → `OutputLineageResponse`（与 `/outputs/{kind}/{ref_id}` 同形，`kind="generated_media"`）。实现：资源可见性由 `resources_router` 既有守卫承担；`generated_media_repository.find_by_promoted_resource(resource_id)`（`ORDER BY id ASC LIMIT 1`，与 `_registered_resource_lookup_stmt` 同形）→ 无 → 404 `not_registered`；有 → `lineage_for("generated_media", gen_id)` → 空 → 404 `not_registered`；链接字段经 T1 的 `visible_issue_ids` 置空规则（能看资源 ≠ 能看产出它的 issue：坐标保留、链接按 issue 可见性）。悬空 `promoted_resource_id`（资源已删）在这条路上不可达（路径以资源为入口）。反查必须走索引：mig 466 顺带 `CREATE INDEX IF NOT EXISTS idx_generated_media_promoted_resource ON generated_media(promoted_resource_id) WHERE promoted_resource_id IS NOT NULL`（取号时核一遍是否已存在；deepseek-harness 的 `sourceEventSeqs`/`derivedEventSeqs` 正反双向索引是同一道理——反向不物化就是全表扫）。

## 3. 花费

### 3.1 文本类（script_*）：读时从 `step_end` 事件折出，不回写

（改判 2026-09-14，对照 deepseek-harness `token-meter` 的做法：用量随产出事件走、总额是对事件流的纯折叠、不存第二份。本仓 run 的 `spent_cents` 本来就是 `budget_hook` 对 `step_end` 折出来的，同一口径。）

- **不写 `run_deliverables.cost_cents`**（文本类该列保持 NULL，语义留给「登记时就精确知道」的媒体类）。血缘端点 `lineage_for` 对每个有 `run_id` 的版本，按 `(run_id, turn, step)` 取该 run 的 `step_end` 事件 `cost_cents`，除以该 `(turn, step)` 下的登记行数 → 响应字段 `cost_cents`，并加 `cost_kind: "allocated" | "exact" | null`（文本=allocated，媒体=exact）。一条 `SELECT … WHERE run_id = ANY(:runs) AND event_type='step_end'`，与现有的 run 读同批。
- 前端 `foldEvents` 在 `step_end` 到达时对该 `(turn, step)` 下的产出卡按同一公式补值（`stepCost / cards.length`）——两侧都是**同一事件的折叠**，不再有「写侧 UPDATE + 读侧折叠」两套；`tests/services/deliverables/test_step_cost_alloc.py` 与前端 `foldEvents.test.ts` 各自钉住 `n=2 → 各一半`。
- 均摊值只是参考，不是计费或门禁输入（deepseek-harness `projection.ts:27` 同一告诫）：UI 显示为 `≈¢0.09`；预算钩子仍只吃 `step_end` 与媒体精确价。
- 消掉的东西：`stamp_step_cost` 写路径、best-effort 回填失败留 NULL 的不一致、「`step_end` 永远不到 → 永远 NULL」与折叠结果不一致的窗口。
- `turn` 硬编码 1 的既有事实写进 `lineage_view` 的分摊函数 docstring；键仍用 `(turn, step)`，将来 turn 真正推进时零改动。

### 3.2 媒体类（generated_media）：目录每次调用价

- `ai_model_prices.per_call_cents`：管理员为图片/视频模型追加行（`model`, `provider`, `per_call_cents`，`prompt/completion` 填 0）。admin 页 `admin/src/pages/ai/` 的价格编辑器加一个字段（同 PR，若该页直接写 Supabase 则只是多一列）。
- `register_generated_media()`：`origin.cost_cents is None and origin.model and origin.provider` → `media_price_cents(model, provider)`（按 `effective_at DESC` 取最新一行的 `per_call_cents`；无行 → None）。**唯一咽喉点**，17 个调用点零改动（与 3a 登记口同一手法）。写进 `run_deliverables.cost_cents`（媒体类是登记时就精确的价，`cost_kind=exact`）。
- **前置票（3a 补验记的）**：agent 工具路径登记 `model=""`、`provider`=目录行名；分镜链登记 `provider=""`、`model`=`dall-e-3` 哨兵。两条路径都改成写服务层解析出的 `ImageGenResult.provider/model`，否则按 `(model, provider)` 查价必然落空。作为 3b Task 0。
- 视频同价表（`per_call_cents` 按模型行，`seedance` 之类按次）。按时长计价的模型本期不支持：行留空即 `—`。

### 3.3 媒体花费进 run 账

- `deliverable` 事件已带 `cost_cents`（媒体类登记时即有值）。后端 fold `folds/deliverables.py` 累加 `view.cost.media_cents`（去重键同 `outputs.seen`）；`recompute_spent`：`spent = own + Σ by_child + media`。预算钩子读 `spent_cents` → 生图花费开始计入预算（预算变黄/停机口径不变）。
- 前端 `RunCost` 加 `media_cents`；`BudgetBlock` 在 `media_cents > 0` 时多一行 `Media ¢x.xx`；Cockpit 预算格数字自然包含。

### 3.4 显示

产出卡元信息行：`model · ¢0.12 · step 3`（有值才显示各段；花费 null → `—`，`title="No price configured for <model>"` 仅媒体类）。右栏产出行、差异弹层两侧头、来源块（画布/资源面板）各显示该版花费。

## 4. 实时刷新

```
useIssueProgress ──(current_run.id 从 X 变为非 X，携 last_seq)┐
issueChatSocket ──(status phase:'done', runId, seq)─┼──► issueTurnSignal.notify(issueId, {runId, seq})   [seq 水位：≤ lastSeq 即丢]
POST /revert 成功 ──────────────────────────────────┘        │
                                                             ├─► IssueDetailView: outputsRefresh++ → OutputsBlock 重拉
                                                             ├─► useMentionOutputsTab: requested 复位（下次打开重拉）
issueChatSocket ──(deliverable 事件 kind/ref_id)────────────► outputsService.invalidateOutputLineage(kind, ref_id)（按键）
                                                             └─► OutputProvenance / OutputDiffDialog: useSyncExternalStore(generation) → 重拉
```

- `components/Todolist/issueTurnSignal.ts`：`useSyncExternalStore` 微 store（照 `outputHighlight.ts`），`notify(issueId, {runId, seq})`、`subscribe`、`useTurnSignal(issueId)`；逐订阅者 try/catch。**去重规则改为 seq 水位（借 deepseek-harness `projection-store.ts:135` "higher seq wins; replays and stale frames drop"）**：store 按 issueId 记 `lastSeq`，`seq <= lastSeq` 的通知直接丢——轮询边沿与 WS done 的重复、乱序到达都被同一条规则吃掉，不再需要 runId 集合。WS `status{phase:'done'}` 帧已带 run 的末 seq；轮询边沿用 `progress.current_run.last_seq`（`useIssueProgress` 响应已有 run 摘要，缺则补一个字段）。
- **议题页精确失效**：`/ws/issue/{id}` 已经推 transcript 事件，`deliverable` 事件 payload 自带 `kind/ref_id`——`IssueDetailView` 收到它就 `invalidateOutputLineage(kind, ref_id)`（按键失效，不整表），产出块与 @页签在回合结束信号时重拉一次。整表失效只留给回退成功（调用方明确知道只有一个键，也按键失效）。
- 边沿推导放在 `useIssueProgress` 内部（它已经持有上一次 `progress`）：`prev.current_run?.id` 非空且 `next.current_run?.id !== prev` → notify(prevRunId)。WS `done` 事件在 `IssueDetailView` 现有处理处也 notify——两源同 runId 只触发一次。
- `outputsService`：缓存条目带 `fetchedAt` 与响应里的 `as_of_seq`（血缘端点新增：该链最新登记行的 transcript seq，人手版为其 `id`）；`invalidateOutputLineage(kind?, ref_id?)` 按键删条目并 generation++，不带参才整表。**TTL 60 s + `visibilitychange`** 只服务没有 WS 的页面（画布节点来源块、资源面板）；议题页靠事件精确失效，不靠 TTL。`clearOutputLineageCache` 退役，测试改用 `invalidateOutputLineage`（C10）。
- 回退成功：调用方 `invalidateOutputLineage(kind, ref_id)` + `notify(issueId, {runId:null, seq: 响应新版 id})`，弹层用响应里的新版就地更新，不等重拉。

## 5. UI（画板「三期 3b · 回退 / 花费 / 实时 / 资源来源（浅色）」，用户只验这一节）

**稿一「差异弹层 · 回退」**：`Revert To v1` 由 disabled 变可用（仅 script_shot / script_scene 且非最新版）；点后弹层底部出现一行确认「Revert to v1? Your current edits are kept as v4 · v1 becomes v5.」（无未登记编辑时为「Revert to v1? This creates v4.」）+ `[Revert]` `[Cancel]`；成功后弹层右侧切到新版 `v5 ↩ v1 · Reverted · You`（info 色），toast `Reverted to v1 as v5`；失败 toast 按码：`version_conflict` → "Someone registered v4 meanwhile — reopen to see it"，`content_unavailable` → "v1 can't be rebuilt (no ledger)"。
**稿二「产出卡 · 花费与回退版」**：线程卡元信息 `doubao-seed-2-0-lite · ≈¢0.09 · step 2`（文本类分摊值带 ≈，hover「Allocated from step cost」）；媒体卡 `gpt-6-astra · ¢12.00 · step 3`（精确价无 ≈）；无价媒体卡 `gpt-6-astra · — · step 3`（hover 提示）；右栏产出块每行尾追加花费；回退版在右栏显示为 `v4 ↩ v1` chip（info 色）；Budget 块多一行 `Media ¢24.00`。
**稿三「实时」**：右栏产出块在回合结束瞬间自刷新（列表多一行 + 顶部 1 秒「Updated」淡入）；@ 页签重开即见新版；画布分镜节点来源块 `2 versions → 3 versions`。
**稿四「资源信息面板 · 来源块」**：与画布同一块 `Made By An Agent · Script Ai · MH-94 · run #…731 · step 3 · ¢12.00`，三按钮 Open Issue / Open Run / Diff（媒体无 Diff → 隐藏）；人手上传资源无此块；已 promote 但产出 issue 不可见 → 显示坐标、按钮禁用（title "Issue not visible to you"）。

## 6. 明确不做

媒体回退与媒体版本链（需要 shot_id+slot 或 parent_resource_id 作键，另立）；分镜图回退（`image_url` 无写路径）；章节回退（无账本无生产者）；「回退整个 run」（undo 服务已存在，产品面另议）；`script_shots`/`script_scenes` Realtime 频道；`FileInfoPanel` 来源块；积分→分换算与 provider 实报价；存量 `cost_cents` 回填；`turn` 硬编码 1 的修复；检索 / 日报 / 效率账 / 消息反馈 / 制片人视图（3c）。

## 7. 顺手吃掉的 3a 小票

C17（legacy 无 agent 的评论路径在 A7 之后成了唯一静默吃附件的分支：`issue_messages_router.py:616` 早于附件解析返回——3b 在 `no_open_question` 旁给它一个类型化 409 `citations_need_agent`，无 agent 的 issue 拒绝 `output_ref`，与「触发路径必须类型化失败回显」同族）、C5（undo 与版本语义：undo 不占号，注释写明）、C6（`ledger_ref`）、C7（媒体 v1 结论写进 README）、C10（`invalidateOutputLineage` 接线 + `clearOutputLineageCache` 退役）、C12（`OutputsBlock` 不再 `match:()=>true` 每页一拉：改为订阅信号 + rollup `has_outputs` 不做，仍每页一拉但只在信号后重拉）、C14（cost_cents）、C15（卡元信息行加模型/步号）、C16（资源面板来源块）。

## 8. 验收（真栈）

① 分镜 v1→v2（agent）→ 回退到 v1 → `run_deliverables` 出 v3（`run_id` NULL、`actor_user_id`=我、`reverted_from_version=1`）、分镜六字段 == v1、`script_shot_ops` 多一行 `actor='revert:…'`；diff v1↔v3 两侧文本相同；② 场次同形（逆操作批、`content_version` +1）；③ 并发：两次 revert 第二次 409 `version_conflict`；④ 媒体 400 `kind_not_revertible`；⑤ 配一条 `per_call_cents` → 生图后 `generated_media.cost_cents` 与登记行同值、run `spent_cents` 含它、预算格数字变化；⑥ 文本类：一步两镜 → 血缘端点各半、`cost_kind=allocated`、`run_deliverables.cost_cents` 仍 NULL；⑧ 回退前人手改过分镜（未登记）→ 回退产生两版（v3=当前内容 · You，v4 ↩ v1），v3 内容 == 改过的内容；⑨ 实时：同一回合的轮询边沿与 WS done 只触发一次刷新（seq 水位），且议题页收到 deliverable 事件后只有该 (kind, ref_id) 的血缘缓存被清；⑦ 回合结束 ≤ 5 s 内右栏产出块出现新行、@ 页签重开可见、画布来源块版本数 +1；⑧ 资源面板：promote 一张 agent 图 → 面板来源块可点回 issue；人手上传 → 无块；⑨ `npm run e2e:prod` 绿。

## 9. 纪律（继承 3a §9）

每 Task 独立 worktree（从 `origin/master` 建）+ PR；TDD + 突变记录；对抗评审（opus）全修；迁移与消费代码分 PR（ORM 镜像除外）；新 SQL 一律 ORM；边界 mock 用真实 wire 形状；错误码走 `details.code`；同一轮最多一条依赖 cwd 的 Bash；主检出只读。

## 10. Hand-off

1. 画板四块稿已由用户放行（Version 15，2026-09-14「继续」）。
2. `superpowers:writing-plans`，Task 粗切：T1 mig 466 + 三处 ORM → T2 登记口扩参 + `ledger_ref` 填写 + diff 优先 `ledger_ref` → T3 回退端点（shot/scene 两臂 + 同事务登记）→ T0 图片登记归因修复（两条路径写 `ImageGenResult.provider/model`）→ T4 花费（血缘端点读时分摊 + `cost_kind` + `media_price_cents` 咽喉 + fold `media_cents` + `recompute_spent`）→ T5 前端回退（弹层确认态 / 结果态 / 错误码）+ 花费显示三处 + Budget 行 → T6 实时信号（seq 水位）+ deliverable 事件按键失效 + 缓存 TTL（无 WS 页面）+ 三个消费方接线 → T7 资源来源端点 + 面板块 → T8 真栈验收 + 完成账。
