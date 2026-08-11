# 批量 LLM 路径接 fallback 链（summarize / visual analyze）设计

2026-08-11 · Provider 容错 P1（spec `2026-08-11-provider-resilience-design.md`）§7 明列的
follow-up。P1 把 chat 路径的降级与类型化错误面补齐了；**真正引发事故的批量路径**
（DBOS `ai_summary` / `analyze_l1` workflow 撞 doubao 429）至今零降级、且把真异常吞成
`None`/合成 RuntimeError。本期接上同一条 `LLMFallbackChain`，并让真异常到达
`record_ai_error_code`。

## 0. 现状盘点（侦察实据，file:line）

- `SummarizeService`（`backend/app/services/ai/summarize/summarize_service.py`）：
  `_build_adapter(model)`:65 → `AgentRunner(adapter=...)`:185，**裸 adapter 无链**。模型来自
  per-user `resolve_summarization_config(user_id)`（`ai_provider_helpers.py:705`，按
  doubao→qwen→openai→deepseek 扫第一个有 key 的 provider，**不读 ai_agents 行**，
  `agent_slug=""`）。`summarize()` 内部 `except Exception: return None`（~:262-268）——
  DBOS step `run_summarize_agent`（`ai_summary.py:157`，`max_attempts=2`）看到 None 后
  raise 合成 `RuntimeError("summarize agent returned None")`，真因丢失。
- `VisualAnalysisService`（`backend/app/services/ai/visual/visual_analysis_service.py`）：
  `_build_adapter`:191 → `AgentRunner(adapter=...)`:271，同样裸 adapter。analyze 路径的
  `resolve_task_ai_config`（`ai_provider_helpers.py:417`）**已加载 ai_agents 行**（:~493）
  但 `ResolvedAIConfig`（:31-47）没把 `fallback_models` 带出来。
- `LLMAnalysisService`（`backend/app/services/ai/llm/llm_analysis_service.py`）：**生产零
  调用方**（仅自测试引用）——疑似死代码，本期不接线（见 §6）。
- chat 侧组合范式（照抄对象）：`ai_library_chat_wiring.build_agent_runner_stack` ~:354-389
  ——对 `[primary, *fallbacks]` 逐模型预解析平台目录 adapter（`resolve_mediahub_model` →
  `get_adapter_for_key`），缺失落 `get_adapter_for_user`（BYOK）；`app.state.model_health`
  冷却注册表；`_llm_total_deadline_s()` 总时限。`LLMFallbackChain.call()` 与裸 adapter
  同 duck-type，`AgentRunner(adapter=chain)` 即插即用。
- workflow 侧「record-then-raise」契约已就位（PR #1743 + d78c041b 清扫）：workflow 尾部
  `record_workflow_failure`（→ `error_catalog.record_ai_error_code`）后 re-raise——只要
  service 层 raise 真异常，`PROVIDER_RATE_LIMIT` 等码就能落 `task_tracking`。
- `ModelHealthRegistry` 在 worker 进程可用（backend/worker 同跑 `app.main:app`，
  chat 侧 `from app.main import app` 取 `app.state.model_health` 的模式原样可用）。

## 1. 拍板决策（2026-08-11 用户确认）

| 决策点 | 结论 |
|--------|------|
| 接线对象 | `SummarizeService` + `VisualAnalysisService`（两条真实批量路径）；`LLMAnalysisService` 不接线，标注疑似死代码待独立清理 |
| fallback 来源 | **读 agent 行 `fallback_models`**（summarize/analyze 两个预设 slug），与 chat 路径同源同语义；不发明 per-user provider 阶梯（YAGNI）。primary 仍是各自现行解析（summarize=per-user，analyze=ResolvedAIConfig）——链 = per-user primary + 平台预设 fallbacks |
| 重试叠加 | 接链后两个 LLM step 的 `@DBOS.step` 降为 `max_attempts=1`；链内已有 每模型 3 重试 + 逐模型降级 + 总时限——重试职责全部收进链，不再相乘（现状最坏 2×2×4=16 发） |
| 异常口径 | `summarize()` 停止吞成 None：LLM 调用失败 **raise 真异常**；`run_summarize_agent` 删除合成 RuntimeError 分支。`VisualAnalysisService` 同口径核对 |

## 2. 共享组合器：`build_fallback_llm`

chat 侧的组合逻辑（预解析平台 adapter + BYOK 兜底 + health registry + 总时限）在
`build_agent_runner_stack` 里是内联的；两个批量 service 不该复制第三份。抽一个共享
帮手（新文件 `backend/app/services/ai/llm/fallback_wiring.py`）：

```python
async def build_fallback_llm(
    *,
    primary_model: str,
    fallback_models: list[str],
    user_provider_config: Optional[dict],
    user_id: Optional[str] = None,   # 仅日志/归因
) -> LLMFallbackChain:
    """[primary, *fallbacks] 逐模型预解析平台目录 adapter,缺失落 BYOK;
    health registry 从 app.state 取(拿不到为 None);总时限用统一 env。
    组合语义与 build_agent_runner_stack 完全一致——单一来源。"""
```

- `build_agent_runner_stack` 的内联段**同步改为调用它**（这是三处共用的前提，也是
  「refactor 与 feature 不分家」的例外：抽取即本 feature 的必要组成，改动为等价搬移，
  chat 侧既有测试作等价性守卫）。
- `_llm_total_deadline_s` 随之迁到 `fallback_wiring.py`（chat_wiring re-export 或改引用）。

## 3. summarize 接线

- `ai_summary.py` 的 `load_summary_inputs` step（或紧邻处）在 per-user 解析后**追加一次
  agent 行读取**：`agent_repo.get_by_slug("summarize")` 取 `fallback_models`（无行/空数组
  → 空链，行为与今天一致）。
- `SummarizeService.summarize()`：`adapter = await build_fallback_llm(primary_model=...,
  fallback_models=..., user_provider_config=self._provider_config)` 替换
  `_build_adapter`；`AgentRunner(adapter=chain)`。
- **异常口径**：LLM 调用段的 `except Exception: return None` 改为只捕**预期的解析/组装
  类**错误（保持原有非 LLM 段行为），`AllModelsFailed`/`LLMCallError` 一律向上抛；
  `run_summarize_agent` 删除 "returned None" 合成分支，`max_attempts=2 → 1`。
- `fallback_models` 参数化进 service 调用签名（step 读行，service 收列表——service 不
  自己查库，保持可测性）。

## 4. visual analyze 接线

- `ResolvedAIConfig` 增加 `fallback_models: list[str] = field(default_factory=list)`，
  `resolve_task_ai_config` 在已加载的 agent 行上顺手带出（行缺失 → 空）。
- `VisualAnalysisService` 同 §3 模式替换 `_build_adapter` → `build_fallback_llm`；
  `analyze_l1.py` 的 `call_analyze_l1` step `max_attempts=2 → 1`。
- 异常口径同 §3 核对（该 service 现状若已 raise 则零改动，若吞则同款修）。

## 5. 测试口径

- `fallback_wiring` 单测：平台目录命中/缺失落 BYOK、health registry 缺省 None、deadline
  取 env；chat 侧既有 wiring/fallback 测试全绿 = 等价搬移守卫。
- summarize：primary 失败（429 链）→ 换 fallback 成功（fake adapter factory）；全败 →
  `AllModelsFailed` 冒泡到 step、workflow record-then-raise 落
  `PROVIDER_RATE_LIMIT`（挂靠 `workflows/test_workflow_failure_raises.py` 范式）；
  `test_model_assignment_honored.py` 既有"per-user model 优先"断言不回归（primary 语义
  未变）。
- visual analyze：ResolvedAIConfig 新字段的解析测试 + 同款链路测试。
- DBOS：两个 step 的 `max_attempts=1` 有断言（防将来无意识改回相乘）。

## 6. 范围外（YAGNI）

- `LLMAnalysisService` 的删除/归档（疑似死代码——生产零调用方，仅自测试引用；先在其
  文件头加注释标注状态与本 spec 出处，删除另立 refactor PR，遵守「refactor 与 feature
  分开」）。本期只加注释不删码。
- caption/classify/topic-scorer 等其他批量 agent 路径（同模式，等本期验证后批量推广）。
- per-user fallback 阶梯、Redis 冷却接线、主动探针（路线图既有项）。
- doubao-pro 429 根因（运营）。

## 7. 生产验收（merge 后）

1. 调试账号触发一次真实 summarize（媒体已有 transcript 的重摘要路径）→ 成功（lite 主
   模型直接过即可，不要求触发降级）。
2. 若 doubao-pro 仍 429：临时把调试自建 agent 指 pro 验证链降级不现实（批量路径不走
   自建 agent）——以单测+staging 语义为准，生产只验"不回归"。
3. `task_tracking` 近 7 天错误漏斗里，summarize 失败行的 `error_code` 应开始出现类型码
   而非空/合成串（若期间有失败）。
