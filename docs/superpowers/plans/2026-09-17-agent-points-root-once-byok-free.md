# agent 积分「root 一次取整」+「BYOK 免扣」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一个回合扣的积分 = `ceil(整棵 run 树的**平台**花费之和)`，只在 root 定稿时扣这一次；用户自带 key（BYOK）烧掉的钱一分不扣，但用量与预算口径照记真实花费。

**Architecture:** 不加迁移、不加列。三条调用链（LLM / 图片 / 子 agent）各自把「这笔钱是不是用户自己的 key 付的」写进已有的 transcript 事件 payload，由已有的 fold 收进 `metadata_json.cost` 的三条**平行 BYOK 道**（`own_byok_cents` / `media_byok_cents` / `by_child_byok`，与 `own_cents` / `media_cents` / `by_child` 一一对应）。`RunRecorder._finish` 在终态用这六个数分桶出 `tree_platform` 与 `own_platform`，root 扣前者一次、晚到的子 run 补扣后者、root 仍在跑的子 run 不扣。读方 `charged_points_for_run_trees` 按 `root_run_id` 全树合计，扣法从「6 行相加」变成「1 行」，同一个数，**读方一行不改**。

**Tech Stack:** FastAPI + SQLAlchemy async（ORM，禁裸 SQL）+ Postgres；pytest（`uv run pytest`）；无前端改动、无迁移。

**Spec:** 用户裁定两条（见下「产品口径」），落地依据是侦察报告 `/Volumes/program/project-code/repos/nous-app/.superpowers/sdd/2026-09-15-harness-p4-phase3c-search-efficiency/recon-billing-followup.md`（只读，origin/master ffa2390c9）。本计划基于 **origin/master ffa2390c9**。

## 产品口径（用户裁定，只有这两条）

1. **一个回合的积分 = `ceil(整棵树的平台花费之和)`，只在 root 定稿时扣一次。** 子 run 不各自 `ceil`（此前一次带委派的回合在 `point_transactions` 里是 6 行、每行各向上取整，真栈实测 ≈¢0.92 被收成 7 分）。
2. **BYOK 的调用一律不扣积分** —— LLM、embedding、图片都算。靠调用级 / run 级标记自动跳过，**不靠管理员摘 `per_call_cents`**。用量记录（`ai_usage_logs`、`ai_usage_hourly`、预算门禁读的 `spent_cents`）仍记真实花费。

## 裁定（控制方 + 本计划作者的核实结论，实施者以此为准）

| # | 裁定 | 核实结论（作者在 ffa2390c9 上逐条 grep / 读源码） |
|---|---|---|
| ① | LLM 只认 `ResolvedAIConfig.origin == "byok"`；`env` 是平台凭证不算 | 成立。`ai_provider_helpers._byok_origin()`：provider_config 带非空 `api_key` → `"byok"`，否则 `"env"`（adapter factory 回落 env 凭证）。四态里只有 `byok` 是用户自己的钱 |
| ② | 半程 BYOK：若 `LLMFallbackChain` 能暴露每次调用由哪个 adapter 服务，则按调用/步分桶；否则整轮退到平台 fallback 的 run 按平台计 | **能暴露，走按步分桶（路 A）**。判定过程与两条路的代码都在 Task 1 Step 0 / Step 3。关键事实：`fallback_wiring.build_fallback_llm` 在构建时就把**每一个**命中平台目录 `mediahub_models` 的模型预解析进 `_platform_adapters`（用 admin 凭证），只有目录没命中的模型才落到 `_get_adapter_for_user(...)`（用户 BYOK 配置）。所以「这一步的钱谁付的」= `actual_model ∈ _platform_adapters`，构建时已知；`LLMFallbackChain.call()` 成功分支已经在注入 `response["_actual_model"]`，加一个并列键即可 |
| ③ | `subagent_done.cost_cents` 必须是子 run **自身**花费，若是树总额则改发射点 | **前提不成立，不改发射点。** `_cost_cents_of()`（`subagent_task_service.py:142-166`）的 docstring 明写它取 `views["cost"]["spent_cents"]`，即**整棵子树**；`by_child` 是「每个直接子 run 一项、每项是那棵子树的合计」，父行 `children_cents = Σ by_child` 因此已经把孙子算进去且**不重复**（孙子不会在父的 `by_child` 里另开一项）。改成「自身花费」会让孙子的钱从 `agent_runs.cost_cents`（树总额，review I3 靠它不倒退）和预算门禁读的 `spent_cents` 里整体消失 —— 那是把一个正确的口径改坏。**真正的不变量是「`by_child` 与 `by_child_byok` 必须同海拔」**：两者都是子树合计，减出来的平台额才对。Task 2 按这条写，并加一条断言钉住 |
| ④ | refold 竞态：子 run 以自己看到的 root `status` 为准，root 以 `refold_external_slices()` 快照为准；宁少收不重收 | 采纳。判据函数 `root_run_is_settled()`（Task 3），读不出来一律 False（不扣）。竞态测试 `test_the_refold_race_bills_the_child_at_most_once` 断言恰好 0 或 1 行、绝不 2 行 |
| ⑤ | 纯 BYOK 树（`tree_platform == 0` 且树总额 > 0）与零花费树在 `note` / 日志里分开，`byo_key=True` 只给前者 | 采纳。`reconcile_run` 的 `byo_key` 分支 note 是 `"byo_key — billed by user's provider, no points charge"`，零花费走 `"no team_id or zero cost — skipping points consume"`，两者本就不同串，只要**传对** `byo_key` 即可 |
| ⑥ | 急停期间跑完的树不补扣（现状，用户已知），docstring 写明 | 采纳。`AGENT_POINTS_CHARGE_ENABLED` 为 false 时 `reconcile_run` 只写 `ai_usage_logs` 不动余额，恢复后没有补扣机制。root-once 让单次金额变大，这条要在 docstring 里显式写成 Stated Limitation |
| ⑦ | 不加迁移；`RunRecorder` 只加 `credential_origin: str \| None` 与 `parent_run_id: Optional[str]` 两个 dataclass 字段 | 采纳。两个字段都用**简单默认值**（`None`），所以它们会成为类属性 —— 现存大量用 `RunRecorder.__new__(...)` 造桩的测试（`test_hourly_usage_is_own_spend.py`、`test_run_recorder_finish_idempotent.py` …）不设这两个字段也读得到 `None`，不会 `AttributeError`。**千万不要写成 `field(default_factory=...)`**，那样就没有类属性，上述测试会整片炸 |
| ⑧ | Embedding 现无 BYOK 路径，本计划不动 | 核实成立：`providers/embedding_config.py::resolve_embedding_config()` 只读 admin governance `ai_module.embedding.*` 与平台目录，`source` 只有 `platform` / `governance`。**接入用户 embedding key 时必须走同一标记** —— 届时在 `resolve_embedding_config` 返回值上加 `origin`，由调用方填进 `RunRecorder.credential_origin`，不要新开第二套判据 |
| ⑨ | 图片：`db_registry._stamp_provider_key` 旁贴 `is_byok` → `image_generation_service` 返回 `byok` → `GenerationOrigin.byok` → `generated_media_service` 仍算 `cost_cents` 但 payload 加 `byok_cents` → `folds/deliverables.py` 累进 `media_byok_cents`（与 `media_cents` 同 `seen` 去重键） | 采纳，Task 2。BYOK 图片行的 `actual_provider` **刻意**用协议名（如 `ark`，与平台目录同键），所以 `media_price_cents()` 照样命中平台价 —— 这正是「BYOK 图片被按平台价扣」的确切机理，也正是为什么不能靠摘 `per_call_cents` 解决（那会把价钱从血缘里也抹掉） |
| ⑩ | 分桶：`tree_platform = (own+Σchildren+media) − (own_byok+Σby_child_byok+media_byok)`；`own_platform = (own+media) − (own_byok+media_byok)`；root → `reconcile_run(cost_points=tree_platform)`；非 root 且 root 已终态 → `own_platform` 补扣；非 root 且 root running → 不扣。`spent_cents` 与小时表口径不变 | 采纳，Task 3。**补一条作者加的守卫**：每条 BYOK 道在相减前 `min()` 到对应分量，见 Task 3 `bucket_tree` 的注释（两侧来源不同，钳位是防「BYOK 道虚高把真该收的钱抹成 0」） |
| ⑪ | 会被推翻的测试按侦察表反转，`test_reconcile_byo_key_skips_points` 保留为 BYOK 钉子；`test_child_run_inherits_team.py` 全保留 | 采纳，Task 3 Step 6 |
| ⑫ | 真栈验收（最后一个 Task）：fixture 团队 MH-95 跑一次派子 agent 的回合 → 该树**恰一行**、`points == ceil(tree_platform)`；BYOK ark 图片模型出一次图 → 该树零行且用量表有花费；消耗行 ◇n 与流水一致；`AGENT_POINTS_CHARGE_ENABLED` 不在生产切换 | 采纳，Task 4 |

**⑬（作者补，实施者必须知道）：`ai_usage_logs.cost_points` 与「扣多少分」是同一个入参。** 今天 `_finish` 传 `own_media_cents` 一个数同时当审计值和扣费值。root-once 之后扣费值变成树总额，若直接沿用同一入参，`ai_usage_logs` 会「root 记树 + 每个子 run 记自己」双计。所以 Task 3 给 `reconcile_run` 加 `usage_cost_points: Optional[float] = None`（缺省沿用 `cost_points`，**既有调用方零改动**），`_finish` 恒传 `usage_cost_points=buckets.own_total`。这正是用户裁定 (2) 里「用量记录仍记真实花费」那半句的落点。

## Global Constraints

- **不加迁移、不加列。** 全部新状态住在 `agent_runs.metadata_json` 的 `cost` 视图与 `point_transactions` 既有形状里。谁想加 `agent_runs.byok_cents` 都要先另立票（加列牵动 schema-drift 门禁与 C1 ORM 索引对账）。
- 新 SQL 一律 ORM；`text()` 裸 SQL 只许经 `app/db/scoped_sql.py::scoped_sql()` 且显式 `system=True, reason=...`（本计划全部 ORM，无裸 SQL）。
- 每 Task 独立 worktree + 独立 PR：`git -C /Volumes/program/project-code/repos/nous-app worktree add -b <branch> .worktrees/<name> origin/master`。**所有 git 命令 `git -C <绝对路径>`；主检出只读；同一轮最多一条依赖 cwd 的 Bash**（2026-09-09 两次事故：master 误 reset、嵌套 worktree 开出重复 PR）。
- 分支从 **origin/master** 建（本机 master 会带 squash 前提交），建完立刻 `git -C <worktree> rebase --onto origin/master`，推送用显式 lease。
- TDD：先红后绿。每个 Task 末尾**突变记录** —— 改一处实现让新测试转红、贴出失败输出、再改回。
- 后端测试：`cd <worktree>/backend && uv run pytest <path> -q`。lint 三件套一条都不能少：`uv run ruff check . && uv run black --check . && uv run isort --check-only .`。
- **根级守卫必须跑**：`cd <worktree>/backend && uv run pytest tests/test_*.py -q`（3b 教训：子目录测试全绿而根级守卫红）。
- `stream_turn` 的**缓冲回退分支**（adapter 无 `stream` 属性）是生产上每个带 `chunk_callback` 回合的唯一路径。本计划 Task 1 改 runner 事件 payload，**必须**有该分支的用例（照 `tests/runner/test_turn_end_reasons.py::test_stream_turn_buffered_fallback_carries_the_hook_stop_reason` 的 `_NoStreamAdapter` 写法）。
- 边界 mock 用真实 wire 形状；错误码走 `details.code`（`ErrorResponse` 外壳，不是 FastAPI 裸 `{detail}`）。
- 不变性：新代码不原地改 `origin` 这类调用方对象，需要新值就造局部变量（`generated_media_service` 里已有先例注释）。
- 类型注解用 `X | None` / `Optional[X]` 跟着被改文件的既有风格走；值对象 `@dataclass(frozen=True)`。
- 急停开关 `AGENT_POINTS_CHARGE_ENABLED`（`backend/config.yml` 大写键，默认 true）**不在生产切换**。
- Python 3.13（`.python-version` 是唯一来源）。测试进程摘代理由 `backend/tests/conftest.py` 的 session autouse fixture 负责，不要自己 unset。

## 合并顺序

1. **Task 1**（LLM 标记 + 按步分桶 + `RunRecorder` 两个字段）先行 —— 它建立 `cost` 视图里三条 BYOK 道的**形状**与 `RunRecorder` 的两个新字段，Task 2/3 都消费。
2. **Task 2**（图片 + 子 agent 两条 BYOK 道）在 Task 1 合并后开分支。它只加另外两条道，不碰扣费。
3. **Task 3**（分桶 + root 一次扣 + 反转旧测试）在 1、2 都合并后开分支。⚠️ **Task 3 单独上线会真的改变用户余额**，它必须是最后一个后端 PR。
4. **Task 4** 真栈验收 + 完成账。
- Task 1 与 Task 2 都改 `app/services/ai/runner/run_projection.py` 的空 `cost` 视图字面量：Task 1 加 `own_byok_cents`，Task 2 加 `media_byok_cents` / `by_child_byok`。后合的一方 rebase 时把三行并成相邻三行，键名逐字照本计划。

---

### Task 1: LLM 的 BYOK 标记进 run，并按步分桶进 `cost.own_byok_cents`

> 这个 Task 同时把 `RunRecorder` 的两个新字段（裁定 ⑦）一次加齐并接好线，Task 3 只消费不再改 dataclass。

**Files:**
- Create: `backend/app/services/ai/billing/byok_step.py`
- Modify: `backend/app/services/ai/runner/run_recorder.py`（`RunRecorder` dataclass，`metadata` 字段之后、`run_id` 之前那一段）
- Modify: `backend/app/services/ai/llm/llm_fallback_chain.py`（dataclass 字段块 :117-119 之后；成功分支 :300-311）
- Modify: `backend/app/services/ai/llm/fallback_wiring.py`（`build_fallback_llm` 的 `return LLMFallbackChain(...)`，:231-238）
- Modify: `backend/app/services/ai/runner/agent_runner.py`（`_step_ended` 定义 :1178-1226；缓冲路调用点 :1898-1905；流式路调用点 :762-764）
- Modify: `backend/app/services/ai/runner/folds/step.py`（`fold_step_end`）
- Modify: `backend/app/services/ai/runner/run_projection.py`（空 `cost` 视图字面量 :73-94）
- Modify: `backend/app/services/ai/chat/ai_library_chat_wiring.py`（`AgentRunnerStack` :86-104；`resolve_chat_config` 消费处 :322-328；`return AgentRunnerStack(` :471）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py`（`RunRecorder(` :1498）
- Modify: `backend/app/services/chat/conversation_agent_turn.py`（`RunRecorder(` :459）
- Modify: `backend/app/services/workforce/agent_worker.py`（`RunRecorder(` :291）
- Modify: `backend/app/services/ai/runner/subagent_task_service.py`（`RunRecorder(` :564）
- Modify: `backend/app/services/ai/tools/forced_finish_declaration.py`（`RunRecorder(` :227）
- Test: `backend/tests/runner/test_byok_step_bucket.py`（新建）
- Test: `backend/tests/ai/test_credential_origin_wiring.py`（新建）

**Interfaces:**
- Produces `app.services.ai.billing.byok_step.step_byok_cents(cost_cents: Any, credential_origin: Optional[str], served_by_platform: Optional[bool]) -> Optional[float]`。
- Produces `LLMFallbackChain.platform_models: frozenset[str]`（构建时预解析成功的平台目录模型全集）与成功响应上的 `response["_served_by_platform"]: bool`。
- Produces `RunRecorder.credential_origin: Optional[str]`（`"governance" | "platform" | "byok" | "env" | None`）与 `RunRecorder.parent_run_id: Optional[str]`（`agent_runs.id` 的字符串形，root 为 `None`）。
- Produces `AgentRunnerStack.credential_origin: Optional[str]`。
- Produces transcript 事件 `step_end` 的新可选 payload 键 `byok_cents: float`（**只在这一步确实由用户 key 付钱时出现**；平台步一律不带这个键，不是带 0）。
- Produces `views["cost"]["own_byok_cents"]: float`（累加语义，与 `own_cents` 平行；**不进** `spent_cents`）。
- Consumes（既有，不改）：`ai_provider_helpers.resolve_chat_config(...) -> ResolvedAIConfig`（字段 `provider_key / provider_config / model / agent_slug / origin / fallback_models`）。

- [ ] **Step 0: 先做裁定 ② 的判定（读代码，不写代码）**

裁定 ② 要求实施者自己确认「chain 能不能暴露每次调用由哪个 adapter 服务」。判定方法是读这三处，**不要靠猜**：

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/<name>/backend
sed -n '124,150p' app/services/ai/llm/fallback_wiring.py   # _platform_adapters 预解析
sed -n '186,210p' app/services/ai/llm/fallback_wiring.py   # _adapter_factory 的两个分支
sed -n '298,312p' app/services/ai/llm/llm_fallback_chain.py # 成功分支已注入 _actual_model
```

判据（作者在 ffa2390c9 上已跑过，结论写在裁定 ②）：

- `_platform_adapters[_m]` 在**构建时**就为每个命中 `mediahub_models` 的模型建好了 adapter，凭证来自目录行（admin 的）。
- `_adapter_factory(model)` 第一句就是 `pre_resolved = _platform_adapters.get(model)`，命中就直接返回它；**没命中**才走 `_get_adapter_for_user(model, user_provider_config, ...)`，即用户 BYOK 配置。
- `call()` 成功时已注入 `response["_actual_model"] = model`。

→ 「这一步谁付的钱」= `actual_model ∈ platform_models`，构建时完全可知。**走路 A（按步分桶）**，即下面的 Step 1–5。

**路 B（若将来这个结构变了、判定为不可暴露）**：不要猜，退到 run 级 —— `_step_ended` 收到的 `served_by_platform` 传 `None`，`step_byok_cents` 的语义（见 Step 1 的实现）此时自动退化成「整轮按 `credential_origin` 计」。也就是说路 B **不需要第二份代码**，它就是 `served_by_platform=None` 这一支；届时只要把 Step 3 的调用点改回不传该参数，并把 Step 4 里 `_NoStreamAdapter` 那个用例的期望从「fallback 步不算 BYOK」改成「整轮算 BYOK」。这一段必须留在 docstring 里，下一个人才知道两条路的关系。

- [ ] **Step 1: 写失败测试 —— 分桶纯函数 + 三条 runner 路径（红）**

```python
# backend/tests/runner/test_byok_step_bucket.py
"""BYOK 免扣（用户裁定 2）的第一段：一步 LLM 调用的钱是不是用户自己的 key 付的，
必须在 ``step_end`` 落事件的那一刻就定下来，因为那是唯一同时知道「这一步花了多少」
与「这一步由谁的 adapter 服务」的地方。

三条 runner 路径都要覆盖，其中「adapter 无 ``stream`` 属性 → stream_turn 委托
run_turn」那条是生产上每个带 chunk_callback 回合的**唯一**路径（CLAUDE.md 血泪：
2026-09-08 stop_reason 事故就是只测了另外两条）。
"""

from unittest.mock import AsyncMock

import pytest

from app.services.ai.billing.byok_step import step_byok_cents

pytestmark = pytest.mark.unit


# ── 纯函数：四态 origin × 三态 served_by_platform ────────────────────────


def test_only_byok_origin_counts_as_the_users_own_money():
    """``env`` 是「BYOK 形状但没有 api_key」，adapter factory 回落平台凭证 ——
    平台真付了钱，绝不能当成用户自己付（裁定 ①）。"""
    assert step_byok_cents(1.5, "byok", False) == 1.5
    for origin in ("platform", "governance", "env", None, ""):
        assert step_byok_cents(1.5, origin, False) is None, origin


def test_a_step_served_by_the_platform_catalog_is_never_byok():
    """半程 BYOK：主模型是用户的、fallback 落到平台目录行 —— 那一步的钱平台
    真付了（裁定 ②）。"""
    assert step_byok_cents(1.5, "byok", True) is None


def test_an_unknown_server_falls_back_to_the_run_level_origin():
    """``None`` = 这条链根本没有 fallback 机制（直连 adapter、子 agent 栈），
    一个凭证服务整轮，run 级 origin 就是精确答案 —— 这也是裁定 ② 的路 B。"""
    assert step_byok_cents(1.5, "byok", None) == 1.5
    assert step_byok_cents(1.5, "platform", None) is None


def test_a_step_with_no_known_price_reports_nothing_rather_than_zero():
    """价钱未知时 ``cost_cents`` 是 None。写 0 会把「不知道」伪装成「没花钱」，
    而下游是拿它去减平台额的 —— 减掉一个假的 0 不痛，减掉一个假的数才痛。
    ``True`` 是 ``int`` 的子类，同样不是价钱。"""
    assert step_byok_cents(None, "byok", False) is None
    assert step_byok_cents("1.5", "byok", False) is None
    assert step_byok_cents(True, "byok", False) is None


# ── 三条 runner 路径 ────────────────────────────────────────────────────


class _Rec:
    """``tests/runner/test_turn_end_reasons.py`` 的 ``_Rec`` 加两样：BYOK 判据要
    读的 ``credential_origin``，以及 ``_step_ended`` 算价要调的 ``cost_of``。"""

    def __init__(self, credential_origin=None):
        self.events = []
        self.views = {"view": {}}
        self.credential_origin = credential_origin

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def cost_of(self, prompt, completion, cached):
        return 2.0

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def step_ends(self):
        return [p for t, p in self.events if t == "step_end"]


def _composed():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(adapter=adapter, skill_tool=_Tool())


def _resp(*, served_by_platform=None):
    """真实 wire 形状：``LLMFallbackChain.call`` 成功时在响应体顶层注入
    ``_actual_model`` / ``_served_by_platform``，choices 与 usage 是 provider 原样。"""
    out = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "_actual_model": "m",
    }
    if served_by_platform is not None:
        out["_served_by_platform"] = served_by_platform
    return out


@pytest.mark.asyncio
async def test_run_turn_marks_the_step_when_the_users_own_key_served_it():
    adapter = AsyncMock()
    adapter.call = AsyncMock(return_value=_resp(served_by_platform=False))
    rec = _Rec(credential_origin="byok")
    await _runner(adapter).run_turn(
        _composed(), user_messages=[{"role": "user", "content": "go"}], recorder=rec
    )
    (step,) = rec.step_ends()
    assert step["cost_cents"] == 2.0
    assert step["byok_cents"] == 2.0


@pytest.mark.asyncio
async def test_run_turn_leaves_the_key_off_a_platform_served_step():
    """键不存在，不是等于 0 —— fold 靠「有没有这个键」判断，0 与缺席在账上
    同值但在语义上不同（缺席 = 平台付的，0 = 用户付了 0 分）。"""
    adapter = AsyncMock()
    adapter.call = AsyncMock(return_value=_resp(served_by_platform=True))
    rec = _Rec(credential_origin="byok")
    await _runner(adapter).run_turn(
        _composed(), user_messages=[{"role": "user", "content": "go"}], recorder=rec
    )
    (step,) = rec.step_ends()
    assert step["cost_cents"] == 2.0
    assert "byok_cents" not in step


class _NoStreamAdapter:
    """没有 ``stream`` 属性 —— 生产上 chunk_callback 回合走的就是它，
    ``stream_turn`` 委托 ``run_turn``（CLAUDE.md「生产的唯一路径」）。"""

    async def call(self, *a, **k):
        return _resp(served_by_platform=False)


@pytest.mark.asyncio
async def test_the_buffered_fallback_path_carries_the_byok_mark():
    rec = _Rec(credential_origin="byok")
    async for _chunk in _runner(_NoStreamAdapter()).stream_turn(
        _composed(),
        [{"role": "user", "content": "go"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    (step,) = rec.step_ends()
    assert step["byok_cents"] == 2.0, rec.events


class _StreamAdapter:
    """有 ``stream``：流式分块里没有响应体，拿不到 ``_served_by_platform``，
    于是退到 run 级 origin（路 B 的那一支）。"""

    async def call(self, *a, **k):
        raise AssertionError("must not fall back to call()")

    async def stream(self, composed, messages, **kw):
        from app.services.ai.adapters.base import StreamChunk

        yield StreamChunk(delta_text="ok")
        yield StreamChunk(
            finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 4}
        )


@pytest.mark.asyncio
async def test_the_streaming_path_falls_back_to_the_run_level_origin():
    rec = _Rec(credential_origin="byok")
    async for _chunk in _runner(_StreamAdapter()).stream_turn(
        _composed(),
        [{"role": "user", "content": "go"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    (step,) = rec.step_ends()
    assert step["byok_cents"] == 2.0, rec.events


# ── fold ────────────────────────────────────────────────────────────────


def _views():
    from app.services.ai.runner.run_projection import empty_views

    return empty_views()


def test_the_fold_accumulates_a_parallel_byok_lane():
    from app.services.ai.runner.folds.step import fold_step_end

    views = _views()
    fold_step_end(views, {"turn": 1, "step": 1, "cost_cents": 2.0, "byok_cents": 2.0})
    fold_step_end(views, {"turn": 1, "step": 2, "cost_cents": 3.0})
    cost = views["cost"]
    assert cost["own_cents"] == 5.0
    assert cost["own_byok_cents"] == 2.0
    # 真花了 5 分：BYOK 的钱用户真付了，预算门禁与 UI 读的 spent_cents 不许缩水。
    assert cost["spent_cents"] == 5.0
```

⚠️ 上面最后一段用到 `empty_views()`。先确认它的真名：

```bash
cd <worktree>/backend && grep -n "^def \|^EMPTY\|_EMPTY" app/services/ai/runner/run_projection.py | head
```
若导出名不是 `empty_views`，**改测试去对齐真名**（不要在 `run_projection.py` 里新造一个别名）。

- [ ] **Step 2: 跑测试确认它红**

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_byok_step_bucket.py -q
```
预期：`ModuleNotFoundError: app.services.ai.billing.byok_step`（纯函数那 4 个），
runner 那 4 个 `KeyError: 'byok_cents'`，fold 那个 `KeyError: 'own_byok_cents'`。
**逐条看失败原因是不是这些** —— 如果某条因为别的原因红，先查清再往下。

- [ ] **Step 3: 判据纯函数 + 三处接线（绿其一）**

新建 `backend/app/services/ai/billing/byok_step.py`：

```python
"""一步 LLM 调用的钱是不是用户自己的 key 付的。

判据只有两个输入，都在 ``AgentRunner._step_ended`` 那一刻可得：

* ``credential_origin`` —— 这条 run 用的是谁的凭证（``ResolvedAIConfig.origin``，
  四态 ``governance`` / ``platform`` / ``byok`` / ``env``）。**只有 ``byok`` 算**：
  ``env`` 是「BYOK 形状但没有 api_key」，adapter factory 回落平台凭证，平台真
  付了钱（用户裁定 2 的裁定 ①）。
* ``served_by_platform`` —— 这一次调用最终由哪一侧的凭证服务。
  ``LLMFallbackChain`` 在构建时就把命中平台目录 ``mediahub_models`` 的模型预解析
  成 admin 凭证的 adapter，所以「命中目录」⟺「平台付钱」，与 run 级 origin 无关。
  三态：``True`` 平台付 / ``False`` 用户付 / ``None`` **这条链没有 fallback 机制**
  （直连 adapter、流式分块里拿不到响应体），此时一个凭证服务整轮，run 级 origin
  就是精确答案。

返回 ``None`` 表示「这一步不计入 BYOK 道」，**不是 0**：下游 ``fold_step_end``
靠 payload 里有没有 ``byok_cents`` 这个键分流，缺席与 0 在账上同值、在语义上不同。
"""

from __future__ import annotations

from typing import Any, Optional


def step_byok_cents(
    cost_cents: Any,
    credential_origin: Optional[str],
    served_by_platform: Optional[bool],
) -> Optional[float]:
    """这一步里应当记进 BYOK 道的分数；不该记就返回 ``None``。"""
    if credential_origin != "byok":
        return None
    if served_by_platform:
        return None
    # ``bool`` 是 ``int`` 的子类，而 ``True`` 不是一个价钱。价钱未知（None）时
    # 报 ``None`` 而不是 0 —— 写 0 是把「不知道」伪装成「没花钱」。
    if isinstance(cost_cents, bool) or not isinstance(cost_cents, (int, float)):
        return None
    return float(cost_cents)


__all__ = ["step_byok_cents"]
```

`app/services/ai/llm/llm_fallback_chain.py`，在 `health_registry: Optional[Any] = None`（:119）之后加字段：

```python
    # 构建时命中平台目录 ``mediahub_models`` 的模型全集（``fallback_wiring``
    # 预解析出来的那些）。它们的 adapter 用 admin 凭证，所以这一步的钱是**平台**
    # 付的，与这条 run 的 ``credential_origin`` 无关 —— 半程 BYOK（主模型是
    # 用户的、fallback 落到目录行）的唯一判据就是它。空集 = 调用方没有告诉我们
    # 谁是平台侧，下游据此退到 run 级 origin。
    platform_models: frozenset[str] = frozenset()
```

同文件成功分支，`response["_actual_model"] = model`（:310）之后加一行：

```python
            # 与 ``_actual_model`` 并列：那一个说「最后跑的是哪个模型」，这一个说
            # 「那次调用的钱谁付的」。两个都是 harness 侧注入的下划线键，不进
            # provider 的 wire 形状，消费方 ``AgentRunner._step_ended`` 读它。
            response["_served_by_platform"] = model in self.platform_models
```

`app/services/ai/llm/fallback_wiring.py` 的 `return LLMFallbackChain(`（:231）加一行：

```python
        platform_models=frozenset(_platform_adapters),
```

`app/services/ai/runner/agent_runner.py`：`_step_ended` 签名（:1178-1180）改成

```python
    async def _step_ended(
        self,
        recorder,
        composed,
        step: int,
        t0: float,
        usage,
        finish_reason,
        *,
        served_by_platform: Optional[bool] = None,
    ) -> None:
```

同函数 `await emit_event(recorder, "step_end", {...})`（:1216-1226）改成：

```python
        # BYOK 免扣（用户裁定 2）：这一步的钱是不是用户自己的 key 付的，只有
        # 这里同时知道「花了多少」与「谁的 adapter 服务的」。缺席即平台付 ——
        # 写一个 0 会让下游分不清「平台付的」和「用户付了 0 分」。
        from app.services.ai.billing.byok_step import step_byok_cents

        payload = {
            "turn": 1,
            "step": step,
            "model": getattr(composed, "model", None),
            "usage": {"prompt": prompt, "completion": completion, "cached": cached},
            "cost_cents": cost,
            "duration_ms": int((_time.monotonic() - t0) * 1000),
            "finish_reason": finish_reason,
        }
        byok = step_byok_cents(
            cost, getattr(recorder, "credential_origin", None), served_by_platform
        )
        if byok is not None:
            payload["byok_cents"] = byok
        await emit_event(recorder, "step_end", payload, turn=1, step=step)
```

缓冲路调用点（:1898-1905）加一个关键字：

```python
            await self._step_ended(
                recorder,
                composed,
                iteration,
                _t0,
                resp.get("usage"),
                (resp.get("choices") or [{}])[0].get("finish_reason"),
                # 只有响应体里才有这个标记；不是 LLMFallbackChain 的 adapter
                # （直连、子 agent 栈）拿到 None，判据自动退到 run 级 origin。
                served_by_platform=resp.get("_served_by_platform"),
            )
```

流式路调用点（:762-764）**一个字不改** —— 流里没有响应体，`served_by_platform`
取默认 `None`，正是「退到 run 级 origin」那一支。在它上面补一行注释说明这不是遗漏：

```python
                # ``served_by_platform`` 不传：流式分块里没有响应体，拿不到
                # 那个标记。有 ``stream`` 的 adapter 都不是 LLMFallbackChain
                # （它没有 stream），即一个凭证服务整轮，run 级 origin 精确。
                await self._step_ended(
                    recorder, composed, iteration, _t0, final_usage, final_finish
                )
```

`app/services/ai/runner/folds/step.py` 的 `fold_step_end`，在 `recompute_spent(cost)` **之前**插入：

```python
        # 与 ``own_cents`` 平行的 BYOK 道（用户裁定 2）。**不进** ``spent_cents``：
        # 那是「真花了多少」，预算门禁与 UI 读它，BYOK 的钱用户真付了。这条道只
        # 回答「这里面有多少不该再收平台积分」。
        byok = payload.get("byok_cents")
        if isinstance(byok, (int, float)) and not isinstance(byok, bool):
            cost["own_byok_cents"] = round(
                float(cost.get("own_byok_cents") or 0.0) + float(byok), 4
            )
        recompute_spent(cost)
```

`app/services/ai/runner/run_projection.py` 的空 `cost` 视图，在 `"media_cents": 0.0,` 之后加：

```python
            # BYOK 道（用户裁定 2）：与 own / by_child / media 三个分量一一平行，
            # 相减得到「平台真付了的那部分」。三条都**不进** spent_cents。
            # 本 Task 只填第一条，另两条由图片 / 子 agent 那个 Task 补齐。
            "own_byok_cents": 0.0,
```

- [ ] **Step 4: 跑测试确认 runner + fold 那 5 条绿**

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_byok_step_bucket.py -q
```
预期：9 passed。

- [ ] **Step 5: 写 `credential_origin` / `parent_run_id` 接线的失败测试（红）**

```python
# backend/tests/ai/test_credential_origin_wiring.py
"""``ResolvedAIConfig.origin`` 一直算得好好的，只是在进 ``RunRecorder`` 之前
被丢掉了（``ai_library_chat_wiring.py`` 只取 ``.provider_config``）。这一组钉住
它真的走完全程，以及五个 agent 栈调用点都把它填进 recorder。

同时钉住 ``RunRecorder`` 两个新字段的**默认值形状**：必须是简单默认值（→ 类属性），
不能是 ``default_factory``。全仓有大量 ``RunRecorder.__new__(...)`` 造的测试桩不
设这两个字段，它们靠类属性读到 None；换成 factory 会让那些测试整片 AttributeError。
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parents[2]

#: 五个 agent 栈调用点。前四个从 ``AgentRunnerStack.credential_origin`` 取，
#: 第五个（forced-declare）自己解析凭证，取法见该文件。
_WIRED = [
    "app/services/ai/chat/ai_library_chat_service.py",
    "app/services/chat/conversation_agent_turn.py",
    "app/services/workforce/agent_worker.py",
    "app/services/ai/runner/subagent_task_service.py",
    "app/services/ai/tools/forced_finish_declaration.py",
]


def _recorder_kwargs(path: str) -> list[set[str]]:
    """该文件里每一处 ``RunRecorder(...)`` 的关键字名集合。"""
    tree = ast.parse((_BACKEND / path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RunRecorder"
        ):
            out.append({kw.arg for kw in node.keywords if kw.arg})
    return out


@pytest.mark.parametrize("path", _WIRED)
def test_every_agent_stack_dispatch_stamps_the_credential_origin(path):
    """漏一处的后果是静默的：那条链的 BYOK run 会被按平台价扣分，而所有测试照绿。"""
    calls = _recorder_kwargs(path)
    assert calls, f"{path} 里没有 RunRecorder(...)——调用点搬家了，更新本清单"
    for kwargs in calls:
        assert "credential_origin" in kwargs, f"{path}: {sorted(kwargs)}"


@pytest.mark.parametrize(
    "path",
    [
        "app/services/workforce/agent_worker.py",
        "app/services/ai/runner/subagent_task_service.py",
    ],
)
def test_the_two_child_dispatch_sites_hand_the_recorder_its_parent(path):
    """root 一次扣的判据是 ``self.parent_run_id is None``。这两处不填，
    每个子 run 都会自认 root 并按**整棵树**再扣一次。"""
    for kwargs in _recorder_kwargs(path):
        assert "parent_run_id" in kwargs, f"{path}: {sorted(kwargs)}"


def test_the_two_new_fields_are_plain_defaults_not_factories():
    from app.services.ai.runner.run_recorder import RunRecorder

    assert RunRecorder.credential_origin is None
    assert RunRecorder.parent_run_id is None


def test_the_chat_stack_carries_the_resolved_origin():
    """``AgentRunnerStack`` 是 wiring 与 recorder 之间唯一的传声筒。"""
    from app.services.ai.chat.ai_library_chat_wiring import AgentRunnerStack

    stack = AgentRunnerStack(
        runner=None,
        graph_facts=[],
        primary_model="m",
        fallback_chain_active=False,
        credential_origin="byok",
    )
    assert stack.credential_origin == "byok"
```

```bash
cd <worktree>/backend && uv run pytest tests/ai/test_credential_origin_wiring.py -q
```
预期：全红（`AttributeError: type object 'RunRecorder' has no attribute
'credential_origin'`、`TypeError: AgentRunnerStack got an unexpected keyword
argument`、五个 parametrize 的 assert 失败）。

- [ ] **Step 6: `RunRecorder` 两个字段 + 六处接线（绿其二）**

`app/services/ai/runner/run_recorder.py`，`RunRecorder` 的 `metadata: dict[str, Any] = field(default_factory=dict)` 之后、`# Internal state` 注释之前插入：

```python
    # 这条 run 用的是谁的凭证：``ResolvedAIConfig.origin`` 的四态
    # ``governance`` / ``platform`` / ``byok`` / ``env``，None = 调用方没说。
    # **只有 ``byok`` 是用户自己的钱**（``env`` 是 BYOK 形状但没有 api_key，
    # adapter factory 回落平台凭证）。用途只有一个：终态分桶时把用户自己付的
    # 那部分从积分里减掉（用户裁定 2）。
    credential_origin: Optional[str] = None
    # 派发这条 run 的父 run（``agent_runs.id`` 的字符串形），root 为 None。
    # ``metadata["parent_run_id"]`` 里也有一份，但那是给人看的 jsonb；扣费判据
    # （root 一次扣）要一个不会被 metadata 结构变动带偏的字段。
    # ⚠️ 两个字段都必须是**简单默认值** —— 简单默认值会成为类属性，全仓用
    # ``RunRecorder.__new__(...)`` 造桩的测试才读得到 None；换成
    # ``field(default_factory=...)`` 就没有类属性，那些测试会整片 AttributeError。
    parent_run_id: Optional[str] = None
```

`app/services/ai/chat/ai_library_chat_wiring.py`：`AgentRunnerStack` 末尾加字段

```python
    # 这次回合用的是谁的凭证（``resolve_chat_config`` 已经算好，此前在 :328
    # 被当场丢弃）。recorder 构造点从这里取，传给 ``RunRecorder``。
    credential_origin: Optional[str] = None
```

同文件 :328 附近，`user_provider_config = _chat_cfg.provider_config` 之后加一行：

```python
    # origin 不是凭证，是「这些凭证是谁的」。此前只取 provider_config，这个标记
    # 当场丢掉，于是 BYOK 的 run 在账上与平台 run 无从区分（用户裁定 2）。
    credential_origin = _chat_cfg.origin
```

同文件 `return AgentRunnerStack(` 加 `credential_origin=credential_origin,`。

四个 agent 栈调用点，在各自的 `RunRecorder(` 里加参数（`stack` 变量都在作用域内，
作者已核实：`ai_library_chat_service.py` 的 `stack` 在 :779 绑定、recorder 在 :1498，
同属 `_run_session_turn_inner`）：

| 文件 | 加什么 |
|---|---|
| `ai_library_chat_service.py:1498` | `credential_origin=stack.credential_origin,` |
| `conversation_agent_turn.py:459` | `credential_origin=stack.credential_origin,` |
| `workforce/agent_worker.py:291` | `credential_origin=stack.credential_origin,` 与 `parent_run_id=str(parent_run_id) if parent_run_id else None,` |
| `ai/runner/subagent_task_service.py:564` | `credential_origin=stack.credential_origin,` 与 `parent_run_id=str(parent_run_id) if parent_run_id else None,` |

`ai/tools/forced_finish_declaration.py` 自己解析凭证（:148-160 的 if/else）。在
`RunRecorder(` 里加：

```python
        # 目录命中 = admin 凭证 = 平台付；否则用 resolve_chat_config 判出来的
        # 四态。这条路有 team_id（session 的），所以它真的会扣分，不能漏。
        credential_origin="platform" if hit else chat_cfg.origin,
```

⚠️ `chat_cfg` 只在 else 分支里存在。先把它提出来：

```python
    chat_cfg = None
    hit = await resolve_mediahub_model(model, "chat")
    if hit:
        ...
    else:
        chat_cfg = await resolve_chat_config(...)
```
然后 recorder 那行写 `credential_origin="platform" if hit else (chat_cfg.origin if chat_cfg else None)`。

- [ ] **Step 7: 跑两组测试 + 根级守卫 + lint**

```bash
cd <worktree>/backend
uv run pytest tests/runner/test_byok_step_bucket.py tests/ai/test_credential_origin_wiring.py -q
uv run pytest tests/runner tests/ai tests/workforce -q
uv run pytest tests/test_*.py -q
uv run ruff check . && uv run black --check . && uv run isort --check-only .
```
预期：全绿。⚠️ `tests/test_*.py`（根级守卫）里有扫描类测试，`RunRecorder` 改字段
最容易在那里炸；先跑它再开 PR。

- [ ] **Step 8: 突变记录**

把 `byok_step.py` 里的 `if served_by_platform: return None` 删掉，重跑
`tests/runner/test_byok_step_bucket.py`，把
`test_a_step_served_by_the_platform_catalog_is_never_byok` 的失败输出贴进 PR body，再改回。

- [ ] **Step 9: 提交 + PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> add -A
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> commit -m "feat(billing): LLM 的 BYOK 标记按步分桶进 cost.own_byok_cents

RunRecorder 加 credential_origin / parent_run_id 两个字段；LLMFallbackChain
在成功响应上注入 _served_by_platform（构建时预解析的平台目录模型全集），
_step_ended 据此给 step_end 加可选 byok_cents，fold 收进与 own_cents 平行的
BYOK 道。spent_cents 与小时表口径不变。"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> push -u origin <branch>
gh pr create --base master --title "feat(billing): LLM BYOK 标记按步分桶（积分改造 1/3）"
```

---

### Task 2: 图片与子 agent 的 BYOK 标记 —— `media_byok_cents` 与 `by_child_byok`

**Files:**
- Modify: `backend/app/services/media/parsers/video_providers/db_registry.py`（`_stamp_provider_key` :151-163；`resolve_image_provider` 的调用点 :211）
- Modify: `backend/app/services/ai/media/image_generation_service.py`（`generate_image` 的 `return asdict(result)` :145）
- Modify: `backend/app/services/library/generated_media_service.py`（`GenerationOrigin` :278-311；`register_deliverable_best_effort(...)` 调用 :414-430）
- Modify: `backend/app/services/deliverables/registry.py`（`register_deliverable` 签名 :63-78；事件 payload :150-162）
- Modify: `backend/app/services/ai/tools/generate_media_tools.py`（`GenerationOrigin(` :117-134）
- Modify: `backend/app/services/ai/runner/folds/deliverables.py`（`fold_deliverable` 尾部）
- Modify: `backend/app/services/ai/runner/folds/subagents.py`（`fold_done` 尾部）
- Modify: `backend/app/services/ai/runner/subagent_task_service.py`（`_cost_cents_of` 旁 :142-167；`_build_envelope` :1012-1023；两处同步 `subagent_done` :650、:672）
- Modify: `backend/app/services/workforce/agent_worker.py`（`content` dict :488-495；异步 `subagent_done` :591）
- Modify: `backend/app/services/ai/runner/run_projection.py`（空 `cost` 视图，补另两条道）
- Test: `backend/tests/runner/test_byok_media_and_children.py`（新建）

**Interfaces:**
- Consumes Task 1 的 `views["cost"]["own_byok_cents"]` 与 `RunRecorder.credential_origin`。
- Produces `BaseImageProvider` 实例上的 `is_byok: bool` 属性（与既有 `provider_key` 并列，同一个 stamp 点贴）。
- Produces `image_generation_service.generate_image(...)` 返回 dict 的新键 `"byok": bool`。
- Produces `GenerationOrigin.byok: bool = False`。
- Produces `register_deliverable(..., byok_cents: Optional[float] = None)` —— **只进事件 payload，不进 `run_deliverables` 的列**（本计划不加迁移）。
- Produces transcript 事件 `deliverable` 与 `subagent_done` 的新可选 payload 键 `byok_cents: float`。
- Produces `views["cost"]["media_byok_cents"]: float`（累加，与 `media_cents` **同一个 `seen` 去重键**）与 `views["cost"]["by_child_byok"]: dict[str, float]`（SET 语义，与 `by_child` 同键同海拔）。
- Produces `subagent_task_service._byok_cents_of(recorder) -> float`。

- [ ] **Step 1: 写失败测试（红）**

```python
# backend/tests/runner/test_byok_media_and_children.py
"""BYOK 免扣的另两条道：生图与子 agent。

``by_child`` 与 ``by_child_byok`` 必须**同海拔**（都是整棵子树的合计）。
``_cost_cents_of`` 的 docstring 写明 ``by_child`` 存的是子树总额 —— 孙子的钱通过
子 run 自己的 ``spent_cents`` 已经含在里面。BYOK 侧要是只报「子 run 自身」的
BYOK，父行 ``by_child - by_child_byok`` 就会把孙子的 BYOK 花费当成平台花费收一遍。
"""

import pytest

pytestmark = pytest.mark.unit


def _views():
    from app.services.ai.runner.run_projection import empty_views

    return empty_views()


# ── deliverable（生图）────────────────────────────────────────────────


def test_a_byok_image_lands_in_the_media_byok_lane():
    from app.services.ai.runner.folds.deliverables import fold_deliverable

    views = _views()
    fold_deliverable(
        views,
        {"kind": "generated_media", "ref_id": "1", "version": 1, "cost_cents": 4.0, "byok_cents": 4.0},
    )
    fold_deliverable(
        views, {"kind": "generated_media", "ref_id": "2", "version": 1, "cost_cents": 6.0}
    )
    cost = views["cost"]
    assert cost["media_cents"] == 10.0
    assert cost["media_byok_cents"] == 4.0
    # 真花了 10 分：BYOK 的钱用户真付了，预算门禁读的 spent_cents 不许缩水。
    assert cost["spent_cents"] == 10.0


def test_a_replayed_image_registration_does_not_double_the_byok_lane():
    """同一个 ``(kind, ref_id, version)`` 第二次到达：既不多计一件，也不多计一次钱，
    **两条道都不许多**（``media_cents`` 的去重键必须同时护住 ``media_byok_cents``）。"""
    from app.services.ai.runner.folds.deliverables import fold_deliverable

    views = _views()
    row = {"kind": "generated_media", "ref_id": "1", "version": 1, "cost_cents": 4.0, "byok_cents": 4.0}
    fold_deliverable(views, row)
    fold_deliverable(views, dict(row))
    assert views["cost"]["media_cents"] == 4.0
    assert views["cost"]["media_byok_cents"] == 4.0


# ── subagent_done（子 agent）──────────────────────────────────────────


def test_a_childs_byok_total_is_set_not_added():
    """``subagent_done`` 可能到达不止一次（重放的 DBOS 步、views 重折）。
    与 ``by_child`` 同样是 SET 语义，否则父行的 BYOK 额随投递次数膨胀。"""
    from app.services.ai.runner.folds.subagents import fold_done

    views = _views()
    row = {"child_run_id": "c1", "mode": "async", "cost_cents": 9.0, "byok_cents": 6.0}
    fold_done(views, row)
    fold_done(views, dict(row))
    assert views["cost"]["by_child"] == {"c1": 9.0}
    assert views["cost"]["by_child_byok"] == {"c1": 6.0}
    assert views["cost"]["spent_cents"] == 9.0


def test_a_child_that_reports_no_byok_leaves_the_lane_empty():
    """键缺席 = 平台付的。不要写 0 —— 缺席与 0 在账上同值，但缺席还说明
    「这个子 run 的发射点根本没接线」，写 0 会把接线缺失伪装成结论。"""
    from app.services.ai.runner.folds.subagents import fold_done

    views = _views()
    fold_done(views, {"child_run_id": "c1", "mode": "sync", "cost_cents": 9.0})
    assert views["cost"]["by_child_byok"] == {}


# ── 两个发射点的取数函数同海拔 ─────────────────────────────────────────


class _Child:
    def __init__(self, cost):
        self.views = {"cost": cost}
        self.credential_origin = "byok"

    def compute_cost_cents(self):
        return 0.0


def test_the_two_emitters_report_the_same_altitude():
    """``_cost_cents_of`` 取整棵子树（``spent_cents``），所以 ``_byok_cents_of``
    也必须取整棵子树 = own_byok + media_byok + Σ by_child_byok。"""
    from app.services.ai.runner.subagent_task_service import (
        _byok_cents_of,
        _cost_cents_of,
    )

    child = _Child(
        {
            "spent_cents": 12.0,
            "own_cents": 5.0,
            "own_byok_cents": 5.0,
            "media_cents": 3.0,
            "media_byok_cents": 0.0,
            "by_child": {"g1": 4.0},
            "by_child_byok": {"g1": 4.0},
        }
    )
    assert _cost_cents_of(child) == 12.0
    assert _byok_cents_of(child) == 9.0  # 5 own + 0 media + 4 孙子


def test_a_child_with_no_step_folds_reports_byok_symmetrically():
    """``_cost_cents_of`` 在 ``spent_cents`` 为 0 时回落到 ``compute_cost_cents()``。
    BYOK 侧必须在**同一个**分支上回落，否则一条没有 step fold 的 BYOK 子 run 会
    报出「花了 7 分、BYOK 0 分」，父行照平台价收它。"""
    from app.services.ai.runner.subagent_task_service import (
        _byok_cents_of,
        _cost_cents_of,
    )

    class _NoFolds(_Child):
        def compute_cost_cents(self):
            return 7.0

    child = _NoFolds({"spent_cents": 0.0})
    assert _cost_cents_of(child) == 7.0
    assert _byok_cents_of(child) == 7.0

    platform = _NoFolds({"spent_cents": 0.0})
    platform.credential_origin = "platform"
    assert _byok_cents_of(platform) == 0.0


def test_a_recorder_that_cannot_answer_reports_zero_not_a_crash():
    from app.services.ai.runner.subagent_task_service import _byok_cents_of

    assert _byok_cents_of(None) == 0.0
    assert _byok_cents_of(object()) == 0.0


# ── 生图链把标记一路带到登记口 ──────────────────────────────────────────


def test_the_generation_origin_carries_the_byok_flag():
    from app.services.library.generated_media_service import GenerationOrigin

    assert GenerationOrigin(kind="agent_run").byok is False
    assert GenerationOrigin(kind="agent_run", byok=True).byok is True


@pytest.mark.asyncio
async def test_a_byok_image_provider_stamps_the_flag_on_the_result(monkeypatch):
    """``resolve_image_provider`` 只返回 ``(provider, actual_model)``，BYOK 行的
    ``source`` 从此贴在 provider 上 —— 与既有的 ``provider_key`` 同一个 stamp 点。"""
    from dataclasses import dataclass

    from app.services.ai.media import image_generation_service as svc

    @dataclass
    class _Result:
        image_url: str = "https://example.test/a.png"

    class _Provider:
        is_byok = True

        async def generate(self, *a, **k):
            return _Result()

    async def _resolve(name=None, *, user_id=None):
        return _Provider(), "doubao-seedream-4-0"

    monkeypatch.setattr(svc, "resolve_image_provider", _resolve, raising=False)
    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        _resolve,
    )
    out = await svc.ImageGenerationService().generate_image(
        project_id="1",
        node_id="n1",
        prompt="a cat",
        provider_name="ark",
        user_id="u1",
    )
    assert out["byok"] is True
```

⚠️ 最后那个用例依赖 `ImageGenerationService.generate_image` 的**真实签名与 provider
注册表的 KeyError 分支**。动手前先读一遍 `app/services/ai/media/image_generation_service.py`
的 `generate_image` 定义，把参数名与必填项对齐（作者核实：DB 路径由
`provider_registry.get_image_provider(provider_name)` 抛 `KeyError` 进入，本仓
image registry 现在是空的，所以这条分支必走）。**若签名对不上，改测试去对齐真签名**，
不要为了测试改生产签名。

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_byok_media_and_children.py -q
```
预期：全红（`KeyError: 'media_byok_cents'` / `'by_child_byok'`、
`ImportError: cannot import name '_byok_cents_of'`、
`TypeError: GenerationOrigin got an unexpected keyword argument 'byok'`、
`KeyError: 'byok'`）。

- [ ] **Step 2: 两条 fold 道（绿其一）**

`app/services/ai/runner/run_projection.py` 空 `cost` 视图，在 Task 1 加的
`"own_byok_cents": 0.0,` 之后补：

```python
            "media_byok_cents": 0.0,
            # 与 ``by_child`` 同键、同海拔（每项是那棵子树的 BYOK 合计），
            # 所以 ``by_child - by_child_byok`` 才是那棵子树的平台额。
            "by_child_byok": {},
```

`folds/deliverables.py` 的 `fold_deliverable`，把尾部那段改成：

```python
    cents = payload.get("cost_cents")
    if isinstance(cents, (int, float)) and not isinstance(cents, bool):
        cost = views["cost"]
        cost["media_cents"] = round(
            float(cost.get("media_cents") or 0.0) + float(cents), 4
        )
        # BYOK 道与 ``media_cents`` 共用上面那个 ``seen`` 去重键（本函数在 key
        # 命中时已经 return），所以重复到达既不多计一件，也不多计任何一条道的钱。
        byok = payload.get("byok_cents")
        if isinstance(byok, (int, float)) and not isinstance(byok, bool):
            cost["media_byok_cents"] = round(
                float(cost.get("media_byok_cents") or 0.0) + float(byok), 4
            )
        recompute_spent(cost)
    views["view"]["outputs"] = outputs
    return views
```

`folds/subagents.py` 的 `fold_done`，把尾部那段改成：

```python
    cents = payload.get("cost_cents")
    if isinstance(cents, (int, float)) and not isinstance(cents, bool):
        # SET, never add: this event can arrive more than once for the same
        # child (a replayed DBOS step, a re-fold of stored views), and the
        # parent's cost must not grow once per arrival.
        views["cost"]["by_child"] = {
            **(views["cost"].get("by_child") or {}),
            str(child_run_id): float(cents),
        }
        # 同样 SET。两条道**同海拔**：都是那棵子树的合计（见发射点
        # ``_cost_cents_of`` / ``_byok_cents_of`` 的 docstring），父行拿
        # ``by_child - by_child_byok`` 减出平台额才成立。
        byok = payload.get("byok_cents")
        if isinstance(byok, (int, float)) and not isinstance(byok, bool):
            views["cost"]["by_child_byok"] = {
                **(views["cost"].get("by_child_byok") or {}),
                str(child_run_id): float(byok),
            }
        recompute_spent(views["cost"])
    return views
```

- [ ] **Step 3: 子 agent 两个发射点（绿其二）**

`app/services/ai/runner/subagent_task_service.py`，在 `_cost_cents_of`（:167）之后加：

```python
def _byok_cents_of(recorder: Any) -> float:
    """那棵子树里由**用户自己的 key** 付掉的部分，供父行 ``cost.by_child_byok``。

    与 ``_cost_cents_of`` **严格同海拔**，两个分支都对齐：

    * 有 step fold 时取三条 BYOK 道之和（own + media + 孙子），对应
      ``_cost_cents_of`` 取 ``spent_cents``（own + media + 孙子）；
    * ``spent_cents`` 为 0 而 ``_cost_cents_of`` 回落到 ``compute_cost_cents()``
      时，这里也回落 —— 此时没有任何 step fold，唯一可用的判据是这条 run 的
      ``credential_origin``。不回落的后果：一条没有 step fold 的 BYOK 子 run
      报出「花了 7 分、BYOK 0 分」，父行照平台价收它。

    海拔不一致的代价是静默的：父行拿 ``by_child - by_child_byok`` 减平台额，
    BYOK 侧少报多少，就多收多少。

    报不出来一律 0.0 —— 遥测永远不该让一次 turn 失败。
    """
    try:
        cost = (getattr(recorder, "views", None) or {}).get("cost") or {}
        if float(cost.get("spent_cents") or 0.0):
            children = sum(
                float(v or 0) for v in (cost.get("by_child_byok") or {}).values()
            )
            return round(
                float(cost.get("own_byok_cents") or 0.0)
                + float(cost.get("media_byok_cents") or 0.0)
                + children,
                4,
            )
        if getattr(recorder, "credential_origin", None) == "byok":
            return _cost_cents_of(recorder)
        return 0.0
    except Exception:  # noqa: BLE001 — telemetry never fails a turn
        return 0.0
```

`_build_envelope` 的返回 dict，在 `"cost_cents": ...` 之后加：

```python
            # 其中由用户自己的 key 付掉的部分（用户裁定 2）。与上一行同海拔
            # （整棵子树），父行减出平台额靠这个对齐。
            "byok_cents": _byok_cents_of(recorder) if recorder is not None else 0.0,
```

`_failed(...)` 的 envelope 同样加 `"byok_cents": 0.0,`（键恒在：读方永远不必分辨
「没花钱」与「没有这个字段」）。

两处同步 `subagent_done` payload，在 `"cost_cents": _cost_cents_of(recorder),`
（:650）与 `"cost_cents": _cost_cents_of(announced_recorder),`（:672）之后各加一行
`"byok_cents": _byok_cents_of(recorder),` / `"byok_cents": _byok_cents_of(announced_recorder),`。

`app/services/workforce/agent_worker.py`：`content` dict（:488-495）加

```python
        "byok_cents": envelope.get("byok_cents") or 0,
```

异步 `subagent_done` payload（:591）加

```python
                    "byok_cents": content["byok_cents"],
```

- [ ] **Step 4: 生图链（绿其三）**

`app/services/media/parsers/video_providers/db_registry.py`：

```python
def _stamp_provider_key(
    provider: object, actual_provider: str, *, source: str = "catalog"
) -> None:
    """Record which catalog row's ``actual_provider`` built this provider,
    and WHOSE credentials it runs on.

    （原 docstring 保留不动，下面是新增的一段。）

    ``source`` 是目录行的**层**（``"catalog"`` / ``"byok"``）。BYOK 行的
    ``actual_provider`` **刻意**用协议名（如 ``ark``，与平台目录同键），所以
    ``media_price_cents()`` 照样命中平台价 —— 这正是「BYOK 出的图被按平台价扣分」
    的确切机理。价钱该算（血缘要真），但积分不该收，所以判据必须跟着 provider
    对象走到登记口（用户裁定 2）。
    """
    provider.provider_key = actual_provider  # type: ignore[attr-defined]
    provider.is_byok = source == "byok"  # type: ignore[attr-defined]
```

调用点（:211）改成：

```python
    _stamp_provider_key(
        provider, actual_provider, source=str(row.get("source") or "catalog")
    )
```

⚠️ 先确认没有别的调用点：

```bash
cd <worktree>/backend && grep -rn "_stamp_provider_key" app/
```
有别的就一并改（新参数有默认值，不改也不会报错 —— 这正是危险处：漏改的那条链
`is_byok` 恒 False，静默按平台价收）。

`app/services/ai/media/image_generation_service.py` 的 `return asdict(result)`（:145）改成：

```python
            # provider 对象上的层标记（``db_registry._stamp_provider_key``）。
            # in-proc registry 那条分支的 provider 没有这个属性 → False，
            # 那条分支今天是空的（house rule：provider 配置住数据库）。
            return {
                **asdict(result),
                "byok": bool(getattr(image_provider, "is_byok", False)),
            }
```

`app/services/library/generated_media_service.py` 的 `GenerationOrigin` 加字段：

```python
    # 这次生成用的是用户自己的 provider key。``cost_cents`` 照算（血缘里那是真价），
    # 只是不该再收平台积分（用户裁定 2）。缺省 False —— 画布 / DBOS 分镜那些
    # 调用点不接线也不会误判成 BYOK。
    byok: bool = False
```

同文件 `register_deliverable_best_effort(` 调用（:414-430），在 `cost_cents=cost_cents,`
之后加：

```python
            # BYOK 时把同一个价钱同时写进 BYOK 道；不是 BYOK 就不写这个键
            # （缺席 = 平台付的）。⚠️ 不改 ``origin``（调用方的对象，不可变纪律）。
            byok_cents=cost_cents if origin.byok else None,
```

`app/services/deliverables/registry.py` 的 `register_deliverable` 签名，在
`cost_cents: Optional[float] = None,` 之后加：

```python
    byok_cents: Optional[float] = None,
```
并在 docstring 里加一段：

```
    ``byok_cents`` **只进事件 payload，不进 ``run_deliverables`` 的列** —— 本期
    不加迁移，这个数唯一的消费方是 ``folds/deliverables.py``（折进
    ``cost.media_byok_cents``）。要做可审计的行级归属才需要加列，那是另一张票。
```

事件 payload（:150-162）在 `"cost_cents": cost_cents,` 之后加：

```python
            **({"byok_cents": byok_cents} if byok_cents is not None else {}),
```

`app/services/ai/tools/generate_media_tools.py` 的 `GenerationOrigin(`（:117-134）
在 `derivation_kind="image_gen",` 之后加：

```python
                        # adapter 解析出来的层标记（image_generation_service 在
                        # 返回 dict 上并列注入）。用户自己的 key 出的图不收积分。
                        byok=bool((raw or {}).get("byok")),
```

- [ ] **Step 5: 跑测试**

```bash
cd <worktree>/backend
uv run pytest tests/runner/test_byok_media_and_children.py -q
uv run pytest tests/runner tests/workforce tests/services/deliverables tests/services/library -q
uv run pytest tests/test_*.py -q
uv run ruff check . && uv run black --check . && uv run isort --check-only .
```
预期：全绿。若 `tests/services/...` 路径不存在，先 `ls tests/` 找对应目录再跑。

- [ ] **Step 6: 突变记录**

把 `folds/subagents.py` 里 `by_child_byok` 的 SET 改成 `+`（累加），重跑
`test_a_childs_byok_total_is_set_not_added`，贴失败输出，再改回。

- [ ] **Step 7: 提交 + PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> add -A
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> commit -m "feat(billing): 生图与子 agent 的 BYOK 标记进 cost 的另两条道

db_registry 在既有 stamp 点旁贴 is_byok → image_generation_service 返回 byok
→ GenerationOrigin.byok → 登记口 byok_cents → fold 收进 media_byok_cents（与
media_cents 同一个 seen 去重键）；subagent_done 加 byok_cents，与 cost_cents
同海拔（整棵子树），SET 进 by_child_byok。spent_cents 口径不变。"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> push -u origin <branch>
gh pr create --base master --title "feat(billing): 生图/子 agent 的 BYOK 标记（积分改造 2/3）"
```

---

### Task 3: 分桶 + root 一次取整扣费 + 晚到子 run 补扣 + 竞态口径

> ⚠️ **这个 PR 上线会真的改变用户余额。** 它必须最后合，合并前把 Task 1、2 都
> rebase 进来并确认三条 BYOK 道在真栈上确实有数（Task 4 Step 1 的探针）。

**Files:**
- Create: `backend/app/services/ai/billing/tree_charge.py`
- Modify: `backend/app/services/ai/runner/run_recorder.py`（`_finish` :818-1015）
- Modify: `backend/app/services/ai/billing/token_billing.py`（`reconcile_run` 签名 :210-233、docstring :234-262、审计插入 :277-291）
- Test: `backend/tests/runner/test_root_once_charge.py`（新建）
- Test: `backend/tests/test_hourly_usage_is_own_spend.py`（**反转**两个用例）

**Interfaces:**
- Consumes Task 1 的 `RunRecorder.parent_run_id` 与 `views["cost"]["own_byok_cents"]`；Task 2 的 `media_byok_cents` / `by_child_byok`。
- Produces `app.services.ai.billing.tree_charge.TreeBuckets`（frozen dataclass：`tree_total` / `tree_platform` / `own_total` / `own_platform`，单位分）。
- Produces `bucket_tree(folded: Optional[Mapping[str, Any]], own_cents: Optional[float]) -> TreeBuckets`。
- Produces `async root_run_is_settled(*, run_id: Optional[str], parent_run_id: Optional[str]) -> bool`。
- Produces `reconcile_run(..., usage_cost_points: Optional[float] = None)` —— 缺省沿用 `cost_points`，既有调用方零改动。
- **不改**：`billing/run_tree_points.charged_points_for_run_trees`（按 `root_run_id` 全树合计，扣法从多行变一行、同一个数）、`point_transactions` 形状、`AGENT_RUN_REFERENCE_TYPE`、mig 474 的 partial 索引谓词、`ai_usage_hourly` 口径（仍收 `own_media_cents`）、`agent_runs.cost_cents` 列（仍是树总额）。

- [ ] **Step 1: 写分桶与扣费规则的失败测试（红）**

```python
# backend/tests/runner/test_root_once_charge.py
"""用户裁定：一个回合的积分 = ceil(整棵树的**平台**花费之和)，只在 root 定稿时
扣一次；BYOK 的调用一分不扣。

此前是「每条 run 各 ceil 一次自身花费」——一次带委派的回合在 point_transactions
里是 6 行，真栈实测 ≈¢0.92 被收成 7 分。读方 charged_points_for_run_trees 按
root_run_id 全树合计，所以改成 root 一次扣之后它一行不用改：合计从「6 行相加」
变成「1 行」，同一个数。
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.billing.tree_charge import bucket_tree
from app.services.ai.runner import run_recorder as rr

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ── 纯分桶 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("_unused", [None])
def test_the_tree_bucket_subtracts_all_three_byok_lanes(_unused):
    b = bucket_tree(
        {
            "own_byok_cents": 4.0,
            "media_cents": 5.0,
            "media_byok_cents": 5.0,
            "by_child": {"c1": 3.0, "c2": 4.0},
            "by_child_byok": {"c1": 3.0},
        },
        10.0,
    )
    assert b.tree_total == 22.0          # 10 own + 7 children + 5 media
    assert b.tree_platform == 10.0       # 22 − (4 + 3 + 5)
    assert b.own_total == 15.0           # 10 own + 5 media
    assert b.own_platform == 6.0         # 15 − (4 + 5)


def test_a_byok_lane_can_never_exceed_the_component_it_belongs_to():
    """两侧来源不同：``own_cents`` 可能来自 token 费率（``compute_cost_cents``），
    而 ``own_byok_cents`` 来自 step fold。BYOK 道虚高会把真该收的钱抹成 0，
    所以相减前钳位 —— 少收一次的代价远小于「整棵树静默免单」。"""
    b = bucket_tree({"own_byok_cents": 999.0}, 10.0)
    assert b.own_platform == 0.0
    assert b.tree_platform == 0.0


def test_no_folds_at_all_is_a_plain_platform_tree():
    b = bucket_tree(None, 3.0)
    assert (b.tree_total, b.tree_platform, b.own_total, b.own_platform) == (
        3.0,
        3.0,
        3.0,
        3.0,
    )


def test_junk_in_the_lanes_reads_as_zero_not_as_a_crash():
    b = bucket_tree(
        {"own_byok_cents": "4", "by_child": None, "media_byok_cents": True}, 10.0
    )
    assert b.tree_platform == 10.0


# ── _finish 的扣费分支 ──────────────────────────────────────────────────


class _Writer:
    def __init__(self, views):
        self.views = views

    async def refold_external_slices(self):
        return None


def _recorder(*, views, parent_run_id=None, run_id="900000000000001"):
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = run_id
    rec.user_id, rec.agent_id = uuid4(), uuid4()
    rec.team_id, rec.project_id, rec.session_id = 42, None, None
    rec.model, rec.trigger, rec.attribution = "doubao-seed-2-0-lite", "chat", "direct_human"
    rec._prompt_tokens, rec._completion_tokens, rec._cached_input_tokens = 10, 20, 0
    rec._skill_slugs_used, rec._output_summary = [], None
    rec._prompt_rate = rec._completion_rate = None
    rec._event_writer = _Writer(views)
    rec.parent_run_id = parent_run_id
    rec.credential_origin = None
    return rec


@pytest.fixture
def billed(monkeypatch):
    """拦下 reconcile_run、agent_runs 的 UPDATE、小时表 upsert 与检索投影。"""
    charged = AsyncMock()
    stmts: list[Any] = []

    @asynccontextmanager
    async def _scope():
        class _S:
            async def execute(self, stmt):
                stmts.append(stmt)

                class _R:
                    rowcount = 1

                return _R()

        yield _S()

    import app.db.session as db_session
    import app.services.ai_usage as ai_usage

    monkeypatch.setattr(db_session, "write_scope", _scope)
    monkeypatch.setattr(ai_usage, "write_scope", _scope)
    monkeypatch.setattr(ai_usage, "record_usage", AsyncMock())
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    monkeypatch.setattr(
        "app.services.search.projection.project_run_best_effort", AsyncMock()
    )
    return charged


_TREE = {
    "cost": {
        "own_cents": 10.0,
        "own_byok_cents": 0.0,
        "by_child": {"c1": 3.0, "c2": 4.0},
        "by_child_byok": {},
        "media_cents": 5.0,
        "media_byok_cents": 0.0,
    }
}


async def test_the_root_charges_the_whole_tree_exactly_once(billed):
    await _recorder(views=_TREE)._finish(status="completed")
    assert billed.await_count == 1
    kwargs = billed.await_args.kwargs
    assert kwargs["cost_points"] == 22.0          # 整棵树
    assert kwargs["usage_cost_points"] == 15.0    # 审计行仍记自身真实花费
    assert kwargs["byo_key"] is False


async def test_a_child_does_not_charge_while_its_root_is_still_running(billed, monkeypatch):
    """root 会替它收（它在 root 的 by_child 里）。两边都收就是对同一笔钱收两次。"""
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.root_run_is_settled",
        AsyncMock(return_value=False),
    )
    await _recorder(
        views={"cost": {"own_cents": 3.0, "by_child": {}, "media_cents": 0.0}},
        parent_run_id="800000000000001",
    )._finish(status="completed")
    assert [c.kwargs["cost_points"] for c in billed.await_args_list] == [0.0]
    # 审计行照写：用量表要记真实花费（用户裁定 2）。
    assert billed.await_args.kwargs["usage_cost_points"] == 3.0


async def test_a_late_child_charges_its_own_platform_spend(billed, monkeypatch):
    """root 已经终态 → 它的 refold 快照里没有这个子 run，谁也不会替它收。"""
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.root_run_is_settled",
        AsyncMock(return_value=True),
    )
    await _recorder(
        views={
            "cost": {
                "own_cents": 3.0,
                "own_byok_cents": 1.0,
                "by_child": {},
                "media_cents": 0.0,
            }
        },
        parent_run_id="800000000000001",
    )._finish(status="completed")
    assert billed.await_args.kwargs["cost_points"] == 2.0   # 3 − 1 BYOK


async def test_the_refold_race_bills_the_child_at_most_once(billed, monkeypatch):
    """竞态：子 run 在 root 的 ``refold_external_slices()`` 之后、那条 UPDATE
    之前结束。root 的快照里没有它（所以 root 不收），它看到的 root 还是
    ``running``（所以它也不收）—— 宁少收不重收。

    可证伪点：断言这个子 run 的 3 分**恰好出现 0 次或 1 次，绝不 2 次**。
    把 ``_finish`` 里那个 elif 改成无条件补扣，这条会立刻转红。"""
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.root_run_is_settled",
        AsyncMock(return_value=False),
    )
    child_views = {"cost": {"own_cents": 3.0, "by_child": {}, "media_cents": 0.0}}
    await _recorder(views=child_views, parent_run_id="800000000000001")._finish(
        status="completed"
    )
    # root 的快照没赶上这个子 run。
    root_views = {
        "cost": {"own_cents": 10.0, "by_child": {}, "media_cents": 0.0}
    }
    await _recorder(views=root_views, run_id="800000000000001")._finish(
        status="completed"
    )
    charged_amounts = [c.kwargs["cost_points"] for c in billed.await_args_list]
    assert charged_amounts == [0.0, 10.0]
    assert sum(1 for a in charged_amounts if a == 3.0) <= 1


async def test_a_pure_byok_tree_is_reported_as_byo_key_not_as_zero_spend(billed):
    """产品含义不同：一个是「你自己付了」，一个是「什么都没烧」。
    ``ReconcileResult.note`` 两者必须分得开（裁定 ⑤）。"""
    await _recorder(
        views={
            "cost": {
                "own_cents": 8.0,
                "own_byok_cents": 8.0,
                "by_child": {},
                "media_cents": 0.0,
            }
        }
    )._finish(status="completed")
    kwargs = billed.await_args.kwargs
    assert kwargs["cost_points"] == 0.0
    assert kwargs["byo_key"] is True


async def test_a_tree_that_spent_nothing_is_not_reported_as_byo_key(billed):
    await _recorder(
        views={"cost": {"own_cents": 0.0, "by_child": {}, "media_cents": 0.0}}
    )._finish(status="completed")
    # 一分没花：没有审计行可写，也没有钱可扣。
    assert billed.await_count == 0


async def test_the_idempotency_guard_still_covers_the_new_branch(billed, monkeypatch):
    """rowcount 为 0 = 别人已经把这条 run 收工了。root-once 之后单次金额更大，
    这道守卫更不能漏。"""

    @asynccontextmanager
    async def _lost():
        class _S:
            async def execute(self, stmt):
                class _R:
                    rowcount = 0

                return _R()

        yield _S()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "write_scope", _lost)
    await _recorder(views=_TREE)._finish(status="completed")
    billed.assert_not_awaited()
```

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_root_once_charge.py -q
```
预期：`ModuleNotFoundError: app.services.ai.billing.tree_charge`（分桶四条），
其余 `KeyError: 'usage_cost_points'` 或 `assert 15.0 == 22.0`。

- [ ] **Step 2: 分桶模块（绿其一）**

新建 `backend/app/services/ai/billing/tree_charge.py`：

```python
"""一棵 run 树上「平台真付了钱的那部分」—— 积分只按它扣（用户裁定）。

``RunRecorder._finish`` 里的花费有三个分量，各有独立来源：

* ``own_cents``   —— 这条 run 自己的 LLM 步（``step_end`` 折叠，或 token 费率算出）
* ``by_child``    —— 每个**直接**子 run 一项，每项是那棵子树的合计（``subagent_done``）
* ``media_cents`` —— 生图等媒体产出的精确价（``deliverable``）

本计划给每个分量配一条平行的 ``*_byok`` 道，相减得到平台侧。

⚠️ **``by_child`` 与 ``by_child_byok`` 必须同海拔**（都是子树合计）。孙子的钱通过
子 run 自己的 ``spent_cents`` 已经含在 ``by_child`` 里且不重复；BYOK 侧要是只报
「子 run 自身」，减出来的平台额会把孙子的 BYOK 花费当成平台花费收一遍。两个发射点
``subagent_task_service._cost_cents_of`` / ``_byok_cents_of`` 的 docstring 记着
这条不变量。

⚠️ **每条 BYOK 道在相减前钳位到它所属的分量**。两侧来源不同（``own_cents`` 可能
来自 token 费率，而 ``own_byok_cents`` 来自 step fold），一条虚高的 BYOK 道会把
真该收的钱抹成 0 —— 那是静默免单。钳位之后最坏情况只是少收一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


def _num(value: Any) -> float:
    """非数字（含 ``bool``、字符串、None）一律读作 0.0。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _sum(mapping: Any) -> float:
    if not isinstance(mapping, Mapping):
        return 0.0
    return sum(_num(v) for v in mapping.values())


@dataclass(frozen=True)
class TreeBuckets:
    """一次 ``_finish`` 上算好的四个数，单位都是分（cents），都非负。"""

    #: 整棵树的真实花费（own + Σ子树 + media）。就是 ``agent_runs.cost_cents``。
    tree_total: float
    #: 整棵树里**平台**付的那部分 —— root 定稿时按它扣一次。
    tree_platform: float
    #: 这条 run 自身（own + media）的真实花费 —— 用量审计按它记。
    own_total: float
    #: 自身里平台付的那部分 —— root 已经收工后才结束的子 run 按它补扣。
    own_platform: float


def bucket_tree(
    folded: Optional[Mapping[str, Any]], own_cents: Optional[float]
) -> TreeBuckets:
    """把折叠视图的 ``cost`` 切成「真实花费」与「平台花费」两组四个数。

    ``own_cents`` 单独传入而不从视图里取：``_finish`` 在费率已知时用
    ``compute_cost_cents()``（token 口径），只有费率未知才回落到折叠值，
    两种来源在那里已经选好了。
    """
    view = folded or {}
    own = _num(own_cents)
    media = _num(view.get("media_cents"))
    children = _sum(view.get("by_child"))
    # 钳位：见模块 docstring。
    own_byok = min(_num(view.get("own_byok_cents")), own)
    media_byok = min(_num(view.get("media_byok_cents")), media)
    children_byok = min(_sum(view.get("by_child_byok")), children)

    tree_total = round(own + children + media, 4)
    own_total = round(own + media, 4)
    return TreeBuckets(
        tree_total=tree_total,
        tree_platform=round(
            max(tree_total - (own_byok + children_byok + media_byok), 0.0), 4
        ),
        own_total=own_total,
        own_platform=round(max(own_total - (own_byok + media_byok), 0.0), 4),
    )


async def root_run_is_settled(
    *, run_id: Optional[str], parent_run_id: Optional[str]
) -> bool:
    """这条子 run 的 root 是不是已经终态了。

    决定一个**晚到**的子 run 要不要自己补扣：root 收工前最后一次
    ``refold_external_slices()`` 只捞得到那一刻已经写出 ``subagent_done`` 的子
    run，此后才结束的不在 ``by_child`` 里，没人替它收。

    竞态口径（本计划裁定 ④）：子 run 以**自己看到的** root ``status`` 为准，
    root 以**它自己的 refold 快照**为准。窄窗口里两边都判「对方会收」时就都不收
    —— 宁可少收一次，也不对同一笔钱收两次。所以这里读不出来一律 ``False``：
    「不知道」按「root 会替我收」处理。

    ``root_run_id`` 优先，回落到行上的 ``parent_run_id``，再回落到调用方手里那个
    （``_attach_to_parent_run`` 失败时行上两列都是 NULL —— 那是已记票的既有缺陷，
    别让它在这里变成一次误扣）。
    """
    from loguru import logger

    if run_id is None:
        return False
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentRuns

        async with read_scope() as session:
            own = (
                await session.execute(
                    select(AgentRuns.root_run_id, AgentRuns.parent_run_id)
                    .where(AgentRuns.id == int(run_id))
                    .limit(1)
                )
            ).first()
            root_id = None
            if own is not None:
                root_id = own[0] or own[1]
            if root_id is None and parent_run_id is not None:
                root_id = int(parent_run_id)
            if root_id is None:
                return False
            row = (
                await session.execute(
                    select(AgentRuns.status).where(AgentRuns.id == int(root_id)).limit(1)
                )
            ).first()
    except Exception:  # noqa: BLE001 — 一次读失败不该变成一次误扣
        logger.exception(
            "[tree_charge] root status lookup failed run=%s parent=%s",
            run_id,
            parent_run_id,
        )
        return False
    if row is None or row[0] is None:
        return False
    return str(row[0]) != "running"


__all__ = ["TreeBuckets", "bucket_tree", "root_run_is_settled"]
```

- [ ] **Step 3: `reconcile_run` 加 `usage_cost_points` + 重写 docstring（绿其二）**

`app/services/ai/billing/token_billing.py`：签名里 `cost_points: float,` 之后加

```python
    usage_cost_points: Optional[float] = None,
```

审计插入处 `"cost_points": Decimal(str(cost_points)),` 改成：

```python
                        # 审计行记**这条 run 自身的真实花费**，而 ``cost_points``
                        # 是「该扣多少分」。root-once 之后两者不再是同一个数：
                        # root 的扣费基数是整棵树，沿用同一个入参会让
                        # ai_usage_logs 变成「root 记树 + 每个子 run 记自己」双计。
                        "cost_points": Decimal(
                            str(
                                cost_points
                                if usage_cost_points is None
                                else usage_cost_points
                            )
                        ),
```

docstring 从 ``cost_points`` 那段起整体替换（删掉旧的「按自身花费扣」与 BYOK
Stated Limitation 两段）：

```
    ``cost_points`` 是**该扣多少分**的基数，``usage_cost_points`` 是写进
    ``ai_usage_logs`` 的**真实花费**（缺省沿用前者）。root-once 之后两者不同：

    * **root**（``parent_run_id is None``）→ ``cost_points`` = 整棵树的平台花费。
      一个回合只在这里扣一次，向上取整一次。此前是每条 run 各 ceil 一次自身花费
      （真栈实测 ≈¢0.92 被收成 7 分）。
    * **root 已终态后才结束的子 run** → ``cost_points`` = 它自身的平台花费。
      root 的 refold 快照没赶上它，没人替它收。
    * **root 仍在跑的子 run** → ``cost_points=0``，只写审计行。

    读方 ``billing/run_tree_points.charged_points_for_run_trees`` 按 ``root_run_id``
    全树合计，所以这次改动它一行不用动：合计从「多行相加」变成「一行」，同一个数。

    ``byo_key=True`` 只给**纯 BYOK 树**（平台花费为 0 而真实花费 > 0）。它与
    「零花费」在 note 里必须分得开：一个是「你自己付了」，一个是「什么都没烧」。

    仍然不扣的既有缺口：顶层 workforce 派发没有父 run 可继承团队（``team_id``
    为空，下面 ``if not team_id`` 早退）；父 run 自己就没有团队（个人 scope 的
    对话派出去的活）；``team_of_run`` 查库失败降级 None。

    **Stated Limitation（急停不补扣）**：``AGENT_POINTS_CHARGE_ENABLED`` 为
    false 时只写审计行、不动余额，恢复后**没有补扣机制**。root-once 让单次金额
    变大，所以急停期间跑完的树漏掉的钱也更多。这是已知且用户接受的口径；要补扣
    得另做一条按 ``ai_usage_logs`` 反查未扣行的回填链。
```

- [ ] **Step 4: `_finish` 的扣费分支（绿其三）**

`app/services/ai/runner/run_recorder.py::_finish`。在 `own_media_cents = ...` 那行
之后插入分桶：

```python
        # 积分只按「平台真付了钱的那部分」扣（用户裁定 2）。三条 BYOK 道由
        # step_end / deliverable / subagent_done 三个 fold 各自填，这里只做减法。
        from app.services.ai.billing.tree_charge import bucket_tree

        buckets = bucket_tree(folded, own_cents)
```

把文件末尾那段 `if status == "completed" and own_media_cents > 0 and closed_by_us:`
整体替换成：

```python
        # Phase 3 Token Billing: reconcile usage on terminal status only.
        # Failure here is logged but never raised — billing must not be
        # able to roll back a finished agent_runs row.
        #
        # 用户裁定：一个回合的积分 = ceil(整棵树的平台花费)，只在 root 定稿时扣
        # 一次。此前是每条 run 各 ceil 一次自身花费 —— 一次带委派的回合在
        # point_transactions 里是好几行、每行各向上取整（真栈 ≈¢0.92 收成 7 分）。
        #
        # 三分支的竞态口径（宁少收不重收）：root 以自己的 refold 快照为准，子 run
        # 以自己看到的 root status 为准。窄窗口里两边都判「对方会收」时就都不收。
        if status == "completed" and closed_by_us:
            from app.services.ai.billing import tree_charge

            if self.parent_run_id is None:
                cost_points, why = buckets.tree_platform, "root charges the tree"
            elif await tree_charge.root_run_is_settled(
                run_id=self.run_id, parent_run_id=self.parent_run_id
            ):
                cost_points, why = buckets.own_platform, "late child charges its own"
            else:
                cost_points, why = 0.0, "root is still running and will charge"

            # 纯 BYOK 树（平台额 0 而真的烧了钱）与零花费树在 note 里必须分开：
            # 一个是「你自己付了」，一个是「什么都没烧」。
            byo_key = cost_points <= 0 and (
                buckets.tree_total > 0
                if self.parent_run_id is None
                else buckets.own_total > 0
            )
            logger.info(
                f"[RunRecorder] run {self.run_id} billing: {why}; "
                f"tree={buckets.tree_total} platform={buckets.tree_platform} "
                f"own={buckets.own_total} charge={cost_points} byo_key={byo_key}"
            )
            # 审计行的条件是「真的烧了钱」，与扣不扣分无关 —— 一个不扣分的子 run
            # 照样要在 ai_usage_logs 里留下它花掉的钱（用户裁定 2 的后半句）。
            if buckets.own_total > 0 or cost_points > 0:
                try:
                    from app.services.ai.billing.token_billing import reconcile_run

                    await reconcile_run(
                        run_id=self.run_id,
                        user_id=self.user_id,
                        team_id=self.team_id,
                        project_id=self.project_id,
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        model=self.model or "?",
                        prompt_tokens=self._prompt_tokens,
                        completion_tokens=self._completion_tokens,
                        cost_points=cost_points,
                        # 审计行仍记这条 run 自身的真实花费（含 BYOK 的那部分）。
                        usage_cost_points=buckets.own_total,
                        byo_key=byo_key,
                        action=self.trigger,
                    )
                except Exception as exc:
                    logger.warning(
                        f"[RunRecorder] reconcile_run failed (non-fatal): {exc}"
                    )
```

⚠️ `own_media_cents` 那个局部变量**保留不动** —— 小时表（`record_usage`）仍然收它，
A1 口径不变。分桶的 `own_total` 与它是同一个数，但两者各有来源与用途，不要合并。

- [ ] **Step 5: 跑新测试**

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_root_once_charge.py -q
```
预期：11 passed。

- [ ] **Step 6: 反转被新规则推翻的两个既有用例**

`backend/tests/test_hourly_usage_is_own_spend.py`。**只改这两个**，同文件另外两个
（`test_the_children_own_rows_sum_to_the_root_column`、
`test_the_counters_default_to_zero_then_follow_the_fold`）一个字不动 —— 小时表 A1
口径没变。

```python
async def test_the_points_charge_is_the_whole_tree_once_at_the_root(
    captured, monkeypatch
):
    """用户裁定（2026-09-17）：一个回合的积分 = ceil(整棵树的平台花费)，只在
    root 定稿时扣一次。此前这条用例断言的是「按自身花费 15 扣」——那是每条 run
    各 ceil 一次的口径，一次带委派的回合因此在 point_transactions 里留下好几行、
    每行各向上取整（真栈 ≈¢0.92 收成 7 分）。

    小时表（A1）仍收自身花费 15：那张表没有 parent_run_id 维度，父行带上子 run
    的花费就再也剔不掉。**两套账口径不同是对的** —— 一个回答「这条 run 烧了多少」，
    一个回答「这个回合该收多少钱」。"""
    charged = AsyncMock()
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    await _recorder(
        views={
            "cost": {
                "own_cents": 10.0,
                "by_child": {"c1": 3.0, "c2": 4.0},
                "media_cents": 5.0,
            }
        }
    )._finish(status="completed")
    assert _values(_run_row_updates(captured)[-1])["cost_cents"] == 22.0
    assert charged.await_args.kwargs["cost_points"] == 22.0
    assert charged.await_args.kwargs["usage_cost_points"] == 15.0
    assert captured["usage"][-1]["cost_cents"] == 15.0


async def test_a_root_that_spent_nothing_itself_still_charges_its_children(
    captured, monkeypatch
):
    """自身零花费、只有子 run 烧了钱：root-once 之后这个 root **要**扣 3 分
    （整棵树的平台花费），而子 run 看到 root 还在跑时不会自己扣。此前这条断言的
    是「根本不进扣费分支」——那在「每条 run 各扣各的」口径下才成立。"""
    charged = AsyncMock()
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    await _recorder(
        views={"cost": {"own_cents": 0.0, "by_child": {"c1": 3.0}, "media_cents": 0.0}}
    )._finish(status="completed")
    assert _values(_run_row_updates(captured)[-1])["cost_cents"] == 3.0
    assert charged.await_args.kwargs["cost_points"] == 3.0
```

⚠️ 该文件的 `captured` fixture 里 `write_scope` 的桩 `execute` **不返回任何东西**，
于是 `getattr(result, "rowcount", None)` 是 `None` ≠ 0，`closed_by_us` 为 True ——
本计划依赖这个既有行为，不要动那个 fixture。

`tests/test_token_billing.py` 的两个用例按裁定 ⑪ **一个字不改**：
`test_reconcile_byo_key_skips_points` 正好是 BYOK 规则的现成钉子；
`test_fractional_cost_rounds_up_never_to_zero` 的 ceil 仍在，只是换了入参。
`tests/test_child_run_inherits_team.py` 全保留 —— 晚到的子 run 自扣仍需继承 team。

- [ ] **Step 7: 跑全量 + 根级守卫 + lint**

```bash
cd <worktree>/backend
uv run pytest tests/test_hourly_usage_is_own_spend.py tests/test_token_billing.py tests/test_child_run_inherits_team.py -q
uv run pytest tests/runner tests/workforce -q
uv run pytest tests/test_*.py -q
uv run ruff check . && uv run black --check . && uv run isort --check-only .
```
预期：全绿。任何**其他**文件因为 `cost_points` 变了而红，先判断它断言的是旧口径
（那就按本 Task 的口径反转并在 PR body 里逐条列出）还是真缺陷（那就修实现）。
**不许为了让测试绿而把新口径改回去。**

- [ ] **Step 8: 突变记录（两处）**

1. 把 `_finish` 的 `elif await tree_charge.root_run_is_settled(...)` 改成无条件
   `elif True:`，重跑 `test_the_refold_race_bills_the_child_at_most_once`，贴失败输出，改回。
2. 把 `bucket_tree` 里 `own_byok = min(...)` 的 `min` 去掉，重跑
   `test_a_byok_lane_can_never_exceed_the_component_it_belongs_to`，贴失败输出，改回。

- [ ] **Step 9: 提交 + PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> add -A
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> commit -m "feat(billing): 积分按整棵树的平台花费在 root 扣一次，BYOK 免扣

bucket_tree 用三条 BYOK 道减出 tree_platform / own_platform；_finish 三分支：
root 扣整棵树、root 已终态后才结束的子 run 补扣自身、root 仍在跑的子 run 不扣。
reconcile_run 加 usage_cost_points，审计行仍记自身真实花费。读方
charged_points_for_run_trees 按 root_run_id 全树合计，一行未改。"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> push -u origin <branch>
gh pr create --base master --title "feat(billing): 积分 root 一次取整 + BYOK 免扣（积分改造 3/3）" --body "⚠️ 这个 PR 上线会真的改变用户余额。前两个 PR 必须先合并并在真栈确认三条 BYOK 道有数。见 plan Task 3。"
```

---

### Task 4: 真栈验收 + 完成账

**Files:** 无代码改动 —— 三个 PR 落地后的验证动作。结论写回本文件末尾的「完成账」一节
（改 `docs/superpowers/plans/2026-09-17-agent-points-root-once-byok-free.md`，单独一个
`docs(plan):` PR）。

**Interfaces:** Consumes Task 1–3 全部产物。

- [ ] **Step 1: 确认三条 BYOK 道真的装进了容器**

```bash
ssh ubuntu "docker exec nous-backend /app/.venv/bin/python -c \
  'from app.services.ai.billing.tree_charge import bucket_tree; \
   from app.services.ai.billing.byok_step import step_byok_cents; \
   from app.services.ai.runner.run_projection import empty_views; \
   print(sorted(k for k in empty_views()[\"cost\"] if \"byok\" in k))'"
```
预期：`['by_child_byok', 'media_byok_cents', 'own_byok_cents']`。

这一步**不能**用 `/api/v1/readyz` 替代：那个探针不 import 本期任何新模块，一个
import 期就炸的模块会让 readyz 照样绿而每次 `_finish` 静默刷 WARNING（CLAUDE.md
验收纪律：探针要探客户端真实走的那条路径）。

- [ ] **Step 2: 派子 agent 的回合 —— 整棵树恰一行流水**

用 fixture 团队 **MH-95** 在 `https://app.nous.ink` 真跑一个会派子 agent 的回合
（在议题里让 agent 委派一次），记下 root run id。然后：

```bash
ROOT=<root run id>
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT id, parent_run_id, root_run_id, cost_cents,
            metadata_json->'cost'->>'own_byok_cents'   AS own_byok,
            metadata_json->'cost'->>'media_byok_cents' AS media_byok,
            metadata_json->'cost'->'by_child_byok'     AS child_byok
     FROM public.agent_runs
     WHERE id = $ROOT OR root_run_id = $ROOT ORDER BY id\""
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT reference_id, amount, created_at FROM public.point_transactions
     WHERE type='consume' AND reference_type='agent_run'
       AND reference_id IN (SELECT id::text FROM public.agent_runs
                            WHERE id = $ROOT OR root_run_id = $ROOT)
     ORDER BY created_at\""
```

| 判据 | 通过 |
|---|---|
| 流水行数 | **恰好 1 行**，`reference_id` = root 的 id |
| 金额 | `-amount == ceil(tree_platform)`；`tree_platform` 按 root 行的 `cost_cents` 减去三条 BYOK 道手算一遍，两个数必须对上 |
| 子 run | 一行流水都没有 |

⚠️ 拿到 2 行以上就是竞态口径破了（或某个子 run 自认 root）—— **停在这里**，先查
那些子 run 的 `parent_run_id` 是不是 NULL（`_attach_to_parent_run` 失败是已记票的
既有缺陷 K48），再判断是不是本期回归。

- [ ] **Step 3: BYOK 出图 —— 该树零行流水，用量表有花费**

在 Admin → AI Models 里用**用户自己的** ark key 那一行出一次图（BYOK 层），在
agent 回合里触发（不是画布，画布不走 agent 积分链）。然后：

```bash
RUN=<那次回合的 root run id>
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT count(*) FROM public.point_transactions
     WHERE type='consume' AND reference_type='agent_run' AND reference_id='$RUN'\""
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT model, cost_points, created_at FROM public.ai_usage_logs
     ORDER BY created_at DESC LIMIT 3\""
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT hour_bucket, module, cost_cents FROM public.ai_usage_hourly
     ORDER BY hour_bucket DESC LIMIT 3\""
```

| 判据 | 通过 |
|---|---|
| `point_transactions` | `count = 0` |
| `ai_usage_logs` | 最新行 `cost_points > 0`（真实花费，含 BYOK 那部分） |
| `ai_usage_hourly` | 最新桶 `cost_cents > 0` |
| 后端日志 | `grep 'billing:' ` 能看到 `byo_key=True` 那一行 |

**三个判据缺一不可**：只看第一条会把「BYOK 免扣」和「这条链整个没跑」混成同一个
答案（空输出不是否定结论）。

- [ ] **Step 4: 消耗行 ◇n 与流水一致**

在前端打开 Step 2 那个回合，读回复尾部消耗行的 ◇ 数字。

| 判据 | 通过 |
|---|---|
| ◇n | 与 Step 2 查到的那一行 `-amount` **相等** |

读方 `charged_points_for_run_trees` 本期一行没改 —— 它按 `root_run_id` 全树合计，
从「多行相加」变成「一行」是同一个数。这一步就是那句话的真栈证明。

- [ ] **Step 5: 急停开关（不在生产切换）**

```bash
cd <worktree>/backend && AGENT_POINTS_CHARGE_ENABLED=false uv run pytest \
  tests/test_token_billing.py::test_the_kill_switch_skips_the_charge_but_still_logs_usage -q
```
生产上**不切**这个开关（裁定 ⑫）。真栈只确认当前值：

```bash
ssh ubuntu "docker exec nous-backend /app/.venv/bin/python -c \
  'from app.core.config import settings; print(settings.AGENT_POINTS_CHARGE_ENABLED)'"
```
预期 `True`。

- [ ] **Step 6: 前端链走查**

```bash
cd /Volumes/program/project-code/repos/nous-app/frontend && npm run e2e:prod
```
本期没有前端改动，这一步是回归护栏（消耗行读的是同一个端点）。

- [ ] **Step 7: 把结论写进完成账并开 docs PR**

按下面的模板填本文件末尾的「完成账」三张表，然后：

```bash
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> add docs/superpowers/plans/2026-09-17-agent-points-root-once-byok-free.md
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> commit -m "docs(plan): 积分 root 一次取整 + BYOK 免扣的完成账"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/<name> push -u origin <branch>
gh pr create --base master --title "docs(plan): 积分改造完成账"
```

---

## 完成账

### 一、交付

| Task | PR | 合并 SHA | 评审轮次 | 部署证据 |
|---|---|---|---|---|
| 1 LLM 的 BYOK 标记按步分桶 | #2352 | `b4da4971e` | 1 轮（Approved，无修复轮；并入项转 Task 2） | `deploy-gpu` success；容器内 `symbols ok step_byok_cents` |
| 2 生图 / 子 agent 两条 BYOK 道 | #2355 | `65ad78897` | 2 轮（首轮 Needs fixes → 修复轮 1 → Approved） | `deploy-gpu` RID=35176468976 success；`symbols ok _byok_cents_of`；`readyz` dbos=enabled |
| 3 树收口者一次扣 + BYOK 免扣 | #2357 | `c35be6db0` | 5 轮（首轮 + 设计变更返工 + 修复轮 2/3/4 + 收尾核 diff） | `deploy-gpu` RID=35190891682 success（**2026-09-17T06:42:56Z = 新口径切换点**）；`symbols ok settle_tree_if_closed` |
| 热修 切换点常量 + 退款脚本 | #2361 | `25c3cce64` | 1 轮快审（0 Critical，可合） | `deploy-gpu` RID=35198662854 success；容器内 `AGENT_POINTS_TREE_CUTOVER=2026-09-17T06:42:56Z` |
| 最终修复批 T1/T2/T3/T6/T16/T12 + mig 477 | #2362 | `0b41942a4` | 2 轮（首轮 0 Critical / 3 Important → 修复轮 1 → Approved）+ 1 个 black 格式提交 | `deploy-gpu` RID=35552165952 success；`symbols ok CHARGE_STATUS_RAISED`；`run-migration` success，生产 `pg_indexes` 实查到 `idx_point_transactions_agent_run_refund`；`readyz` dbos=enabled |
| 4 真栈验收 + 完成账 | 本 PR | — | — | 无代码改动 |

Task 3 的 5 轮里，第 2 轮不是「修 bug」而是**设计变更返工**：Task 4 Step 1 的探针打出
`by_child` 在 workforce 链上恒为 `{}`，原计划「root 定稿时按 `by_child` 扣整棵树」的前提
当场不成立，改为「谁把树收口谁扣费」。代价是 Task 3 返工一轮，收益是绕开了一个读方假设
（见口径披露 a）。

### 二、真栈验收

**Step 1（Task 3 合并之前，2026-09-17 03:25–03:35 UTC）**

| # | 结论 | 证据 |
|---|---|---|
| (1) 平台 LLM 回合三条 BYOK 道存在 | **PASS** | run `350406624210194`：`? 'own_byok_cents'` / `? 'media_byok_cents'` / `? 'by_child_byok'` 三个 `t`；`own_byok_cents=0.0`；正对照 `own_cents=0.1426 > 0` 证明视图是真写出来的，不是原样落库的空壳 |
| (2) BYOK ark 出图 | **UNVERIFIED** | 真栈无这条道：`provider_byok_keys` 空表（0 行）；`actual_provider ILIKE '%ark%'` 与 `actual_model ILIKE '%seedream-5%'` 均零行；期间 `generated_media` 新增 0 行。未造数据、未拿平台模型冒充 |
| (3) 派子 agent 的回合 | **半通过 → 触发设计变更** | `by_child_byok` 键存在且为 `{}` ✔；但 `by_child` 也是 `{}` ✘。root `350406827261364` 的 `cost_cents` 0.3249 恰等于自身 `own_cents`，后代 0.2205 没折进来。判别式定位到 `trigger`：`subagent_task` 折、`workforce` 不折（全库 10 条 `by_child` 非空的全是前者） |

**Step 2–8（新口径上线后，2026-09-17 08:19–08:35 UTC）**

| # | 结论 | 证据 |
|---|---|---|
| (2) 整棵树恰一行流水 | **PASS** | 树 root `350478260280825`，4 个 run 全 completed。`point_transactions` 恰 1 行，`reference_id` = root，三个子 run 零行；`charged_at` 只在 root 非空；余额 595→594。金额 `1 = ceil(0.5761)`，0.5761 由四行 `own+media−钳位 byok` 逐行手算，与 worker 日志 `runs=4 total=0.5761 platform=0.5761 points=1.0` 逐字相同。**收口者是晚 119ms 结束的子 run**：root 08:21:20 先结束，子 run `350478350515718` 08:21:36.349 最后结束，扣费发生在 08:21:36.468 |
| (3) BYOK 出图零扣费 | **UNVERIFIED** | premise 本批重核：`provider_byok_keys` 0 行、ark/seedream-5 模型 0 行、近 1 小时 `generated_media` 0 行。三条判据（零流水 + 用量有花费 + 日志 `byo_key=True`）无法产生。本次树日志 `byo_key=False`，与「无 BYOK 参与」一致 |
| (4) 消耗行 ◇n 与流水一致 | **PASS** | MH-95 尾栏 `◇ 1.00 · doubao-seed-2-0-lite-260428`，hover `¢0.33` + `Charged ◇ 1.00 (incl. sub-agents)`。正对照：同一线程末尾四条读数各不相同，**同规模 4-run 树旧口径读 ◇ 4.00、新口径读 ◇ 1.00** |
| (5) 急停开关 | **UNVERIFIED by design** | 裁定 ⑫ 规定不在生产切换。只读确认 `AGENT_POINTS_CHARGE_ENABLED=True`，本批没有动过 |
| (6) `npm run e2e:prod` | **PASS** | 3 passed / 1 skipped（默认跳过的 get-token），21.0s。临时 spec 删除后跑的 |
| (7) 清扫器无误扫 | **PASS（样本 1，原因写明）** | 容器启动以来 `settled tree` 仅 1 行，就是 (2) 那棵，root `started_at` 08:19:41 > cutover 06:42:56，`charged=True` 正确。样本少是因为 `nous-worker` 08:16:45Z 刚随热修重启，日志只覆盖之后；这条的强度实际靠 (8) 的账面核对 |
| (8) 流水全部对应 post-cutover root | **PASS** | 热修容器启动后的 consume 行仅 1 条，是 root 且 `started_at >= cutover`，违规计数 0。**正对照**：同一条谓词换到热修之前的窗口立刻抓出 81 条（全部 `pre_cutover_root=t / is_child=f`），证明这个查询会说「不」。账面：`consume 82 / −126` 对 `refund 81 / +125`，净额 −1 正是本批该扣的 |

容器内落地确认（两条都跑过）：`settings.AGENT_POINTS_TREE_CUTOVER = 2026-09-17T06:42:56Z`、
`from app.services.ai.billing.tree_charge import bucket_tree` OK；Step 1 原命令整条通过，
输出 `['by_child_byok', 'media_byok_cents', 'own_byok_cents']`。

⚠️ **对终审 I4 / T4 的更正：异步 workforce 收口路径已经真栈验过，不是 UNVERIFIED。**
终审报告记「异步 workforce 收口（`agent_worker` 钩子 + `pending_children` 归零）真栈未验」，
但 (2) 那棵树正是这条路径。生产原样读数：

```
SELECT id, trigger, started_at::time, ended_at::time FROM agent_runs
WHERE id=350478260280825 OR root_run_id=350478260280825 ORDER BY started_at;

350478260280825|issue_reply|08:19:41.512397|08:21:20.236288
350478350515718|workforce|08:20:03.542454|08:21:36.349126
350478396280342|workforce|08:20:14.715113|08:21:13.778415
350478474391081|workforce|08:20:33.785082|08:20:49.074784
```

三个子 run 的 `trigger` 都是 `workforce`；root 在 `08:21:20.236288` 就结束了，而子 run
`350478350515718` 比它**晚 16 秒**、到 `08:21:36.349126` 才结束，扣费发生在其后 119ms 的
`08:21:36.468`。如果走的是同步链或 root 定稿扣，这笔账会少算该子 run 的 0.0904，而且扣费
时刻会跟着 root 的 `08:21:20` 走。所以 Step 2 的 PASS 里**包含**异步收口，T4 应改判为已验。

### 三、口径披露

这一节是**相对计划原文和相对旧行为的差异**，每条都是用户需要知道的，不是实现细节。

**(a) 扣费发生在「树收口者」身上，取代计划原文的「root 定稿时扣」。**
探针数据推翻了原计划的前提：workforce 委派是 fire-and-forget，root 常常**先于**子 run
结束（本批实测 root 08:21:20、最后一个子 run 08:21:36），而 `subagent_done` 只写直接父，
所以 root 行的 `by_child` 在这条链上恒为 `{}`。按 root 定稿扣会漏掉全部后代，晚到的子 run
各自 `ceil` 又会把叠加重现一遍。现在的语义是：每个 run 终态落库后检查整棵树，树内无
`running` 时由**最后那个收口的 run** 以 CAS 盖戳、按 `root_run_id` 重新聚合全树、开一行
`reference_id=root` 的流水。

**(b) 失败 / 取消的回合现在也扣费（相对旧口径的变化）。**
计划原文写的是「root `completed` 才扣」，评审指出子 run 先完成、root 随后 `failed` 时全树
漏收，裁定改为 **root 任一终态都扣该树平台桶**。所以一次报错收场的回合也会消耗积分——
钱确实花在 provider 上了。例外：`heartbeat_lost` 不经 `_finish`，作为 Stated Limitation 记着。

**(c) 只向前不追扣，切换点是一个常量，它的来历是一次事故。**
`AGENT_POINTS_TREE_CUTOVER = 2026-09-17T06:42:56Z`，取 Task 3 `deploy-gpu` success 的时刻。
两道守卫方向不同：清扫器提名侧下界取 `max(now − 7d, cutover)`；收口侧在判 `running`
**之前**读 root 的 `started_at`，早于切换点一律返回 `pre_cutover`，**不扣也不盖戳**。
这个常量不是设计之初就有的，是下面事故复盘的产物。

**(d) BYOK 免扣：LLM 与图片两条道已接线，embedding 无此路径，生产无 BYOK 配置故未真栈验。**
LLM 在 Task 1（按步分桶 `own_byok_cents`）、图片在 Task 2（`media_byok_cents` +
`by_child_byok`）。embedding 今天根本没有 BYOK 路径可接（裁定 ⑧：接入时走同一标记）。
真栈上 `provider_byok_keys` 是空表、没有任何 ark/seedream-5 模型行，所以
「BYOK 出图零扣费」这条**只有单测和集成测试覆盖，没有真栈证据**（T18）。要验它需要先在
Admin → AI Models 建一行用用户自己 key 的图片模型并对 fixture 团队开放——那是配置动作，
本期没做。

**(e) 退款行不进 `charged_points` 读模型。**
`refund` 类型的流水不抵扣读方的 `charged_points_for_run_trees`，这是既有口径。后果：
被退款的那 81 棵老树，在血缘页和消耗行上**仍然显示「已扣」**，尽管钱已经回到余额里。
余额是对的（2448→2500、522→595），显示是旧的。**已由 #2362 修复并真栈验过**（2026-09-21，
生产容器内调三个宿主共用的 `charged_points_for_run_trees`）：已退款老树 `349592702134848` → `0.0`
（键在场，界面显示 ◇ 0.00）；正常扣的树 `350478260280825` → `1.0`；没扣过的 id → 键缺席。

**(f) 切换点常量的值来自代码默认，改它要改代码 / 配置，不是改 env。**
两个容器 `printenv AGENT_POINTS_TREE_CUTOVER` 都是**空**——没有 `backend.env` 行，
值来自 `app/core/config.py` 的默认 + `backend/config.yml`。这与 CLAUDE.md 里
「`backend.env` 会静默盖掉 `config.yml`」那条陷阱方向相反：这里没有 env 行可盖。

**(g) 团队积分耗尽期间收口的树，永久免单，充值后不补扣。**（T5，**需用户裁定**）
余额不足 / 取不到 team / 急停这三种情况下，收口逻辑**保留戳**并记 `charged=False`，
这棵树就此定稿——之后充值也没有任何机制回头补扣它。此前只有「急停」那一半写在
`tree_charge` 的 docstring 里，「余额不足」这一半至今没有任何裁定提过，而它是本次口径
变更**放大**出来的：旧口径一棵树是 N 笔小额，耗尽时只漏掉后面几笔；新口径一棵树一笔，
漏就漏整棵。请在两条里选一条：① 接受现状（耗尽期间的树免费）；② 把「余额不足」也列进
撤戳，让清扫器稍后重试。已记票「充值后重收口 job」。

### 四、事故复盘：清扫器追扣 81 棵上线前的历史树

**时间线（UTC）**

| 时刻 | 事件 |
|---|---|
| 2026-09-16 09:26 | A3 —— 积分扣费链修好并开始真扣。**此前每轮 `TypeError` 被吞成 WARNING，一分钱没扣过** |
| 2026-09-17 06:42:56 | Task 3 `c35be6db0` 部署 success，新口径上线（这个时刻后来成了切换点常量） |
| 06:43 | 首轮清扫 `force_settle_stale_pending_trees_step` 提名并收口 43 棵历史树，扣 87 分 |
| 06:44:09 | 第二轮再收 38 棵，合计 **81 棵 / 125 分** |
| 06:45 起 | 候选池耗尽，未再增长。06:45:51–06:48:23 每 30 秒复核 `rows=81` 恒定，扣费已停 |
| ~07:20 | 用户要求暂停（热修 PR 未合、退款未执行、Task 4 未跑）；随后「继续」 |
| 08:16:45 | 热修 `25c3cce64` 部署 success，`nous-worker` 重启 |
| 08:2x | 退款脚本 dry-run → `--execute` → 复核余额，事故闭合 |

**机理：「有没有扣过钱」回答不了「该不该扣」。**

链路是这样接上的：① 提名窗是 `now−2h` 到 `now−7d`，那批 `ended_at` 在 09-10~09-15 的树
正好落在窗里；② 它们从没被本机制收过口，`billing.charged_at` 是空的，提名条件
`charged_at IS NULL` 也满足；③ `async_pending` 为 0 的那些不走强制路径、走正常路径；
④ 正常路径的防回溯正查问的是「这棵树在 `point_transactions` 里扣过钱没有」，而它们结束于
A3 之前，**当时一分钱都没扣过、零行**，`_tree_was_ever_charged` 返回 False 放行；
⑤ CAS 盖戳 → `reconcile_run` → 真扣。

防回溯守卫假定「上线前的老树必然留着旧口径逐 run 扣的流水」。A3 之前那整段历史压根没有流水
——假定不成立，守卫就整条失效。而用户的裁定「只向前不追扣」，判据是**时间**，与花没花过钱无关。

**影响面**

| team | 棵数 | 分数 |
|---|---|---|
| 310812366953241 | 8 | 52 |
| 331438215859255（fixture 团队） | 73 | 73 |
| 合计 | **81** | **125** |

⚠️ 这两个数字只用来核对，退款脚本里一个都没写死——候选按查询动态取。第一份通报是
「43 棵 / 87 分」（只含首轮），**按快照写死会漏掉一半**。

**止血与退款**

热修 #2361 落两道时间守卫止住向前的口子；退款脚本 `refund_pre_cutover_tree_charges.py`
按三条谓词选行（`type='consume'` 且 `reference_type='agent_run'`；`created_at >= cutover`；
`reference_id` 指向的 run 是 root 且 `started_at < cutover`——第 2 与第 3 条方向相反，
缺一不可），逐行走 `PointsService.refund_points` → `rpc_refund_team_points_idempotent`
（mig 123，唯一索引保证至多退一次，重跑安全）。**先退钱再改戳**：反过来的话改戳成功而
退款失败就再也认不出这笔该退。

实测：dry-run 81 笔 = 81 个不重复 root = 125 分（52 + 73），`--execute` 退成 81 笔 / 125 分、
改戳 81 行。余额 310812366953241 **2448→2500**、331438215859255 **522→595**。
`point_transactions` 账面 `consume 81 / −125` 对 `refund 81 / +125`；pre-cutover 树的
`charged_at` 全清、`refunded_at` 81 行。

**防复发**

- 收口侧：读到全部行之后、判 `running` **之前**取 root 的 `started_at`，早于切换点返回
  `pre_cutover`，不扣不盖戳。放最前面是因为这是关于这棵树的**永久**结论，与它此刻跑没跑完无关；
  不盖戳是因为盖了就等于宣称「本机制收过这棵树」，而它一分没收。
- 提名侧：下界 `max(now − FORCED_SETTLE_MAX_AGE, cutover)`。这一道是省事，不是权威——
  正常收口路径根本不经过清扫器。
- 三个判断方向都往「不扣」那侧倒：切换点读不出来 → 返回 `cutover_unreadable` 并刷 ERROR，
  清扫器一棵树都不提名（与 `pre_cutover` **分开上报**，一个是正常老树、一个是要人去修的配置）；
  root 的 `started_at` 读不出来 → 按 `pre_cutover` 处理；裸时间戳按 UTC 解析，不随部署机时区漂。

**教训**

用「有没有发生过 X」去代理「该不该做 Y」，只在两者历史上恰好同步时有效；一旦有一段历史
X 没发生过（这里是 A3 之前的静默期），代理关系就断了，而守卫**不会报错，只会放行**。
判据该是什么就写什么——用户说的是时间，就用时间，别用花销记录去猜时间。
同族：CLAUDE.md 的「空输出不是否定结论」「探针够不着 ≠ 目标是坏的」。

### 五、裁定

| # | 裁定 | 场景 |
|---|---|---|
| R1 | 预检扫描无冲突，按计划合并顺序 1→2→3→4 串行；Task 3 是最后一个后端 PR（真扣） | 开工前共享文件扫描 |
| R2 | Task 1 评审的 ①②③（`script_ai_service` 缺 `credential_origin`、AST 守卫只扫五个写死路径、wiring 取值无用例）并进 Task 2（同为标记工作）；Task 1 直接合并 | Task 1 评审 |
| R3 | Task 2 评审的 Critical / Important / Minor 全部进修复轮 1，零调用 shim 直接删 | Task 2 评审 |
| R4 | root **任一终态**都扣树平台桶（失败 / 取消回合也扣，完成账披露）；`heartbeat_lost` 不经 `_finish`，记 Stated Limitation | Task 3 首轮评审 |
| R5 | `byo_key=True` 只在前两分支算，不贴到每个「root 在跑」的子 run（守裁定 ⑤） | Task 3 首轮评审 |
| R6 | **设计变更：改「树收口者扣费」** —— 每 run `_finish` 终态 UPDATE 后 `settle_tree_if_closed(root)`：树内无 `running` → CAS 标 root `charged_at` → 扣 Σ 各行自身平台桶（own + media − 钳位 BYOK），`reference_id=root`，一树一行；崩溃类终态写方也收口；不再依赖 `by_child`；删 `root_run_is_settled` 三分支。理由：workforce fire-and-forget 让 root 定稿扣漏后代，且晚到子 run 各自 `ceil` 重现叠加 | Task 4 Step 1 探针之后 |
| R7 | 接受不加 `RunRecorder.root_run_id`（`agent_runs.root_run_id` 是唯一真相）；让实施者先补 CAS 真 PG 集成用例接 `schema-drift`（省一轮） | Task 3 返工中 |
| R8 | v2 评审的 Critical（`charged_at` 搬到顶层 `billing` 键、`async_pending` 门 + 2h 兜底）、Blocker（真 PG 集成用例）、Minor 全部进修复轮 3 | Task 3 v2 评审 |
| R9 | fix3 评审：worker 写完 `subagent_done` 补调 `settle(child)`；防回溯挂正常路径并加 `type=consume`；清扫器提名加 `charged_at IS NULL` + 双向窗 + root-only，索引记票；仍 resume 同一实施者（非卡住，是设计变更后的新发现，模型已是最高档） | Task 3 fix3 评审 |
| R10 | **事故裁定**：① 热修切换点常量 `AGENT_POINTS_TREE_CUTOVER=2026-09-17T06:42:56Z`，提名侧 `ended_at >= cutover`、正常路径 root `started_at < cutover` → `pre_cutover` 不扣不盖戳；② 一次性退款脚本（dry-run 先）按 `PointsService` 退款路径原额退回；③ 完成账与用户报告披露 | 2026-09-17 06:43 事故 |
| R11 | 热修可合：合并部署止血 → dry-run 核 81 笔 / 81 root / 125 分 → `--execute` → 核余额；「同 root 多条 consume 按笔退会少退」与「退款后读模型仍显示 charged_points」两条记票并告知用户 | 热修快审 |

### 六、记票

底表是终审报告的「记票合并表」T1–T19（从 ledger、五份 review 报告、四份 task 报告去重合并）。
分三类：**最终修复批 PR 现在就修** / **归档记票** / **需用户裁定或披露**。终审 Verdict 是
**Approved with follow-ups**：没有 Critical，6 个 Important **全部是少收方向**，没有一条能多扣
或错扣他人。

**现在就修（最终修复批 PR #2362，已合并上线 `0b41942a4`，不碰扣费算法）**

> 修复批自己的评审又推翻/收紧了下表的三处，以这里为准：
> - **T1 换不到「晚收但收对」。** 镜像失败后没有人重写那一行，清扫器 2 小时后读到的是同一份陈旧
>   视图、按旧数结账。T1 真正换到的只有「一条带 run_id 的 ERROR」和「不把错数即时钉死」。少收方向，
>   接受；视图重写记票 R-1。下表 T1 行里「把树让给清扫器」应按此理解。
> - **T2 扩到了 `_finish` 的第一次重折**，否则行上的 `cost` 与审计行 / 小时表会分歧。评审沿
>   `spend_of_run` 公式逐项核过：不会多扣（重折是重建后赋值、按 `kind:ref_id:version` 去重）。
>   **用户可见后果：纯生图回合从此如实计入出图花费**（此前静默漏收）。
> - **T6 带了一条迁移（mig 477）**，与本计划「禁迁移」的全局约束相悖，控制方裁定破例：refund 腿
>   没有任何可用索引，而这条查询被前端轮询——正是 mig 474 存在的理由。没用 `type IN (…)`
>   （会让两条 partial 索引都不被蕴含、退回顺扫），改成两条腿各配一个 partial 索引，真库 EXPLAIN
>   用例 + 突变验证钉住。全额退款显示 **◇ 0**（键在场）而非徽章消失。
> - T12 的安静档定为「除 `charged` / `forced` / `error` 之外全部降 DEBUG」：无戳的树每分钟都会被
>   重新提名，只放过 `deferred` 堵不住噪声。代价：卡住的树只在 DEBUG 可见。
> - `partially_charged` 只对新发生的撤戳生效，历史不回填（回填得靠猜）。
>
> 修复批新记的票（不在本批）：**R-1** 镜像失败后的视图重写；**R-2** 终态重折两次，两次之间落地的
> 产出会让镜像 media 高于 `cost_cents`（best-effort）；**R-3** 纯生图回合数字上涨需上线后观察首批
> run 行；**R-4** `force` 重折在「内存产出 > 账本产出」时会把 media 调低；**R-5** 下限 0 只加在
> refund 分支；**R-6** `billing.charge_status` 写入后不清除；**R-7** EXPLAIN 用例的 `_LEG_SQL` 是手写
> 字面量，给查询加第三个条件时它仍会绿。

| # | 票 | 严重度 | 要做什么 |
|---|---|---|---|
| T1 | `persist_views` 失败即整棵树静默少收且不重试 | 高 | `persist_views()` 返回成功与否；`_finish` 失败时打 ERROR 并**跳过** settle，把树让给清扫器。本批唯一一处「契约声明是硬的、实现是软的」，而它正对着钱 |
| T2 | `refold_external_slices` 提前返回可在终态前抹掉 `media_cents` | 高 | `persist_views()` 路径无条件重折（或守卫加上「`cost` 四个金额分量任一非零」）。缺陷先于本批，后果由本批放大 |
| T3 | `by_child_byok` 全链无读方，五处注释声称的减法不存在 | 中 | 改那五处注释，把「`by_child − by_child_byok` 减出平台额」换成「当前无消费方，扣费按行聚合」。零行为改动，防的是下一个人按一句假话去改钱 |
| T6 | refund 行不进 `charged_points_for_run_trees`，81 棵已退树仍显示 ◇ 已扣 | 中 | `charged_points_for_references` 把 refund 行算进去。用户可见、已经错了、两个真实团队受影响 |
| T16 | 撤戳后重试被误标 `legacy_charged` | 低 | 加 `partially_charged` 结局。两行代码，换掉一条会误导运维的日志 |
| T12 | 清扫器缺按 `reason` 分桶的遥测，`forced > 0` 无告警 | 中 | 按 `SettleOutcome.reason` 分桶打 INFO。这是事故当天唯一能自动抓住的形状 |

**归档记票，不在本批做**

| # | 票 | 严重度 | 备注 |
|---|---|---|---|
| T7 | `agent_runs(ended_at DESC) WHERE parent_run_id IS NULL` 索引缺失，稳态下清扫器每 60 秒一次全表扫 | 中 | 需迁移，本计划禁迁移；另立票并接 C1 索引棘轮 |
| T8 | `RunEventWriter.mirror_stmt` 的 `cast("{}", JSONB)` 双重编码 | 低 | 同款写法在 Task 3 里把 cost 视图冲成数组而钱已扣。票里要写清**无害的理由是列的 NOT NULL 约束，不是这段代码** |
| T9 | 退款脚本无 runbook、无入口 | 中 | 脚本仍在仓库，下次要用时没人知道怎么跑 |
| T10 | 退款脚本按笔退 + 按 root 幂等 → 同 root 多笔会少退 | 低 | 本次未咬到（dry-run 核过笔数 = 不重复 root 数 = 81） |
| T11 | `AGENT_POINTS_TREE_CUTOVER` 可被 `backend.env` 静默覆盖；`cutover_unreadable` = 收入归零且无告警 | 中 | 见口径披露 (f)：今天没有 env 行可盖，但没有东西拦住将来加一行 |
| T13 | `simulate-complete` dev 端点是第五个终态写方，未接收口也未在 docstring 点名 | 低 | dev 端点，不影响生产账 |
| T14 | 孤儿 run 各成单节点树，逐 run `ceil` 回归 + 读方看不到 | 低 | **刻意不修**，已在 `tree_charge` docstring 文档化 |
| T17 | 收口那次 `reconcile_run` 的 token 参数是拼凑的，只流向 description | 极低 | 不参与任何金额计算 |
| T19 | CI `isort` 逐文件与本地目录模式不等价 | 低 | 终审建议**直接写进 CLAUDE.md** |

**需用户裁定或披露（对应口径披露 (d)(e)(g) 与下方更正）**

| # | 票 | 严重度 | 需要用户做什么 |
|---|---|---|---|
| T5 | 余额不足 / 无 team / 急停 → 保留戳 → 那棵树永久免单、无补扣 | 中 | **请明确裁定**：接受「积分耗尽期间的树永久免费」，或同意把「余额不足」列进撤戳让清扫器重试。见口径披露 (g)。此前只有「急停」那一半被文档化过 |
| T18 | BYOK 图片道真栈两轮都是 UNVERIFIED | 中 | 需先在 Admin → AI Models 配一个用用户自己 key 的 BYOK 图片模型并对 fixture 团队开放，才能验。见口径披露 (d) |
| T15 | 跨切换点的树（root 在 cutover 前、子 run 在之后）永久免费 | 低 | 一次性、量极小，披露即可 |
| T4 | 终审记「异步 workforce 收口真栈未验」 | 中 | **更正：不成立，已真栈验**——详见第二节末的更正说明 |

**计划原文记的票里，终审合并表未覆盖的三条**（P1 已并入 T5，P4 已并入 T14）：

| # | 票 | 位置 | 严重度 | 去向 |
|---|---|---|---|---|
| P2 | Embedding 无 BYOK 路径，接入用户 key 时必须走同一标记 | `providers/embedding_config.py` | — | 票（今天没有这条路，见口径披露 (d)） |
| P3 | 画布直出的图不走 agent 积分链，`GenerationOrigin.byok` 在那些调用点恒 False | 画布 / DBOS 分镜四个调用点 | Minor | 票（那条链本就不收积分） |
| P5 | `by_child` 的 50 条 `seen` 窗口给账划界，超过 50 个不同产出后重复到达会多计一次钱，BYOK 道同此 | `folds/deliverables.py` | Minor | 票（既有） |

---

## 自审（作者按 writing-plans 的三问跑过）

**1. Spec 覆盖。** 用户裁定只有两条。裁定 (1)「root 一次取整」→ Task 3 Step 4 的三
分支 + Step 6 反转的两个用例 + Task 4 Step 2 的真栈判据。裁定 (2)「BYOK 免扣，
LLM / embedding / 图片都算」→ LLM 在 Task 1、图片在 Task 2、embedding 在裁定 ⑧
写明「今天没有这条路径，接入时走同一标记」（不是漏，是没有可接的线）；「用量记录
仍记真实花费」落在裁定 ⑬ 的 `usage_cost_points` 与 Task 3 Step 3、Task 4 Step 3 的
三判据；「不靠管理员摘 `per_call_cents`」落在 Task 2 Step 4 的 `_stamp_provider_key`
注释（价钱照算、积分不收）。控制方 ① – ⑫ 逐条在裁定表里有落点，其中 ② 在 Task 1
Step 0 给了判定过程与两条路的关系，③ 给了核实结论与反对理由。

**2. 占位符扫描。** 全文无 TBD / TODO / 「类似 Task N」/ 「加上适当的错误处理」。
每个代码步都有可照抄的完整代码块。两处**刻意**要求实施者先跑一条命令再写代码
（Task 1 Step 1 的 `empty_views` 真名、Task 2 Step 1 的 `generate_image` 真签名、
Task 2 Step 4 的 `_stamp_provider_key` 调用点普查）—— 那不是占位符，是防「按记忆
写名字」的护栏，三处都写明了「对不上就改测试去对齐真名，不要改生产签名」。

**3. 类型 / 签名一致。** `step_byok_cents(cost_cents, credential_origin, served_by_platform)`
三处用法一致（纯函数测试、`_step_ended`、docstring）。`bucket_tree(folded, own_cents)`
四个字段名 `tree_total / tree_platform / own_total / own_platform` 在 Task 3 的
测试、实现、`_finish` 调用处逐字一致。`_byok_cents_of` 与 `_cost_cents_of` 同海拔
这条不变量在裁定 ③、Task 2 的 docstring、`tree_charge` 模块 docstring、
`test_the_two_emitters_report_the_same_altitude` 四处说同一件事。三条 fold 道的键名
`own_byok_cents` / `media_byok_cents` / `by_child_byok` 在 run_projection 字面量、
三个 fold、`bucket_tree`、`_byok_cents_of`、真栈 SQL 六处逐字一致。
`usage_cost_points` 在 `reconcile_run` 签名、审计插入、`_finish` 调用、两处测试断言
四处一致。
