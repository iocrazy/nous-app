# 批量 fallback 推广：caption / classify / translate — 设计（2026-08-12）

> 来源：#1810 backlog + 交接文档 §2-⑤。用户已批准（2026-08-12 会话 AskUserQuestion，
> 含 topic-scorer 跳过与全程推进授权）。
> 样板：`2026-08-11-batch-llm-fallback-design.md`（summarize/visual 接线）。

## 0. 侦察结论（2026-08-12 在 origin/master=8dbbe4c8 上实证）

- 未接链的批量 LLM 路径：caption（`caption_service.py:280 _build_adapter`，
  caption_asset + caption_slide 两 workflow 共用 `call_caption` step）、
  classify（`classify_service.py:82`）、translate（`translate_service.py:56`，
  同步 API 无 workflow）、topic-scorer（自建 failover，见 §4）。
- **caption/classify 的 provider 解析走 `resolve_task_provider_config` 元组 shim
  （`ai_provider_helpers.py:955`），会丢 `cfg.fallback_models`**——visual 当年的修法
  是 resolve step 直调 `resolve_task_ai_config`（`analyze_l1.py:49-75` 先例）。
- 三个 service 的 LLM 异常都吞成 None（caption:404-410、classify:206-210、
  translate:203-207），违「触发路径类型化失败回显」纪律。
- `record_ai_error_code`：caption 两处已有（`caption_asset.py:245`、
  `caption_slide.py:153`）**不要重复加**；classify 没有；translate 无 workflow 不适用。
- `test_fallback_wiring.py` 对 batch 分支（`provider_key=`）零覆盖——
  `_flat_degrade`、`provider_key=""` 哨兵、双 ValueError 降级、module 透传全裸奔
  （#1810 遗留，唯一间接覆盖是 summarize §2b 的 happy-path）。

## 1. 改动清单

### F1 — caption 接链

1. `backend/app/workflows/caption_asset.py`：
   - `resolve_caption_provider` step（:46）改为直调 `resolve_task_ai_config`
     （`task_key="caption"`），返回 dict 增加 `fallback_models`（照
     `analyze_l1.py:49-75` 先例，绕开元组 shim）。
   - `call_caption` step 装饰器 `max_attempts` 2→1（重试收敛进链，
     与 #1810 spec「重试相乘」论证一致）；step 输入透传 `fallback_models`。
2. `backend/app/services/ai/caption/caption_service.py`：
   - 删 `_build_adapter`；改用
     `await build_fallback_llm(primary_model=..., fallback_models=...,
     user_provider_config=self._provider_config, provider_key=self._provider_key,
     module="caption")`（照 `summarize_service.py:173-189` 原文样板）。
   - LLM 类异常真上抛（不再吞成 None）；非 LLM 的解析/后处理失败维持现状口径。
   - `fallback_models` 作为方法/构造 kwarg 进入（跟 summarize 的传参形状对齐）。
3. `caption_slide.py` 复用同一 step，自动生效——但要核对它的调用点传参完整。
4. `record_ai_error_code` 两处已有，不改。

### F2 — classify 接链

1. `backend/app/workflows/classify_asset.py`：resolve step 直调
   `resolve_task_ai_config`（`task_key="classification"`）带出 `fallback_models`；
   `call_classify` step `max_attempts` 2→1；
   **workflow 尾新增 `record_ai_error_code`**（照 `ai_summary.py:365-374` 先例：
   先记码、再 `record_workflow_failure`、然后 re-raise）。
2. `backend/app/services/ai/classify/classify_service.py`：同 F1.2，
   `module="classification"`（⚠️ agent slug 是 `classify`，门禁键是 `classification`）。

### F3 — translate 接链（同步 API）

1. `backend/app/services/ai/translate/translate_service.py`：同 F1.2，
   `module="translation"`；LLM 异常真上抛。
2. `backend/app/api/resources_ai_router.py:73-127`（`translate_gen_prompt`）：
   - provider 解析改直调 `resolve_task_ai_config`（`task_key="translation"`）
     带出 `fallback_models` 传给 service；
   - catch-all `except Exception → 500` **放行** `AllModelsFailed` / `LLMCallError`
     （re-raise），让全局类型化错误面（`core/provider_errors.py`）产出
     503 `provider_rate_limit` 等码——否则 P1 的类型化回显被裸 500 遮蔽。

### F4 — 测试

1. **fake-adapter 真链集成测试**（照 `test_summarize_fallback.py:166-253` §2b 结构，
   只 patch PromptComposer / resolve_mediahub_model / get_adapter_for_user /
   asyncio.sleep，链全真跑）：caption、classify、translate 各一条
   429→fallback 直通测，断言 scoped config 形状（C1 回归钉）+ 实际服务模型。
2. **`max_attempts=1` regex 钉子**：caption（`call_caption`）、classify
   （`call_classify`）各一条（照 `test_summarize_fallback.py:391-400` 写法）。
3. **`test_fallback_wiring.py` 补 batch 分支直接测试**：
   - `provider_key="doubao"` → `get_adapter_for_user` 收到 `{key: {...}}` 包裹形状；
   - `provider_key=""` 且模型前缀可派生 → 用派生 key；
   - `provider_key=""` 且模型前缀不可派生（`provider_key_for_model` raise）→
     `_flat_degrade`（断言产出 OpenAICompatibleAdapter 且无 app_id 路径不炸）；
   - `get_adapter_for_user` raise ValueError → `_flat_degrade`；
   - `module` 参数透传到 `resolve_mediahub_model` 的断言。
4. 既有测试适配：`test_caption_error_code.py` 三例、
   `test_caption_classify_materialize.py`、`test_classification_service.py`、
   `test_gen_prompt_translate_plan.py` 必须保持绿（或因异常口径变化做最小适配，
   适配理由写进报告）。

## 2. 范围外（带依据）

- **topic-scorer**：已有自建 failover（governance + 全部启用平台模型的动态候选，
  `topic_scorer.py:261-341`，`test_topic_scorer.py:349` 钉住），韧性目标已达成；
  其模型解析语义（候选各带独立凭证）与链的 primary+fallbacks 形状不同构，
  硬套是重设计。用户已拍板跳过。
- **不配 fallback 池 / 零 migration**：预设全 lite、pro 未恢复（2026-08-12 复探
  仍 503），池子留空等 admin 配置——链就位即本立项完成态。
- script_ai / forced_finish：非批量路径。
- `resolve_task_provider_config` 元组 shim 不删不改（还有别的消费者），
  只是绕开。

## 3. 验证

- 全量收集无 import 错；上述新旧测试全绿；black 干净。
- CI 绿 → merge → deploy-gpu 绿 → readyz。生产冒烟延后
  （与 #1810 同口径：以真链集成测试 + 错误漏斗为验收面）。
