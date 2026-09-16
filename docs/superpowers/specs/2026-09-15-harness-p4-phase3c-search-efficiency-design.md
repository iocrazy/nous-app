# harness 第七轮 · 三期 3c「检索 / @引用 / 效率账 / 回合可读性」设计

> 前序：3b spec `2026-09-13-harness-p4-phase3b-revert-cost-realtime-design.md`（回退 / 花费 / 实时 / 资源来源，2026-09-14 全部上线）；P4 总 spec `2026-09-05-harness-p4-task-visibility-control-design.md` §6 把第 3 期后半段列为「检索 + @引用；日报；效率账；消息反馈；制片人视图（另立）」。**用户于 2026-09-15 把 3c 收成三个主题**：检索 + @引用、效率账、回合可读性（对照 WorkBuddy 的两处体验：每条回复尾部「共消耗 ◇ 积分 · 模型」、回合进行中叙述与动作交错的阶段性结果）。日报推 3d；消息反馈、制片人视图另立。
> **状态（2026-09-15）**：草案。技术取舍由作者定（用户明示）；用户验 §6 的画板稿。

## 0. 前提核对（2026-09-15 四路勘察，master 69cd7164；报告在 `.superpowers/sdd/2026-09-15-harness-p4-phase3c/recon-3c-*.md`）

| 假设 | 实况 | 结论 |
|---|---|---|
| Runs 页有基本检索 | 三个 run 列表端点都不接文本 / agent / issue / 工具名；`agent_runs` 11 个索引全是坐标索引；`input_summary` 三个写方语义各异且列表投影刻意排除（`agent_runs_repository.py:367-370`） | run 检索不按 `input_summary`，按 `output_summary`（agent 的结论摘要）+ 坐标过滤（§2.1） |
| Issues 有服务端检索 | mig 166 建的三个 trgm GIN 从未被任何谓词碰过；UI 是对最新 200 行的内存 `includes`（`IssueListView.tsx:621-630`），第 201 条搜不到且不报错 | `GET /issues?q=` 直接用现成索引（§2.3），不进投影表 |
| 产出可按内容搜 | `run_deliverables` 只有 ≤120 字标签 `title`，无内容无索引；正文散在 `script_shots` 六列 / `script_ops` 元素数组 / `generated_media.prompt`；跨议题按关键词找产出今天完全不可能 | 搜索投影表 `search_docs`，登记时由生产者交正文（§2.1） |
| 「被谁引用过」能反查 | 引用落在 `messages.body` jsonb，零索引；画布侧同类反查靠物化镜像表（`canvas_asset_refs`） | 镜像表 `output_citations`，在消息落库咽喉点写（§2.2） |
| 全文检索基建可复用 | 唯一 tsvector 是 `agent_memory.search_tsv`（`'english'` 配置，中文会被切碎）；embedding 链 503（`qwen3-embedding-8b` 未加载）；`rpc_user_media_text_search` 的 OR 链让索引失效（TODO-SEARCH-001） | 全部用 pg_trgm（`ILIKE` + `gin_trgm_ops`，`escape_like` 转义），不用 tsvector、不用向量 |
| 效率账要从零建 | 已有 `ai_usage_hourly` + `team_ai_budgets`（mig 374）、7 个跨 run 汇总端点、`UsagePage` / `TeamAiUsagePage`、每小时 z-score 异常 workflow | 3c 不建第二套汇总面；**先对账**（§3.1），再把指标接进既有面（§3.3） |
| 各面读出的花费一致 | `RunRecorder._finish` 把**树总额**推进 `ai_usage_hourly`，子 run 自己又写一行 → 跨 run 求和子 agent 双计（`run_recorder.py:805-822, 897-909`）；`/usage/issues/{id}` 无 root 过滤而预算门禁与 rollup 有（`usage_repository.py:200-232` vs `agent_runs_repository.py:843`） | 小时表改推**自身**花费；议题合计加 root 过滤；一致性测试钉住（§3.1 A1/A2） |
| 积分账在扣 | `token_billing.py:289-297` 调 `check_and_consume(points=…)`，真签名是 `(team_id, user_id, action_type, …, override_cost)`，四个关键字全不匹配、两个必填缺失；每轮 TypeError 吞成 WARNING；`ai_usage_logs` 那半写成功 → 有记录从未扣分 | 修好并真扣、只向前（用户裁定）；按 run 自身花费扣，不按树总额（§3.1 A3） |
| `(turn, step)` 是真坐标 | `turn` 在 `_step_started/_step_ended/emit_turn_end` 三处硬编码 1，键实际是 step-only；`tool_call` payload 无 duration 无 error_code；唯一带 per-tool 耗时的 `agent_run_events` 零读方、delta 靠进程内 LRU | `turn` 不动（改它牵动 3a 坐标契约）；`tool_call` 补 `duration_ms`/`error_code`；`CostAuditorHook` 停写（§3.2） |
| 跨 run 指标可读时聚合 | `agent_run_transcript_events` 只有 `(run_id)` 索引，`created_at` 无索引、无分区、无 TTL | 指标在 `_finish` 时由 fold 数好落 `agent_runs` 列，按时间窗只扫 `agent_runs`（§3.2） |
| run 的 `team_id` 可靠 | 大量为 NULL（`UsagePage.tsx:14-16` 自认「团队 scope 只给月度」）；`idx_agent_runs_billing` 是 `WHERE team_id IS NOT NULL` 的 partial | 议题派发链补戳 + 已结束 run 一次性回填（§3.1 A6） |
| 收件箱可用 | `GET /api/v1/inbox` 每次 500：kind 枚举四处口径（schema 3 / DB CHECK 5 / ORM 4 / 前端 5），`inbox_router.py:37` 逐行构造炸整表；3b 记票未修 | 前置票 A4（§3.1） |
| 回合里的阶段性结论有数据 | `assistant` 事件**只在这一步没有工具调用时**写（`agent_runner.py:1887-1896`）；同一步「先写结论再调工具」的文本不进 transcript；折叠器把 `assistant` 当 step 内的一行 output（`foldEvents.ts:539-549`） | 带工具调用的步也写 `assistant{partial:true}`；折叠成 step 之间的正文节点（§4.1） |
| 回复气泡能标花费 | 聊天气泡元数据带 `run_id`（`chatMessageMeta.ts`），尾巴只显示 tokens；议题线程 run 头部芯片只显示 fork 来源与工具超时；rollup `runs[]` 已带 `cost_cents`/`model` | 一个 `RunCostTail` 组件挂两处宿主（§4.2） |

## 1. 范围（三主题，每条都有「不做」）

| # | 做 | 不做（记票 / 另立） |
|---|---|---|
| S 检索 + @引用 | 顶栏 ⌘K 统一面（议题 / run / 产出三组）；Issues 页搜索改服务端；产出正文进投影表；@ 页签 Outputs 服务端检索并放开到同项目兄弟议题；`output_citations` 反查，血缘每版带 `cited_in` | @agent / @user（那是通知不是引用）；聊天面板（无议题）引用产出（3a 的拒绝仍成立）；tsvector / 中文分词 / 向量检索；一次 run 内部按工具名搜（回放视图已有类型过滤） |
| E 效率账 | 六条对账前置票（A1–A6）；run 行落指标列；`tool_call` 补耗时与错误码；议题 rollup 加 `efficiency`；用量页加效率 tiles 与 turn_end 分布 | 存量积分追扣；跨团队对比；`turn` 编号；transcript 分区 / TTL；`agent_run_events` 表 DROP（停写后记票）；项目级 / agent 级预算 |
| V 回合可读性 | 阶段性叙述进 transcript 并在线程里交错显示；每条回复尾部消耗行；聊天面板运行中的一行状态 | 日报（3d）；消息反馈；制片人视图 |

## 2. 检索与引用

### 2.1 `search_docs` 投影表（mig 472；取号时重扫，471 是当前最后一个）

P4 原语②「投影是注册表」：真相仍在 `agent_runs` / `run_deliverables` / 各内容表，`search_docs` 只是一张可搜的投影，丢了可以重建。

| 列 | 说明 |
|---|---|
| `id BIGINT` identity PK | |
| `entity_kind TEXT CHECK IN ('run','output')` + `entity_id TEXT` | `UNIQUE (entity_kind, entity_id)`；output 的 `entity_id` = `run_deliverables.id`（一版一行，不是一对象一行——检索命中要能说出「是哪一版」） |
| `kind TEXT` / `ref_id TEXT` / `version INT` | output 专用（`script_shot` / `script_scene` / `script_chapter` / `generated_media`） |
| `team_id BIGINT` / `project_id BIGINT` / `issue_id BIGINT` / `run_id BIGINT` / `owner_user_id UUID` / `agent_id UUID` | 坐标；`owner_user_id` 给无 issue 的 run 做可见性 |
| `title TEXT NOT NULL` / `body TEXT` / `model TEXT` / `status TEXT` / `error_code TEXT` | `body` 上限 8 KB（截断，不是拒绝） |
| `updated_at` / `created_at` | |

索引：`gin (title gin_trgm_ops)`、`gin (body gin_trgm_ops) WHERE body IS NOT NULL`、`(team_id, updated_at DESC)`、`(issue_id)`、`(project_id) WHERE project_id IS NOT NULL`。ORM 镜像同批（C1 门禁按 (名字, 轴) 棘轮，新表零漂移进场）。

**写方（三个咽喉点，各一个 `upsert_search_doc` 调用，失败只记 ERROR 不连坐主写入——与 `register_deliverable_best_effort` 同口径）**：

- `register_deliverable` 新增关键字参数 `search_text: str | None`。三个生产者交正文：分镜 = 六字段渲染（复用 `diff.py::render_shot`）；场次 = 元素数组渲染（`render_elements`）；生成媒体 = `prompt` 全文。回退产生的人手版也登记（内容 = 回退目标版，`revert.py` 已算出来）。`search_text` 缺省时只写 `title`，**不报错**——新 kind 的生产者忘传只是搜不到正文，不是接线 bug。
- `RunRecorder._finish`：`entity_kind='run'`，`title` = 所属议题 identifier + 标题（无议题取 `input_summary` 首 80 字），`body` = `output_summary`，`status` / `error_code` / `model` / `agent_id` / 坐标。只在终态写一次。
- `issues` **不进投影**：三个现成 GIN 直接用，避免两份口径。

**回填**（数据迁移，同一 mig 的第二段）：`agent_runs` 已结束行按上面口径全量写；`run_deliverables` 存量只写 `title`（正文要跑账本重放，量大且 3c 的价值在新登记；存量正文检索记票）。

### 2.2 `output_citations` 镜像表（同 mig 472）

| 列 | 说明 |
|---|---|
| `id BIGINT` identity | |
| `kind` / `ref_id` / `version` | 被引的那一版 |
| `issue_id BIGINT` / `conversation_id BIGINT` / `message_id BIGINT NOT NULL` | 引用发生在哪条消息；`UNIQUE (message_id, kind, ref_id, version)` |
| `cited_by_user_id UUID NOT NULL` / `created_at` | |

索引：`(kind, ref_id, version)`、`(issue_id)`。

**写点唯一**：`ConversationsAiStore.append_user_message` 拿到新行 id 之后，若 `attachments` 里有 `ref_kind='output'` 的条目就逐条 insert（同事务）。注释路径、唤醒路径、聊天面板守卫三条入口都经它落库，所以只此一处。删除消息不级联（引用是历史事实）。

**读点**：`GET /outputs/{kind}/{ref_id}` 每版多 `cited_in: [{issue_id, issue_key, message_id, user_id, at}]` 与 `cited_count`；`cited_in` 只列调用方可见的议题（复用 `visible_issue_ids` 批量门），`cited_count` 是全量——「被引 3 次、你能看 1 条」是允许的诚实答案。

### 2.3 检索端点

`GET /api/v1/search?q=&kinds=issue,run,output&team_id?&project_id?&issue_id?&limit_per_group=10`

- `q` 长度 ≥ 2，`escape_like` 转义后拼 `%q%`（拼模式者负责转义，SQL 侧 `ESCAPE '\'`——CLAUDE.md 立约）。
- **议题组**：`issue_repository.list_for_user(q=…)`（新参数）——`identifier ILIKE OR title ILIKE OR description ILIKE`，走 mig 166 的 GIN；`visibility_predicate` 不变；`GET /issues?q=` 同一条路，Issues 页搜索框改传 `q`（防抖 250 ms），客户端 `includes` 删除。返回 `total`，UI 显示「N matches」而不是静默截断。
- **run 组 / 产出组**：`search_docs` 上 `title ILIKE OR body ILIKE`，SQL 预过滤 `team_id IN (我的 team) OR owner_user_id = me`，`ORDER BY GREATEST(similarity(title,q), similarity(coalesce(body,''),q)) DESC, updated_at DESC`，取 `limit_per_group × 3` 后在 Python 里用 `visible_issue_ids` 批量裁到 `limit_per_group`（无 issue 的 run 已在 SQL 层按 owner 过滤）。
- 每条命中带 `deep_link`（服务端用 `issue_links.py` 生成：议题 → 议题页；run → `?step=`；产出 → 议题页 `?step=&output=kind/ref_id`）与 `snippet`（命中片段 ±60 字，`ts_headline` 不用，Python 截）。
- 响应 `{groups: {issues, runs, outputs}, took_ms}`；跨团队边界完全由本端点负责（`scoped_sql` 不管 `agent_runs`，F20）。

### 2.4 @ 页签 Outputs 跨议题

- `useMentionOutputsTab` 改成 assets 页签的契约：空查询 = 本议题产出（现有 `listIssueOutputs`）；有查询 = `GET /search?kinds=output&project_id=<issue.project_id>&q=`（无 project 的议题退回本议题）。行上多一枚议题 chip（`MH-96`）以区分来源。
- `resolve_output_refs` 的归属校验从「属于本议题」放宽为「调用方可见」（`visible_chain` 同一把尺子）；附件上多带 `issue_key`，线程里的引用卡显示来源议题。
- 聊天面板仍拒绝（`refuse_citations_without_issue`）。

### 2.5 ⌘K 统一面

- 组件 `frontend/components/search/CommandPalette.tsx`：顶栏放大镜（`TopBar.tsx:593-600` 的 Phase 2+ 占位）与 ⌘K / Ctrl+K 打开；输入防抖 200 ms；三组各 ≤10 行；↑↓ 在组间连续移动，Enter 深链，Esc 关。空态显示「Type to search issues, runs and outputs」，无命中显示「No matches for “…”」，失败显示错误码文案（走 `errorEnvelope.ts`）。
- 不做最近搜索、不做快捷命令——占位的注释写的是 search，本期只填 search。

## 3. 效率账

### 3.1 对账前置票（每条独立 PR，全部带一致性测试）

| # | 缺陷 | 修法 | 钉住的测试 |
|---|---|---|---|
| A1 | 小时表推树总额 → 子 run 双计 | `record_usage(cost_cents = own_cents + media_cents)`；`by_child` 不进小时表（子 run 自己那一行就是它） | 父 + 两子 + 一张图的 fixture：`SUM(ai_usage_hourly.cost_cents) == SUM(root agent_runs.cost_cents)` |
| A2 | `/usage/issues/{id}` 无 root 过滤 | `issue_totals` 加 `parent_run_id IS NULL` | 同 fixture：`issue_totals().cost_cents == spent_cents_for_issue()` |
| A3 | 积分从未真扣 | `check_and_consume(team_id, user_id, action_type="agent_run", reference_id=str(run_id), override_cost=ceil(points), description=f"{model} · {tokens} tokens")`，按返回 dict 判成功；`reconcile_run` 收 **自身**花费（own + media），不收树总额；BYOK run 跳过（3b BYOK tier 的判定复用）；`config.yml` 加 `AGENT_POINTS_CHARGE_ENABLED`（默认 true）作为急停；存量「有 `ai_usage_logs` 未扣分」只在完成账报数 | 真签名的桩：参数集合精确匹配（防再次静默漂移）；返回非空 dict 但 `success=false` 时 `charged=False` |
| A4 | inbox 500 | `InboxKind` 从 `NotificationKind` 派生（单一来源）；ORM CHECK 补 `agent_question`；`idx_inbox_notifications_user_unread` partial 谓词在 ORM 声明（消掉一条索引漂移）；路由逐行构造失败的行 **跳过并记 ERROR**，不再 500 整表 | 镜像测试：schema 枚举 == ORM CHECK == 最新 migration CHECK == 前端 `InboxKind` |
| A5 | issues 第 201 条搜不到 | 并入 §2.3 | `q` 命中 offset 200 之外的行 |
| A6 | run 的 `team_id` 大量 NULL | 议题派发链的 `RunRecorder(...)` 调用点（`conversation_agent_turn.py:459` / `ai_library_chat_service.py:1440`）从议题取 `team_id`/`project_id` 兜底；数据迁移一次性回填**已结束** run：`UPDATE agent_runs a SET team_id = i.team_id, project_id = COALESCE(a.project_id, i.project_id) FROM issues i WHERE a.issue_id = i.id AND a.team_id IS NULL AND a.ended_at IS NOT NULL` | `test_no_repository_write_path_touches_scope_columns` 保持（迁移不是 repository 写路径）；派发链 fixture 断言 `team_id` 非空 |

### 3.2 指标落 run 行

- **`tool_call` 事件补两字段**：`duration_ms`（monotonic 包住工具执行）与 `error_code`（结果里 `error` / `outcome != ok` 时的类型化码，否则 null）。三个发射点（`agent_runner.py:1050 / 1277 / 2162`）收成一个 `emit_tool_call()` helper，守卫测试断言仓内没有第四处裸发 `"tool_call"`。
- **新 fold `folds/efficiency.py`**：`tool_call` → `tool_calls += 1`，有 `error_code` → `tool_errors += 1`；`step_end` → `steps += 1`；`deliverable` → `deliverables += 1`（按 `seen` 去重后的键，与花费同口径）；`turn_end` → `turn_end_reason`。`views["efficiency"]` 随 `run.view` 暴露（运行中可读）。
- **mig 472 第三段**：`agent_runs` 加 `steps INT` / `tool_calls INT` / `tool_errors INT` / `deliverables INT` / `turn_end_reason TEXT`；索引 `(user_id, created_at DESC)`（用户视角时间窗）。`_finish` 从 fold 写入；`interrupted_turn` sweeper 补写 `turn_end_reason='interrupted'`。存量行 NULL，UI 显示 `—`。
- **`ai_usage_hourly` 加计数列**：`run_count` / `failed_runs` / `tool_calls` / `tool_errors` / `deliverables`，`record_usage` 同一次 upsert 累加；`event_count` 保留（它是「有 token 的完成」数，语义不同）。
- **`CostAuditorHook` 停写**：从 PostToolUse 链摘掉，类与测试删除；`agent_run_events` 表与 `provider_monthly_spend` 记票（DROP 走单独 mig，等一个发布周期确认无人读）。

### 3.3 读面

- **`issue.rollup.efficiency`**：`{runs, steps, tool_calls, tool_errors, deliverables, cost_per_deliverable_cents, avg_run_ms, turn_end_reasons: {completed: n, …}}`，一条聚合 SQL（`agent_runs WHERE issue_id = ?`，root + children 都算——计数是自身量，不会双计）；`cost_per_deliverable_cents = budget.spent_cents / deliverables`（分子沿用 rollup 已有的 root-only 树总额，分母全体 run）。驾驶舱 Outputs 格加 `¢x / output`，Tools 格加 `n errors`。
- **`GET /usage/summary`**：每组多 `run_count / failed_runs / tool_calls / tool_errors / deliverables / cost_per_deliverable_cents`（小时表新列）。
- **`GET /ai-library/usage/efficiency?scope=user|team|project&id=&from=&to=&group_by=agent|model`**（新）：从 `agent_runs` 新列出 `turn_end_reasons` 分布、`avg_run_ms`、`tool_error_rate`；团队 scope 复用 `usage_router` 的成员门。
- **`UsagePage`**：stat tiles 从 4 枚扩到 6 枚（+ Cost / output、Tool error rate），日堆叠图下加一条 turn_end 原因分布条；`TeamAiUsagePage` 同两枚 tile。既有 `success rate` tile 改读 `turn_end_reason == completed` 而不是 `status`（`awaiting_input` 不是失败）。

## 4. 回合可读性

### 4.1 阶段性叙述

- **后端**：带工具调用的步，若 `msg.content` 非空，先 `emit_event(recorder, "assistant", {"content": content, "partial": True, "step": iteration})` 再执行工具。三条路径都改：`run_turn` 非流式（:1887）、`stream_turn` 真流式（`tool_call_buf` 非空时把本步累计的 `delta_text` 落一次）、`StreamingNotSupported` 缓冲回退（生产唯一路径，CLAUDE.md 血泪）。`tests/runner/test_turn_end_reasons.py` 同族加用例证明三条路径都写出了 `partial` 事件。
- **折叠器**：`assistant{partial:true}` → 新节点 `kind:'narration'`（`text`, `step`, `at`），插在它所属 step 节点**之前**（叙述先于动作，与模型输出顺序一致）；`assistant` 无 `partial` 维持现状（最终回答）。
- **渲染**：`TrajectoryRenderer` 把 `narration` 渲成正文段落（markdown，与最终回答同一渲染器）；Chat 折叠策略下 step 节点折成一行「Ran `<tool>` · 1.2 s」/「Edited `<file>`」样式的动作行（现有 `StepLine` 的 label 换成动作动词 + 对象，`type:'tool'` 按工具名映射动词表：`RunCommand → Ran`、`UpdateShot → Edited shot`、`GenerateImage → Generated image`，未映射的用工具名原样）。议题线程与聊天面板共用，实时靠既有轮询 / WS 边沿。
- **聊天面板运行中状态行**：最后一条气泡下方一行 `Step 3 · Running GenerateImage…`，读 `run.view.current` + 最近一条未结束的 `tool_call`；回合结束即消失（议题驾驶舱已有「Now:」，聊天面板补齐）。

### 4.2 每条回复尾部消耗行

- 组件 `frontend/components/agentActivity/RunCostTail.tsx`：`◇ 0.82 · doubao-seed-2-0-lite`；积分 = 已扣的 `charged_points`（A3 之后），未扣（BYOK / 急停关闭 / 零花费）显示 `¢x` 兜底；两位小数；hover：`prompt/completion tokens · ¢cents · charged / not charged (原因)`。运行中显示活值加 `…`。
- **数据**：议题线程 run 头部从 rollup `runs[]` 取（已有 `cost_cents`/`model`，加 `charged_points`）；聊天面板由 `MessageList` 收集可见气泡的 `run_id`，一次 `GET /ai-library/runs/costs?ids=`（新，≤50 个，返回 `{id: {cost_cents, charged_points, model, status, prompt_tokens, completion_tokens}}`），运行中的 run 由 WS done 帧带 `cost_cents`/`charged_points` 补齐（3b T4b 的 done 帧扩两字段）。tokens 数从气泡正文移到 hover。
- `agent_runs` 不加 `charged_points` 列：读 `point_consumption_log`（按 `reference_id = run_id`）——那是积分账的真相，效率账不复制它；批量端点一次 join。

## 5. 数据迁移清单（mig 472，一份文件四段，各段独立可重跑）

1. `search_docs` 表 + 索引；2. `output_citations` 表 + 索引；3. `agent_runs` 五列 + `(user_id, created_at DESC)` 索引，`ai_usage_hourly` 五列；4. 回填：`search_docs` 的 run 行与 output 标题行、`agent_runs.team_id`。ORM 镜像同 PR；真 PG 集成用例接 `schema-drift.yml`（step 级校验，3b 教训）。迁移先行，代码侧对新列缺席容错（`SQLSTATE 42703` → 503 typed，不 `return []`）。

## 6. UI（画板「三期 3c · 检索 / 效率 / 回合可读性（浅色）」，用户验这一节）

**稿一「⌘K」**：顶栏放大镜点开居中面板，输入 `rain`，三组：Issues（`MH-96 · Alpha rain on glass · running`）、Runs（`MH-96 · run #…731 · completed · doubao · ¢0.09`，snippet 高亮）、Outputs（`S3 · Shot 1 · MS · v4 · MH-96 · Cited ×2`）；底部 `↑↓ navigate · ↵ open · esc close`。
**稿二「线程 · 叙述与动作交错」**：agent 头像后依次：正文段「先说清现状：6403 张唯一图片……」→ 动作行 `Ran ListFiles · 0.8 s` → 正文段「样本选定……」→ 动作行 `Edited shot S3-01` → 提问卡（选项 + Other…）；最后一条气泡尾部 `◇ 0.82 · doubao-seed-2-0-lite`，hover 浮层三行；运行中的状态行 `Step 3 · Running GenerateImage…`。
**稿三「用量页效率 tiles」**：六枚 tile（Spend / Tokens / Requests / Success / Cost per output / Tool errors），下方 turn_end 分布条（completed / awaiting_input / error / interrupted / cancelled 五色，语义色 token）。
**稿四「血缘 · 被引用」**：产出弹层右栏版本行尾 `Cited ×2`，展开列出 `MH-96 · You · 2h ago`、`MH-98 · Alice · 1d ago`（不可见的议题只计数不列）；@ 页签 Outputs 跨议题命中行带 `MH-98` chip。

## 7. 明确不做

日报（3d，等本期花费口径对齐后再发）；消息反馈；制片人视图；@agent / @user；聊天面板引用产出；tsvector / 分词 / 向量；`turn` 编号；存量产出正文回填；存量积分追扣；跨团队效率对比；transcript 分区 / TTL；`agent_run_events` / `provider_monthly_spend` DROP；项目级 / agent 级预算；最近搜索与快捷命令。

## 8. 顺手吃掉的既有票

3a/3b 记票里与本期同根的：`/api/v1/inbox` 500（A4）；`idx_inbox_notifications_user_unread` ORM 漂移（A4）；`PointsService.check_and_consume` 每轮 WARNING（A3）；「前端分母按去重卡数、后端按事件数」（§3.2 fold 用 `seen` 去重键，两边同口径）；「媒体无价 → `cost_kind=None`」显示口径不变但 `RunCostTail` 兜底显示 `¢`；`UsagePage` 团队 scope 只给月度（A6 之后放开到日/范围）。

## 9. 验收（真栈，cn.nous.ink）

① ⌘K 搜一个只在第 201+ 条议题标题里出现的词 → Issues 组命中；② 搜分镜正文里的词（不在标题里）→ Outputs 组命中并深链到该 step；③ 搜 agent 结论摘要里的词 → Runs 组命中；④ 别团队成员搜同一词 → 三组零命中（curl 断言 `groups` 全空，不是 404）；⑤ 在 MH-A 的回复框 @ 引用 MH-B（同项目）的产出 → 发帖成功、引用卡带 `MH-B` chip、`output_citations` 一行、MH-B 那版血缘 `cited_in` 含 MH-A；⑥ 父 + 子 agent 的一次回合后 `SUM(ai_usage_hourly.cost_cents)` == 根 run `cost_cents`；`/usage/issues/{id}` == 驾驶舱 Budget 的 spent；⑦ 一次完成回合后 `point_consumption_log` 多一行 `reference_id = run_id`、`points == ceil(自身花费)`，团队余额减少同值；急停关闭后再跑一回合不扣；⑧ `GET /api/v1/inbox` 200 且列表含 `agent_question` 行；⑨ 带工具调用的回合：线程里正文段与动作行交错出现且顺序与模型输出一致，`agent_run_transcript_events` 有 `assistant{partial:true}` 行；⑩ 回复尾部 `◇ n · model`，hover 见 tokens 与 ¢；聊天面板同一条只发一次 `/runs/costs`；⑪ `agent_runs` 新行五列非空，`tool_call` 事件带 `duration_ms`；驾驶舱 Outputs 格 `¢x / output`；用量页六 tile 与分布条；⑫ `npm run e2e:prod` 绿。

## 10. 纪律（继承 3b §9）

每 Task 独立 worktree（从 `origin/master` 建）+ PR；TDD + 突变记录；对抗评审（opus）全修；迁移与消费代码分 PR（ORM 镜像除外）；新 SQL 一律 ORM；边界 mock 用真实 wire 形状；错误码走 `details.code`；同一轮最多一条依赖 cwd 的 Bash；主检出只读；`stream_turn` 缓冲回退分支必须有用例。

## 11. Hand-off

spec 合入后走 writing-plans：建议 Task 序 = mig 472（先行）→ A1/A2/A3/A4/A6（并行，互不相交）→ `tool_call` helper + efficiency fold + `_finish` 落列 → `search_docs` 三个写方 + 回填 → `/search` + `/issues?q=` → `output_citations` 写点 + 血缘 `cited_in` → @ 页签跨议题 + 引用校验放宽 → ⌘K 面 → rollup/usage 读面 + tiles → 阶段性叙述（后端三路径 + 折叠 + 渲染）→ `RunCostTail` + `/runs/costs` + done 帧 → 真栈验收 + 完成账。
