# 死代码清理：LLM adapter 遗骸 — 设计（2026-08-12）

> 来源：批量 LLM fallback 立项（PR #1810）backlog + 交接文档 §2-④。
> 纯 refactor PR（24h 纪律）：只删结构，零逻辑改动。
> 用户已授权推进到终审和 PR（2026-08-12 会话指示）。

## 0. 前提核实（2026-08-12 在 origin/master=7cbe9a94 上 grep 实证）

| 对象 | 证据 |
|------|------|
| `llm_analysis_service.py`（321 行） | 唯一真实 import 是它自己的测试；文件头有 2026-08-11 死代码标注；真实 analyze 路径是 `visual_analysis_service.py` |
| `SummarizeService._build_adapter`（summarize_service.py:72） | 类内零调用（#1810 起走 `build_fallback_llm`） |
| `VisualAnalysisService._build_adapter`（visual_analysis_service.py:191） | 类内零调用，引用仅存在于 docstring |
| `test_llm_analysis_service.py`（164 行） | 只测死模块的私有函数；活孪生 `SummarizeService._parse_json` 有自身实现且有 `test_summarize_fallback.py` 覆盖——**直接删，无需迁移** |

⚠️ 不在范围：caption/classify/translate 各自的 `_build_adapter` 仍被真实调用
（它们是菜单 ⑤ fallback 推广的对象，不是死代码）。

## 1. 改动清单

**删除：**
1. `backend/app/services/ai/llm/llm_analysis_service.py` 整文件
2. `backend/tests/test_llm_analysis_service.py` 整文件
3. `SummarizeService._build_adapter` 方法（含方法体内 docstring）
4. `VisualAnalysisService._build_adapter` 方法

**伴随清理（删除导致的悬空引用，仍属纯结构）：**
5. 两个 service 里因删方法而不再使用的 import（如 factory/AIAdapter 相关，按删后 lint 实际结果为准；仍被其他方法使用的保留）
6. 过期注释/docstring 修正：
   - `summarize_service.py:204` "Mirror the twin llm_analysis_service" 注释
   - `visual_analysis_service.py:7` 模块 docstring 提到 `LLMAnalysisService`
   - `visual_analysis_service.py:88` docstring 提到 `:meth:_build_adapter`
   - `ai_provider_helpers.py:446` docstring "WhisperService and LLMAnalysisService use"
   - `fallback_wiring.py:103` 提到 `SummarizeService/VisualAnalysisService._build_adapter`（改为过去式/删除指涉，与 :160 已有的 "the deleted `_build_adapter`" 口径一致）

注释修正原则：只改指涉对象（删掉或改为"已删除"表述），不重写周边语义。

## 2. 验证

- `uv run pytest --collect-only -q` 全量收集无 import 错误（防删漏悬空引用）。
- `uv run pytest tests/test_summarize_fallback.py tests/test_visual_analysis*.py -v`（按实际文件名）全绿——证明活路径未被触碰。
- `uv run black --check` 触碰的文件干净。
- diff 里不允许出现任何行为改动（终审重点核这一条）。

## 3. 验收

CI 绿 → 24h 内 merge（refactor 纪律）→ deploy-gpu 绿 → readyz。
无前端改动，不需要 e2e:prod。
