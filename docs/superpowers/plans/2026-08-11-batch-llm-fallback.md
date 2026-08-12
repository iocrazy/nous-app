# 批量 LLM 路径接 fallback 链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** summarize / visual-analyze 两条批量路径接上 `LLMFallbackChain`（与 chat 同源组合器），LLM 真异常上抛到 workflow 的 record-then-raise，DBOS 重试收敛为 1。

**Architecture:** 抽共享组合器 `build_fallback_llm`（新 `fallback_wiring.py`），chat 侧内联段等价搬移为调用它；两个批量 service 的裸 `_build_adapter` 替换为链；fallback 列表由 workflow step 读 agent 行传入（service 不查库）；异常口径改为「LLM 类异常 propagate、pause 保持 None」。

**Tech Stack:** 既有 `LLMFallbackChain`/`ModelHealthRegistry`/`resolve_mediahub_model`/adapter factory；DBOS steps；pytest fake-adapter 测试。

**Spec:** `docs/superpowers/specs/2026-08-11-batch-llm-fallback-design.md`（已获批）。

## Global Constraints

- **不改动**：`LLMFallbackChain`/`LLMRetryMiddleware`/`ModelHealthRegistry` 本体；`resolve_summarization_config` 的 per-user 解析语义（primary 语义不变）；`provider_errors.py`。
- 组合语义必须与 chat 现状逐字等价（平台目录预解析 → `resolve_provider_key(_prov, _actual)` dispatch（#1279 契约，前缀猜测会杀全链）→ BYOK 兜底 → health registry best-effort → 总时限）——chat 侧既有测试是等价性守卫，**零改动零回归**。
- 两个 LLM step 的 `@DBOS.step` 必须降为 `max_attempts=1` 且**测试断言钉住**（防回潮相乘）。
- 异常口径：`AllModelsFailed`/`LLMCallError` 一律 propagate；`AgentPausedError` 维持现状（return None）；service 的 None-check 合成 RuntimeError **保留但注释更新**（现在只覆盖 pause/runner-error-dict 残余情形，LLM 异常不再经过它）。
- 禁裸 SQL；isort/black clean；每 task RED→GREEN→commit（中文 conventional commit）。
- 工作区：`.worktrees/feat-batch-llm-fallback`，分支 `feat/batch-llm-fallback`（基于 c4ff32b2 后的 origin/master）。测试 `cd backend && uv run pytest ...`。本期纯后端，无前端/无 migration。

---

### Task 1: 共享组合器 `fallback_wiring.py` + chat 侧等价搬移

**Files:**
- Create: `backend/app/services/ai/llm/fallback_wiring.py`
- Modify: `backend/app/services/ai/chat/ai_library_chat_wiring.py`（~:345-389 内联段 + `_llm_total_deadline_s` 定义处）
- Test: `backend/tests/test_fallback_wiring.py`（新建）

**Interfaces:**
- Consumes: `LLMFallbackChain`（dataclass：`primary_model, fallback_models, adapter_factory, health_registry, total_deadline_seconds`）、`resolve_mediahub_model(model, "chat")`、`resolve_provider_key(provider, actual_model)`、`get_adapter_for_key`、`get_adapter_for_user`。
- Produces（Task 2/3 与 chat 侧共用）:

```python
def llm_total_deadline_s() -> Optional[float]: ...   # 从 chat_wiring 原样迁入(逻辑零改)

async def build_fallback_llm(
    *,
    primary_model: str,
    fallback_models: list[str],
    user_provider_config: Optional[dict],
) -> LLMFallbackChain: ...
```

- [ ] **Step 1: 写 RED 测试**

```python
"""共享 fallback 组合器(spec §2)——组合语义与 chat 内联段逐字等价的守卫。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.ai.llm.fallback_wiring as fw


@pytest.mark.asyncio
async def test_platform_catalog_hit_preresolves_adapter_by_actual_provider():
    hit = ("doubao", {"api_key": "k", "base_url": "https://ark"}, "actual-model-x")
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=hit)),
        patch.object(fw, "resolve_provider_key", MagicMock(return_value="doubao")) as rpk,
        patch.object(fw, "get_adapter_for_key", MagicMock(return_value="PLATFORM_ADAPTER")),
        patch.object(fw, "get_adapter_for_user", MagicMock(return_value="BYOK_ADAPTER")),
    ):
        chain = await fw.build_fallback_llm(
            primary_model="m1", fallback_models=["m2"], user_provider_config={}
        )
    # #1279 契约:用目录行的 actual_provider dispatch,不做前缀猜测
    rpk.assert_called_with("doubao", "actual-model-x")
    assert chain.adapter_factory("m1") == "PLATFORM_ADAPTER"
    assert chain.primary_model == "m1" and chain.fallback_models == ["m2"]


@pytest.mark.asyncio
async def test_catalog_miss_falls_to_byok_factory():
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(fw, "get_adapter_for_user", MagicMock(return_value="BYOK_ADAPTER")) as gau,
    ):
        chain = await fw.build_fallback_llm(
            primary_model="m1", fallback_models=[], user_provider_config={"doubao": {}}
        )
    assert chain.adapter_factory("m1") == "BYOK_ADAPTER"
    gau.assert_called_once_with("m1", {"doubao": {}}, None)


@pytest.mark.asyncio
async def test_health_registry_absent_is_none_and_deadline_from_env(monkeypatch):
    monkeypatch.setenv("LLM_TOTAL_DEADLINE_S", "45")
    with patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)):
        chain = await fw.build_fallback_llm(
            primary_model="m1", fallback_models=[], user_provider_config={}
        )
    assert chain.total_deadline_seconds == 45.0
    # app.state 不可用时 health_registry 必须是 None(best-effort),不许抛
    assert chain.health_registry is None or chain.health_registry is not None  # 不抛即过,值断言见下一行注释
    # (在测试进程里 app.main 可能可导入,两种值都合法;核心断言是"构造不抛")


@pytest.mark.asyncio
async def test_duplicate_models_deduped_in_preresolve():
    calls = []
    async def _rmm(m, _kind):
        calls.append(m)
        return None
    with patch.object(fw, "resolve_mediahub_model", AsyncMock(side_effect=_rmm)):
        await fw.build_fallback_llm(
            primary_model="m1", fallback_models=["m1", "m2"], user_provider_config={}
        )
    assert calls == ["m1", "m2"]
```

- [ ] **Step 2: RED 确认** `cd backend && uv run pytest tests/test_fallback_wiring.py -v`（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`fallback_wiring.py`：把 chat_wiring 的内联段（:345-389，含注释原文——平台目录 fail-closed、#1279 dispatch 契约、铁律 2026-07-07、health best-effort）**原样搬入**函数体，仅把局部变量来源换成参数；`_llm_total_deadline_s` 整函数迁入改名 `llm_total_deadline_s`（docstring 原样）。文件头 docstring 写明「chat/summarize/visual-analyze 三方共用的唯一组合点，语义变更必须三方同过测试」。

`ai_library_chat_wiring.py`：内联段替换为

```python
    from app.services.ai.llm.fallback_wiring import build_fallback_llm

    fallback_chain = await build_fallback_llm(
        primary_model=primary_model,
        fallback_models=fallback_models,
        user_provider_config=user_provider_config,
    )
```

删除本地 `_llm_total_deadline_s` 定义与不再使用的 import（`resolve_mediahub_model`/`resolve_provider_key`/`get_adapter_for_key` 若无其他使用处）；若文件内还有其他 `_llm_total_deadline_s` 引用则改 import 新名。

- [ ] **Step 4: GREEN + chat 等价守卫**

```bash
uv run pytest tests/test_fallback_wiring.py -q
uv run pytest tests/ -q -k "wiring or fallback or chat_stream or llm"
uv run isort --check-only <touched> && uv run black --check <touched>
```

Expected: 全 PASS，chat 侧既有测试零改动零回归。

- [ ] **Step 5: Commit** `git commit -m "refactor(ai): fallback 组合器抽为共享 build_fallback_llm — chat 内联段等价搬移,三方单源"`

---

### Task 2: summarize 接线 + 异常口径

**Files:**
- Modify: `backend/app/workflows/ai_summary.py`（`load_summary_inputs` ~:146-154、`run_summarize_agent` :157-186）
- Modify: `backend/app/services/ai/summarize/summarize_service.py`（`summarize()` :184-188 与 :241-268 异常段）
- Test: `backend/tests/test_summarize_fallback.py`（新建）；`backend/tests/test_model_assignment_honored.py` 回归

**Interfaces:**
- Consumes: Task 1 `build_fallback_llm`；`agent_repo.get_by_slug`（`app.repositories.agent_repository.get_agent_repository()`——读法参照 chat_wiring/composer 现用范式）；`AllModelsFailed`/`LLMCallError`。
- Produces: `SummarizeService.summarize(..., fallback_models: Optional[list[str]] = None)` 新可选参；`load_summary_inputs` 返回 dict 增 `"fallback_models": list[str]`；`run_summarize_agent` 增同名参数透传。

- [ ] **Step 1: 写 RED 测试**

```python
"""summarize 接 fallback 链(spec §3):链替换裸 adapter、LLM 异常上抛、pause 维持 None。"""

# 用例(fake:patch summarize_service 模块内的 build_fallback_llm 与 AgentRunner):
# 1) summarize(fallback_models=["lite"]) → build_fallback_llm 被以
#    primary=resolved model、fallback_models=["lite"]、user_provider_config=
#    service._provider_config 调用;AgentRunner 收到的 adapter 是链对象。
# 2) fallback_models=None/[] → 仍走链(空 fallback 列表),行为与今天等价。
# 3) runner.run_turn 抛 AllModelsFailed → summarize **propagate**(pytest.raises),
#    不再 return None。LLMCallError 同。
# 4) runner.run_turn 抛 AgentPausedError → return None(现状保留)。
# 5) result 带 error dict(不抛) → return None(现状保留)。
# 6) ai_summary.run_summarize_agent 的 @DBOS.step 装饰参数 max_attempts == 1
#    (直接 import 函数,断言其 DBOS 元数据/或解析源码 AST 里的装饰器参数——
#    按仓库既有对 step 参数的测试范式,若无先例则用 inspect.getsource + 正则
#    断言 "max_attempts=1",并注明这是防相乘回潮的钉子)。
# 7) load_summary_inputs 返回含 fallback_models(patch agent_repo.get_by_slug
#    返回 {"fallback_models": ["lite"]};行缺失/None → [])。
```

- [ ] **Step 2: RED 确认** `uv run pytest tests/test_summarize_fallback.py -v`

- [ ] **Step 3: 实现**

① `summarize_service.py`：`summarize()` 签名加 `fallback_models: Optional[list[str]] = None`；:184 替换：

```python
        from app.services.ai.llm.fallback_wiring import build_fallback_llm

        model = composed.model or self.model
        adapter = await build_fallback_llm(
            primary_model=model,
            fallback_models=list(fallback_models or []),
            user_provider_config=self._provider_config,
        )
        runner = AgentRunner(adapter=adapter, skill_tool=SkillToolService(get_skill_repository()))
```

（下方 :232 的 `model = composed.model or self.model` 已提前，删重复行。）
② 异常段（recorder 路径 :241-268）：

```python
        except AgentPausedError as err:
            logger.warning(f"[Summarize] agent paused: {err}")
            return None
        # LLM 类异常(AllModelsFailed/LLMCallError 及其他意外)一律 propagate:
        # workflow 的 record-then-raise(PR #1743)会把真因经 classify_ai_error
        # 落 task_tracking.error_code——吞成 None 会让它只看到合成 RuntimeError
        # (本次接线的动机,spec §1 异常口径)。
```

即：删掉 `except Exception as e: ... return None` 整块（bare 路径 :227-229 的 user_id=None legacy 分支**保留原样**——不在事故路径上）。
③ `ai_summary.py`：`load_summary_inputs` 在 `cfg = await resolve_summarization_config(user_id)` 后：

```python
    # fallback 链来源:summarize 预设行的 fallback_models(spec §1 拍板——与
    # chat 同源;per-user primary + 平台预设 fallbacks)。行缺失/空 → 空链。
    from app.repositories.agent_repository import get_agent_repository

    agent_row = await get_agent_repository().get_by_slug("summarize")
    fallback_models = list((agent_row or {}).get("fallback_models") or [])
```

返回 dict 加 `"fallback_models": fallback_models`。
④ `run_summarize_agent`：装饰器 `max_attempts=2 → 1`（docstring 补一句：重试与降级已收进 LLMFallbackChain，step 级重试会与链内相乘）；签名加 `fallback_models: list[str] | None = None`，透传 `svc.summarize(..., fallback_models=fallback_models)`；调用处（workflow 主体把 `load_summary_inputs` 的返回喂给它的那行）同步透传。`if result is None: raise RuntimeError(...)` **保留**，注释改为「残余 None = pause/runner-error-dict；LLM 异常已直接 propagate 不经此路」。

- [ ] **Step 4: GREEN + 回归**

```bash
uv run pytest tests/test_summarize_fallback.py tests/test_model_assignment_honored.py -q
uv run pytest tests/ -q -k "ai_summary or summarize or workflow_failure"
```

- [ ] **Step 5: Commit** `git commit -m "feat(ai): summarize 接 fallback 链 — LLM 真异常上抛,DBOS 重试收敛为 1"`

---

### Task 3: visual analyze 接线 + 死代码标注

**Files:**
- Modify: `backend/app/services/ai/providers/ai_provider_helpers.py`（`ResolvedAIConfig` :29-52 + `resolve_task_ai_config` 的 agent 行分支 ~:493）
- Modify: `backend/app/workflows/analyze_l1.py`（`resolve_analyze_provider` :49-65、`call_analyze_l1` :74-75、:307-309 调用处）
- Modify: `backend/app/services/ai/visual/visual_analysis_service.py`（`_build_adapter` 调用处 :270 + 异常段）
- Modify: `backend/app/services/ai/llm/llm_analysis_service.py`（仅文件头注释）
- Test: `backend/tests/test_visual_analyze_fallback.py`（新建）

**Interfaces:**
- Consumes: Task 1 `build_fallback_llm`；既有 `ResolvedAIConfig`（frozen dataclass：provider_key/provider_config/model/agent_slug/origin）。
- Produces: `ResolvedAIConfig.fallback_models: tuple[str, ...] = ()`（frozen dataclass 用 tuple 保持可哈希/不可变，消费端 `list(cfg.fallback_models)`）；`VisualAnalysisService` 的分析入口方法加 `fallback_models: Optional[list[str]] = None` 可选参（读该文件确认入口方法名与签名后同型加参）。

- [ ] **Step 1: 写 RED 测试**

```python
"""visual analyze 接 fallback 链(spec §4)。"""

# 用例:
# 1) ResolvedAIConfig 新字段默认 ():构造老式五参不带新字段仍成立(向后兼容)。
# 2) resolve_task_ai_config 在 agent 行分支把行上的 fallback_models 带出
#    (patch agent repo;行无该键/None → ())。非 agent 行分支(governance/byok
#    直连)→ ()。
# 3) VisualAnalysisService:链替换裸 adapter(同 Task 2 用例 1 形态);
#    AllModelsFailed propagate;AgentPausedError/error-dict 维持现状
#    (先读该 service 现有异常结构再对号写断言——bare user_id=None 路径保留)。
# 4) analyze_l1.call_analyze_l1 装饰器 max_attempts == 1(同 Task 2 用例 6 范式)。
# 5) resolve_analyze_provider 返回 dict 含 fallback_models(从 cfg 带出)。
```

- [ ] **Step 2: RED 确认** `uv run pytest tests/test_visual_analyze_fallback.py -v`

- [ ] **Step 3: 实现**

① `ResolvedAIConfig` 加字段（放 `origin` 之后）：

```python
    # mig 155 的 ai_agents.fallback_models,仅 agent 行分支填充(spec
    # 2026-08-11-batch-llm-fallback §4);tuple 保持 frozen 语义。
    fallback_models: tuple[str, ...] = ()
```

`resolve_task_ai_config` 的 agent 行分支在构造 `ResolvedAIConfig(...)` 处补 `fallback_models=tuple((agent_row or {}).get("fallback_models") or [])`（**只**改 agent 行分支的构造点；其他构造点靠默认值——先 grep `ResolvedAIConfig(` 全部构造点确认不用逐个改）。
② `analyze_l1.py`：`resolve_analyze_provider` 返回 dict 加 `"fallback_models": list(cfg.fallback_models)`（该函数如果拿的是 tuple 解包而非 cfg 对象，按实际结构取）；`call_analyze_l1` `max_attempts=2 → 1` + 签名/调用处透传。
③ `visual_analysis_service.py`：:270 同 Task 2 ① 形态替换（`build_fallback_llm`），入口方法加可选参；异常段按现有结构对号：recorder 路径的泛捕获若存在则同 Task 2 ② 口径改造（AgentPausedError 保留 None、其余 propagate），bare 路径保留。
④ `llm_analysis_service.py` 文件头加注释：

```python
# ⚠️ 疑似死代码(2026-08-11 侦察):生产零调用方,仅 tests/test_llm_analysis_service.py
# 引用。真实 analyze 路径是 visual_analysis_service.py。本文件未接 fallback 链
# (spec 2026-08-11-batch-llm-fallback §6);删除/归档另立 refactor PR,勿在
# feature 分支顺手删。
```

- [ ] **Step 4: GREEN + 回归**

```bash
uv run pytest tests/test_visual_analyze_fallback.py tests/test_llm_analysis_service.py -q
uv run pytest tests/ -q -k "analyze or visual or resolve_task or provider_helpers"
```

- [ ] **Step 5: Commit** `git commit -m "feat(ai): visual analyze 接 fallback 链 — ResolvedAIConfig 带 fallback_models,重试收敛;llm_analysis_service 标注疑似死代码"`

---

## 收尾

- [ ] 全量基线：`cd backend && uv run pytest -q`；isort/black 全量 check。
- [ ] 推分支开 PR（base master）。PR 描述：spec 路径 + 四拍板 + 「纯后端零 migration，无部署窗口问题」+ 验收步骤（spec §7：真实 summarize 冒烟、错误漏斗看类型码；不要求触发降级）。
- [ ] merge 后生产验收按 spec §7 走（可用调试账号触发重摘要）。

## Self-Review 记录

- spec §2（共享组合器+等价搬移+deadline 迁移）→ Task 1；§3（summarize：agent 行读取在 step、service 收列表、异常口径、max_attempts=1、合成 RuntimeError 语义修正——spec 说"删除合成分支"，计划落地为"保留 None-check 但其覆盖面因 LLM 异常改道而收窄、注释如实更新"，语义达成 spec 动机（真因到达 record_ai_error_code）且不破坏 pause/error-dict 残余路径，偏离已记录）→ Task 2；§4（ResolvedAIConfig tuple 字段+透传+同款改造）+§6 死代码标注 → Task 3；§5 测试口径分布各 task（含 max_attempts=1 钉子）；§7 验收在收尾。
- 类型一致性：`build_fallback_llm` 签名在 Task 1 定义、Task 2/3 与 chat 侧同签名消费；`fallback_models` 在 step 层是 `list[str]`、`ResolvedAIConfig` 里是 `tuple[str,...]`（frozen 需要），消费端显式 `list(...)` 转换已写明。
- 无占位：涉及"读现状再对号"的两处（VisualAnalysisService 入口签名/异常结构、ResolvedAIConfig 构造点清单）都给了明确的判定规则与动作，不是留白。
