# deepseek-harness 借鉴落地计划（2026-08-22）

> **✅ 完成账（2026-08-24 回填）**：五波全部实施完毕。执行序 W5→W3→W1→W4→W2，
> 每波开工先验计划假设 —— **五波里四波的计划描述与勘察后的现状不符**，照原文
> 直接动手会做错四次。逐波状态：
>
> | 波 | PR | 实测后的改判 |
> |---|---|---|
> | W5 纪律包 | #1966 | 第 5 项"工具 schema 改白名单"实测**已是白名单**，降级为回归测试钉住；多抓出未登记框 `pending_followups` |
> | W3 压缩 | #1972 #1975 | 勘察时挖出**信封误读六处**（记忆/摘要链恒空写，生产 `ai_session_memory` 唯一行全空实证）；第 2 项已实现→补真覆盖；第 4 项改判为"分母坏了"（12/20 agent 窗口回落默认 28000，静默）；**第 1 项 warm-prefix 未做待拍板**（要求摘要改走对话模型，是成本挪移） |
> | W1 重试 | #1978 #1979 #1981 | 第 3 项"EMPTY_RESPONSE 改可重试"**不能先做**：生产 doubao-lite 22% 空产出、649 token 计费零内容、无任何 transcript 证据分辨"字段没读"还是"真没产出"——先落取证（W1-A）；Retry-After 全仓从未被读过；mig 436 + llm_retry 事件 + policy_key（W1-B）。读侧计数/前端进度/errorChain 未做 |
> | W4 遥测 | #1982 | 改判：不是缺 record 契约，是 **agent 热路径日志哪儿都没去**（InterceptHandler 只挂 8 个三方 logger；agent_runner/fallback/compactor/agent_worker 在 280 万行 application_logs 里 0 条）。桥 `app` 命名空间不桥 root。契约 1-4 项无实证缺口，未做 |
> | W2 Projection | #1988 #1990 | 改判：只做第 5 项（mig 438 意图落库，刷新不丢摘要；#1927 documented boundary 收口）。1-4 项不搬：Postgres 行即 fold 后完整状态（整值规则天然成立），无撕裂实证 |
>
> 附录小刀清单仍开放（env 擦洗 35 处 spawn 等）；`_MODEL_WINDOWS` 缺
> doubao-seed-2-0-lite / nous-qwen3-llm 真实窗口值，补表后 compactor 的
> fallback note 自动消失（那是收口的验收信号）。

> 来源：对 `/media/heygo/program/projects-code/github-repos/deepseek-harness`（MIT，b150a551）的
> 完整侦察评估。结论：**无 evals 体系可搬**；架构纪律与五个机制高度对口 nous 已知短板。
> 用户拍板顺序：**W5 → W3 → W1 → W4 → W2**（性价比降序的执行序）。
> 评估全文要点存于 memory：`reference-deepseek-harness-assessment.md`（新 session 同项目自动可见）。
> 执行纪律沿用本仓 SDD 惯例：每波独立 worktree 独立 PR；opus 实施+独立审查+突变复做；
> 可证伪测试；报告文件为权威通道；合并后真栈验收。

## 通用约束

- dsh 是 TypeScript，我们是 Python/FastAPI + React——**一律重写设计，不复制代码**。
  借鉴保留 MIT 版权声明不是必需（无代码复制），但 PR body 注明设计来源。
- 每波开工前先读 dsh 对应锚点原文（下面逐波列出），**以 dsh 仓库现码为准**，
  本计划里的行号可能随其更新漂移。
- 与既有拍板不冲突：**agent 链无 fallback、失败必提示**仍是铁律——W1 的"重试"指
  provider 层短时故障重试（同一模型），不是跨模型 fallback。

---

## W5 零代码纪律包（成本 S，先做——当天可完成）

四件纯规则/纯文档 + 一件小代码：

1. **defensive-patterns 7 条译入 CLAUDE.md**（新开「防御模式」节）。
   源：`dsh/docs/defensive-patterns.md`（33 行）。重点译准：
   正交结果独立上报（timedOut/signal/exitCode 各自暴露）；公共契约两侧都要遵守；
   **异步状态不是同步状态**（不要把整体 idle 当某条消息的结果；等待的转移可能永不发生，
   必须显式处理"无事可等"分支）；Dispose 必须到达静止（先摘监听器再 kill 再 await done）；
   分发器容纳回调异常；env 擦洗（spawn 丢 *KEY*/*SECRET*/*TOKEN*/*PASSWORD*）；
   0700 目录 + 随机名 + 'wx' 0600 独占写。
2. **Model Experience 三问成为 agent/prompt 模块 README 的强制段**：
   What the model sees / Token effect / KV Cache effect + Known Limitations。
   源：`dsh/docs/cookbook/adding-a-package.md` §4。落点：backend/app/services/ai/ 下
   现有 prompt/composer/attachment 相关模块 README 先补齐 3-5 个作示范，
   并把要求写进 CLAUDE.md「开发指南」。
3. **提示词快照纪律**："一个场景 pin 全文、其余 tokenize"。
   源：`dsh/docs/testing.md` pinned-header 段。落点：给 prompt_composer 的系统提示词
   加一条逐字快照测试（唯一 pin 全文场景）+ 现有断言改 tokenize 化（若有重复 pin）。
4. **【小代码】用户内容进 prompt 的框标记转义**（安全项，本波唯一动代码）：
   拼进系统提示词/上下文的用户可控文本（剧本正文、文件名、AGENTS 类指令文件）中出现
   字面 `</system-reminder>`、`<available_skills>` 等框闭合标记时转义。
   源：`dsh/packages/context/agent-instructions/README.md` 末段。
   侦察点：prompt_composer 的 `<available_resources>` 属性值（文件名！）、
   memory/graph facts 注入、skill 内容注入。可证伪测试：构造带 `</available_resources>`
   的文件名 → 渲染后不闭合框。
5. 顺手：工具 schema 显式 allowlist 检查（若现状是黑名单/直通，改白名单）。
   源：`dsh/docs/subsystems/tools.md:11`。

验收：CLAUDE.md 两节落地；3+ README 示范；快照测试 pin 策略生效；
转义突变（去掉转义 → 注入测试红）。

## W3 压缩省钱三连（成本 S-M）

落点：`backend/app/agent_framework/context_compactor.py`、`tool_result_pruner.py`、
`services/ai/llm/` 摘要调用处。源：`dsh/packages/compaction/compaction-basic/README.md` 全文 +
`dsh/docs/subsystems/compaction.md`。

1. **摘要调用复用 warm prefix cache**：压缩摘要请求 = 逐字重放本会话自己的
   system prompt + tools + 被压缩区间消息，压缩指令作为**最后一条 user message** 追加
   ——而不是另造一个独立 prompt（那会让 provider 前缀缓存全失效）。
   注意归因：如需标记压缩流量，用请求头（dsh 用 x-*-compact: 1），不碰模型可见 body。
2. **压缩前先跑免模型 pruner 并重新计量**：tool_result_pruner 先裁，压力回落到阈值内
   就**跳过整次 LLM 摘要**。
3. **摘要产物只取 text**：剔除 reasoning（泄漏私有推理）与 tool call（产生孤儿调用）；
   收敛保证：拒绝"不比源短"的摘要，重试上限后 raise。
4. **阈值按 (provider, model) 覆盖**（modelPolicies）：多 provider 下不同 context 窗口
   不能共用一个 thresholdRatio。配置形态参考 dsh：thresholdRatio 0.8 / retainRatio 0.16。
5. 压缩过程事件化（start/summary/end 三事件，孤儿 start 可检测）——若 agent_runs
   事件流已可挂，顺手；重则拆到 W1 一起。

验收：真实长会话对照（压缩前后 token 计量 + provider cache 命中差异若可观测）；
"pruner 已解压则零 LLM 调用"可证伪；摘要含 tool-call 的构造用例被剔除。

## W1 重试事件化 + provider 归属策略（成本 M）

落点：`backend/app/services/ai/llm/llm_retry_middleware.py`、`llm_fallback_chain.py`、
agent_runs 事件流。源：`dsh/packages/llm/llm-retry/src/index.ts`、
`dsh/packages/llm/llm/src/retry-policy.ts`、`error.ts`、`adapter-failure.ts`。

1. 重试状态**从内存计数器改为持久事件**：每次重试前落"llm/retry"事件（retryId、provider、
   policyKey、retry/maxRetries、delayMs、failure 摘要），等待完成再落 "retry-started"；
   重试次数从事件流 `findLast((turn,step,provider,policyKey))` 数出——进程重启不丢计数。
2. **策略归属 provider 并在使用时快照**；policyKey = 策略规范化序列化，策略一变计数自然重开。
3. 分类修正：**EMPTY_RESPONSE（200 空产出）归为可重试**（当前 llm_retry_middleware
   明写不重试——dsh 的论证：没产出任何持久内容所以重试安全，静默空回复比重试贵）；
   `Retry-After > maxDelay` 语义：normal 模式放弃并透出，不被 provider 一个超长
   Retry-After 挂死。
4. UI 消费：任务中心/agent 卡可显示"第 2/5 次重试，等待 3.2s"（吃事件流，前端小改）。
5. errorChain 式完整 cause 链渲染进错误详情（治 fetch failed 掩盖真因）。
⚠️ 与"无 fallback"拍板的边界写进实现注释：这里全部是**同模型短时重试**。

验收：重启进程后重试计数延续（事件流实证）；EMPTY_RESPONSE 重试可证伪；
Retry-After 超限放弃有测试；UI 能渲染重试进度。

## W4 遥测最小契约（成本 M）

落点：`backend/app/agent_framework/telemetry.py` + application_logs 桥接。
源：`dsh/docs/subsystems/session-telemetry.md`。

1. 定义 record 契约：`{channel: ledger|ops, time, severity, attributes, body}`；
   **ledger = agent_runs/task_tracking 事件流的一一镜像（零改造覆盖）；
   ops = 无日志归宿的运营信号（进程启停、agent-error），故意无 seq 身份**。
2. `emit()` 非阻塞入队；异常被容纳绝不回传业务路径。
3. severity 在捕获点预映射（isError → error），接收端零配置告警。
4. 脱敏 waterfall：seam 零内置规则；只改导出副本，源日志不动；异常监听器 fail-closed 扣留。
5. **47 模块 stdlib logging 的解法**：不逐个改 logger——ledger 镜像吃掉大头，
   剩余真正的 ops 信号点名接入（预计 <10 处）；顺手评估 root-level intercept 的风险。
6. 接收端按 (session_id, seq) 去重——同时为 legacy 端点 dedup 提供正解路径（记关联不实施）。

验收：抽 3 个此前不入库的模块信号在 application_logs/新表可查；emit 阻塞注入测试
（sink 卡死不影响业务路径）；脱敏规则突变可红。

## W2 服务端 Session Projection（成本 M-L，最后做——架构收益最大改动也最大）

落点：任务中心/后台任务推送层（TaskManagerContext 的前端 fold 逻辑后移到服务端）。
源：`dsh/packages/session/session-projection/src/index.ts` +
`dsh/packages/host/apiproxy/README.md` §29/41/43。

1. 后端 projection 注册表：`{key, init, apply(state,event), stateVersion}`，
   一次订阅事件流 fold 所有 unit。
2. **三条硬契约一条不能少**：同引用即无事发生（apply 对无关事件返回同一引用）；
   整值事件规则（**推完整状态，绝不推裸 delta**）；apply/view 同步（asOfSeq 才是一致切面）。
3. 传输：history 尾页带 projections 基线（含 asOfSeq），变更推整值帧；
   前端只做 higher-seq-wins 的 KV 存储；断线重连 = 重拉基线，天然自愈。
4. 后台任务同款：jobs 变更广播整份快照（清空也发 []）。
5. 直接消灭：转录→摘要 follow-up 的"前端内存态、刷新即丢"（登记表后移服务端投影）。

验收：刷新页面后 follow-up 仍触发（内存态消失的实证）；断线重连状态一致；
并发变更无撕裂（asOfSeq 对照）。

---

## 附：随手小刀清单（可夹在任意波顺手做，各 ≤半天）

- repeat-tool-reminder 两规则：未跟踪调用对链透明（记账工具不洗白循环）+ 被拒调用也计数
- 工具超时按 signal 判定（非结果形状）；多 wrapper 注册顺序语义写明
- skill 目录快照 complete 位：incomplete 永不缓存、保留 last-good
- 取消工具调用为未启动的 call 补写合成 error 结果（call/result 配对、replay 合法）
- spill/临时文件 0700 + 随机名 + 'wx' 0600；保存失败 best-effort 保留内联，不把成功变 isError
- **子进程环境擦洗**（W5 实测发现，规则已入 CLAUDE.md「防御模式」，代码未动）：
  `backend/app` 下 35 处 `create_subprocess_exec` / `subprocess.run` **零处擦洗**——
  仅 2 处传 `env=`，传的还是 `os.environ.copy()` / `{**os.environ, ...}`。
  区分两类再动手：`services/workforce/isolated_runner.py` 跑的是我们自己的
  Python 子进程，**需要**凭证，全量继承是对的；其余（yt-dlp、ffmpeg/ffprobe、
  node、jimeng/codex CLI）不需要 `SUPABASE_SERVICE_ROLE_KEY` 和各家 LLM API key，
  而 yt-dlp 处理的正是攻击者可控的 URL。
  ⚠️ 有真实回归风险：`PATH`、代理变量、`FFMPEG_PATH` 都在环境里，逐点确认后
  再改，且需真栈验收（下载/转码/缩略图三条链都要走一遍）。

## 待另行拍板（不在本计划）

- DB max_connections 100→200（基础设施底噪 60-70，部署窗即顶满；需重启 nous-db）
- script_ai 半接线统一、team 级 override 后台生效口径
- legacy platform_id 端点 dedup 收口
