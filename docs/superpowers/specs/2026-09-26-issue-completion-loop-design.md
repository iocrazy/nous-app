# issue「做完」闭环设计（完成标准 · 证据核验 · 受闸的自动关单）

- 日期：2026-09-26
- 状态：已评审通过（2026-09-26）；实施计划 `docs/superpowers/plans/2026-09-26-issue-completion-loop.md`。写计划时核对代码发现四处偏离，PR-1 评审又加一处（verifier 自开 root run，§5.3 / §8），均已就地修订并标 **[修订]**。
- 侦察：`.superpowers/sdd/2026-09-26-done-loop-recon.md`（锚点基于 origin/master d6737c8c3）、`.superpowers/sdd/2026-09-23-nous-issue-agent-recon.md`
- 第一个场景：剧本链（`script_ai` agent：大纲 / 扩写 / 分镜 / 出图）。生产 108 个 issue 里 36 个指派给 `script_ai`，其余全是验收探针，这是唯一的真实业务。

## 1. 背景

今天 `FinishIssue(outcome=completed)` 就是「模型说完了」。`route_finish_outcome` 把它路由到 `done`（`issue_agent_auto_close` 开时）或 `in_review`（关时，生产现状）。没有完成标准、没有核验、没有证据要求。生产实况（2026-09-26）：28 个 in_review 里 23 个声明 completed，**只有 1 个**有任何 `run_deliverables`；MH-74 声称「3-beat 大纲已生成」而零产出、且 3 beat 不符合 skill 要求的三幕 11–18 beat；14 个 done 没有一个是基于核对过的产出。`auto_close` 之所以一直关着，是因为开了就等于让模型自己盖章。

目标：让「完成」成为一个可核验的事实，使 `auto_close` 可以安全打开，并让人在 in_review 看到「按什么标准、核了什么、结论是什么」。

## 2. 目标与非目标

**目标**
1. 每个进入执行的 issue 都有一份记录在案的完成标准（人写或 agent 先提出）。
2. agent 声明 completed 后，由**独立于 agent 的核验**给出 pass / fail / unverified，附带未满足项。
3. fail 时 agent 在同一 issue 内得到反馈并再做一轮，有次数上限。
4. 只有 pass 才允许 auto_close 到 done；unverified 与 fail 永不自动关单。
5. 不改任何 `@DBOS.workflow` 体、不加 step；新增逻辑全部在既有 step 体内、工具 handler 与新表列上。

**非目标**
- 不做「等异步出图落地再核 image_url」（需要独立 workflow，第二阶段，见 §12）。
- 不做多 verifier / 多轮辩论 / 人类评分训练数据。
- 不改 in_review 的人工路径：人仍然可以把任何状态点成 done。
- 不改 FinishIssue 的三个结局枚举（`completed / needs_input / continue`）。

## 3. 术语

- **标准（criteria）**：`issues.acceptance_criteria`，自然语言，≤4000 字，来源 `user` 或 `agent`。
- **证据包（evidence bundle）**：本 issue 全部 run 登记的 `run_deliverables` 及其对应表行的关键字段 + 本轮最终 assistant 正文。
- **谓词（predicate）**：不花钱的确定性检查，输入证据包，输出 `satisfied / violated / not_applicable` + 事实。
- **判定（verdict）**：`pass | fail | unverified`，附 `unmet[]`、`facts[]`、`attempt`、`checked_at`、`verifier_run_id`。
- **核验回合（verification）**：谓词 + 一次独立 LLM 判定，跑在当前 issue run 所在的 step 内。

## 4. 架构总览

```
execute_issue (workflow body — 不动)
  └─ run_issue_agent_step (step)
       └─ run_issue_agent (step 体内)
            ├─ run_session_turn(...)                 # 今天已有
            ├─ resolve_turn_outcome(...)             # 今天已有 → outcome
            ├─ [新] if outcome == completed and not cancelled:
            │       verdict = verify_completion(issue, run, content)   # 谓词 + LLM
            │       merge_execution_state(verification=verdict, verify_attempts+=1)
            │       if verdict.fail and attempts <= MAX: outcome = "continue"
            └─ return {content, outcome, reason, verification}
  └─ route_finish_outcome (body — 不动)：
       completed → done if auto_close and verified_pass else in_review
       continue  → 既有续跑分支（ISSUE_MAX_CONTINUATIONS）
```

`respond_to_issue_reply` 的 `run_issue_reply_step` 走同一个 `verify_completion` seam（它今天就镜像了 `run_issue_agent` 的 FinishIssue 提取，见 recon §1）。

为什么选这个位置（三选一的结论）：
- FinishIssue handler 内同步判：此时本轮正文未写完，纯文本任务（大纲、扩写）无法判。
- 新 `verify_issue` workflow：deferred dispatch 在 `route_finish_outcome` 之前 drain，verifier 起跑时 issue 可能还是 in_progress，要处理 CAS、重派、版本钉；只在需要等异步产物时才值得（§12）。
- **turn 结束后、step 体内**：正文与产出都齐，被拒改 `continue` 走既有续跑分支，零 body 改动、零竞态。

## 5. 组件

### 5.1 完成标准

**数据**：`issues.acceptance_criteria TEXT NULL`（CHECK `char_length <= 4000`）、`issues.acceptance_criteria_source TEXT NULL CHECK IN ('user','agent')`。**[修订]** mig 170 的 trigger 是「不可变列黑名单」而非白名单（逐列比对 `id / execution_state / origin_*` 等），新列不在其上，creator / assignee 默认可改，**不改 trigger**；真库集成测试钉住「非 service_role 更新新列不被拦」。

**写入路径**
1. 人：`POST /issues`、`PATCH /issues/{id}`（`IssueCreate` / `IssueUpdate` 加 `acceptance_criteria`；PATCH 写入时 `source='user'`；`clear_acceptance_criteria: bool` 仿 `clear_budget`）。
2. agent：新工具 `SetAcceptanceCriteria(criteria: str)`，只在 `trigger ∈ {issue_dispatch, issue_dispatch_auto, issue_reply}` 注入（与 FinishIssue 同一注入点 `ai_library_chat_service.py:1324-1341`）。规则：
   - issue 已有 `source='user'` 的标准 → 工具返回 `{"error": "criteria_locked", "criteria": <现值>}`，不写。
   - 无标准或 `source='agent'` → 写列、`source='agent'`，并写一条 `issue_messages(kind='comment', author_agent_id=<assignee>, meta={"kind":"criteria_proposed","source":"agent"})`，正文为标准原文，人可在详情页改（改后 source 变 user）。**[修订]** 用 `comment` 而非 `system_status`：后者的 CHECK 要求 `from_status` 或 `to_status` 非空，它是状态变更行。
   - 每个 run 最多成功调用一次；第二次返回 `{"error":"criteria_already_set"}`。
3. `FINISH_ISSUE_INSTRUCTION` 追加一句：没有标准时，开始工作前必须先调 `SetAcceptanceCriteria` 把可核验的完成标准写下来；标准要具体到可以核对的产出（几个场景、几个 shot、是否出图、字数范围）。这一改动会进唯一全文 pin，刷新并读 diff。

**注入到模型**：`_build_user_message`（`issue_agent_executor.py:40-56`）在 `Details:` 之后加 `Acceptance criteria (source=user|agent):\n{criteria}`；与 title/description 同源同信任级，不进系统前缀、不碰缓存、不需要框。

### 5.2 证据包与谓词

新模块 `app/services/issues/verification/`：
- `evidence.py::build_evidence_bundle(issue_id, run_id, final_text) -> EvidenceBundle`（frozen dataclass）：
  - `deliverables`: 本 issue 所有 run（`agent_runs.issue_id = ?`）登记的 `run_deliverables`（kind, ref_id, version, run_id, created_at），按 kind 分组。
  - `shots`: 对 `script_shot` 的 ref_id 读 `script_shots`（scene_id, shot_number, shot_type, camera_angle, description, image_url, status）。
  - `scenes`: 对 `script_scene` 的 ref_id 读 `script_scenes`（scene_number, content_version, omitted_at）；另读 scope 内全部未 omit 的 scene 及各自 shot 数（用于「每个 scene ≥1 shot」）。
  - `media`: `generated_media` 登记数与状态。
  - `final_text`: 本轮最终 assistant 正文（经 `neutralize_external_text`；上限 12k 字符，超出截尾并标记 `truncated`）。
  - `prior_texts`: 本 issue 前几轮的 assistant 最终正文（最多 3 条、各 4k），供纯文本任务判「累计产出」。
- `predicates.py`：每个谓词是纯函数 `(criteria_text, bundle) -> PredicateResult(name, status, facts)`，`status ∈ {satisfied, violated, not_applicable}`。第一版四条，触发词按标准文本粗匹配（中英文关键词表，不做 NLP）：
  1. `shots_exist`：标准提到分镜/shot/镜头 → 本 issue 登记的 `script_shot` ≥ 1，且每个 shot 的 `description`、`shot_type`、`camera_angle` 非空。
  2. `scenes_covered`：标准提到「每个场景/each scene」 → scope 内未 omit 的 scene 每个 ≥1 shot。
  3. `scene_rewritten`：标准提到扩写/改写/rewrite/expand → 本 issue 有 `script_scene` 登记（**[修订]** 写入咽喉点的登记即是改写事实，不再比 `content_version`）。
  4. `image_dispatched`：标准提到出图/image/生成图 → 本 issue 有 `generated_media` 登记（只核「已派发」，因为图异步落地；`image_url` 第二阶段）。
  - 没有任何谓词命中（纯文本任务）→ 全部 `not_applicable`，交给 LLM 判定。
  - 任一 `violated` → 直接 `fail`，不调 LLM（省钱、且这类错误是硬错误）。

### 5.3 独立 LLM 判定

`verifier.py::judge(criteria, bundle, predicate_results) -> Verdict`：
- 独立请求，**[修订]** 复用当前 issue session 绑定的模型与凭证（`forced_finish_declaration._resolve_agent_and_adapter`，单 adapter、无 fallback 链），不引入 `system_settings.verifier_model`：BYOK / 平台凭证与计费归因天然一致，少一个要维护的设置项。
- **输入只有**：标准、谓词事实、`final_text` / `prior_texts`（已 neutralize）。**不给**：agent 的系统提示、推理、工具轨迹、FinishIssue 的 reason、对话历史。reason 只作为「agent 声称」显示给人，不进判定。
- 系统提示词是固定字面量（进 prompts README 三问；独立请求、无共享前缀）。要求输出严格 JSON：`{"verdict":"pass"|"fail","unmet":[{"criterion":"…","why":"…"}],"confidence":0..1}`；解析失败一次重试；仍失败 → `unverified(verifier_bad_output)`。
- 超时 30 s（`VERIFIER_TIMEOUT_S`），`max_tokens` 400，temperature 0。
- 计费：**[修订]** verifier 自开 root run（与 forced declare 同形）：issue 根 run 在 verifier 起跑前已结账，子 run 挂树永远拿到 already 而不扣费；血缘用 metadata.issue_run_id。`RunRecorder(trigger=f"{trigger}_verify", issue_id=…, metadata={"verifier": True, "issue_run_id": <当前 issue run>})`，继承 `attribution` 与 `credential_origin`；BYOK 免扣照常成立。~~原稿：`parent_run_id=<当前 issue run>`，花费进当前 run 的树~~

### 5.4 结局路由（全部在 step 内）

`run_issue_agent` / `run_issue_reply_step` 在 `resolve_turn_outcome` 之后：

```
if outcome == "completed" and stop_reason != "cancelled":
    attempts = execution_state.verify_attempts or 0
    verdict = await verify_completion(...)          # 谓词 + LLM；异常/超时 → unverified
    merge_execution_state(issue_id, verification=verdict.as_dict(), verify_attempts=attempts+1)
    if verdict == fail and attempts + 1 <= VERIFY_MAX_ATTEMPTS (=2):
        outcome, reason = "continue", f"verifier_rejected: {首条 unmet}"
    # fail 且次数用尽 / unverified / pass：outcome 保持 completed
```

**[修订]** `load_auto_close_flag` 在派发时、回合之前就被调用并 checkpoint，不能承载「pass 才 done」。改为：两个 step 的结果字典多一个 `verification` 键；`route_finish_outcome`（普通 async 函数，不是 workflow 体也不是 step）新增可选关键字 `verification`，completed 分支变成 `"done" if auto_close and verification.verdict == "pass" else "in_review"`（仍是同一个 `set_status` 调用，step 顺序不变）。三个调用点透传：`_run_reply_turns`（在源码哈希守卫里，同 PR 更新哈希并说明）与 `_run_dispatch_with_continuation` 的两处。

`agent_outcome` 不变（仍是 completed / continue…），新增 `execution_state.verification` 供 UI 与 SQL 读。`continue` 的续跑用户消息（step 内，`CONTINUATION_NUDGE` 之后）在 `verification.verdict == fail`、`retry == true` 且 `consumed_at` 为空时追加：

```
<verifier_feedback attempt="1" of="2">
The completion check rejected your last declaration. Unmet:
- <criterion>: <why>
Fix these and call FinishIssue again.
</verifier_feedback>
```

框登记进 `OWNED_FRAMES`，正文 `escape_frame_body`；消费后写 `consumed_at`（**[修订]** 组消息时新 run 尚不存在，用时间戳而非 run id），同一 verdict 不重复注入。

### 5.5 数据模型变更

| 变更 | 位置 |
|---|---|
| `issues.acceptance_criteria`、`acceptance_criteria_source` | mig 5xx（编号推 PR 前对）；两向 drift 门禁；ORM `models/issues.py` |
| mig 170 trigger 不改（黑名单不含新列，**[修订]**） | 真库测试钉住非 service_role 可写 |
| `execution_state.verification`、`verify_attempts` | jsonb 键，经 `merge_execution_state`，无迁移 |
| `issue_messages` verdict 消息 | `kind='comment'` + `author_agent_id`，`meta.kind='verdict'`（**[修订]**，同 5.1）；不改 CHECK |
| `agent_runs` verifier 子 run | 复用现有列：`parent_run_id`、`trigger='issue_dispatch_verify'` |
| ~~`system_settings.verifier_model`~~ | **[修订]** 不引入；verifier 用 session 自己的模型 |

### 5.6 API 与前端

- `IssueBase/IssueCreate/IssueUpdate/IssueRead` 加 `acceptance_criteria`、`acceptance_criteria_source`、`verification`（只读）。同批重导出 `backend/openapi.json` 与 `frontend/types/api.generated.d.ts`（契约门禁零容忍）。
- 前端两个新块（块注册表「一个文件 + 一行」）：
  - `CriteriaBlock`（context 区，挨着 StageBriefBlock）：显示标准与来源，可编辑（PATCH），agent 提出的标准带「Proposed by agent」标签。
  - `VerdictBlock`（挨着 StatusBlock）：`Verified` / `Rejected (attempt n/2)` / `Unverified: <原因>`，展开看 unmet 与谓词事实；agent 的 FinishIssue reason 单独一行标「Agent's claim」。
- UI 文案英文，i18n key。

### 5.7 设置

- `issue_agent_auto_close`（现有，生产 false）：语义改为「仅在核验 pass 时自动关单」。
- 新 `issue_verification_enabled`（默认 true）：关掉即回到今天的行为（不跑核验、不写 verdict）；这是回滚开关，不需要重新部署。

## 6. 数据流

1. 人建 issue（可写标准）→ dispatch → `run_issue_agent_step`。
2. step 内组装 user 消息（含标准段）；无标准时 agent 先调 `SetAcceptanceCriteria` → 列 + 消息。
3. agent 工作、调 FinishIssue(completed)、写正文 → `run_session_turn` 返回。
4. `resolve_turn_outcome` = completed → `verify_completion`：证据包 → 谓词 → （必要时）LLM 判定 → verdict 落 `execution_state` + 一条 `system_status` verdict 消息。
5. fail 且未超次 → outcome=continue → body 既有续跑分支起下一轮，user 消息带 `<verifier_feedback>`。
6. pass → completed → `load_auto_close_flag` 为真才 done，否则 in_review + Verified。
7. unverified / fail 超次 → in_review + 相应卡片；永不 done。

## 7. 错误处理

- verifier 超时、模型不可用、JSON 不合法、证据包读取异常 → `unverified(<typed reason>)`，按今天行为进 in_review；**不许**当 pass（探针够不着 ≠ 通过）。日志 WARNING 带 issue_id / run_id / reason。
- 谓词内部异常 → 该谓词 `not_applicable` + 事实里记 `error`，不阻断其它谓词，不把整体判成 fail。
- `SetAcceptanceCriteria` 写库失败 → 工具返回类型化错误，agent 可继续（标准缺失时 verifier 走通用模式）。
- 取消：`stop_reason == cancelled` 先于 verifier（沿用 `turn_outcome.py:107-108` 的降级），被取消的 run 不送审、不扣 verifier 费用。
- 无标准的老 issue：只跑通用谓词（「agent 正文里声称写了 shot/scene/图，有没有对应 deliverable」——用 `final_text` 关键词触发同一组谓词），不跑 LLM；verdict 标 `criteria_missing`。

## 8. 计费与配额

- **[修订]** verifier 自开 root run（与 forced declare 同形）：issue 根 run 在 verifier 起跑前已结账，子 run 挂树永远拿到 already 而不扣费；血缘用 metadata.issue_run_id。它是自己的 root，按 root 单独结账扣分；BYOK 来源继承（BYOK 免扣）。~~原稿：verifier 子 run 挂当前 run 树，不新开 root，不单独扣分~~
- 每次 completed 声明最多一次 LLM 判定；`VERIFY_MAX_ATTEMPTS=2` → 每 issue 最多 3 次判定、2 次续跑（续跑本身受 `ISSUE_MAX_CONTINUATIONS=2` 上限，verifier 驳回消耗的是同一个上限）。
- 预算 hook（80% 警告 / 100% 停机）对 verifier 子 run 同样生效；预算停机的 completed（`budget_wrap_up`）不送审，直接按今天路由。

## 9. 安全与隔离

- 自证隔离：verifier 看不到 agent 的推理、工具轨迹与 reason（DeepSeek auto-review 规则）。
- 所有进 verifier 提示词的用户/模型文本经 `neutralize_external_text`；`<verifier_feedback>` 进 `OWNED_FRAMES`，正文 `escape_frame_body`；wiring 守卫会拒绝未登记的框。
- `SetAcceptanceCriteria` 的输入限长 4000、经 `escape_frame_body` 后才进 user 消息；人写的标准 agent 不能覆盖。
- verdict 消息对用户可见但不可编辑；不暴露 verifier 提示词。

## 10. 可观测性

- `agent_run_transcript_events` 新事件 `verification`（payload = verdict 摘要，不含正文；事件类型 CHECK 若存在则同批改）。
- `execution_state.verification` 可 SQL：`SELECT execution_state->'verification'->>'verdict', count(*) FROM issues WHERE …`。
- Runs 视图 folds 加 `verification` 字段（`run_projection` + `folds/`），前端 VerdictBlock 读它；若 `RunProjection` 是响应模型则重导出 OpenAPI。
- 日报 / 完成率指标（`done` 中 verified 的比例）留给后续。

## 11. 测试策略

**RED 先行**
1. `SetAcceptanceCriteria`：无标准 → 写列 + 消息；`source=user` → `criteria_locked`；同 run 二次 → `criteria_already_set`；超长 → 类型化错误。
2. user 消息含 `Acceptance criteria (source=…)` 段；无标准时不含该段；标准里含 `</verifier_feedback>` 等闭合标记不可伪造。
3. 四条谓词各自 satisfied / violated / not_applicable 三态 + 触发词表；`violated` 短路不调 LLM（spy 零调用）。
4. LLM 判定：合法 JSON → pass/fail；非法 JSON 重试一次仍失败 → `unverified(verifier_bad_output)`；超时 → `unverified(verifier_timeout)`；模型不可用 → `unverified(verifier_unavailable)`；**输入里不含** agent reason / 工具轨迹（断言提示词文本）。
5. 结局路由（用「adapter 无 `stream`」fixture 走缓冲分支）：fail 第 1 次 → outcome continue + `verify_attempts=1` + 下一轮 user 消息含反馈框；fail 第 3 次 → completed + in_review；`route_finish_outcome(verification=)`：pass + auto_close on → done；pass + auto_close off → in_review；unverified / fail / 无 verdict + auto_close on → in_review；cancelled 不送审；budget_wrap_up 不送审。
6. reply 路径（`run_issue_reply_step`）同 5 的 pass/fail 两例。
7. verifier 子 run：`parent_run_id` = 当前 run、`root_run_id` 同树、`attribution`/`credential_origin` 继承（真库集成，drift DB）。
8. 迁移：drift 两向零容忍；mig 170 白名单含新列（真库：UPDATE 新列不被 trigger 拦）。
9. OpenAPI：导出 diff 与提交一致（契约门禁）。
10. 源码守卫：`verify_completion` 与新 helper 不是 DBOS step；`execute_issue` / `respond_to_issue_reply` 源码哈希不变（沿用 #2453 的守卫）。
11. 前端：两个块的渲染测试（verified / rejected / unverified 三态；criteria 编辑 PATCH）。
12. 提示词：`FINISH_ISSUE_INSTRUCTION` 改动 → 刷新唯一全文 pin 并读 diff；verifier 提示词与反馈框写进 prompts README 三问；README 守卫绿。

**真栈验收**（部署后，debug 账号，一个真实剧本 issue）
- A：标准写「为场景 1–2 各建 2 个 shot」，agent 只回文本就声明 completed → verdict fail（`shots_exist` violated）、issue 续跑一轮、反馈框出现在下一轮 user 消息（transcript 可见）。
- B：agent 补建 shot 后再声明 → pass；`auto_close=false` → in_review + Verified；打开 `auto_close` 重跑一例 → done。
- C：把该 agent 的 model 改成一个不存在的目录行 → `unverified(verifier_unavailable)`，in_review，`auto_close` 开也不 done。
- D：SQL 核对：verifier 子 run 的 `parent_run_id` 指向 issue run、root 花费包含它；`execution_state.verification.attempt` 单调。

## 12. 分期与留票

- **第二阶段**：异步产物落地核验（`image_url IS NOT NULL AND status='done'`）需独立 `verify_issue` workflow（deferred dispatch + CAS on in_review），或由出图 workflow 完成时回写 verdict。
- 大纲 / 扩写这类纯文本任务的谓词化（数 `<h3>Beat` 与三幕结构）留给 skill 自己声明「产出形状」。
- verifier 多模型交叉、人工评分回流、完成率日报。
- forced declare 自开 root 单独扣分（既有）不在本设计内改。

## 13. 风险

| 风险 | 缓解 |
|---|---|
| verifier 误拒导致多花 1–2 轮 | 上限 2；unmet 必须具体；`issue_verification_enabled` 一键关 |
| verifier 误过（纯文本任务只能靠 LLM） | `auto_close` 仍默认关；in_review 保留人工路径；谓词优先 |
| `route_finish_outcome` 多一个关键字，`_run_reply_turns` 哈希要更新 | 只加关键字、不加 step、不改顺序；PR 描述写明哈希更新原因 |
| 迁移与部署无顺序保证 | 新列可空、代码读不到列时按无标准处理（`getattr` 兜底），迁移先行或后行都安全 |
| 提示词字面量变化影响缓存 | INSTRUCTION 在 CACHE_BOUNDARY 之后，不影响稳定前缀 |
