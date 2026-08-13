# Agent override 在聊天链路失效 — 设计（2026-08-13）

> 用户报障 → 定位 → 本设计（用户已批准全范围 F1-F4，含记忆注入一并接回）。
> 完整定位证据见分支内 `DIAGNOSIS.md`（不进 PR，仅工作副本）。

## 0. 缺陷（生产实证）

用户在设置页把 `script_ai` 的模型改成 `deepseek-v4-pro`（落 `agent_overrides` user 层），
UI 显示正确，但聊天里模型自称 doubao、`agent_runs.model` 记 doubao。

**丢失点唯一且在契约层**：`ChatContextEngine.assemble` 的 request 契约
（`chat_context_engine.py:15-27` docstring + `:83-90` 转发）没有 override 字段，
调用方想传也传不进去。而 engine 在生产必然命中
（`startup/agent_framework_init.py:111` 无条件注册，`/health/deep` 实测
`context_engines:["chat"]`）；`ai_library_chat_service.py:580-591` 那条**传了 override 的
else 分支在生产从不执行**——只在不走 FastAPI lifespan 的单测里生效。这就是
「单测全绿而生产失效」的机理。

**是半边生效，且是最坏的一半**：

| 侧 | 来源 | 用的模型 |
|---|---|---|
| adapter / fallback chain（真正打 LLM） | `ai_library_chat_service.py:455` 的 `agent_record`（**传了** override）→ `ai_library_chat_wiring.py:145` `primary_model` | **override**（deepseek） |
| prompt 正文 + Runtime 行 + `agent_runs` + vision 判定 + 计价 | engine → composer（**未传** override） | **基础**（doubao） |

- 「模型自称 doubao」是 **prompt 泄题**：`prompt_composer.py:348` 把
  `agent.get('model')`（基础行）写进 system message 的 Runtime 行，模型复读它。
  不是路由证据。
- 审计 #8 的 wire-model 守卫（`_model_routing.py:39`）本该抓住这种不一致，但被
  `llm_fallback_chain.py:204-211` 的归一化消音（调 adapter 前把 `composed.model`
  改写成 primary，于是永远"对齐"）。修 F1 后两侧自然收敛，守卫不再被绕过。

**同一处契约缺口连带吞掉的**（engine 分支相比 else 分支少传的字段）：
`graph_facts` / `user_context` / `agent_memory_facts` —— `build_agent_runner_stack`
（`ai_library_chat_wiring.py:180-200`）每轮花完整 `MEMORY_RECALL_BUDGET_S` 预算并发召回，
engine 路径全部丢弃：**付了延迟成本，拿不到注入效果**。

override 的 `identity_md` / `soul_md` / `agent_md` / `temperature` / `max_tokens`
同样只影响 `composed`，故**用户自定义人格/指令在 1:1 聊天里完全不起作用**，且无任何字段暴露。

**第二处缺口**：`script_ai_service.py:194`（大纲/扩写/分支/转换等用户触发路径）
不传 override，且 `:199` 的 adapter 从 `composed.model` 派生 —— 两侧一致地用基础模型，
即**完全不生效**（比 chat 更彻底）。该函数手边就有 `self._user_id`（`:127`）。

## 1. 改动清单

### F1 — 补齐 engine 契约（核心）

1. `backend/app/services/ai/chat/chat_context_engine.py`
   - request 契约 docstring 增补五个可选字段：`override_user_id` / `override_team_id` /
     `graph_facts` / `user_context` / `agent_memory_facts`。
   - `assemble()` 把这五个字段透传进 `ComposerInput`（其余不变）。
2. `backend/app/services/ai/chat/ai_library_chat_service.py:571-578`
   - engine 分支补传这五个值，取值与 else 分支（`:580-591`）**逐字一致**：
     `override_user_id=user_id`、`override_team_id=session.get("team_id")`、
     `graph_facts=stack.graph_facts`、`user_context=stack.user_context`、
     `agent_memory_facts=stack.agent_memory_facts`。

### F2 — 等价性回归测试（防复发，必做）

新建 `backend/tests/test_chat_context_engine_parity.py`：钉住
**engine 路径与直连 composer 路径在相同输入下产出相同的 `ComposerInput`**。
实现方式：patch `PromptComposer.compose` 捕获入参，分别驱动两条分支，逐字段比对。

理由：本次缺陷正是「else 分支正确、engine 分支漏字段」，而没有任何测试比较过两者。
只补 override 的用例挡不住下次新增字段时同样漏传——**要钉的是等价性本身，不是某个字段**。

同时补一条 override 端到端用例：给定 user 层 override，
经 engine 路径 compose 出的 `composed.model` == override 模型（当前实现下必红）。

### F3 — script_ai 写作路径补 override

`backend/app/services/storyboard/script/script_ai_service.py:192-197`：
`ComposerInput(..., override_user_id=self._user_id)`。
（不传 team：与 `conversation_agent_turn.py:319-325` 的口径一致——个人 override
不得泄漏进共享上下文；此处是单用户写作路径，只取 user 层。）
配一条测试：`self._user_id` 存在时 override 生效、为 None 时退回基础行。

### F4 — 悬浮窗拖拽可发现性（前端）

`frontend/components/FloatingChatWidget.tsx:251-255`：标题栏 `h-8`（32px）内密集排布按钮，
而 `onDragStart`（`:116`）对 `closest('button')` 直接 return，可拖区域实际很窄。
改动：标题栏加高到 `h-10`，并在左侧加一个显式把手（`GripVertical` 图标，`cursor-grab`，
`aria-hidden`，不抢按钮事件）。不改拖拽/缩放逻辑本身。

## 2. 范围外

- 不动 `llm_fallback_chain` 的归一化（F1 后两侧收敛，守卫自动恢复效力）。
- 不改 `agent_runs.actual_model` 的记录缺口（chat 链路从不读 `_actual_model`——
  fallback 真实切换后遥测仍记 primary）。记 backlog。
- 「设计如此」的后台管线（summarize/visual/caption/classify/translate/topic-scorer/
  subagent/workforce）不传 override 是刻意的（`prompt_composer.py:68-71`），不动。
- 群聊 `conversation_agent_turn.py` 只传 team 层是刻意的，不动。

## 3. 验证

- 后端：F2 的等价性测试 + override 端到端用例先红后绿；`ai_library_chat_service` /
  composer / script_ai 相关既有测试保持绿；全量 collect 无 import 错。
- 前端：`FloatingChatWidget` 既有测试保持绿；新增拖拽把手的可见性断言。
- 生产验收：部署后用调试账号设一个显眼 override（如 `nous-qwen3-llm`），
  发一条真实 chat，断言 `agent_runs.model` == override 模型（当前会记基础模型），
  验完删除 override。**这是本缺陷的直接可证伪探针。**
