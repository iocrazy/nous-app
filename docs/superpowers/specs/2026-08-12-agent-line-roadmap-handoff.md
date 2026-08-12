# Agent 体系改进线 — 交接与待办路线图（2026-08-12）

> 新 session 启动用：先读本文件 + memory 的 `project-*-shipped` 系列，再动手。
> 已档案化的事实不要重新侦察。

## 0. 已闭环的四个立项（全部合并上线并验收）

| 立项 | PR | migration | 一句话 |
|------|----|-----------|--------|
| Agent Run 撤销 | #1767(+#1768 撞号治理) | **415**_agent_run_undo | shot 记账/归属 + run 级逆操作 + Undo 按钮；生产 E2E 六项全过 |
| 权限页梳理 | #1783 | **420**_agent_permission_audits | 三柱重组/假开关下架/变更审计/拦截可见化；审计链生产实测过 |
| Provider 容错 P1 | #1794 | **423**_preset_doubao_fallback（no-op，见 memory） | 类型化错误面(503 provider_rate_limit 等)/request-id 贯通；pro 探针实证 |
| 批量 LLM fallback | #1810 | 无 | summarize/visual-analyze 接链,真异常上抛,record_ai_error_code 落库 |

详情/教训/backlog 各见 memory：`project-agent-run-undo-shipped` / `project-agent-permissions-overhaul-shipped` / `project-provider-resilience-shipped` / `project-batch-llm-fallback-shipped`。

## 1. 工作纪律（本线已验证的流程）

- 每个立项走 superpowers 全流程：brainstorming → spec（用户审）→ writing-plans →
  subagent-driven-development → 终审（fable）→ PR。用户偏好：给带推荐的选项。
- **新立项必须新建 worktree**：主仓库执行 `bash scripts/worktree-manager.sh create feat/<name>`，
  然后 `git reset --hard origin/master` 对齐（create 基于本地 master，会落后）。
- `/ship` skill 在 Claude Code 环境不可用 → 手动 `git push` + `gh pr create`；
  merge 用 `gh pr merge --squash --delete-branch`（本地 checkout master 步会报
  worktree 占用，无害，验 `gh pr view --json state` 即可）。
- **migration 取号**：spec/计划里的号只是预定，实现前和 push 前都要 `git fetch` 复核
  （本线撞过两次：413 撞进 master 事后治理 #1768，421 在 push 前抓到避让为 423）。
- 验收纪律：等 `run-migration.yml` + `deploy-gpu.yml` 都绿 → readyz 探针 → 尽量真实链路
  （调试账号用法见 memory `claude-debug-test-account`；测试数据英文、用完清理）。
- Discord MCP 在 Claude Code 环境不挂载 → 通知用 PushNotification 代替并在总结中说明。
- 终审必做且屡有大发现：单源契约破裂、跨层死代码、spec 前提错误都是终审抓的。
- 子代理偶发 API 中断（idle reason=failed）：resume 一条消息让它从现场继续。
- 后台 CI 监视用 Monitor 工具（bash run_in_background 的长循环会被环境终止）。

## 2. 待办菜单（按 2026-08-12 离场时的优先级建议）

### ①（运营·小）doubao-pro 恢复探针 + 配 fallback 池
用调试账号建一次性 pro 主模型 agent（`doubao-seed-2-0-pro-260215`）发一条 chat 探
429 是否恢复（2026-08-11 实测仍 503 `provider_rate_limit`；探完删 agent 与会话）。
恢复后在 **admin 后台** agents 页给预设配 `fallback_models`（逗号输入框，零代码）。
背景：预设主模型已全是 lite、fallback 池全空——链就位、池子空。

### ②（运营·中）给 storyboard/script_ai 开 write_level 授权
让写作工具（ListScenes/CreateShot/ApplyEdit…）对真实用户可达——当前生产没有任何
agent 有授权，功能整体不可达。系统预设的 capabilities PATCH 走
`_can_edit_chat_permissions` 的 **admin-only 分支**（preset 无 user_id/team_id），
先探通 platform admin 身份路径（此前 E2E 用 user-owned agent 绕的，预设路径未验）。
开完顺带真实验一次「拦截可见化」警示条（需要一个 write_level 不足的 agent 触发）。

### ③（立项·中）撤销立项 backlog 清扫 PR
四条 + 一条同族：already_undone 前端独立文案（现与 done 相同，误导）；
`list_ops_by_scene` DB 错误返回 [] 静默（违类型化回显）；scene 正文撤销不刷新已开
script sheet；undo 的 `skipped.reason` 枚举加 `internal_error` 档——顺带把权限立项
`_undo_scenes` 泛异常归 `edited_after_run` 的问题一起收。

### ④（立项·中）死代码 refactor PR（24h 纪律，纯结构零逻辑）
`SummarizeService._build_adapter`、`VisualAnalysisService._build_adapter`（均已被
`build_fallback_llm` 取代成死代码）、`llm_analysis_service.py` 整文件（生产零调用方，
文件头已有标注注释）+ 迁移/删除其测试 `test_llm_analysis_service.py`。

### ⑤（立项·中）其他批量 agent 路径接 fallback 链
caption/classify/topic-scorer 等，照 #1810 的 summarize/visual 样板推广。模式已定型：
`fallback_wiring.build_fallback_llm`（记得传 `provider_key` + 正确的 `module` 门禁键）
+ LLM 异常 propagate + step `max_attempts=1`（regex 测试钉死）+ workflow 尾
`record_ai_error_code`（caption 系两处已有，别重复加）。**必配 fake-adapter 真链
集成测试**（mock 组合器的测试抓不住契约破裂——#1810 C1 教训）。

### ⑥（立项·大）权限路线图 P2：授权的时间维度
write_level 带 `expires_at` 的临时授权（parser 过期回落 none，fail-closed 顺理成章）
+ 分发线社媒账号的 keychain 式授权（按账号、写用途、限时、Ask 流程——`external_publish`
假开关的最终归宿）。设计基础见 `2026-08-10-agent-permissions-overhaul-design.md` §7
与 qm 借鉴分析（memory）。

### ⑦（立项·中）Redis 冷却注册表接线
`backend/app/agent_framework/model_health_redis.py`（RedisModelHealthRegistry）已写好
但从未挂 `app.state`——当前每个 worker 进程各自撞一次 429 才冷却。接线点：
`backend/app/startup/agent_framework_init.py:31`（现在实例化的是进程内版）。

### ⑧（立项·大）主动 provider 探针进 readyz
纯反应式健康的补齐（qwen 引擎曾静默停摆三周、/health 绿而 /v1 全炸）。
硬约束：对齐「探针必须可证伪」纪律（CLAUDE.md 验收纪律节 + memory
`reference-healthcheck-must-be-falsifiable`）。

### 权限路线图 P3/P4（远期）
团队权限地板（只收紧不放松，qm composeSecurityPosture 范式）；agent 例行任务产品化
（DBOS scheduled + routine 归因骨架已在）。

## 3. 跨立项方法论（血泪三条，全线适用）

1. **侦察生产状态必须 SELECT 活库**，不能只读 migration 历史文本（mig 423 no-op 教训：
   预设模型早被人切了，migration 文本还停在旧世界）。
2. **spec 引用"机制已存在"必须 grep 到调用点为证**，不能从相邻事实推断
   （#1810 C3 教训：record_workflow_failure 存在 ≠ record_ai_error_code 已接）。
3. **跨层协议改动必须配真链集成测试**——把共享组件 mock 掉的测试全绿不代表契约成立
   （#1810 C1 教训：三个任务的测试全绿，契约在生产是断的）。
