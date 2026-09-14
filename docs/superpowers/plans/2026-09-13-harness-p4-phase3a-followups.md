# harness 三期 3a 收尾 · 40 条延后小票分诊 + 四张即修小票

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement the four tasks below, one worktree + one PR each. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 3a SDD ledger（`.superpowers/sdd/2026-09-10-harness-p4-phase3a-outputs-lineage/progress.md`）里 40 条 `minor (deferred)` 与终审 Minor 分成三桶——归 3b / 30 分钟可修 / 直接关闭——并把四张最影响真栈体验的即修票各以一个小 PR 修掉。

**Spec:** `docs/superpowers/specs/2026-09-10-harness-p4-phase3a-outputs-lineage-design.md`（§4 接口、§5 UI；本文件的四个 Task 都不改 spec 契约，只把已裁定「比泄露面宽」的置空规则精确化、补三处漏掉的边角）。

**Tech Stack:** FastAPI + SQLAlchemy async（新 SQL 一律 ORM，禁 `text()`）、pytest；React 19 + vitest/RTL、tiptap。

## Global Constraints

- 每 Task 独立 worktree（从 `origin/master` 建）+ 独立 PR；主检出只读；写操作一律 `git -C <绝对路径>`；同一轮最多一条依赖 cwd 的 Bash。
- TDD：关键断言先红后绿；每 Task 至少一处突变让测试转红并记进 PR 描述。对抗评审（opus）发现全修。
- 后端：`cd <wt>/backend && uv run pytest -q -p no:cacheprovider <相关目录>` 再全量（`--deselect tests/api/test_distribution_music_search.py --deselect tests/api/test_distribution_topic_suggest.py tests`）；`flake8 app tests` 零告警；isort/black/ruff 改动文件。
- 前端：`cd <wt>/frontend && npx vitest run <相关文件>` 再全量 `npx vitest run`；`npx eslint <改动文件>`；`npx tsc --noEmit`（仓库既有错误在未触碰文件，只看改动文件零新增）。前端 worktree 需 `ln -s <主检出>/frontend/node_modules <wt>/frontend/node_modules`。
- 边界 mock 用真实 wire 形状（Snowflake id 在 `outputs` 端点上是 **string**；错误体是 `ErrorResponse` 外壳，类型化码在 `details.code`）。
- 新触发路径必须带类型化失败回显；不引入旧色相类名；UI 文案英文 Title Case；新 i18n key en/zh 同加。
- 不改 `run_deliverables` 表；不写 `metadata_json.lineage`；不加第二个全文 prompt pin。
- PR 描述必有「复用 / 删除了什么」「偏差」「对抗评审」「突变记录」「测试」。

---

## 分诊结果（40 条，来源 = 3a ledger 行号 + 终审 Minor + 验收偏差）

**桶 A · 本轮即修（四张，各一 PR，= 下面 Task 1–4）**

| # | 来源 | 小票 | Task |
|---|---|---|---|
| A1 | L187（T8b） | 跨议题置空规则比泄露面宽——同团队兄弟议题旧版也丢链接；精确化需对 chain 内 distinct issue 批量可见性判定 | T1 |
| A2 | L156（T3b） | `lineage_view.py` 的 `deep_link` 写在 `_VERSION_KEYS` 之外，投影元组不再列全 wire 键——加注释 | T1（同文件顺手） |
| A3 | 验收偏差 4 | 选中 outputs/assets 页签后编辑器里的 `@query` 不删（资源页签走 `insertResourceRef` 会删；`AIChatPanel` 已有 `dropMentionTrigger` 先例） | T2 |
| A4 | L165（T6b） | `IssueReplyBox.test.tsx:156` 自称「REAL wire shape」的无类型 mock 缺两字段（`issue_key` / `deep_link`） | T2（同测试文件顺手） |
| A5 | 终审 Minor | 四份 TS kind 枚举（`outputMentionRows.ts` / `OutputMentionList.tsx` / `OutputChipBody.tsx` / `OutputsBlock.tsx` 的 `Record<string,…>` 表）无镜像守卫 | T3 |
| A6 | L97（T4） | `MAX_OUTPUT_REF_ATTACHMENTS`(8) 无 TS 镜像测试（`MAX_ASSET_REF_ATTACHMENTS` 有） | **关闭**：执行 T3 时发现票的前提已过时——`tests/services/ai/chat/test_attachment_limit_frontend_mirror.py` 早已钉住同一断言；T3 评审按 DRY 删掉了重复的 cap 半边 |
| A7 | L186（T8a） | `POST /issues/{id}/messages` 的乐观 `comment` 不带 `attachments`（必须先过 `display_attachments`，否则回显 `data_url`） | T4 |

**桶 B · 30 分钟可修但本轮未修（下一个空档批量一个 PR）**

| # | 来源 | 小票 |
|---|---|---|
| B1 | L96 | `<referenced_outputs>` 空串标题渲染成 `title=""`，README/docstring 承诺省略；`_verified` 应把 `""` 视为缺失 |
| B2 | L110 | 前端 `deliverable` case 的 `num(ev.turn) ?? 1` 不镜像 `step_start` 的 `?? num(p.turn)` |
| B3 | L113 | `OutputsBlock` 用 `Number(ctx.issue.id)`，service 收 string，应直传保 Snowflake 纪律 |
| B4 | L114 | 差异弹层 `version_not_found` 分支无测试；lineage 缺 `latest_version` 时会发 `?from=undefined` |
| B5 | L144 | `useMentionOutputsTab` 的 `requested.current` 在 issueId 变化时不清，跨 issue 复用作曲区留旧行 |
| B6 | L146 | `stagedOutputs.ts:76` 无坐标行原样返回，调用方分不清「已暂存」 |
| B7 | L158 | `frontend/components/FileCard.tsx:65` 内联拼 todolist 路径，应退役改用统一 builder |
| B8 | L232 | `onIssueDispatched` 回读失败只记日志，无 toast |
| B9 | 终审 Minor（T2） | `IssueReplyBox.test.tsx` A4 fixture 的 `deep_link` 值与坐标不自洽（`issue_deep_link` 只在有 step 时才带 `&turn=`，两版不该是同一串） |
| B10 | 终审 Important 2 + T3 小票 | 既有 `test_attachment_limit_frontend_mirror.py` / `test_slots_frontend_mirror.py` 都缺 `_ROOT` 锚点断言（路径算错即整文件 SKIP 成假绿，移植 `test_kinds_frontend_mirror.py:65-69` 三行）；`attachmentLimits.ts` 无前端侧测试，只改 TS 的 PR 形状下两个 cap 常量可静默漂移 |

**桶 C · 归 3b（与 3b 四个主题同根，或需要 3b 的数据/接线才有意义）**

| # | 来源 | 小票 | 归入 3b 哪条 |
|---|---|---|---|
| C1 | L46 | 没有任何门禁比对 ORM 索引（schema-drift 只管表/列/可空/类型/外键） | 独立基建票（写进 3b spec §不做/小票） |
| C2 | L61 + L82 + 终审 `_SHOT_FIELDS` | 三条源码守卫脆弱：enqueue 站点正则要求 kwargs 紧跟 callable；咽喉点子串扫描漏换行/`session.add`；`_SHOT_FIELDS` 只靠注释镜像网关 | 守卫加固批 |
| C3 | L88 / L98 | 框守卫（`test_frame_escape_wiring`）看不见 f-string 拼的闭合标记（`<inbox_message>` 同病） | 守卫加固批 |
| C4 | L62 | workflow 本体 → step 那一跳无测试（`script_shot_generate.py` / `script_shot_video.py`） | 与 ③ 真栈补验同批 |
| C5 | L63 | `run_undo_service.py:163` 直接 update `ScriptShots` 绕过登记口，需明确「不占版本号」 | Revert To vN（回滚/撤销的版本语义一并定） |
| C6 | L74 + L79 + L83 | diff 按 `created_at`（事务开始顺序）把版本映射到 ops 账本行，不是外键；解析失败时 `available:true`；真修 = `run_deliverables` 加账本行 id 列 | Revert To vN（需要每版精确内容才能回写） |
| C7 | L73 裁定 | `generated_media` 的版本链恒为 v1（重新生成 = 新 ref_id）；媒体版本链需要另一把键 | Revert To vN（媒体类 Revert 语义） |
| C8 | L111 | `step_end` 仍 `current ?? ensureStep`，迟到 step_end 绕过坐标查找 | 实时刷新（事件到达顺序） |
| C9 | L112 | `outputDiff.ts` 截断时中段被丢且无面板内标记 | Revert To vN（差异弹层要承载「回到 vN」） |
| C10 | L150 + L149 裁定 | `clearOutputLineageCache` test-only export；同页内 agent 后登记 → 缓存的 `not_registered` 让来源块隐身到刷新 | 实时刷新（`invalidateOutputLineage` 接线到回合完成事件） |
| C11 | L211 | `focusTrajectoryStep` 的 step-only 回退可延到半程 deadline | 实时刷新（深链与流式到达并存时才显） |
| C12 | L105 | `OutputsBlock` 的 `match:()=>true` 让每个详情页多一次 GET；rollup 加 `has_outputs` | 实时刷新（同一条数据面） |
| C13 | 终审 Minor | 六份手写信封解码器（前端各 service 各拆一遍 `ErrorResponse`） | 独立重构票（先收敛到 `apiClient` 再谈） |
| C14 | 勘察 + 验收偏差 3 | `cost_cents` 从未填；来源块无花费 | cost_cents 真正填值 |
| C15 | 验收偏差 1–2 | 卡无「新建 / 修订」字样、无模型与步号 | 与 cost_cents 同批改卡元信息行 |
| C16 | L139 裁定 | 资源信息面板不挂来源块（`generated_media.ref_id` 与 `resources.id` 无桥） | 资源信息面板来源块反查 |
| C17 | L91 + 终审 Important 1 | legacy 路径（issue 无 assignee agent）不转发附件，引用既不校验也不到达；**A7 之后它成了唯一静默吃附件的分支**（`issue_messages_router.py:616` 早于附件解析返回，201 带 `attachments: null`） | 3b：在 `no_open_question` 旁给该分支一个类型化 409（无 agent 的 issue 拒绝引用） |

**桶 D · 直接关闭（已被后续实现取代、或纯风格、或收益不抵改动）**

| # | 来源 | 小票 | 关闭理由 |
|---|---|---|---|
| D1 | L44 | `enqueue` 无 key 时也进 try 只为再 raise | 纯风格；行为正确 |
| D2 | L45 | `CREATE UNIQUE INDEX IF NOT EXISTS` 同名不同定义静默跳过，值得一行注释 | 迁移已在生产执行，改已跑迁移无意义 |
| D3 | L47 | `..._key` 后缀是约束惯例、这里是裸索引 | 改名要新迁移，收益零 |
| D4 | L60 + L75 | 咽喉点守卫按文件封禁 / 已改扫 `insert(RunDeliverables)` | 已被 T3 实现取代 |
| D5 | L64 | 「行落了而 emit 静默失败无日志」只在 recorder 无 `record_event` 时成立 | 无调用方触发 |
| D6 | L80 | per-object 端点暴露旧版 `issue_id`/`run_id` | T3b 裁定裸 id 是坐标不是路由；链接面由 Task 1 精确化 |
| D7 | L81 | issue 无 `identifier` 时 `deep_link` null 契约要写明 | T6b 起前端只读 `deep_link`，不自己拼 |
| D8 | L120 | `_WATCHED_PREFIXES` 写死两个前缀 | 测试基建 YAGNI |
| D9 | L121 | `_get_or_create_dbos_registry()` 在每个测试 setup 都跑 | 未量测；CI 后端 job 6.5 分钟不是瓶颈 |
| D10 | L145 | `OutputProvenance.test` 不断言 console 静默 | 收益零 |
| D11 | L157 | 字节相同测试 docstring 限定「证一个 builder」 | 纯措辞 |

---

### Task 1: 跨议题置空精确化——对血缘链内每个 distinct issue 批量判可见性

**Files:**
- Modify: `backend/app/api/outputs_router.py`（`get_output_lineage` 与 `get_output_diff` 共用的 `_visible_chain` 之后）
- Modify: `backend/app/services/deliverables/lineage_view.py`（`redact_foreign_issue_links` 签名与 docstring；`deep_link` 在 `_VERSION_KEYS` 外的一行注释，A2）
- Modify: `backend/app/repositories/issue_repository.py`（新增 `get_by_ids(ids) -> list[dict]`，一条 `IN` 查询）
- Modify: `backend/app/services/issues/issue_visibility.py`（新增 `visible_issue_ids(ids, auth) -> set[str]`：一次 `get_by_ids` + 对每行 `is_issue_visible`，同一 `team_id` 的 `is_team_member` 结果在本次调用内记忆化）
- Test: `backend/tests/api/test_outputs_router.py`（既有三条置空用例改按新语义 + 新增两条）
- Test: `backend/tests/services/issues/test_issue_visibility.py`（新建或追加：批量判定、记忆化）

**现状**：`redact_foreign_issue_links(versions, gated_issue_id=rows[0].issue_id)` 把 `issue_id ≠` 最新版所在议题的每一版 `issue_key`/`deep_link` 置空——刻意比泄露面宽（同团队兄弟议题也丢链接，T8b 裁定）。

**目标语义**：链内每个 distinct `issue_id`（含门控议题）批量判一次可见性；**可见的保留链接，不可见的置空**。`issue_id` 本身不动（T3b 裁定：裸 snowflake 是坐标不是路由）。无 issue 的版本照旧无链接。

- [ ] **Step 1: 红——路由测试**
  - `test_a_visible_sibling_issue_keeps_its_link`：链 v2 在 issue A（门控）、v1 在同团队 issue B，`visible_issue_ids` 桩回 `{A,B}` → v1 的 `issue_key`/`deep_link` 保留。
  - `test_an_invisible_older_issue_loses_its_link`：桩回 `{A}` → v1 置空、v2 保留、`issue_id` 两版都在。
  - 既有 `test_an_older_version_on_another_issue_loses_its_key_and_link` / `test_every_version_of_the_gated_issue_keeps_its_link` / `test_a_no_issue_chain_redacts_an_older_version_that_has_one` 改成经批量桩表达同一意图（不要删）。
  - `test_visibility_is_checked_once_per_distinct_issue`：链 6 版分属 2 个 issue → 桩被调用恰一次且入参是去重集合。
  - diff 端点走同一条 `_visible_chain`，不需要置空（它不返回 versions）；断言 diff 不调批量判定（一次可见性即够）。
- [ ] **Step 2: 红——可见性单测**：`visible_issue_ids` 对 `[A,B,C]`：A 创建者、B 同团队成员、C 别的团队 → `{A,B}`；`is_team_member` 对同一 team 只调一次；空列表不查库。
- [ ] **Step 3: 绿**：`get_by_ids` 用 ORM `select(...).where(Issues.id.in_(ids))`；`_visible_chain` 返回 rows 后，`get_output_lineage` 收集 `{row.issue_id}`，调 `visible_issue_ids`，把 `redact_foreign_issue_links(versions, visible_issue_ids=...)`（关键字改名；旧的 `gated_issue_id` 参数删除，全仓 grep 无其它调用方）。链长有界（`lineage_for` 本就全量），distinct issue 数不设额外上限但要在 docstring 写明「一条 IN 查询 + 每 team 一次成员判定」。
- [ ] **Step 4: 突变**：把 `visible_issue_ids` 结果改成恒 `{gate}`（旧行为）→ sibling 用例红；把置空分支去掉 → invisible 用例红。记进 PR。
- [ ] **Step 5: A2**：`lineage_view.py` `_VERSION_KEYS` 旁一行注释说明 `deep_link`/`issue_key` 由 `version_of` 另行拼装、不在元组内。
- [ ] **Step 6: lint + 相关目录 + 全量；提交；PR**（标题 `fix(outputs): 血缘跨议题链接按每个议题的可见性置空，不再一刀切（3a 小票 A1）`）。

---

### Task 2: 选中 outputs / assets 页签后删掉编辑器里的 `@query`

**Files:**
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx`（`handleMentionAssetSelect` / `handleMentionOutputSelect`）
- Test: `frontend/components/Todolist/IssueReplyBox.test.tsx`（新增两条 + 修 A4 的 mock 形状）

**现状**：资源页签选中走 `insertResourceRef`（tiptap 插节点，`@query` 被替换）；assets / outputs 页签只**暂存**附件、不碰编辑器，于是 `@que` 留在正文里，发出去的 body 带着孤立 `@`（真机验收 run `348429859900467` 正文只剩 `@` 的病根之一）。`AIChatPanel.tsx:517` 已有 `dropMentionTrigger()`（调编辑器命令 `removeMentionTrigger`）的先例，`ChatInputResourceMention.ts:72` 定义了该命令。

- [ ] **Step 1: 红**：在 `IssueReplyBox.test.tsx` 用既有的 tiptap 挂载方式（文件里已有 `@` 触发 picker 的用例可抄）：输入 `hello @me`，选中 outputs 页签一行 → 断言编辑器文本为 `hello `（尾随空格由 `removeMentionTrigger` 的语义决定，按实际命令结果断言，不要 trim 掩盖）且 `staged-output-chip` 出现；assets 页签同形一条。
- [ ] **Step 2: 绿**：两处 select handler 在 `setStaged*` 成功后、`closeMentionPicker()` 前调 `editorRef.current?.commands.removeMentionTrigger?.()`；抽成一个 `dropMentionTrigger` 回调复用（与 `AIChatPanel` 同名同形——若能把它上提到 `components/chat/` 共享而不改 `AIChatPanel` 行为，就上提；否则各留一份并互相注释指向）。
- [ ] **Step 3: 突变**：注掉 `removeMentionTrigger` 调用 → 两条新用例红。
- [ ] **Step 4: A4**：`IssueReplyBox.test.tsx:156` 附近那份 mock 补 `issue_key` / `deep_link` 两字段（照 `GET /outputs/{kind}/{ref_id}` 真实响应，值用 `"MH-94"` / `"/team/331438215859255/todolist/MH-94?step=1&turn=1"`），并给它加类型（`OutputLineageResponse` 或 service 里导出的类型），让下次缺字段在 tsc 就红。
- [ ] **Step 5: eslint + tsc + 相关文件 + 全量 vitest；提交；PR**（标题 `fix(todolist): 选中产出/资产页签后清掉编辑器里的 @query（3a 小票 A3）`）。

---

### Task 3: 四份 TS kind 枚举 + 引用上限的镜像守卫

**Files:**
- Create: `frontend/components/chat/deliverableKinds.ts`（`export const DELIVERABLE_KINDS = ['generated_media','script_shot','script_scene','script_chapter'] as const; export type DeliverableKind = (typeof DELIVERABLE_KINDS)[number];`；头注释写明镜像 `backend/app/services/deliverables/kinds.py::ALL_KINDS`，顺序也要一致）
- Modify: `frontend/components/chat/outputMentionRows.ts:41`、`frontend/components/chat/OutputMentionList.tsx:50`、`frontend/components/chat/OutputChipBody.tsx:33`、`frontend/components/Todolist/blocks/OutputsBlock.tsx:28`——四张表改 `Record<DeliverableKind, …>`（编译期完备性）；`OutputProvenance.tsx:39` 与 `types.ts:2514` 的散文类型改用 `DeliverableKind`
- Create: `backend/tests/services/deliverables/test_kinds_frontend_mirror.py`（照 `backend/tests/services/assets/test_slots_frontend_mirror.py` 的文本解析法：读 `deliverableKinds.ts` 断言数组 == `ALL_KINDS`（含顺序）；读 `frontend/components/chat/attachmentLimits.ts` 断言 `MAX_OUTPUT_REF_ATTACHMENTS` == `output_ref_resolver.MAX_OUTPUT_REF_ATTACHMENTS`）
- Test: `frontend/components/chat/deliverableKinds.test.ts`（四张表的 `Object.keys` 与 `DELIVERABLE_KINDS` 集合相等——运行期守卫，防止有人把类型改回 `Record<string,…>`）

- [ ] **Step 1: 红**：先写后端镜像测试（TS 文件还不存在 → 红）与前端集合测试。
- [ ] **Step 2: 绿**：建常量文件、改四张表类型、`tsc` 通过。
- [ ] **Step 3: 突变**：TS 数组删一项 → 后端镜像红 + tsc 红（四张表缺键）；把 `attachmentLimits.ts` 改成 9 → 后端镜像红。记进 PR。
- [ ] **Step 4: 两侧 lint/全量；提交；PR**（标题 `test(outputs): 产出 kind 枚举与引用上限加前后端镜像守卫（3a 小票 A5/A6）`）。

---

### Task 4: `POST /issues/{id}/messages` 的乐观 comment 带 attachments

**Files:**
- Modify: `backend/app/api/issue_messages_router.py`（`_optimistic_comment` 加 `attachments` 关键字参数；四个走 `_optimistic_comment` 的返回点传入已解析的附件；`inserted` 行那条 legacy 路径不动）
- Modify（可能）: `backend/app/services/issues/issue_message_mapper.py`（把 `_display_attachments` 提成可复用的公开名，供 router 用同一投影）
- Test: `backend/tests/api/test_issue_messages_router.py` 或既有 `tests/services/deliverables/test_output_ref_attachment.py`（照文件里现有的 POST 用例形状）

**现状**：`_optimistic_comment(issue_id, body, auth)` 只合成 `body`，`attachments` 永远缺席；前端靠本地乐观态画 chip。修的约束（T8a 记的）：**必须先过 `ConversationsAiStore.display_attachments`**（去掉 `data_url` 等重字段），再过 `issue_message_mapper` 的逐条校验投影成 `IssueMessageAttachment`——与 `GET /messages` 读回路**同一形状**（`ref_id` string、`title` 快照）。

- [ ] **Step 1: 红**：POST 带一条 `output_ref`（真实 wire 形状 `{kind:"output_ref", ref_kind:"script_shot", ref_id:"337650953731886", version:1}`）→ 响应 `comment.attachments` 恰一条，含 `title`（解析器盖上的快照）、`ref_id` 是 string；POST 带一个文件附件（`data_url` 形状）→ `comment.attachments` 里**没有** `data_url` 键；POST 无附件 → `comment.attachments` 为 `null`（与老行读回路一致）。Note 路径（`/note` 或 suppressed）与 wake 路径各覆盖一条。
- [ ] **Step 2: 绿**。
- [ ] **Step 3: 突变**：跳过 `display_attachments` 直接塞原始 payload → `data_url` 用例红。
- [ ] **Step 4: 前端核对**：读 `frontend/components/Todolist/IssueDetailView.tsx:490-500`，若乐观行由响应 `comment` 构造则不用改；若由本地态构造且忽略响应 `attachments`，本 Task **不改前端**（记一行到 PR 描述「前端仍用本地态，响应字段留给 3b 消息反馈」）。
- [ ] **Step 5: lint + 相关 + 全量；提交；PR**（标题 `fix(issues): 发帖响应的乐观 comment 带 display 形状的 attachments（3a 小票 A7）`）。

---

**完成账（2026-09-13）**

四张即修票各一个 PR，全部走 worktree（从 `origin/master` 4be9bcf4 建，并发实施）+ TDD + opus 对抗评审 + 突变 + 合并后盯部署链。SDD 工作区 `.superpowers/sdd/2026-09-13-harness-p4-phase3a-followups/`（ledger、briefs、reports、评审包、两份 3b 勘察）。

| 票 | PR | 合并 SHA | 评审轮次 | 部署验证 |
|---|---|---|---|---|
| A7 乐观 comment 带 attachments | #2266 | a11b2383 | 1 轮 approve（Minor ×4 记票） | deploy-gpu success；容器内 `to_display_attachments`；readyz ready |
| A1/A2 跨议题置空按每议题可见性 | #2268 | 135e73b8 | 1 轮 + 修复轮 1（`team_memo` 键 `(user_id, team_id)`，顺带修桩 `is_team_member` 忽略 user_id） | deploy-gpu success；容器内 `visible_issue_ids`；readyz ready |
| A3/A4 三个页签选中后清 `@query` | #2267 | 67e3749a | 1 轮 + 修复轮 1（资源页签同病；实测必须**先删后插**） | version.json 67e3749；真栈走查 MH-94：Outputs 页签选中后正文 `"hello "` + chip 在（PASS） |
| A5 kind 枚举镜像守卫（A6 关闭） | #2269 | c2f535c9 | 1 轮 + 修复轮 1（删重复 cap 守卫；`_ROOT` 锚点先于 skip） | deploy-gpu success + version.json c2f535c + readyz ready |

**终审（opus）**：ship as-is。两条 Important 均为记录项：legacy 无 agent 评论路径在 A7 后成了唯一静默吃附件的分支（→ C17，3b 给类型化 409）；镜像守卫家族在「只改 TS」的 PR 形状下不跑后端 job，`attachmentLimits.ts` 无前端测试（→ B10）。合并前门（#2267/#2269 谁后落地跑 tsc）已执行：61 条既有错误、触碰文件零新增。

**裁定**（全文在 ledger）：四 Task 并发派发；T4 突变替换（读模型放宽）；T1 门控 issue 双判定接受；A6 作废（前提过时）；终审 Minor「撞名」不改、「fixture 值」进桶 B。

**本轮新记的票**：B9、B10、C17 补充（见上表）；T4 的 `display_attachments` docstring、router 896 行；T2「先删后插」无机器守卫；T1 `get_by_ids` 无真 PG 覆盖、双判定正解；T3 sibling 镜像测试缺锚点、`ref_kind` 收窄注释、`deliverableKinds.ts` 位置。
