# harness 三期 3b（回退 / 花费 / 实时 / 资源来源）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 3a 登记下来的产出版本链能被人手回退（Revert To vN）、每版带真实花费（文本类读时按步分摊、媒体类目录每次调用价并计入 run 账）、回合结束后前端各消费方自刷新（seq 水位）、资源库文件能反查出产出它的 run / issue / step。

**Architecture:** 数据层一份迁移（466）把「人手占号」写进 `run_deliverables` / `script_shot_ops`（`run_id` 可空 + actor 列 + CHECK），登记口 `register_deliverable` 扩三参并支持加入调用方事务；回退是一个同事务的服务函数（重建目标版 → 若有未登记人手编辑先登记当前内容 → 写内容与账本 → 登记回退版）。花费不写第二份：文本类在血缘读时从 `step_end` 事件折出（`cost_kind=allocated`），媒体类在 `register_generated_media` 咽喉点按目录价填（`exact`）并经 fold 进 run `spent_cents`。实时层是一个按 issue 记 seq 水位的微 store，双源（轮询边沿 + WS done）汇入、低于等于即丢；议题页按 `deliverable` 事件的 `(kind, ref_id)` 精确失效缓存，无 WS 页面靠 TTL + visibility。资源来源反查走 `generated_media.promoted_resource_id` 的部分索引。

**Tech Stack:** FastAPI + SQLAlchemy async ORM（Supabase Postgres，Snowflake BIGINT）；DBOS workflows；React + TypeScript + vitest/RTL；`useSyncExternalStore` 微 store；Playwright（真栈验收，不入库）。

**Spec:** `docs/superpowers/specs/2026-09-13-harness-p4-phase3b-revert-cost-realtime-design.md`（PR #2279）。接口契约汇总在本计划「Global Constraints」之后的「接口契约」节，所有 Task 以它为准。

## Global Constraints

- 迁移号 **466**（465 是当前最后一个；取号时重扫）；ORM 镜像与迁移同 PR，消费代码分 PR。⚠️ `tests/models/test_transcript_event_types_phase2a.py::LATEST_MIGRATION` **不动**（它只指向最近一次重写 transcript 事件白名单的迁移 461；466 不改白名单）。
- 每 Task 独立 worktree（从 `origin/master` 建）+ 独立 PR；TDD 先红后绿；每 Task 记录一次突变；对抗评审（opus）全修；合并后盯部署链（后端：deploy-gpu + 容器符号 + `readyz`；前端：`version.json` 7 位 SHA）。
- 新 SQL 一律 ORM，禁止新增 `text()`（仅 `dbos.*` 两处文档化例外）。
- DBOS 步骤写 `resources` 必须包 `system_request_scope`。
- 边界 mock 用真实 wire 形状：`shots`/`scenes` router 的 id 是 JSON number，`canvases` 与血缘响应的 id 是 string。
- 生产错误体是 `ErrorResponse` 外壳；后端 `HTTPException(status, detail={"code": ...})`，前端只读 `details.code`。
- UI 文案英文；状态色用语义 token（info / ok / warn / danger / agent），不用旧色相类名。
- 3a 不变量不破：`register_deliverable(run_id=None, actor_user_id=None)` 仍是 no-op（普通人手改动不占号）；`deliverable` 事件只在有 run 时落 transcript。
- 均摊花费只是参考值（UI 带 ≈），不是计费/门禁输入；预算钩子只吃 `step_end` 与媒体精确价。
- 前端 `npx tsc --noEmit -p .` 有约 61 条既有错误：触碰文件零新增即可。
- 同一轮最多一条依赖 cwd 的 Bash；写操作一律 `git -C <绝对路径>`；主检出只读。

---

## 接口契约（绑定；各 Task 不得自创别名）

迁移号：**466**（`supabase/migrations/466_*.sql`）。465 是当前最后一个。`LATEST_MIGRATION` 测试常量不动（见 Global Constraints）。

### 数据（T1）
- `run_deliverables`: `run_id BIGINT NULL`（原 NOT NULL）；新列 `actor_user_id UUID NULL`、`reverted_from_version INT NULL`、`ledger_ref TEXT NULL`；`CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL)` 名 `run_deliverables_run_or_actor`。
- `script_shot_ops`: `run_id BIGINT NULL`；新列 `actor TEXT NULL`；`CHECK (run_id IS NOT NULL OR actor IS NOT NULL)` 名 `script_shot_ops_run_or_actor`。
- `ai_model_prices`: 新列 `per_call_cents NUMERIC(12,4) NULL`。
- `generated_media`: **不建新索引**——mig 456 已建 partial unique `uq_genmedia_promoted_resource`（`456_*.sql:10-13`），反查走它；T1 只在集成测试里断言该索引存在。
- ORM 镜像同 PR：`RunDeliverables`、`ScriptShotOps`、`AiModelPrices`。

### 登记口（T2，`backend/app/services/deliverables/registry.py`）
```python
async def register_deliverable(*, run_id: int | None, kind: str, ref_id: int | str, title: str | None = None,
    model: str | None = None, cost_cents: float | None = None, turn: int | None = None, step: int | None = None,
    recorder=None, actor_user_id: str | None = None, reverted_from_version: int | None = None,
    ledger_ref: str | None = None, session=None) -> RegisteredDeliverable | None
```
- `run_id is None and actor_user_id is None` → 返回 None（no-op，3a 不变量）。
- `actor_user_id` 有值 → 登记，不落 transcript 事件。`reverted_from_version` 非空而 `actor_user_id` 空 → `ValueError`。
- `session=` 非空 → 用该 AsyncSession（调用方事务），不自开事务、不 commit。
- `ledger_ref`：script_shot → `str(script_shot_ops.id)`；script_scene → `str(op_seq 水位)`；其他 None。

### 回退端点（T3）
`POST /api/v1/outputs/{kind}/{ref_id}/revert`，body `RevertRequest{to_version: int, expected_latest: int}`，201 `RevertResponse{version: OutputVersion, kept_version: OutputVersion | None}`（`kept_version` = 回退前把未登记人手编辑登记成的那一版，无则 null）。
错误（`HTTPException(status, detail={"code": ..., ...})`，前端读 `details.code`）：400 `kind_not_revertible`；404 `version_not_found`；409 `version_conflict`（detail 带 `latest_version`）；409 `content_unavailable`（detail 带 `reason`）；500 `revert_failed`。
服务函数：`backend/app/services/deliverables/revert.py::revert_output(*, kind, ref_id, to_version, expected_latest, auth) -> RevertResult(version: OutputVersion, kept_version: OutputVersion | None)`。

### 血缘响应扩展（T4，`OutputVersion` / `OutputLineageResponse`）
- `OutputVersion` 新字段：`actor_user_id: str | None`、`reverted_from_version: int | None`、`cost_kind: Literal["allocated","exact"] | None`；`run_id` 变 `str | None`。
- `OutputLineageResponse` 新字段：`as_of_seq: int`（= 该链最新登记行的 `seq`；人手版无 seq 用其 `id`）。
- 文本类 `cost_cents` 读时分摊：`lineage_view.allocate_step_costs(versions, step_costs)`，`step_costs: dict[tuple[int,int,int], float]` 键 `(run_id, turn, step)`，来源 `agent_run_transcript_events` 的 `step_end` payload `cost_cents`。同 `(run_id, turn, step)` 下 n 行各得 `cost/n`；`cost_kind="allocated"`。媒体类：登记行 `cost_cents` 非空 → 原值，`cost_kind="exact"`。

### 媒体价（T4）
- `backend/app/services/deliverables/media_price.py::media_price_cents(model: str, provider: str) -> float | None`（`ai_model_prices` 按 `effective_at DESC` 取最新 `per_call_cents`）。
- `register_generated_media()` 在 `origin.cost_cents is None and origin.model and origin.provider` 时调用并写入。
- run 账：`view.cost.media_cents`（fold `deliverable` 事件的 `cost_cents`，去重键同 `outputs.seen`），`recompute_spent`: `spent = own + Σ by_child + media`。前端 `RunCost.media_cents: number`。

### 归因（T0）
`generate_media_tools.generate_image/generate_shot_image` 与 `script_shot_generate.persist_generation` 登记时 `provider=result.provider`、`model=result.model`（`ImageGenResult` 已有这两个字段）。

### 实时（T6）
- WS `/ws/issue/{id}` 的 `status{phase:'done'}` 帧新增 `seq: int`（该 run 最后一个 transcript seq）；`useIssueProgress` 响应 `current_run` 新增 `last_seq: int | null`。
- `frontend/components/Todolist/issueTurnSignal.ts`：
  ```ts
  export type TurnSignal = { runId: string | null; seq: number };
  export function notifyTurn(issueId: string, signal: TurnSignal): void   // seq <= lastSeq[issueId] → 丢
  export function subscribeTurn(issueId: string, cb: (s: TurnSignal) => void): () => void
  export function useTurnSignal(issueId: string): TurnSignal | null        // useSyncExternalStore
  ```
- `frontend/services/outputsService.ts`：`invalidateOutputLineage(kind?: DeliverableKind, refId?: string): void`（带参按键，不带参整表）；缓存条目 `{ data, fetchedAt, asOfSeq }`；`LINEAGE_TTL_MS = 60_000`；`visibilitychange → visible` 时超 TTL 条目失效。`clearOutputLineageCache` 删除。
- 议题页收到 transcript `deliverable` 事件 → `invalidateOutputLineage(ev.kind, ev.ref_id)`。

### 资源来源（T7）
- `GET /api/v1/resources/{resource_id}/provenance` → `OutputLineageResponse`（`kind="generated_media"`）；404 `not_registered`。
- `generated_media_repository.find_by_promoted_resource(resource_id: int) -> GeneratedMedia | None`（`ORDER BY id ASC LIMIT 1`）。
- 前端 `resourceService.getResourceProvenance(resourceId: string): Promise<OutputLineage | null>`（404 → null）；`ResourceInfoPanel` 挂 `OutputProvenance` 块（画布同一组件）。

### UI 文案（T5，英文）
回退确认：有 kept 版「Revert to v1? Your current edits are kept as v4 · v1 becomes v5.」，无则「Revert to v1? This creates v4.」；按钮 `Revert` / `Cancel`；toast `Reverted to v1 as v5`；错误 toast：`version_conflict` → "Someone registered v4 meanwhile — reopen to see it"，`content_unavailable` → "v1 can't be rebuilt (no ledger)"。chip `v5 ↩ v1`（info 色）。花费：`allocated` 显示 `≈¢0.09`（title "Allocated from step cost"），`exact` 显示 `¢12.00`，null 显示 `—`（媒体类 title "No price configured for <model>"）。Budget 块行 `Media ¢24.00`。

### 全局纪律
每 Task 独立 worktree（origin/master）+ 独立 PR；TDD（先红后绿）；新 SQL 一律 ORM（`text()` 只许 dbos 两处例外）；DBOS 步骤写 resources 包 `system_request_scope`；边界 mock 用真实 wire 形状（Snowflake id 在 shots/scenes 是 number、canvases 是 string）；错误体是 `ErrorResponse` 外壳、前端读 `details.code`；UI 文案英文；迁移与消费代码分 PR（ORM 镜像除外）；前端状态色用语义 token（info/ok/warn/danger）。

---

### Task 0: 图片登记归因修复（两条路径写 `ImageGenResult` 解析出的 provider/model）

媒体价按 `(model, provider)` 查表（Task 4），而今天两条出图路径登记的都不是真值：agent 工具写 args/env 的目录行名 + 常为空的 model（`generate_media_tools.py:48-56,82,122-123`）；DBOS 分镜链写 `model="dall-e-3"` 哨兵 + `provider=None`（`script_shot_generate.py:53-54,224-225`，workflow 默认值直传）。三个 adapter 都已填真值（`provider_protocols/codex.py:74-79`、`jimeng.py:40-45`、`video_providers/ark_image.py:119-126`），服务层返回的正是 `asdict(result)`（`image_generation_service.py:145,220,290`）——真值一直在手上，只是被丢掉。与 mig 466 无关，可先发。

**Files:**
- Create `backend/app/services/ai/media/gen_attribution.py`
- Create `backend/tests/services/ai/media/__init__.py`、`backend/tests/services/ai/media/test_gen_attribution.py`
- Modify `backend/app/services/ai/tools/generate_media_tools.py`（`generate_image` :73-140、`generate_video` :159-226）
- Modify `backend/app/workflows/script_shot_generate.py`（`generate_shot_image_step` :96-136、`persist_generation` :160-233、workflow body :301-305）
- Modify `backend/tests/services/ai/tools/test_generate_media_tools.py`（215 行，追加；`_tools` :30-45、`_scratch` :48-53）
- Modify `backend/tests/workflows/test_shot_generate_carries_run.py`（286 行，追加；`_call_step` :244-247、`_fake_scope_id` :240-241）

**Interfaces:**
- Produces `gen_attribution.resolved_attribution(raw: dict, *, requested_provider: str | None, requested_model: str | None) -> tuple[str | None, str | None]`
- Produces `script_shot_generate.generate_shot_image_step(...) -> dict[str, str]`（`{"url","provider","model"}`）、`script_shot_generate._step_output(out: Any) -> tuple[str, str | None, str | None]`
- Consumes `GenerationOrigin(model=…, provider=…)`（`generated_media_service.py:252-285`，签名不改）

- [ ] **Step 1: 失败测试 — 归因三规则**（`backend/tests/services/ai/media/test_gen_attribution.py`）
  ```python
  """生成结果的归因：adapter 报什么就是什么，请求值只是兜底，哨兵永不是答案。"""

  import pytest

  from app.services.ai.media.gen_attribution import (
      DEFAULT_MODEL_SENTINEL,
      resolved_attribution,
  )

  pytestmark = pytest.mark.unit
  OK = {"image_url": "http://x/y.png", "provider": "ark", "model": "doubao-seedream-4-0"}

  def test_adapter_values_win_over_the_request():
      assert resolved_attribution(
          OK, requested_provider="nous-image", requested_model="dall-e-3"
      ) == ("ark", "doubao-seedream-4-0")

  def test_the_request_is_the_fallback_when_the_adapter_says_nothing():
      assert resolved_attribution(
          {"image_url": "u"}, requested_provider="ark", requested_model="seedream"
      ) == ("ark", "seedream")

  def test_the_default_sentinel_is_never_an_answer():
      """``dall-e-3`` 的含义是「用目录行的 actual_model」，不是一个模型名。
      写进登记行，Task 4 按 (model, provider) 查价必然落空（spec §3.2 前置票）。"""
      assert resolved_attribution(
          {"provider": "ark"}, requested_provider=None, requested_model="dall-e-3"
      ) == ("ark", None)

  def test_the_sentinel_matches_the_generation_service():
      from app.services.ai.media.image_generation_service import _DEFAULT_IMAGE_MODEL

      assert DEFAULT_MODEL_SENTINEL == _DEFAULT_IMAGE_MODEL
  ```
- [ ] **Step 2: 跑，看红** — `cd backend && uv run pytest tests/services/ai/media/test_gen_attribution.py -v`
  期望 `ModuleNotFoundError: No module named 'app.services.ai.media.gen_attribution'`。
- [ ] **Step 3: 实现**（`backend/app/services/ai/media/gen_attribution.py`，另建空的 `tests/services/ai/media/__init__.py`）
  ```python
  """生成结果 → 登记行的 (provider, model)。

  ``ImageGenResult`` / ``VideoGenResult``（``video_providers/base.py:6-27``）都带
  这两个字段且三个 adapter 都填了真值；请求侧只是兜底——一边是目录行名，一边是
  ``_DEFAULT_MODEL`` 哨兵，都不是模型真名。哨兵 ``dall-e-3`` 的含义是「用目录行的
  actual_model」（``image_generation_service.py:120-127``），写进登记行会让查价
  落空，所以宁可 None——None 说「不知道」，哨兵说「就是这个模型」。
  """

  from __future__ import annotations

  from typing import Optional

  #: ``image_generation_service._DEFAULT_IMAGE_MODEL`` 的同一个值。此处复制而不
  #: import，是为了不让登记侧反向依赖生成服务；同源守卫在测试里。
  DEFAULT_MODEL_SENTINEL = "dall-e-3"

  def resolved_attribution(
      raw: dict,
      *,
      requested_provider: Optional[str],
      requested_model: Optional[str],
  ) -> tuple[Optional[str], Optional[str]]:
      """``(provider, model)``：adapter 报的优先，请求值兜底，哨兵作废。"""
      provider = ((raw or {}).get("provider") or "").strip() or (
          requested_provider or ""
      ).strip()
      model = ((raw or {}).get("model") or "").strip() or (requested_model or "").strip()
      if model == DEFAULT_MODEL_SENTINEL:
          model = ""
      return (provider or None, model or None)

  __all__ = ["DEFAULT_MODEL_SENTINEL", "resolved_attribution"]
  ```
- [ ] **Step 4: 跑绿** — `cd backend && uv run pytest tests/services/ai/media/test_gen_attribution.py -v` → 4 passed。
- [ ] **Step 5: 失败测试 — agent 工具两条路径**（追加到 `tests/services/ai/tools/test_generate_media_tools.py`）
  ```python
  @pytest.mark.asyncio
  async def test_generate_image_registers_the_resolved_provider_and_model(
      monkeypatch, tmp_path
  ):
      """目录行名 + 空模型不是归因；查价靠的是 adapter 解析出的真值。"""
      local = _scratch(tmp_path, "codeximg_res", "out.png")

      async def _gen(**kwargs):
          return {
              "image_url": "",
              "image_path": local,
              "provider": "ark",
              "model": "doubao-seedream-4-0",
          }

      captured: dict = {}
      tools = _tools(monkeypatch, captured, 556, generate_image=_gen)
      monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "nous-image")
      monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_MODEL", "")

      assert (await tools.generate_image({"prompt": "a cat"}, dict(_CTX)))["ok"] is True
      origin = captured["origin"]
      assert (origin.provider, origin.model) == ("ark", "doubao-seedream-4-0")

  @pytest.mark.asyncio
  async def test_generate_video_registers_the_resolved_provider_and_model(
      monkeypatch, tmp_path
  ):
      local = _scratch(tmp_path, "jimengvid_res", "out.mp4")

      async def _gen(**kwargs):
          return {
              "video_url": "",
              "video_path": local,
              "provider": "jimeng-cli",
              "model": "jimeng-video-3.0",
          }

      captured: dict = {}
      tools = _tools(monkeypatch, captured, 557, generate_video=_gen)
      monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_PROVIDER", "nous-video")
      monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_MODEL", "")

      assert (await tools.generate_video({"prompt": "pan"}, dict(_CTX)))["ok"] is True
      origin = captured["origin"]
      assert (origin.provider, origin.model) == ("jimeng-cli", "jimeng-video-3.0")
  ```
- [ ] **Step 6: 跑，看红** — `cd backend && uv run pytest tests/services/ai/tools/test_generate_media_tools.py -v -k resolved`
  期望两红：`assert ('nous-image', '') == ('ark', 'doubao-seedream-4-0')`、`assert ('nous-video', '') == ('jimeng-cli', 'jimeng-video-3.0')`。
- [ ] **Step 7: 改 agent 工具**
  `generate_media_tools.py` 在 :27 的 import 旁加
  `from app.services.ai.media.gen_attribution import resolved_attribution`。
  `generate_image` 里 `raw = await self._svc().generate_image(...)` 之后、
  `register_generated_media(` 之前插：
  ```python
  gen_provider, gen_model = resolved_attribution(
      raw or {}, requested_provider=provider, requested_model=model
  )
  ```
  origin（:122-123）`model=model,` → `model=gen_model,`；`provider=provider,` → `provider=gen_provider,`。
  `generate_video` 同形（origin 在 :203-210，变量名照该函数现有写法）。
  ⚠️ 只改 origin 两个字段：传给服务层的 `provider_name=` / `model=` 仍是请求值。
- [ ] **Step 8: 跑绿** — `cd backend && uv run pytest tests/services/ai/tools/test_generate_media_tools.py -v` → 全绿（含原有 CLI 路径用例）。
- [ ] **Step 9: 失败测试 — 分镜链 + 旧 checkpoint 兼容**（追加到 `tests/workflows/test_shot_generate_carries_run.py`）
  ```python
  async def test_persist_registers_the_resolved_provider_and_model(monkeypatch):
      """哨兵 dall-e-3 + provider=None 不是归因（spec §3.2 前置票）。"""
      import app.workflows.script_shot_generate as wf

      seen: dict = {}

      async def _fake_register(**kwargs):
          seen["origin"] = kwargs["origin"]
          return {"id": 58}

      monkeypatch.setattr(
          "app.services.library.generated_media_service.register_generated_media",
          _fake_register,
      )
      _stub_repos(monkeypatch)
      monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)

      await _call_step(
          wf.persist_generation,
          shot_id="1",
          provider_url="http://cdn/x.png",
          model="dall-e-3",
          provider=None,
          user_id="u",
          run_id=777,
          turn=1,
          step=4,
          resolved_provider="ark",
          resolved_model="doubao-seedream-4-0",
      )
      origin = seen["origin"]
      assert (origin.provider, origin.model) == ("ark", "doubao-seedream-4-0")

  def test_a_legacy_string_checkpoint_still_persists():
      """DBOS 冻结的旧 step 返回值是裸 str——回放时必须照旧能走完。"""
      import app.workflows.script_shot_generate as wf

      assert wf._step_output("http://cdn/x.png") == ("http://cdn/x.png", None, None)
      assert wf._step_output({"url": "u", "provider": "ark", "model": "m"}) == (
          "u",
          "ark",
          "m",
      )
  ```
  同时把两个已有用例里重复的仓库桩（现 :41-58 的 `_ShotRepo` / `_SceneRepo` 两个
  `monkeypatch.setattr`）抽成模块级 `_stub_repos(monkeypatch)`，三处共用。
- [ ] **Step 10: 跑，看红** — `cd backend && uv run pytest tests/workflows/test_shot_generate_carries_run.py -v -k "resolved or legacy"`
  期望 `TypeError: persist_generation() got an unexpected keyword argument 'resolved_provider'` 与
  `AttributeError: module 'app.workflows.script_shot_generate' has no attribute '_step_output'`。
- [ ] **Step 11: 改分镜链四处**
  1. `generate_shot_image_step` 末尾（:132-136）改成回字典，返回注解改 `dict[str, str]`：
     ```python
     from app.services.ai.media.gen_attribution import resolved_attribution

     produced = (result or {}).get("image_url") or (result or {}).get("image_path")
     if not produced:
         raise RuntimeError(f"Image provider returned no image for shot {shot_id}")
     gen_provider, gen_model = resolved_attribution(
         result or {}, requested_provider=provider, requested_model=model
     )
     logger.info(f"[script_shot_generate][step] shot {shot_id} → {produced}")
     return {"url": produced, "provider": gen_provider or "", "model": gen_model or ""}
     ```
  2. `_compose_prompt` 之后新增：
     ```python
     def _step_output(out: Any) -> tuple[str, Optional[str], Optional[str]]:
         """``generate_shot_image_step`` 的返回值 → ``(url, provider, model)``。

         DBOS 把 step 返回值冻进 checkpoint，所以部署前排队的 workflow 恢复时
         拿回来的仍是旧的裸 ``str``。两种形状都要接：归因是增益，不是继续跑完
         的前提。"""
         if isinstance(out, dict):
             return (
                 str(out.get("url") or ""),
                 out.get("provider") or None,
                 out.get("model") or None,
             )
         return (str(out), None, None)
     ```
  3. `persist_generation` 签名（:160-169）末尾加
     `*, resolved_provider: Optional[str] = None, resolved_model: Optional[str] = None`
     （keyword-only + 默认 None = DBOS 冻结输入兼容）；origin（:224-225）改成
     ```python
     model=resolved_model or (None if model == _DEFAULT_MODEL else model),
     provider=resolved_provider or provider,
     ```
  4. workflow body（:301-305）：
     ```python
     step_out = await generate_shot_image_step(shot_id, model, provider, user_id)
     provider_url, gen_provider, gen_model = _step_output(step_out)
     urls = await persist_generation(
         shot_id, provider_url, model, provider, user_id, run_id, turn, step,
         resolved_provider=gen_provider, resolved_model=gen_model,
     )
     ```
- [ ] **Step 12: 跑绿** — `cd backend && uv run pytest tests/workflows/test_shot_generate_carries_run.py tests/services/ai/media -v` → 全绿。
- [ ] **Step 13: 记录一次突变** — 删掉 `gen_attribution.py` 里 `if model == DEFAULT_MODEL_SENTINEL: model = ""` 两行，跑
  `cd backend && uv run pytest tests/services/ai/media tests/workflows/test_shot_generate_carries_run.py -v`，
  确认 `test_the_default_sentinel_is_never_an_answer` 与 `test_persist_registers_the_resolved_provider_and_model` 转红，
  失败输出抄进 PR 的「突变记录」，然后 `git checkout -- backend/app/services/ai/media/gen_attribution.py`。
- [ ] **Step 14: 提交**
  `git add backend/app/services/ai/media/gen_attribution.py backend/app/services/ai/tools/generate_media_tools.py backend/app/workflows/script_shot_generate.py backend/tests/services/ai/media backend/tests/services/ai/tools/test_generate_media_tools.py backend/tests/workflows/test_shot_generate_carries_run.py && git commit -m "fix(genmedia): 图片/视频登记写 adapter 解析出的 provider/model，哨兵不入账（3b Task 0）"`

---

---

### Task 1: mig 466 — 回退占号列 + 媒体按次价 + ORM 三处镜像

> **反查索引不加。** `generated_media(promoted_resource_id)` 上已有两个索引：`idx_genmedia_promoted`（`supabase/migrations/307_generated_media.sql:38`）与 partial UNIQUE `uq_genmedia_promoted_resource`（`supabase/migrations/456_generated_media_promoted_unique.sql:10-12`，ORM 镜像 `backend/app/models/generated_media.py:61-68`）。spec §2.4 要的「反查必须走索引」已成立，466 不再新建；改为在集成测试里钉住这两个仍在。

**Files:**
- Create `supabase/migrations/466_harness_3b_revert_cost.sql`
- Create `backend/tests/db/test_migration_466_revert_cost.py`
- Create `backend/tests/db/test_migration_466_revert_cost_integration.py`
- Modify `backend/app/models/agents.py`（`RunDeliverables` 674-726）
- Modify `backend/app/models/scripts.py`（`ScriptShotOps` 719-750）
- Modify `backend/app/models/ai.py`（`AiModelPrices` 189-229）
- Modify `.github/workflows/schema-drift.yml`（`paths:` 清单 + 末尾一个集成 step）

**Interfaces:**
- Produces（SQL）`run_deliverables.run_id` NULLable + 新列 `actor_user_id UUID` / `reverted_from_version INTEGER` / `ledger_ref TEXT` + CHECK `run_deliverables_run_or_actor`；`script_shot_ops.run_id` NULLable + 新列 `actor TEXT` + CHECK `script_shot_ops_run_or_actor`；`ai_model_prices.per_call_cents NUMERIC(12,4)`。
- Produces（ORM）`RunDeliverables.actor_user_id/reverted_from_version/ledger_ref`、`ScriptShotOps.actor`、`AiModelPrices.per_call_cents`。
- Consumes 无（本 Task 不碰消费代码）。

- [ ] **Step 1: 写迁移文本 + ORM 镜像的失败测试**

```python
# backend/tests/db/test_migration_466_revert_cost.py
"""466 的形状：两处放宽 NOT NULL、四个新列、两个具名 CHECK、幂等、不 SET ROLE，
以及同 PR 的 ORM 镜像。照 tests/db/test_migration_462_deliverables.py 的读文件风格
——只读可执行正文（剥掉整行注释），注释里复述语句不算数。"""
from __future__ import annotations
import pathlib
import re
import pytest
from sqlalchemy import CheckConstraint
from app.models.agents import RunDeliverables
from app.models.ai import AiModelPrices
from app.models.scripts import ScriptShotOps

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "466_harness_3b_revert_cost.sql").read_text(encoding="utf-8")
BODY = "\n".join(l for l in _RAW.splitlines() if not l.strip().startswith("--"))
_SQ = re.sub(r"[ \t]+", " ", BODY)
NEW_COLS = ("actor_user_id", "reverted_from_version", "ledger_ref", "actor",
            "per_call_cents")

def test_both_run_id_columns_lose_not_null():
    """回退是人手占号：两张表的 run_id 都要允许 NULL，否则登记结构上不可能。"""
    assert _SQ.count("ALTER COLUMN run_id DROP NOT NULL") == 2

def test_adds_every_new_column_idempotently():
    for col in NEW_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQ, col
    assert "ADD COLUMN " not in _SQ.replace("ADD COLUMN IF NOT EXISTS ", "")

def test_both_checks_are_named_and_say_run_or_actor():
    assert "CONSTRAINT run_deliverables_run_or_actor" in _SQ
    assert "CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL)" in _SQ
    assert "CONSTRAINT script_shot_ops_run_or_actor" in _SQ
    assert "CHECK (run_id IS NOT NULL OR actor IS NOT NULL)" in _SQ
    assert _SQ.count("DROP CONSTRAINT IF EXISTS") == 2

def test_no_third_index_no_set_role_single_transaction():
    """307 与 456 已各建一个索引；再建一个只是多一份写放大。"""
    assert "idx_generated_media_promoted_resource" not in BODY
    assert not any(re.match(r"(?i)^SET\s+ROLE\b", l.strip())
                   for l in BODY.splitlines()), "migrations must not SET ROLE"
    assert BODY.count("BEGIN;") == 1 and BODY.count("COMMIT;") == 1

def test_the_orm_mirrors_the_nullable_run_ids_and_new_columns():
    assert RunDeliverables.__table__.columns["run_id"].nullable
    assert ScriptShotOps.__table__.columns["run_id"].nullable
    d = RunDeliverables.__table__.columns
    for name in ("actor_user_id", "reverted_from_version", "ledger_ref"):
        assert name in d and d[name].nullable, name
    assert str(d["actor_user_id"].type) == "UUID"
    assert ScriptShotOps.__table__.columns["actor"].nullable
    price = AiModelPrices.__table__.columns["per_call_cents"]
    assert price.nullable and str(price.type) == "NUMERIC(12, 4)"

def test_the_orm_mirrors_both_check_constraints():
    """CHECK 漏在 ORM 侧，drift 门禁比的是名字——少一个就是两个 schema。"""
    def names(model):
        return {c.name for c in model.__table__.constraints
                if isinstance(c, CheckConstraint)}
    assert "run_deliverables_run_or_actor" in names(RunDeliverables)
    assert "script_shot_ops_run_or_actor" in names(ScriptShotOps)
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd backend && uv run pytest tests/db/test_migration_466_revert_cost.py -v
```
预期：collection 阶段 `FileNotFoundError: .../supabase/migrations/466_harness_3b_revert_cost.sql`。

- [ ] **Step 3: 写迁移**

```sql
-- supabase/migrations/466_harness_3b_revert_cost.sql
-- 466: harness 三期 3b —— 回退占号 + 媒体按次价（spec §2.1）。
--
-- 回退是「人手发起但要占号」的唯一动作：run_deliverables.run_id 与
-- script_shot_ops.run_id 原本都 NOT NULL REFERENCES agent_runs(id)，于是人手回退
-- 结构上写不进账本、也拿不到版本号。这里放宽两处 NOT NULL，各加一个具名 CHECK
-- 换回约束 —— 「既没有 run 也没有人」的行仍然不允许存在。
--
-- per_call_cents：图片/视频按次计价，沿用 ai_model_prices 这张「按 effective_at
-- 版本化、只追加不改」的表，不造第二张价格表。
--
-- generated_media(promoted_resource_id) 的反查索引不在这里：307 的
-- idx_genmedia_promoted 与 456 的 uq_genmedia_promoted_resource 已经覆盖。
BEGIN;

ALTER TABLE public.run_deliverables
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS actor_user_id UUID,
    ADD COLUMN IF NOT EXISTS reverted_from_version INTEGER,
    ADD COLUMN IF NOT EXISTS ledger_ref TEXT;

ALTER TABLE public.run_deliverables
    DROP CONSTRAINT IF EXISTS run_deliverables_run_or_actor;
ALTER TABLE public.run_deliverables
    ADD CONSTRAINT run_deliverables_run_or_actor
    CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL);

ALTER TABLE public.script_shot_ops
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS actor TEXT;

ALTER TABLE public.script_shot_ops
    DROP CONSTRAINT IF EXISTS script_shot_ops_run_or_actor;
ALTER TABLE public.script_shot_ops
    ADD CONSTRAINT script_shot_ops_run_or_actor
    CHECK (run_id IS NOT NULL OR actor IS NOT NULL);

ALTER TABLE public.ai_model_prices
    ADD COLUMN IF NOT EXISTS per_call_cents NUMERIC(12,4);

COMMIT;
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 4: 写 ORM 镜像**

`app/models/agents.py` — `RunDeliverables.__table_args__` 在 `Index("idx_run_deliverables_run", "run_id"),`（682 行）之后插入 CheckConstraint，707 行的 `run_id` 起替换：

```python
        CheckConstraint("run_id IS NOT NULL OR actor_user_id IS NOT NULL",
                        name="run_deliverables_run_or_actor"),
...
    # 466: 回退是人手占号 —— run 为空时 actor_user_id 必须有值（CHECK 兜住）。
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    reverted_from_version: Mapped[Optional[int]] = mapped_column(Integer)
    #: 账本位置：script_shot → script_shot_ops.id，script_scene → op_seq 水位。
    #: 存量行 NULL，diff 退回 created_at。
    ledger_ref: Mapped[Optional[str]] = mapped_column(Text)
```

`app/models/scripts.py` — `ScriptShotOps.__table_args__` 加同形 CheckConstraint（`name="script_shot_ops_run_or_actor"`，表达式 `"run_id IS NOT NULL OR actor IS NOT NULL"`），742 行替换：

```python
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    #: 同 script_ops.actor 的形状：'revert:<uid>' / 'keep:<uid>'。NULL = agent 写的。
    #: undo 按 run_id 读（run_undo_service.py:62），人手行天然不入 undo。
    actor: Mapped[Optional[str]] = mapped_column(Text)
```

`app/models/ai.py` — `AiModelPrices`（229 行 `cached_input_cents_per_1k` 旁）加：

```python
    #: 466: 图片/视频模型按**次**计价（每千 token 的两列对它们无意义）。
    per_call_cents: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 4))
```

按需补 `CheckConstraint` / `uuid` / `Uuid` 的 import（`app/models/ai.py:203` 已有 `Uuid`，`app/models/agent_messaging.py:12` 已有 `CheckConstraint`）。

- [ ] **Step 5: 再跑，确认绿**

```bash
cd backend && uv run pytest tests/db/test_migration_466_revert_cost.py -v
```
预期：6 passed。

- [ ] **Step 6: 写真 PG 集成测试**

```python
# backend/tests/db/test_migration_466_revert_cost_integration.py
"""466 在真 Postgres 上的样子。文本测试读的是我们写了什么，这里读的是服务器接受了
什么——不同的问题（CLAUDE.md「读正常 ≠ 服务正常」）。

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_migration_466_revert_cost_integration.py -v

script_shot_ops 的正例要 script_projects→scenes→shots 整条 FK 链，代价远大于它能
回答的问题，所以那张表只做负例：CHECK 在 ExecConstraints 阶段求值、FK 是语句末尾的
AFTER 触发器，所以违反 CHECK 的插入先拿 CheckViolation；CHECK 若不存在，同一句拿
ForeignKeyViolation —— 两种结果都不是 pass，测试仍可证伪。"""
from __future__ import annotations
import decimal
import os
import uuid
import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
pytest.importorskip("asyncpg")
_skip = pytest.mark.skipif(
    not _TEST_DSN, reason="INTEGRATION_DATABASE_URL not set — mig 466 needs a DB.")

@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()

@_skip
async def test_a_human_registration_needs_no_run(pg):
    """回退行：run_id NULL + actor_user_id 有值。整条回退路径靠它，且不需要 FK。"""
    ref = f"mig466-{uuid.uuid4().hex[:12]}"
    row_id = await pg.fetchval(
        "INSERT INTO public.run_deliverables (run_id, kind, ref_id, version,"
        " actor_user_id, reverted_from_version, ledger_ref)"
        " VALUES (NULL, 'script_shot', $1, 1, $2, 3, '9001') RETURNING id",
        ref, uuid.uuid4())
    try:
        got = await pg.fetchrow(
            "SELECT run_id, reverted_from_version, ledger_ref"
            " FROM public.run_deliverables WHERE id = $1", row_id)
        assert got["run_id"] is None
        assert got["reverted_from_version"] == 3 and got["ledger_ref"] == "9001"
    finally:
        await pg.execute("DELETE FROM public.run_deliverables WHERE id = $1", row_id)

@_skip
async def test_neither_a_run_nor_an_actor_is_refused(pg):
    """「既无 run 也无人」的行不许存在——否则版本号可被任何路径白占。"""
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as err:
        await pg.execute(
            "INSERT INTO public.run_deliverables (run_id, kind, ref_id, version)"
            " VALUES (NULL, 'script_shot', $1, 1)", f"bad-{uuid.uuid4().hex[:8]}")
    assert "run_deliverables_run_or_actor" in str(err.value)

@_skip
async def test_shot_ops_refuses_a_row_with_neither_run_nor_actor(pg):
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as err:
        await pg.execute(
            "INSERT INTO public.script_shot_ops (run_id, shot_id, scene_id, action,"
            " after_json, actor) VALUES (NULL, 1, 1, 'update', '{}'::jsonb, NULL)")
    assert "script_shot_ops_run_or_actor" in str(err.value)

@_skip
async def test_per_call_cents_keeps_four_decimals(pg):
    """按次价常是 12.0000，但 0.0125 这种不能被截成 0.01。"""
    model = f"mig466-{uuid.uuid4().hex[:8]}"
    await pg.execute(
        "INSERT INTO public.ai_model_prices (model, provider, prompt_cents_per_1k,"
        " completion_cents_per_1k, per_call_cents) VALUES ($1,'mig466',0,0,0.0125)",
        model)
    try:
        got = await pg.fetchval(
            "SELECT per_call_cents FROM public.ai_model_prices WHERE model=$1", model)
        assert got == decimal.Decimal("0.0125")
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model=$1", model)

@_skip
async def test_the_promoted_resource_reverse_lookup_is_already_indexed(pg):
    """3b 的资源反查（spec §2.4）不建新索引，因为这两个已经在。删掉任一个都会让
    反查退化成全表扫，而没有别的测试会说出来。"""
    names = {r["indexname"] for r in await pg.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
        " AND tablename='generated_media'")}
    assert "idx_genmedia_promoted" in names
    assert "uq_genmedia_promoted_resource" in names
```

```bash
cd backend && uv run pytest tests/db/test_migration_466_revert_cost_integration.py -v
```
预期（本机无 DB）：5 skipped —— 这正是 `pytest-no-full-skip.sh` 在 CI 里拦的状态；真验证在 Step 7。

- [ ] **Step 7: 接进 schema-drift.yml**

`pull_request.paths` 里（`- 'backend/tests/db/test_run_deliverables_repository_integration.py'` 之后）加 `- 'backend/tests/db/test_migration_466_revert_cost_integration.py'`；文件末尾追加：

```yaml
      - name: Assert mig 466's nullable run ids, both CHECKs & the per-call price
        working-directory: backend
        env:
          INTEGRATION_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/drift
        run: |
          set -euo pipefail
          # 466 放宽两个 NOT NULL，用两个 CHECK 换回约束。文本测试读迁移写了什么；
          # 只有服务器能回答 CHECK 是否真的拒绝「既无 run 也无人」的行，以及
          # NUMERIC(12,4) 是否真的留住第四位小数。同一步顺带钉住 generated_media
          # 上那两个反查索引仍在 —— 3b 正因为它们才没有再建一个。
          bash "$GITHUB_WORKSPACE/.github/scripts/pytest-no-full-skip.sh" tests/db/test_migration_466_revert_cost_integration.py -v
```

把 `.github/scripts/pytest-no-full-skip.sh` 那条 paths 注释里的计数**重数**（不要递增）后改写，并本机 lint：

```bash
grep -cE '^\s*bash .*pytest-no-full-skip\.sh" tests/' .github/workflows/schema-drift.yml
/tmp/actionlint .github/workflows/schema-drift.yml && echo CLEAN
```

- [ ] **Step 8: 突变验证（记进 SDD 小票）**

把迁移里 `CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL)` 临时改成 `CHECK (true)`，跑 `cd backend && uv run pytest tests/db/test_migration_466_revert_cost.py -v`，确认 `test_both_checks_are_named_and_say_run_or_actor` 转红；再把 ORM 的 `run_id: Mapped[Optional[int]] = mapped_column(BigInteger)` 改回 `mapped_column(BigInteger, nullable=False)`，确认 `test_the_orm_mirrors_the_nullable_run_ids_and_new_columns` 转红。两处恢复并重跑确认 6 passed。

- [ ] **Step 9: 提交**

```bash
git add supabase/migrations/466_harness_3b_revert_cost.sql \
  backend/app/models/agents.py backend/app/models/scripts.py backend/app/models/ai.py \
  backend/tests/db/test_migration_466_revert_cost.py \
  backend/tests/db/test_migration_466_revert_cost_integration.py \
  .github/workflows/schema-drift.yml && \
git commit -m "feat(db): mig 466 放宽两处 run_id + 回退血缘三列 + 按次价，ORM 同 PR 镜像（harness 三期 3b Task 1）"
```

---

---

### Task 2: 登记口收人手作者与 `ledger_ref`，diff 改用账本外键定位

**Files:**
- Modify `backend/app/services/deliverables/registry.py`（签名 54-65、no-op 分支 67-80、插行 87-104、事件 106-127、`_as_row` 258-268）
- Modify `backend/app/repositories/run_deliverables_repository.py`（`_row` 32-45、`latest_version` 50-62、`insert_version` 64-73、`set_seq` 75-90、`lineage_for` 的 `.join(AgentRuns…)` 在 133 行）
- Modify `backend/app/services/deliverables/diff.py`（`_at_or_before` 97-112、`_shot_ledger` 115-124、`_scene_ledger` 127-140、`_shot_side` 242-250、`_scene_side` 252-258）
- Modify `backend/app/services/ai/scope/scoped_script_gateway.py`（ops INSERT 863-872 与 927-937，及两处 `return _shot_dict(...)`）
- Modify `backend/app/services/ai/tools/screenwriting_tools.py`（`_register_write` 245-267、调用点 373 / 566 / 777）
- Modify `backend/app/schemas/outputs.py`（`OutputVersion` 22-62、`OutputDiffSide` 88-110）
- Modify `backend/app/services/deliverables/lineage_view.py`（`_VERSION_KEYS` 44-58）
- Modify `backend/tests/services/deliverables/conftest.py`（`RepoSpy`）与 `test_diff.py`（79-233 的 `_ledger` 桩改吐三元组）
- Create `backend/tests/services/deliverables/test_registry_actor.py`
- Create `backend/tests/services/deliverables/test_ledger_ref.py`

**Interfaces:**
- Produces `register_deliverable(*, run_id, kind, ref_id, title=None, model=None, cost_cents=None, turn=None, step=None, recorder=None, actor_user_id: str | None = None, reverted_from_version: int | None = None, ledger_ref: str | None = None, session=None) -> DeliverableRow | None`
- Produces `DeliverableRow(id, run_id: str | None, kind, ref_id, version, parent_version, title, actor_user_id: str | None, reverted_from_version: int | None)`
- Produces `RunDeliverablesRepository.{latest_version,insert_version,set_seq}(..., session=None)`
- Produces `diff._prefix_for(ledger: list[tuple[Any, datetime | None, int]], row: dict) -> list[Any]`
- Produces wire `OutputVersion.run_id: str | None` / `.actor_user_id: str | None` / `.reverted_from_version: int | None`；`OutputDiffSide.run_id: str | None`
- Consumes Task 1 的四个新列

> Part B 单独加 `cost_kind` / `as_of_seq`；本 Task 不定义它们。

- [ ] **Step 1: 写登记口的失败测试**

```python
# backend/tests/services/deliverables/test_registry_actor.py
"""3b：登记口收人手作者。三条不变量：普通人手改动仍 no-op（3a 核心不变量，已由
test_registry.py 钉住）；人手版不落 transcript 事件（没有 run 可挂，硬挂到别的 run
上等于伪造一次运行）；reverted_from_version 只有和 actor 一起才成立——单独出现是
接线 bug，要在 CI 里炸而不是在生产里留一行说不清归属的记录。"""
import pytest
from app.services.deliverables.registry import register_deliverable

ME = "11111111-1111-1111-1111-111111111111"

async def test_an_actor_makes_a_runless_registration_land(repo_spy, emit_spy):
    repo_spy.latest_version_returns = 2
    out = await register_deliverable(
        run_id=None, kind="script_shot", ref_id="9", title="S1 · Shot 3",
        actor_user_id=ME, reverted_from_version=1, ledger_ref="9001")
    assert (out.version, out.parent_version, out.run_id) == (3, 2, None)
    assert out.actor_user_id == ME and out.reverted_from_version == 1
    inserted = repo_spy.inserts[-1]
    assert inserted["run_id"] is None and inserted["ledger_ref"] == "9001"
    assert inserted["reverted_from_version"] == 1

async def test_a_human_version_lands_no_transcript_event(repo_spy, emit_spy):
    await register_deliverable(run_id=None, kind="script_scene", ref_id="7",
                               actor_user_id=ME)
    assert emit_spy.events == [] and repo_spy.seq_calls == []

async def test_reverted_from_without_an_actor_is_a_typed_failure(repo_spy):
    with pytest.raises(ValueError, match="reverted_from_version"):
        await register_deliverable(run_id=777, kind="script_shot", ref_id="9",
                                   reverted_from_version=1)
    assert repo_spy.inserts == []

async def test_an_agent_registration_carries_its_ledger_ref(repo_spy, emit_spy):
    await register_deliverable(run_id=777, kind="script_shot", ref_id="9",
                               ledger_ref="4242")
    assert repo_spy.inserts[-1]["ledger_ref"] == "4242"
    # 账本位置是服务端定位字段，不进模型看得见的事件载荷。
    assert "ledger_ref" not in emit_spy.events[-1].payload

async def test_a_caller_session_is_handed_to_every_repo_call(repo_spy, emit_spy):
    sentinel = object()
    await register_deliverable(run_id=None, kind="script_shot", ref_id="9",
                               actor_user_id=ME, session=sentinel)
    assert repo_spy.sessions == [sentinel, sentinel]  # latest_version + insert
```

`tests/services/deliverables/conftest.py` 的 `RepoSpy` 同步加 `self.sessions: list = []`，并让 `latest_version` / `insert_version` / `set_seq` 各接 `session=None`，前两个记 `self.sessions.append(session)`。

- [ ] **Step 2: 跑，确认红**

```bash
cd backend && uv run pytest tests/services/deliverables/test_registry_actor.py -v
```
预期：`TypeError: register_deliverable() got an unexpected keyword argument 'actor_user_id'`（5 failures）。

- [ ] **Step 3: 改登记口 + repository**

`registry.py` 签名追加 `actor_user_id: Optional[str] = None, reverted_from_version: Optional[int] = None, ledger_ref: Optional[str] = None, session: Any = None`，并把 67-80 行的 no-op 判定替换为：

```python
    if reverted_from_version is not None and actor_user_id is None:
        raise ValueError("reverted_from_version needs an actor_user_id — a revert "
                         "is a human act and must say whose")
    rid = None if run_id in _EMPTY_RUN_IDS else _bigint_run_id(run_id)
    if rid is None and actor_user_id is None:
        # 3a 不变量：普通人手改动 / 画布保存 / 前端直传不占号。非 bigint 的 run id
        # 也落这里，但要说出来——静默会让一条真的接线错误永远不被发现。
        if run_id not in _EMPTY_RUN_IDS:
            logger.warning(f"[deliverables] {kind}/{ref_id}: run_id {run_id!r} is "
                           "not a bigint and no actor was given — registered nothing")
        return None
```

`_insert_next_version(...)` 的 values 追加 `actor_user_id=actor_user_id, reverted_from_version=reverted_from_version, ledger_ref=ledger_ref`，并整条链透传 `session=session`（`latest_version` / `insert_version` / `set_seq`）。在 `rec = recorder if ...` 之前插入：

```python
    if rid is None:
        # 人手版没有 run，也就没有 transcript 可写。血缘端点读的是行，不是事件。
        return row
```

`DeliverableRow.run_id` 改 `Optional[str]`，追加 `actor_user_id: Optional[str] = None` 与 `reverted_from_version: Optional[int] = None`；`_as_row` 相应把 `run_id` 与 `actor_user_id` 做「非 None 才 `str(...)`」，并读出 `reverted_from_version`。

`run_deliverables_repository.py` 顶部加 `import uuid as _uuid`、`from contextlib import asynccontextmanager` 与：

```python
@asynccontextmanager
async def _scope(session, *, write: bool):
    """调用方给了 session 就用它（加入其事务、不 commit）；没给就照旧开自己的。

    回退把「改内容 / 写账本 / 登记版本」放进同一个 postgres 事务——登记在事务外就会
    出现「内容回了、版本没记」（3b spec §2.3 步 4）。"""
    if session is not None:
        yield session
        return
    ctx = write_scope() if write else read_scope()
    async with ctx as owned:
        yield owned
```

三个方法签名各加 `session: Any = None`，把 `async with read_scope()/write_scope() as session:` 换成 `async with _scope(session, write=…) as session:`。`_row` 的循环加一支 `elif isinstance(value, _uuid.UUID): out[key] = str(value)`。`lineage_for` 的 133 行 `.join(AgentRuns, …)` 改 `.outerjoin(AgentRuns, …)`，docstring 追加：

```
        ⚠️ run 侧也是 **outer** join（3b）：回退版的 run_id 是 NULL，内连接会让整条
        链在第一次回退之后当场消失。list_for_issue 保持内连接——人手版本不属于任何
        issue，那个读的入口本来就是 issue。
```

- [ ] **Step 4: 再跑，确认绿**

```bash
cd backend && uv run pytest tests/services/deliverables/ -v
```
预期：`test_registry_actor.py` 5 passed，既有 registry / version_chain 套件全绿。

- [ ] **Step 5: 写 `ledger_ref` 的失败测试**

```python
# backend/tests/services/deliverables/test_ledger_ref.py
"""ledger_ref：版本 → 账本行的**精确**映射（spec §2.2 / 3a 小票 C6）。

3a 按 created_at 映射（diff._at_or_before），那不是外键：同一个事务里的两次写共享
事务开始时间，按它切会把 v1 画成 v2。回退要写回的正是「某一版的内容」，错位比显示
错更糟。存量行没有 ledger_ref 且永远不会有，所以 created_at 是常设退路。"""
import datetime as _dt
import types
import pytest
from app.services.deliverables import diff as mod

pytestmark = pytest.mark.unit

def _at(day: int) -> _dt.datetime:
    return _dt.datetime(2026, 9, day, tzinfo=_dt.timezone.utc)

SHOT = [({"shot_type": "WS", "description": "v1 text"}, _at(1), 101),
        ({"description": "v2 text"}, _at(2), 102),
        ({"description": "v3 text"}, _at(3), 103)]

def _row(version, *, ledger_ref=None, created_at=None):
    return {"version": version, "run_id": "913402881190401", "issue_id": None,
            "created_at": created_at, "model": None, "cost_cents": None,
            "title": f"v{version}", "ledger_ref": ledger_ref}

def test_a_ledger_ref_beats_a_timestamp_that_would_pick_the_wrong_prefix():
    """三行账本落在同一秒（真实：一个事务里的多次写共享 created_at）。只有
    ledger_ref 能把 v1 和 v3 分开。"""
    same_second = [(p, _at(3), k) for p, _c, k in SHOT]
    side = mod._shot_side(
        _row(1, ledger_ref="101", created_at=_at(3).isoformat()), same_second)
    assert side["available"] is True
    assert side["text"] == "shot_type: WS\ndescription: v1 text"

@pytest.mark.parametrize("ref", [None, "not-a-number"])
def test_a_missing_or_garbage_ledger_ref_falls_back_to_the_timestamp(ref):
    """存量行（None）与脏值走同一条退路——一条血缘不该因为一个字段而 500。"""
    side = mod._shot_side(_row(2, ledger_ref=ref, created_at=_at(2).isoformat()), SHOT)
    assert side["text"] == "shot_type: WS\ndescription: v2 text"

def test_a_scene_replays_to_the_ledger_ref_watermark():
    ledger = [
        ({"op_seq": 1, "op_json": {"ops": [{"op": "insert", "element": {
            "id": "e1", "type": "action", "text": "a"}}]}}, _at(5), 1),
        ({"op_seq": 2, "op_json": {"ops": [{"op": "update", "element_id": "e1",
                                            "text": "b"}]}}, _at(5), 2)]
    side = mod._scene_side(
        _row(1, ledger_ref="1", created_at=_at(5).isoformat()), ledger)
    assert side["text"] == "action: a"

def test_ledger_ref_never_reaches_the_wire():
    """账本位置是服务端定位字段。上线等于把 ops 行 id 交给浏览器。"""
    from app.services.deliverables.lineage_view import version_of
    assert "ledger_ref" not in version_of(_row(1, ledger_ref="101"))

async def test_register_write_forwards_the_ledger_ref(monkeypatch):
    """网关 RETURNING 出来的 script_shot_ops.id 必须一路走到登记口——差一步就等于
    这个字段永远是 NULL，而所有读侧测试照样绿。"""
    from app.services.ai.tools import screenwriting_tools as tools
    seen = {}
    async def _reg(**kw):
        seen.update(kw)
    monkeypatch.setattr(tools, "register_deliverable_best_effort", _reg)
    await tools._register_write(
        types.SimpleNamespace(run_id="777", user_id="u"), {"turn": 1, "step": 2},
        kind="script_shot", ref_id="9", title="t", ledger_ref="4242")
    assert seen["ledger_ref"] == "4242"

def test_the_shot_dict_the_model_sees_carries_no_ledger_ref():
    """账本行 id 是宿主侧定位字段。留在工具返回值里 = 进模型上下文。"""
    from app.services.ai.tools import screenwriting_tools as tools
    shot = {"shot_id": "9", "ledger_ref": "4242"}
    assert tools._take_ledger_ref(shot) == "4242"
    assert "ledger_ref" not in shot
```

```bash
cd backend && uv run pytest tests/services/deliverables/test_ledger_ref.py -v
```
预期：三个 side 用例红（`ValueError: too many values to unpack` —— 账本现在是三元组而 `_at_or_before` 只解两个），后两个红（`AttributeError: ... has no attribute '_take_ledger_ref'` / `TypeError: unexpected keyword 'ledger_ref'`）。

- [ ] **Step 6: 实现读侧（diff + wire）**

`diff.py` 把 `_at_or_before`（97-112）整体替换为：

```python
def _prefix_for(ledger: List[Tuple[Any, Optional[_dt.datetime], int]],
                row: Dict[str, Any]) -> List[Any]:
    """这一版能看见的账本前缀。

    优先 ``ledger_ref``（登记时记下的账本位置：shot 是 script_shot_ops.id，scene 是
    script_ops.op_seq 水位）——那是**外键**；``created_at`` 只是巧合上单调的时间戳，
    一个事务里的两次写共享事务开始时间，按它切会把 v1 画成 v2。存量行没有
    ledger_ref 且永远不会有，所以时间戳这条退路是常设的，不是过渡期妥协。解析不出
    数字的脏值同样退回时间戳：一条血缘不该因为一个字段而 500。"""
    ref = row.get("ledger_ref")
    if ref not in (None, ""):
        try:
            cut = int(str(ref))
        except (TypeError, ValueError):
            cut = None
        if cut is not None:
            return [payload for payload, _created, key in ledger if key <= cut]
    stamp = _as_datetime(row.get("created_at"))
    if stamp is None:
        return [payload for payload, _created, _key in ledger]
    return [payload for payload, created, _key in ledger
            if created is not None and created <= stamp]
```

`_shot_ledger` 改选三列（`after_json, created_at, id`）、`order_by(ScriptShotOps.id.asc())`（snowflake 单调，且 id 现在就是 `ledger_ref` 的定义域；`created_at` 为 NULL 的行不再被丢掉），返回 `[(row[0] or {}, _as_datetime(row[1]), int(row[2])) for row in rows]`。`_scene_ledger` 返回 `[({"op_seq": row[0], "op_json": row[1] or {}}, _as_datetime(row[2]), int(row[0])) for row in rows]`。

`_shot_side` / `_scene_side` 的 `_at_or_before(ledger, row.get("created_at"))` 换成 `_prefix_for(ledger, row)`；`_scene_side` 的 watermark 改为：

```python
    ref = row.get("ledger_ref")
    watermark = (int(str(ref)) if str(ref or "").isdigit()
                 else max(int(op["op_seq"]) for op in prefix))
```

`tests/services/deliverables/test_diff.py` 里 79-233 行的 `_ledger` 桩返回值从二元组补成三元组（shot 用递增假 ops id，scene 用 `op_seq` 本身当第三位）。

`schemas/outputs.py`：`OutputVersion.run_id` 改 `Optional[str] = None` 并追加

```python
    #: 3b：人手登记的作者（回退是唯一的人手占号路径）。run_id 为空时它必非空，反之
    #: 亦然——CHECK run_deliverables_run_or_actor 兜住。
    actor_user_id: Optional[str] = None
    #: 这一版是从哪一版回退来的。None = 正常前进的一版。
    reverted_from_version: Optional[int] = None
```

`OutputDiffSide.run_id` 同改 `Optional[str] = None`（回退版两侧都要能画）。`lineage_view._VERSION_KEYS` 追加 `"actor_user_id"` 与 `"reverted_from_version"`（**不加** `ledger_ref`）。

- [ ] **Step 7: 实现写侧（网关 + 工具）**

`scoped_script_gateway.create_shot`（863-872）的 ops INSERT 改为带 RETURNING（`ledger_ref` 声明在 `async with write_scope()` 之前，否则 return 时不可见）：

```python
            ledger_ref = None
            if rid is not None:
                # 同事务——写入失败不留账，记账失败连卡一起回滚。
                ledger_ref = (await session.execute(
                    insert(ScriptShotOps)
                    .values(run_id=rid, shot_id=row.id, scene_id=scene.id,
                            action="create", before_json=None,
                            after_json={f: getattr(row, f)
                                        for f in _WRITABLE_SHOT_FIELDS})
                    .returning(ScriptShotOps.id))).scalar_one()
```

两处 `return _shot_dict(row, scene_no)` 改为
`return {**_shot_dict(row, scene_no), "ledger_ref": str(ledger_ref) if ledger_ref is not None else None}`；`update_shot`（927-937）同形。

`screenwriting_tools.py` 加：

```python
def _take_ledger_ref(shot: dict) -> Optional[str]:
    """把账本位置从工具返回值里**取走**。宿主侧定位字段，留在 dict 里就会进模型
    上下文（同 Tool.to_descriptor 的白名单投影纪律）。"""
    return shot.pop("ledger_ref", None)
```

`_register_write` 签名加 `ledger_ref: Optional[str] = None` 并透传给 `register_deliverable_best_effort`。`create_shot`（373）与 `update_shot`（566）在调用前 `ref = _take_ledger_ref(shot)` / `_take_ledger_ref(updated)`，传 `ledger_ref=ref`。`apply_edit`（777）传 `ledger_ref=str(outcome.content_version)` —— `content_version` 按构造就是新的 `op_seq` 水位（`app/services/script/version_service.py:25`「Watermark = 一个场次的 content_version（== MAX(op_seq)）」）。

```bash
cd backend && uv run pytest tests/services/deliverables/ tests/test_script_shot_ops_ledger.py -v
```
预期：全绿。

- [ ] **Step 8: 突变验证**

把 `_prefix_for` 的 `if ref not in (None, ""):` 临时改成 `if False:`，跑 `cd backend && uv run pytest tests/services/deliverables/test_ledger_ref.py -v`，确认 `test_a_ledger_ref_beats_a_timestamp_that_would_pick_the_wrong_prefix` 与 `test_a_scene_replays_to_the_ledger_ref_watermark` 转红；恢复。再把 `_take_ledger_ref` 的 `shot.pop(...)` 改成 `shot.get(...)`，确认 `test_the_shot_dict_the_model_sees_carries_no_ledger_ref` 转红；恢复并重跑确认全绿。

- [ ] **Step 9: 提交**

```bash
git add backend/app/services/deliverables/registry.py \
  backend/app/repositories/run_deliverables_repository.py \
  backend/app/services/deliverables/diff.py \
  backend/app/services/deliverables/lineage_view.py backend/app/schemas/outputs.py \
  backend/app/services/ai/scope/scoped_script_gateway.py \
  backend/app/services/ai/tools/screenwriting_tools.py \
  backend/tests/services/deliverables/ && \
git commit -m "feat(deliverables): 登记口收 actor/reverted_from/ledger_ref 与调用方 session，diff 改用账本外键定位（harness 三期 3b Task 2）"
```

---

---

### Task 3: 回退服务 + `POST /outputs/{kind}/{ref_id}/revert`

**Files:**
- Create `backend/app/services/deliverables/revert.py`
- Create `backend/tests/services/deliverables/test_revert.py`
- Create `backend/tests/api/test_outputs_revert_router.py`
- Modify `backend/app/services/deliverables/diff.py`（追加 `rebuild_content`）
- Modify `backend/app/api/outputs_router.py`（`_visible_chain` 89-116 改名 `visible_chain`，两处调用点 122 / 155 同改；新增 POST 路由）
- Modify `backend/app/schemas/outputs.py`（追加 `RevertRequest` / `RevertResponse`）

**Interfaces:**
- Consumes `register_deliverable(..., actor_user_id=, reverted_from_version=, ledger_ref=, session=)`（Task 2）、`diff._prefix_for`（Task 2）、`RunDeliverables.actor_user_id/ledger_ref` 与 `ScriptShotOps.actor`（Task 1）
- Consumes `app.core.scope_guards.verify_shot_access(shot_id: str, auth)`（`scope_guards.py:355`）与 `verify_scene_access(scene_id: str, auth)`（`:325`）—— 分镜 PATCH（`script_shots_router.py:134`）与场次 ops（`script_scenes_router.py:236`）用的就是这两个
- Consumes `ScriptSceneRepository.apply_element_ops(scene_id, ops, expected_version, actor)`（`script_scene_repository.py:511`）与 `VersionConflict`；`version_service.inverse_between(ops_rows, from_seq, to_seq)`（`:76`）
- Produces `diff.rebuild_content(kind: str, ref_id: str, row: dict) -> tuple[Any | None, str | None]`
- Produces `revert.revert_output(*, kind, ref_id, to_version, expected_latest, auth) -> RevertResult(version: dict, kept_version: dict | None)`
- Produces `POST /api/v1/outputs/{kind}/{ref_id}/revert` → 201 `RevertResponse{version, kept_version}`

- [ ] **Step 1: 写服务层失败测试**

```python
# backend/tests/services/deliverables/test_revert.py
"""回退（spec §2.3）。四条纪律：只回可回的（媒体恒 v1、章节无账本 → 400，不假装
成功）；expected_latest 是乐观锁（409 并把 latest 交回去）；永不销毁内容（当前内容
≠ 最新登记版重建内容 → 先把当前登记成一版）；三步一个事务（内容 / 账本 / 登记一起
成立或一起不成立，登记在事务外会留下「内容回了、版本没记」）。"""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.services.deliverables import revert as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=ME)

class _FakeUow:
    async def __aenter__(self):
        return object()
    async def __aexit__(self, *exc):
        return False

def _chain(*versions):
    return [{"id": str(700000000000000 + v), "run_id": "913402881190401",
             "kind": "script_shot", "ref_id": "9", "version": v,
             "issue_id": "348087075560200", "ledger_ref": str(100 + v),
             "created_at": f"2026-09-1{v}T00:00:00+00:00",
             "title": f"S1 · Shot {v}"} for v in versions]

@pytest.fixture
def wired(monkeypatch):
    """把 revert 的六个外部面全换成桩：可见性、写权限、内容重建、读/写分镜、登记口、
    事务。断言的是「我们发出去的东西」，真执行留给 Task 8 的真栈验收。"""
    st = SimpleNamespace(
        chain=_chain(3, 2, 1),
        current={"shot_type": "WS", "description": "v3 text"},
        rebuilt={3: {"shot_type": "WS", "description": "v3 text"},
                 1: {"shot_type": "WS", "description": "v1 text"}},
        applied=[], registered=[])

    async def _chain_of(kind, ref_id, auth):
        return st.chain
    async def _ok(*a, **k):
        return None
    async def _rebuild(kind, ref_id, row):
        return st.rebuilt.get(row["version"]), None
    async def _read(ref_id, session):
        return st.current
    async def _write(ref_id, fields, *, actor, session, before):
        st.applied.append((fields, actor))
        return "9999"
    async def _register(**kw):
        st.registered.append(kw)
        return SimpleNamespace(
            id="7000", version=3 + len(st.registered), run_id=None,
            kind=kw["kind"], ref_id=kw["ref_id"], parent_version=None,
            title=kw.get("title"), actor_user_id=kw.get("actor_user_id"),
            reverted_from_version=kw.get("reverted_from_version"))
    async def _latest(*, kind, ref_id, session):
        return 3

    monkeypatch.setattr(mod, "visible_chain", _chain_of)
    monkeypatch.setattr(mod, "verify_shot_access", _ok)
    monkeypatch.setattr(mod, "rebuild_content", _rebuild)
    monkeypatch.setattr(mod, "_read_shot_fields", _read)
    monkeypatch.setattr(mod, "_write_shot_fields", _write)
    monkeypatch.setattr(mod, "register_deliverable", _register)
    monkeypatch.setattr(mod, "_latest_version", _latest)
    monkeypatch.setattr(mod, "unit_of_work", _FakeUow)
    return st

async def test_media_is_not_revertible(wired):
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(kind="generated_media", ref_id="5", to_version=1,
                                expected_latest=1, auth=AUTH)
    assert err.value.status_code == 400
    assert err.value.detail["code"] == "kind_not_revertible"

async def test_a_stale_expected_latest_is_a_conflict_carrying_the_truth(wired):
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(kind="script_shot", ref_id="9", to_version=1,
                                expected_latest=2, auth=AUTH)
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "version_conflict"
    assert err.value.detail["latest_version"] == 3

async def test_an_unknown_target_version_is_a_404(wired):
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(kind="script_shot", ref_id="9", to_version=9,
                                expected_latest=3, auth=AUTH)
    assert err.value.detail["code"] == "version_not_found"

async def test_a_clean_revert_writes_six_fields_and_registers_one_version(wired):
    out = await mod.revert_output(kind="script_shot", ref_id="9", to_version=1,
                                  expected_latest=3, auth=AUTH)
    assert wired.applied == [({"shot_type": "WS", "description": "v1 text"},
                              f"revert:{ME}")]
    assert out.kept_version is None and len(wired.registered) == 1
    reg = wired.registered[0]
    assert reg["run_id"] is None and reg["actor_user_id"] == ME
    assert reg["reverted_from_version"] == 1
    assert reg["ledger_ref"] == "9999"   # 刚写的那行账本，不是目标版的
    assert reg["session"] is not None    # 与内容同一个事务

async def test_unregistered_manual_edits_are_kept_as_their_own_version(wired):
    """当前内容 ≠ 最新登记版重建内容 → 先登记当前，再回退。回退前的人手状态永远可回。"""
    wired.current = {"shot_type": "WS", "description": "hand-typed"}
    out = await mod.revert_output(kind="script_shot", ref_id="9", to_version=1,
                                  expected_latest=3, auth=AUTH)
    assert out.kept_version is not None and len(wired.registered) == 2
    kept, reverted = wired.registered
    assert kept["reverted_from_version"] is None and kept["actor_user_id"] == ME
    assert reverted["reverted_from_version"] == 1
    # 保留版也要有自己的账本行，否则它以后重建不出来。
    assert wired.applied[0][1] == f"keep:{ME}"

async def test_content_that_cannot_be_rebuilt_refuses_instead_of_blanking(
        wired, monkeypatch):
    async def _unavailable(kind, ref_id, row):
        return None, "no_ledger"
    monkeypatch.setattr(mod, "rebuild_content", _unavailable)
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(kind="script_shot", ref_id="9", to_version=1,
                                expected_latest=3, auth=AUTH)
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "content_unavailable"
    assert err.value.detail["reason"] == "no_ledger"
    assert wired.applied == [] and wired.registered == []
```

- [ ] **Step 2: 跑，确认红**

```bash
cd backend && uv run pytest tests/services/deliverables/test_revert.py -v
```
预期：`ModuleNotFoundError: No module named 'app.services.deliverables.revert'`（6 errors）。

- [ ] **Step 3: 实现 `diff.rebuild_content`**

`diff.py` 追加（复用 Task 2 的 `_prefix_for`，并加进 `__all__`）：

```python
async def rebuild_content(kind: str, ref_id: str,
                          row: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
    """一版的**内容**（不是渲染后的文本）：shot 是六字段 dict，scene 是元素数组。

    回退写回的就是它，所以这里和 _shot_side / _scene_side 必须走同一条折叠路径——
    两套重建等于两种「v1 是什么」的说法。``(None, reason)`` 表示重建不出来，调用方
    据此 409 而不是把一个空内容写回去。"""
    try:
        if kind == "script_shot":
            prefix = _prefix_for(await _shot_ledger(ref_id), row)
            if not prefix:
                return None, NO_SNAPSHOT
            snapshot: Dict[str, Any] = {}
            for after in prefix:
                snapshot.update(after or {})
            return {f: snapshot.get(f) for f in _SHOT_FIELDS}, None
        if kind == "script_scene":
            ledger = await _scene_ledger(ref_id)
            prefix = _prefix_for(ledger, row)
            if not prefix:
                return None, NO_SNAPSHOT
            ref = row.get("ledger_ref")
            watermark = (int(str(ref)) if str(ref or "").isdigit()
                         else max(int(op["op_seq"]) for op in prefix))
            return replay_to(prefix, watermark), None
    except Exception as exc:  # noqa: BLE001 — 同 build_diff：说不出来也要说出来
        logger.opt(exception=True).error(
            f"[deliverables] rebuild {kind}/{ref_id} v{row.get('version')} failed: "
            f"{exc!r}")
        return None, NOT_FOUND
    return None, NO_LEDGER
```

- [ ] **Step 4: 实现 `revert.py`**

```python
"""Revert To vN（三期 3b spec §2.3）。

**回退不是回滚**：以目标版的内容新建 v(latest+1)，血缘记下 reverted_from_version 与
actor_user_id。历史只增不减，所以一次回退本身还能再被回退。

**永不销毁内容**：写回之前先比对当前内容与最新登记版重建出来的内容；不同就说明有
未登记的人手编辑（PATCH /shots/{id} 不写 ops 行），先把当前内容登记成一版并给它补
一行账本（否则那一版以后重建不出来），回退版再占下一个号。一次回退最多两行。

**三步一个事务**：改内容 / 写账本 / 登记版本同事务。登记在事务外意味着先提交内容再
登记，中间失败就留下「内容回了、版本没记」——而那正是血缘要回答的问题。登记口的
session= 参数（Task 2）就是为此存在。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import insert, select, update
from app.core.scope_guards import verify_scene_access, verify_shot_access
from app.db.session import unit_of_work
from app.models import ScriptOps, ScriptShotOps, ScriptShots
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.services.deliverables.diff import _SHOT_FIELDS, rebuild_content
from app.services.deliverables.lineage_view import version_of
from app.services.deliverables.registry import register_deliverable
from app.services.script.version_service import inverse_between

REVERTIBLE_KINDS = ("script_shot", "script_scene")

@dataclass(frozen=True)
class RevertResult:
    version: Dict[str, Any]
    kept_version: Optional[Dict[str, Any]]

def _reject(status_code: int, code: str, message: str, **extra: Any):
    """与 outputs_router._reject 同形：detail 必须是 dict，生产的 ErrorResponse 外壳
    只让 dict 活到 details（CLAUDE.md 2026-09-09）。"""
    return HTTPException(status_code=status_code,
                         detail={"code": code, "message": message, **extra})

async def visible_chain(kind: str, ref_id: str, auth) -> List[Dict[str, Any]]:
    """与血缘端点同一把可见性尺子。延迟 import 打断 router ↔ service 的环——
    registry.py:159 的 `from app.db.session import in_unit_of_work` 是同一手法。"""
    from app.api.outputs_router import visible_chain as _chain
    return await _chain(kind, ref_id, auth)

async def _latest_version(*, kind: str, ref_id: str, session) -> Optional[int]:
    return await get_run_deliverables_repository().latest_version(
        kind=kind, ref_id=ref_id, session=session)

async def revert_output(*, kind: str, ref_id: str, to_version: int,
                        expected_latest: int, auth) -> RevertResult:
    if kind not in REVERTIBLE_KINDS:
        raise _reject(status.HTTP_400_BAD_REQUEST, "kind_not_revertible",
                      f"{kind} has no version chain to revert to (media "
                      "re-generation creates a new object; chapters have no ledger)")
    rows = await visible_chain(kind, str(ref_id), auth)   # 404 在里面
    # 写权限走与分镜 PATCH / 场次 ops 完全相同的守卫：能看见 ≠ 能改。
    if kind == "script_shot":
        await verify_shot_access(str(ref_id), auth)
    else:
        await verify_scene_access(str(ref_id), auth)

    latest_row = rows[0]
    latest = int(latest_row["version"])
    by_version = {int(r["version"]): r for r in rows}
    if to_version not in by_version or to_version == latest:
        raise _reject(status.HTTP_404_NOT_FOUND, "version_not_found",
                      f"{kind}/{ref_id} has no earlier version {to_version}")
    if expected_latest != latest:
        raise _reject(status.HTTP_409_CONFLICT, "version_conflict",
                      "someone registered a newer version while you were looking",
                      latest_version=latest)

    target, reason = await rebuild_content(kind, str(ref_id), by_version[to_version])
    if target is None:
        raise _reject(status.HTTP_409_CONFLICT, "content_unavailable",
                      f"v{to_version} cannot be rebuilt from the ledger", reason=reason)
    head, head_reason = await rebuild_content(kind, str(ref_id), latest_row)
    if head is None:
        raise _reject(status.HTTP_409_CONFLICT, "content_unavailable",
                      f"v{latest} cannot be rebuilt, so 'keep current edits' cannot "
                      "be decided", reason=head_reason)

    uid = str(auth.user_id)
    async with unit_of_work() as session:
        fresh = await _latest_version(kind=kind, ref_id=str(ref_id), session=session)
        if int(fresh or 0) != latest:
            # 两人同时回退同一个对象：后者在事务里才看见对方，仍然 409。
            raise _reject(status.HTTP_409_CONFLICT, "version_conflict",
                          "someone registered a newer version while you were looking",
                          latest_version=int(fresh or 0))
        if kind == "script_shot":
            return await _revert_shot(ref_id=str(ref_id), target=target, head=head,
                                      latest_row=latest_row, to_version=to_version,
                                      uid=uid, session=session)
        return await _revert_scene(ref_id=str(ref_id), to_row=by_version[to_version],
                                   latest_row=latest_row, to_version=to_version,
                                   uid=uid, session=session)

async def _read_shot_fields(ref_id: str, session) -> Optional[Dict[str, Any]]:
    """锁住这一行再读六个字段——回退与另一次编辑之间没有别的互斥。"""
    row = (await session.execute(
        select(*[getattr(ScriptShots, f) for f in _SHOT_FIELDS])
        .where(ScriptShots.id == int(ref_id)).with_for_update())).first()
    return None if row is None else {f: getattr(row, f) for f in _SHOT_FIELDS}

async def _write_shot_fields(ref_id: str, fields: Dict[str, Any], *, actor: str,
                             session, before: Dict[str, Any]) -> str:
    """六字段 UPDATE + 一行账本，返回账本行 id（= 新版的 ledger_ref）。

    账本行 run_id 是 NULL、actor 记人 —— undo 服务按 run_id 读
    （run_undo_service.py:62），所以人手行天然不入 undo，不需要额外过滤。"""
    scene_id = (await session.execute(
        select(ScriptShots.scene_id).where(ScriptShots.id == int(ref_id))
    )).scalar_one()
    await session.execute(
        update(ScriptShots).where(ScriptShots.id == int(ref_id)).values(**fields))
    return str((await session.execute(
        insert(ScriptShotOps)
        .values(run_id=None, actor=actor, shot_id=int(ref_id), scene_id=scene_id,
                action="update", before_json=before, after_json=fields)
        .returning(ScriptShotOps.id))).scalar_one())

async def _revert_shot(*, ref_id, target, head, latest_row, to_version, uid,
                       session) -> RevertResult:
    current = await _read_shot_fields(ref_id, session)
    if current is None:
        raise _reject(status.HTTP_404_NOT_FOUND, "version_not_found",
                      f"script_shot/{ref_id} is gone")
    kept = None
    if current != head:
        kept_ref = await _write_shot_fields(ref_id, current, actor=f"keep:{uid}",
                                            session=session, before=head)
        kept = await _register(kind="script_shot", ref_id=ref_id,
                               title=latest_row.get("title"), uid=uid,
                               ledger_ref=kept_ref, reverted_from=None,
                               session=session)
    ledger_ref = await _write_shot_fields(ref_id, target, actor=f"revert:{uid}",
                                          session=session, before=current)
    version = await _register(kind="script_shot", ref_id=ref_id,
                              title=latest_row.get("title"), uid=uid,
                              ledger_ref=ledger_ref, reverted_from=to_version,
                              session=session)
    return RevertResult(version=version, kept_version=kept)

async def _revert_scene(*, ref_id, to_row, latest_row, to_version, uid,
                        session) -> RevertResult:
    """逆操作批，与 undo 服务同机制（run_undo_service.py:190）——不造 replace-all op。

    场次账本是完整的（每次元素编辑都写一行 script_ops），所以「未登记的人手编辑」在
    这条臂上不会发生，kept_version 恒为 None。"""
    from app.repositories.script_scene_repository import (
        VersionConflict, get_script_scene_repository)

    ledger = [{"op_seq": r[0], "op_json": r[1] or {}} for r in (await session.execute(
        select(ScriptOps.op_seq, ScriptOps.op_json)
        .where(ScriptOps.scene_id == int(ref_id))
        .order_by(ScriptOps.op_seq.asc()))).all()]
    current_seq = max((int(r["op_seq"]) for r in ledger), default=0)
    ref = to_row.get("ledger_ref")
    # 存量行没有 ledger_ref：退回「整本账本的末位」，逆操作批为空，回退退化成「把当前
    # 内容登记成新的一版」。宁可少改，也不要按猜出来的水位改写场次。
    from_seq = int(str(ref)) if str(ref or "").isdigit() else current_seq
    inverse = inverse_between(ledger, from_seq=from_seq, to_seq=current_seq)
    try:
        # 逆操作批为空（目标版内容 == 当前）也照走：用户要的是「以 vN 为准」，血缘要
        # 记下这一笔（spec §2.3 步 3b）。apply_ops([]) 只是把 content_version 推一格。
        await get_script_scene_repository().apply_element_ops(
            ref_id, inverse, expected_version=current_seq, actor=f"revert:{uid}")
    except VersionConflict as conflict:
        raise _reject(status.HTTP_409_CONFLICT, "version_conflict",
                      "the scene was edited while the revert was being prepared",
                      latest_version=conflict.current_version)
    return RevertResult(
        version=await _register(kind="script_scene", ref_id=ref_id,
                                title=latest_row.get("title"), uid=uid,
                                ledger_ref=str(current_seq + 1),
                                reverted_from=to_version, session=session),
        kept_version=None)

async def _register(*, kind, ref_id, title, uid, ledger_ref, reverted_from, session):
    row = await register_deliverable(
        run_id=None, kind=kind, ref_id=ref_id, title=title, actor_user_id=uid,
        reverted_from_version=reverted_from, ledger_ref=ledger_ref, session=session)
    if row is None:
        # best-effort 在这里是错的：内容已经改了，登记没成 = 血缘说不出这次回退。
        # raise 让整个事务回滚，内容一并退回（spec §2.3 步 4）。
        logger.error(f"[revert] {kind}/{ref_id}: registration returned nothing")
        raise _reject(status.HTTP_500_INTERNAL_SERVER_ERROR, "revert_failed",
                      "the content was not changed: the new version could not be "
                      "registered")
    return version_of({
        "id": row.id, "version": row.version, "parent_version": row.parent_version,
        "run_id": None, "issue_id": None, "issue_key": None, "seq": None,
        "turn": None, "step": None, "title": row.title, "model": None,
        "cost_cents": None, "created_at": None,
        "actor_user_id": row.actor_user_id,
        "reverted_from_version": row.reverted_from_version})

__all__ = ["REVERTIBLE_KINDS", "RevertResult", "revert_output", "visible_chain"]
```

```bash
cd backend && uv run pytest tests/services/deliverables/test_revert.py -v
```
预期：6 passed。

- [ ] **Step 5: 写路由失败测试**

```python
# backend/tests/api/test_outputs_revert_router.py
"""POST /api/v1/outputs/{kind}/{ref_id}/revert。

与 test_outputs_router.py 同一条纪律：app 装 register_exception_handlers，所以每个
拒绝都是生产的 ErrorResponse 外壳，类型化码在 details.code。裸 FastAPI 的
{"detail": …} 形状在生产从未出现过（CLAUDE.md 2026-09-09）。"""
from __future__ import annotations
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.core.exceptions import register_exception_handlers

mod = importlib.import_module("app.api.outputs_router")
pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"

def _client(monkeypatch, result=None, error=None):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mod.router, prefix="/api/v1")
    from app.core.deps import get_auth
    app.dependency_overrides[get_auth] = lambda: SimpleNamespace(user_id=ME)
    for dep in mod.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None
    monkeypatch.setattr(mod, "revert_output",
                        AsyncMock(side_effect=error) if error
                        else AsyncMock(return_value=result))
    return TestClient(app)

def _version(v, reverted_from=None):
    return {"id": str(700000000000000 + v), "version": v, "parent_version": v - 1,
            "run_id": None, "actor_user_id": ME,
            "reverted_from_version": reverted_from, "title": "S1 · Shot 3"}

def test_a_successful_revert_returns_201_with_the_new_version(monkeypatch):
    from app.services.deliverables.revert import RevertResult
    r = _client(monkeypatch,
                result=RevertResult(version=_version(4, 1), kept_version=None)
                ).post("/api/v1/outputs/script_shot/9/revert",
                       json={"to_version": 1, "expected_latest": 3})
    assert r.status_code == 201, r.text
    body = r.json()["version"]
    assert body["version"] == 4 and body["reverted_from_version"] == 1
    assert body["run_id"] is None and body["actor_user_id"] == ME
    assert r.json()["kept_version"] is None

def test_kept_edits_come_back_as_their_own_version(monkeypatch):
    from app.services.deliverables.revert import RevertResult
    body = _client(monkeypatch,
                   result=RevertResult(version=_version(5, 1),
                                       kept_version=_version(4))
                   ).post("/api/v1/outputs/script_shot/9/revert",
                          json={"to_version": 1, "expected_latest": 3}).json()
    assert body["kept_version"]["version"] == 4
    assert body["kept_version"]["reverted_from_version"] is None

@pytest.mark.parametrize("status_code,code", [
    (400, "kind_not_revertible"), (404, "version_not_found"),
    (409, "version_conflict"), (409, "content_unavailable")])
def test_every_refusal_carries_its_code_in_the_production_envelope(
        monkeypatch, status_code, code):
    r = _client(monkeypatch,
                error=HTTPException(status_code,
                                    detail={"code": code, "message": "x"})
                ).post("/api/v1/outputs/generated_media/5/revert",
                       json={"to_version": 1, "expected_latest": 1})
    assert r.status_code == status_code
    assert r.json()["details"]["code"] == code

def test_the_body_is_validated_before_anything_is_touched(monkeypatch):
    """to_version 缺失是 422，不是一次半成品的回退。"""
    r = _client(monkeypatch).post("/api/v1/outputs/script_shot/9/revert",
                                  json={"expected_latest": 3})
    assert r.status_code == 422
```

```bash
cd backend && uv run pytest tests/api/test_outputs_revert_router.py -v
```
预期：`AttributeError: module 'app.api.outputs_router' has no attribute 'revert_output'`（7 failures）。

- [ ] **Step 6: 实现 schema 与路由**

`app/schemas/outputs.py` 追加（并加进 `__all__`）：

```python
class RevertRequest(BaseModel):
    """expected_latest 是乐观锁，不是可选的礼貌：两人同时回退同一个对象时，没有它
    后者会静默覆盖前者刚登记的那一版。"""
    to_version: int = Field(ge=1)
    expected_latest: int = Field(ge=1)


class RevertResponse(BaseModel):
    """kept_version = 回退前把未登记的人手编辑登记成的那一版（spec §2.3）。多数回退
    是 None——只在当前内容与最新登记版不同时才出现。"""
    version: OutputVersion
    kept_version: Optional[OutputVersion] = None
```

`app/api/outputs_router.py`：`_visible_chain` 改名 `visible_chain`（122 / 155 两处调用同改，docstring 追加「3b 的回退端点也走它——同一个对象，同一把可见性尺子」），顶部加 `from app.schemas.outputs import RevertRequest, RevertResponse` 与 `from app.services.deliverables.revert import revert_output`，末尾追加：

```python
@router.post("/{kind}/{ref_id}/revert", response_model=RevertResponse,
             status_code=status.HTTP_201_CREATED)
async def revert_output_version(kind: str, ref_id: str, body: RevertRequest,
                                auth: AuthDep) -> RevertResponse:
    """把一个对象回到它的某一旧版（3b spec §2.3）。

    201 而不是 200：这次调用**新建**了一版（可能两版），没有任何东西被就地改写。
    可见性与写权限在服务层里，与分镜 PATCH / 场次 ops 用的是同一对守卫。"""
    result = await revert_output(kind=kind, ref_id=str(ref_id),
                                 to_version=body.to_version,
                                 expected_latest=body.expected_latest, auth=auth)
    return RevertResponse(
        version=OutputVersion.model_validate(result.version),
        kept_version=(OutputVersion.model_validate(result.kept_version)
                      if result.kept_version else None))
```

- [ ] **Step 7: 再跑，确认绿**

```bash
cd backend && uv run pytest tests/api/test_outputs_revert_router.py \
  tests/api/test_outputs_router.py tests/services/deliverables/ -v
```
预期：全绿（新增 7 + 既有 outputs / deliverables 套件）。

- [ ] **Step 8: 突变验证**

① `revert_output` 里 `if expected_latest != latest:` 改成 `if False:`，跑 `cd backend && uv run pytest tests/services/deliverables/test_revert.py -v`，确认 `test_a_stale_expected_latest_is_a_conflict_carrying_the_truth` 转红；恢复。
② `_revert_shot` 的 `if current != head:` 改成 `if False:`，确认 `test_unregistered_manual_edits_are_kept_as_their_own_version` 转红；恢复。
③ `_register` 里删掉 `session=session`，确认 `test_a_clean_revert_writes_six_fields_and_registers_one_version` 的 `reg["session"] is not None` 断言转红；恢复。三次都重跑确认 6 passed。

- [ ] **Step 9: 提交**

```bash
git add backend/app/services/deliverables/revert.py \
  backend/app/services/deliverables/diff.py backend/app/api/outputs_router.py \
  backend/app/schemas/outputs.py \
  backend/tests/services/deliverables/test_revert.py \
  backend/tests/api/test_outputs_revert_router.py && \
git commit -m "feat(outputs): Revert To vN —— 两臂回退 + 保留未登记人手编辑 + 同事务登记（harness 三期 3b Task 3）"
```

---

### Task 4: 花费 · 读时分摊 + 媒体每次调用价

文本类不回写（spec §3.1 改判）：血缘端点读时从 `step_end` 事件折出份额。媒体类在唯一咽喉点 `register_generated_media()` 填 `ai_model_prices.per_call_cents`（列由 mig 466 建，本 Task 只消费——依赖 Part A 的 T1 PR 先合）。

**Files:**
- Create `backend/app/services/deliverables/media_price.py`、`backend/app/services/deliverables/step_costs.py`
- Create `backend/tests/services/deliverables/test_media_price.py`、`backend/tests/services/deliverables/test_step_cost_alloc.py`
- Modify `backend/app/services/library/generated_media_service.py`（`register_generated_media` :288-393，插入点在 :350 之前；:367 与 :386-388）
- Modify `backend/app/services/deliverables/lineage_view.py`（末尾新增函数 + `__all__`；`_VERSION_KEYS` :45-59 不动）
- Modify `backend/app/schemas/outputs.py`（`OutputVersion` :40-59、`OutputLineageResponse` :77-81）
- Modify `backend/app/api/outputs_router.py`（`get_output_lineage` :113-138）
- Modify `backend/app/services/ai/model_pricing_coverage.py`（`PRICED_MODEL_TYPES` :42、`price_coverage_for` :45-65、`_priced_models_select_stmt` :68-88）
- Modify `admin/src/pages/ai/index.tsx`（`price_coverage` 注释 :42-50、`PriceCoverageTag` title :148-165）
- Modify `backend/tests/api/test_outputs_router.py`（`_row` :41-66）、`backend/tests/services/deliverables/test_wiring_generated_media.py`

**Interfaces:**
- Produces `media_price.media_price_cents(model: str, provider: str) -> float | None`
- Produces `step_costs.load_step_shares(run_ids: list[int]) -> dict[tuple[int, int, int], float]`
- Produces `lineage_view.allocate_step_costs(versions: list[dict], step_costs: dict[tuple[int, int, int], float]) -> list[dict]`
- Produces wire `OutputVersion.cost_kind: Literal["allocated","exact"] | None`、`OutputLineageResponse.as_of_seq: int`
- Consumes `AiModelPrices.per_call_cents`（mig 466 / Part A T1）、`AgentRunTranscriptEvents`（`models/agents.py:511-566`）、`register_deliverable_best_effort`（`registry.py:191-212`，签名不改）

- [ ] **Step 1: 失败测试 — 媒体价**（`backend/tests/services/deliverables/test_media_price.py`）
  ```python
  """媒体每次调用价：``ai_model_prices.per_call_cents`` 按 effective_at 取最新一行。"""

  from contextlib import asynccontextmanager

  import pytest

  pytestmark = pytest.mark.unit

  def _fake_read_scope(row):
      @asynccontextmanager
      async def _scope():
          class _R:
              def mappings(self):
                  return self

              def first(self):
                  return row

          class _Session:
              async def execute(self, stmt):
                  return _R()

          yield _Session()

      return _scope

  async def test_returns_the_newest_per_call_price(monkeypatch):
      import app.services.deliverables.media_price as mp

      monkeypatch.setattr(mp, "read_scope", _fake_read_scope({"per_call_cents": 12.0}))
      assert await mp.media_price_cents("doubao-seedream-4-0", "ark") == 12.0

  @pytest.mark.parametrize("row", [None, {"per_call_cents": None}])
  async def test_no_price_is_none_not_zero(monkeypatch, row):
      """没配价 ≠ 免费。None 让 UI 显 '—'，0.0 会谎称这次生成不要钱。
      第二种形状是 LLM 行：有每千 token 价，但没有每次调用价。"""
      import app.services.deliverables.media_price as mp

      monkeypatch.setattr(mp, "read_scope", _fake_read_scope(row))
      assert await mp.media_price_cents("m", "p") is None

  async def test_a_failed_read_is_none_and_never_raises(monkeypatch):
      """查价失败不许连坐一次已经生成并付过钱的图。"""
      import app.services.deliverables.media_price as mp

      @asynccontextmanager
      async def _boom():
          raise RuntimeError("db down")
          yield

      monkeypatch.setattr(mp, "read_scope", _boom)
      assert await mp.media_price_cents("m", "p") is None
  ```
- [ ] **Step 2: 跑，看红** — `cd backend && uv run pytest tests/services/deliverables/test_media_price.py -v`
  期望 `ModuleNotFoundError: No module named 'app.services.deliverables.media_price'`。
- [ ] **Step 3: 实现**（`backend/app/services/deliverables/media_price.py`）
  ```python
  """媒体每次调用价（spec §3.2）。

  没有任何图片/视频 provider 回花费，``mediahub_models.pricing_value`` 又是积分，
  所以唯一来源是管理员配的 ``ai_model_prices.per_call_cents``（mig 466）。查法同
  ``RunRecorder._snapshot_rates``（``run_recorder.py:629-657``），差别是 provider
  必填——媒体侧同一模型名可能挂两个 provider。**无价回 None，绝不回 0.0**。
  """

  from __future__ import annotations

  from typing import Optional

  from loguru import logger
  from sqlalchemy import select

  from app.db.session import read_scope

  def _price_stmt(model: str, provider: str):
      """列级 select（row-shape 纪律），不取整行。"""
      from app.models import AiModelPrices

      return (
          select(AiModelPrices.per_call_cents)
          .where(AiModelPrices.model == model)
          .where(AiModelPrices.provider == provider)
          .order_by(AiModelPrices.effective_at.desc())
          .limit(1)
      )

  async def media_price_cents(model: str, provider: str) -> Optional[float]:
      """``(model, provider)`` 的每次调用价（分），没配就是 None。

      永不抛：图已经生成并付过钱，查价失败不该把整次登记判成失败（与
      ``register_deliverable_best_effort`` 同一条纪律）。"""
      if not model or not provider:
          return None
      try:
          async with read_scope() as session:
              row = (
                  await session.execute(_price_stmt(model, provider))
              ).mappings().first()
      except Exception as exc:  # noqa: BLE001 — 见 docstring
          logger.warning(f"[media_price] lookup failed ({model}/{provider}): {exc!r}")
          return None
      value = row.get("per_call_cents") if row else None
      return float(value) if value is not None else None

  __all__ = ["media_price_cents"]
  ```
- [ ] **Step 4: 跑绿** — `cd backend && uv run pytest tests/services/deliverables/test_media_price.py -v` → 4 passed。
- [ ] **Step 5: 失败测试 — 咽喉点填价**（追加到 `tests/services/deliverables/test_wiring_generated_media.py`，复用其 `register_spy` / `insert_stub` fixture）
  ```python
  @pytest.fixture
  def price_stub(monkeypatch):
      import app.services.library.generated_media_service as gm

      asked: list[tuple] = []

      async def _price(model, provider):
          asked.append((model, provider))
          return 12.0

      monkeypatch.setattr(gm, "media_price_cents", _price)
      return asked

  async def test_media_cost_is_filled_from_the_catalog_price(
      register_spy, insert_stub, price_stub
  ):
      """登记行与 generated_media 行拿到同一个数（spec §5 验收⑤）。"""
      gm = insert_stub
      out = await gm.register_generated_media(
          user_id="u",
          scope_id=1,
          source_url="http://x/y.png",
          mime="image/png",
          origin=gm.GenerationOrigin(
              kind="agent_run",
              run_id="777",
              model="doubao-seedream-4-0",
              provider="ark",
              prompt="A cafe at dusk",
          ),
      )
      assert price_stub == [("doubao-seedream-4-0", "ark")]
      assert register_spy[0]["cost_cents"] == 12.0
      assert out["cost_cents"] == 12.0

  async def test_an_explicit_cost_is_never_overwritten(
      register_spy, insert_stub, price_stub
  ):
      """调用方已经知道花费时不查表——目录价是兜底，不是覆盖。"""
      gm = insert_stub
      await gm.register_generated_media(
          user_id="u",
          scope_id=1,
          source_url="http://x/y.png",
          mime="image/png",
          origin=gm.GenerationOrigin(
              kind="agent_run", run_id="777", model="m", provider="p", cost_cents=0.5
          ),
      )
      assert price_stub == [] and register_spy[0]["cost_cents"] == 0.5

  async def test_half_an_attribution_means_no_lookup(
      register_spy, insert_stub, price_stub
  ):
      """归因缺一半就查不出价（Task 0 的理由）——不瞎猜，留 None。"""
      gm = insert_stub
      await gm.register_generated_media(
          user_id="u",
          scope_id=1,
          source_url="http://x/y.png",
          mime="image/png",
          origin=gm.GenerationOrigin(kind="agent_run", run_id="777", model="m"),
      )
      assert price_stub == [] and register_spy[0]["cost_cents"] is None
  ```
- [ ] **Step 6: 跑，看红** — `cd backend && uv run pytest tests/services/deliverables/test_wiring_generated_media.py -v -k "price or cost or attribution"`
  期望 `AttributeError: <module 'app.services.library.generated_media_service'> does not have the attribute 'media_price_cents'`。
- [ ] **Step 7: 接线咽喉点**
  `generated_media_service.py` 在 :27 旁加 `from app.services.deliverables.media_price import media_price_cents`；
  在 `stmt = _generated_media_insert_stmt(`（:350）之前插：
  ```python
  # 媒体花费的唯一填写点（spec §3.2）。17 个调用点零改动——它们的区别只剩
  # origin 里有没有归因。已经带 cost_cents 的调用方（真花费已知）不查表。
  cost_cents = origin.cost_cents
  if cost_cents is None and origin.model and origin.provider:
      cost_cents = await media_price_cents(origin.model, origin.provider)
  ```
  `stmt` 的 `cost_cents=origin.cost_cents,`（:367）与登记调用的同名参数（:388）都改成
  `cost_cents=cost_cents,`；:386-387 那段「今天没有任何调用点填 origin.cost_cents」的
  注释换成「媒体类是登记时就精确的价（``cost_kind=exact``）；无价即 None，UI 显 '—'」。
  ⚠️ 不改 `origin`（不可变纪律），只用局部变量。
- [ ] **Step 8: 跑绿** — `cd backend && uv run pytest tests/services/deliverables -v` → 全绿（含 `test_choke_point_guard.py`）。
- [ ] **Step 9: 失败测试 — 分摊与装载**（`backend/tests/services/deliverables/test_step_cost_alloc.py`）
  ```python
  """文本类花费 = 对 step_end 事件的读时折叠（spec §3.1），不回写。"""

  from contextlib import asynccontextmanager

  import pytest

  from app.services.deliverables.lineage_view import allocate_step_costs

  pytestmark = pytest.mark.unit
  RUN = 913402881190401

  def _v(version, **over):
      row = {
          "id": str(700000000000000 + version),
          "version": version,
          "run_id": str(RUN),
          "turn": 1,
          "step": 3,
          "cost_cents": None,
      }
      row.update(over)
      return row

  def test_a_step_that_produced_two_deliverables_splits_in_half():
      """一步两镜 → 各一半（spec §5 验收⑥）。分母是那一步的 deliverable 事件
      数，不是本对象的版本数——两个分镜是两条链。"""
      out = allocate_step_costs([_v(1)], {(RUN, 1, 3): 0.09})  # 0.18 的一步，两件
      assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (0.09, "allocated")

  def test_no_step_end_yet_is_none_not_zero():
      """step_end 还没到（回合进行中 / 事件没落成）→ 不知道，不是免费。"""
      out = allocate_step_costs([_v(1)], {})
      assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (None, None)

  def test_a_registered_cost_is_exact_and_never_reallocated():
      out = allocate_step_costs([_v(1, cost_cents=12.0)], {(RUN, 1, 3): 0.09})
      assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (12.0, "exact")

  def test_a_version_without_run_coordinates_gets_nothing():
      """人手登记的版本没有 run/turn/step，没有可分摊的步。"""
      out = allocate_step_costs([_v(1, run_id=None, turn=None, step=None)], {})
      assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (None, None)

  def test_the_inputs_are_not_mutated():
      rows = [_v(1)]
      allocate_step_costs(rows, {(RUN, 1, 3): 0.09})
      assert rows[0]["cost_cents"] is None and "cost_kind" not in rows[0]

  def _fake_read_scope(rows):
      @asynccontextmanager
      async def _scope():
          class _R:
              def mappings(self):
                  return self

              def all(self):
                  return rows

          class _Session:
              async def execute(self, stmt):
                  return _R()

          yield _Session()

      return _scope

  def _ev(kind, **payload):
      return {"run_id": RUN, "event_type": kind, "turn": 1, "step": 3, "payload": payload}

  async def test_load_divides_the_step_cost_by_its_deliverable_count(monkeypatch):
      import app.services.deliverables.step_costs as sc

      rows = [
          _ev("step_end", cost_cents=0.18),
          _ev("deliverable", kind="script_shot", ref_id="9", version=1),
          _ev("deliverable", kind="script_shot", ref_id="10", version=1),
      ]
      monkeypatch.setattr(sc, "read_scope", _fake_read_scope(rows))
      assert await sc.load_step_shares([RUN]) == {(RUN, 1, 3): 0.09}

  async def test_a_step_with_no_deliverable_event_is_not_a_share(monkeypatch):
      """分母为 0 的步不进表——除零与「凭空多出一份」都是错的答案。"""
      import app.services.deliverables.step_costs as sc

      monkeypatch.setattr(sc, "read_scope", _fake_read_scope([_ev("step_end", cost_cents=0.18)]))
      assert await sc.load_step_shares([RUN]) == {}

  async def test_no_runs_means_no_query(monkeypatch):
      import app.services.deliverables.step_costs as sc

      @asynccontextmanager
      async def _boom():
          raise AssertionError("must not query")
          yield

      monkeypatch.setattr(sc, "read_scope", _boom)
      assert await sc.load_step_shares([]) == {}
  ```
- [ ] **Step 10: 跑，看红** — `cd backend && uv run pytest tests/services/deliverables/test_step_cost_alloc.py -v`
  期望 `ImportError: cannot import name 'allocate_step_costs' from 'app.services.deliverables.lineage_view'`。
- [ ] **Step 11: 实现装载与分摊**
  `backend/app/services/deliverables/step_costs.py`：
  ```python
  """每个 (run, turn, step) 的**每件产出**花费份额（spec §3.1）。

  分子分母同源、一次查询：``step_end.cost_cents`` 是那一步的 LLM 花费（出生点
  ``agent_runner._step_ended`` :1188-1200），同键下的 ``deliverable`` 事件条数是
  那一步产出了几件。花费是对事件流的折叠，不存第二份。

  ⚠️ ``turn`` 今天硬编码 1（``agent_runner.py:1137,1191``），键实际是 step-only；
  仍写三元组，将来 turn 真正推进时零改动。
  ⚠️ 分母是**事件**数不是 ``run_deliverables`` 行数：没落成事件的登记既不占分母
  也拿不到份额——好过一个查表一个查流然后对不上。
  """

  from __future__ import annotations

  from typing import Any, Dict, List, Optional, Tuple

  from loguru import logger
  from sqlalchemy import select

  from app.db.session import read_scope

  StepKey = Tuple[int, int, int]

  def _events_stmt(run_ids: List[int]):
      from app.models import AgentRunTranscriptEvents as TE

      return (
          select(TE.run_id, TE.event_type, TE.turn, TE.step, TE.payload)
          .where(TE.run_id.in_(run_ids))
          .where(TE.event_type.in_(["step_end", "deliverable"]))
      )

  def _key(row: Dict[str, Any]) -> Optional[StepKey]:
      turn, step = row.get("turn"), row.get("step")
      if turn is None or step is None:
          return None
      return (int(row["run_id"]), int(turn), int(step))

  async def load_step_shares(run_ids: List[int]) -> Dict[StepKey, float]:
      """每件产出应分摊到的分数。算不出的步不进表（缺席 = 不知道）。"""
      runs = sorted({int(r) for r in run_ids if r is not None})
      if not runs:
          return {}
      try:
          async with read_scope() as session:
              rows = (await session.execute(_events_stmt(runs))).mappings().all()
      except Exception as exc:  # noqa: BLE001 — 花费是装饰，不连坐血缘
          logger.warning(f"[step_costs] transcript read failed for {runs}: {exc!r}")
          return {}
      costs: Dict[StepKey, float] = {}
      counts: Dict[StepKey, int] = {}
      for row in rows:
          key = _key(row)
          if key is None:
              continue
          if row["event_type"] == "deliverable":
              counts[key] = counts.get(key, 0) + 1
              continue
          cents = (row.get("payload") or {}).get("cost_cents")
          if isinstance(cents, (int, float)) and not isinstance(cents, bool):
              costs[key] = float(cents)
      return {
          key: round(cost / counts[key], 4)
          for key, cost in costs.items()
          if counts.get(key)
      }

  __all__ = ["StepKey", "load_step_shares"]
  ```
  `lineage_view.py` 末尾新增（`_VERSION_KEYS` 不动——`cost_kind` 是派生的，不是列），
  并把 `__all__` 加上 `"allocate_step_costs"`：
  ```python
  def allocate_step_costs(
      versions: List[Dict[str, Any]], step_costs: Dict[tuple, float]
  ) -> List[Dict[str, Any]]:
      """给每个版本补上 ``cost_cents`` + ``cost_kind``（spec §3.1）。返回新 dict。

      ``step_costs`` 的值是**每件产出**的份额（``load_step_shares`` 已除过那一步
      的产出件数），键 ``(run_id, turn, step)``（turn 今天恒为 1）。三态：登记行
      自带 ``cost_cents``（媒体类，登记时就精确）→ 原值 ``exact``；命中份额 →
      ``allocated``（参考值，不是计费输入——预算钩子只吃 ``step_end`` 与媒体精确
      价）；都没有 → ``None`` + ``cost_kind=None``。**不是 0**：0 说「这一版不要
      钱」，None 说「不知道」。
      """
      out: List[Dict[str, Any]] = []
      for version in versions:
          if version.get("cost_cents") is not None:
              out.append({**version, "cost_kind": "exact"})
              continue
          run_id, turn, step = (
              version.get("run_id"),
              version.get("turn"),
              version.get("step"),
          )
          share = None
          if run_id is not None and turn is not None and step is not None:
              share = step_costs.get((int(run_id), int(turn), int(step)))
          out.append(
              {
                  **version,
                  "cost_cents": share,
                  "cost_kind": "allocated" if share is not None else None,
              }
          )
      return out
  ```
- [ ] **Step 12: 跑绿** — `cd backend && uv run pytest tests/services/deliverables/test_step_cost_alloc.py -v` → 8 passed。
- [ ] **Step 13: 失败测试 — 端点的 cost_kind 与 as_of_seq**
  `tests/api/test_outputs_router.py`：`_row`（:41-66）里 `"cost_cents": 1.25` 改成
  `"cost_cents": None`（文本类不回写，这才是真形状），并追加：
  ```python
  def test_lineage_allocates_the_step_cost_and_reports_the_kind(monkeypatch):
      client = _client(monkeypatch)
      monkeypatch.setattr(
          mod, "load_step_shares", AsyncMock(return_value={(int(RUN_ID), 2, 3): 0.09})
      )
      body = client.get("/api/v1/outputs/script_shot/9").json()
      assert [(v["cost_cents"], v["cost_kind"]) for v in body["versions"]] == [
          (0.09, "allocated")
      ] * 3

  def test_lineage_reports_the_chain_watermark(monkeypatch):
      """``as_of_seq`` = 最新登记行的 seq——前端按它丢过期的刷新信号。"""
      client = _client(monkeypatch)
      monkeypatch.setattr(mod, "load_step_shares", AsyncMock(return_value={}))
      assert client.get("/api/v1/outputs/script_shot/9").json()["as_of_seq"] == 3

  def test_a_version_with_no_seq_falls_back_to_its_row_id(monkeypatch):
      """人手登记的版本不落 transcript 事件、没有 seq——用行 id 当水位。"""
      client = _client(monkeypatch, rows=[_row(3, seq=None), _row(2), _row(1)])
      monkeypatch.setattr(mod, "load_step_shares", AsyncMock(return_value={}))
      body = client.get("/api/v1/outputs/script_shot/9").json()
      assert body["as_of_seq"] == 700000000000003
  ```
  （`_client(...)` 的返回值按该文件 :107 现有写法取用。）
- [ ] **Step 14: 跑，看红** — `cd backend && uv run pytest tests/api/test_outputs_router.py -v -k "allocates or watermark or falls_back"`
  期望 `AttributeError: <module 'app.api.outputs_router'> does not have the attribute 'load_step_shares'`。
- [ ] **Step 15: 实现 schema 与端点**
  `app/schemas/outputs.py`：顶部 import 加 `Literal`；`OutputVersion` 的 `cost_cents`（:58）之后加
  ```python
      # 这个花费怎么来的（3b §3.1）。``exact`` 是登记时就知道的真价（媒体类的
      # 目录每次调用价）；``allocated`` 是产出它那一步的 LLM 花费按该步产出件数
      # 均摊出来的参考值，UI 显示 ``≈¢0.09``，不是计费输入；``None`` 与
      # ``cost_cents=None`` 同时出现，意思是没有花费可报。
      cost_kind: Optional[Literal["allocated", "exact"]] = None
  ```
  `OutputLineageResponse`（:77-81）加
  ```python
      # 这条链的水位：最新登记行的 transcript seq。人手登记的版本不落事件、没有
      # seq，退回它的行 id——两者都单调，前端只拿它比大小丢过期信号（3b §4）。
      as_of_seq: int
  ```
  `app/api/outputs_router.py`：import 加
  `from app.services.deliverables.lineage_view import allocate_step_costs` 与
  `from app.services.deliverables.step_costs import load_step_shares`；
  `get_output_lineage`（:130-138）改成：
  ```python
      visible = await visible_issue_ids({row.get("issue_id") for row in rows}, auth)
      shares = await load_step_shares(
          [int(r["run_id"]) for r in rows if r.get("run_id") is not None]
      )
      versions = redact_foreign_issue_links(
          allocate_step_costs([version_of(row) for row in rows], shares),
          visible_issue_ids=visible,
      )
      newest = rows[0]
      return OutputLineageResponse(
          kind=kind,
          ref_id=str(ref_id),
          latest_version=versions[0]["version"],
          as_of_seq=int(newest.get("seq") or newest["id"]),
          versions=versions,
      )
  ```
- [ ] **Step 16: 跑绿** — `cd backend && uv run pytest tests/api/test_outputs_router.py tests/api/test_issue_outputs.py -v` → 全绿。
  （`/issues/{id}/outputs` 与 `/diff` 不分摊：前者是清单不是版本页，后者两侧花费仍取登记行——本 Task 不扩面。）
- [ ] **Step 17: 媒体模型的「没配价」在 admin 可见**
  这一页今天只**读** `mediahub_models` 并把 `price_coverage` 渲染成一个红 Tag
  （`admin/src/pages/ai/index.tsx:148-165`），**没有价格编辑器**——价格行由 migration 加
  （:160 的 title 原文就这么写）。所以「加一个字段」在这一页的最小形态是：让媒体模型
  也能被判成 `missing`，而不是永远 `not_applicable`。
  `model_pricing_coverage.py`：`PRICED_MODEL_TYPES`（:42）→ `frozenset({"llm", "image", "video"})`，
  注释改为「LLM 走 RunRecorder 的每千 token 价，image/video 走 ``per_call_cents``
  （3b §3.2）；tts/asr/embedding 仍由别的路径计费，留在外面」。
  `_priced_models_select_stmt`（:68-75）旁新增：
  ```python
  def _per_call_priced_models_select_stmt():
      from sqlalchemy import select

      from app.models import AiModelPrices

      return (
          select(AiModelPrices.model)
          .where(AiModelPrices.per_call_cents.isnot(None))
          .distinct()
      )
  ```
  `load_priced_models()` 返回 `Optional[dict[str, set[str]]]`（`{"token": …, "per_call": …}`，
  两条语句一次 session）；`price_coverage_for` 按 `row.get("type")` 选集合
  （`llm` → `token`，`image`/`video` → `per_call`），其余分支（`LOCAL_ENGINE_PROVIDERS`
  → `not_applicable`、`None` → `unknown`）不动。调用方（`app/api/` 里读 `price_coverage`
  的那一处）同批改。
  `admin/src/pages/ai/index.tsx`：`price_coverage` 注释块（:42-50）补一句
  「image / video 看的是 `ai_model_prices.per_call_cents`（mig 466）——没有它，
  生成的图在血缘与预算里都是 '—'」；`PriceCoverageTag` 的 `title`（:160）改成
  `"No ai_model_prices row for this model — LLM runs record cost as NULL; image/video generations have no per_call_cents, so their spend shows '—' and never counts toward a budget. Add a row via a supabase migration."`
- [ ] **Step 18: 跑绿** — `cd backend && uv run pytest tests/services/ai -k pricing -v` 与 `cd admin && npm run build` → 均通过。
- [ ] **Step 19: 记录一次突变** — 把 `load_step_shares` 的 `if counts.get(key)` 去掉、分母改成
  `counts.get(key, 1)`，跑 `cd backend && uv run pytest tests/services/deliverables/test_step_cost_alloc.py -v`，
  确认 `test_a_step_with_no_deliverable_event_is_not_a_share` 转红（多出一个凭空的 0.18 份额），
  抄进 PR 的「突变记录」后 `git checkout -- backend/app/services/deliverables/step_costs.py`。
- [ ] **Step 20: 提交**
  `git add backend/app/services/deliverables backend/app/services/library/generated_media_service.py backend/app/schemas/outputs.py backend/app/api/outputs_router.py backend/app/services/ai/model_pricing_coverage.py backend/tests/services/deliverables backend/tests/api/test_outputs_router.py admin/src/pages/ai/index.tsx && git commit -m "feat(outputs): 文本类花费读时按步分摊、媒体类登记时填目录每次调用价（3b Task 4）"`

---

---

### Task 4b: 媒体花费进 run 账 + WS/progress 的 seq 水位

媒体花费今天到不了 `view.cost`：`step_end` 折的是 LLM 的钱（`folds/step.py:15-40`），`recompute_spent`（`run_projection.py:92-105`）只算 `own + Σ by_child`，于是生图再贵预算钩子（`budget_hook.py:176-180` 读 `views.cost.spent_cents`）也看不见。`deliverable` 事件 payload 早就带 `cost_cents`（`registry.py:99-113`），Task 4 之后它对媒体类有真值——折一下即可。第二半给回合结束信号加水位：Part C 的 `notifyTurn` 按 `seq` 丢重复/乱序帧，两个信号源都得报得出 seq。

**Files:**
- Modify `backend/app/services/ai/runner/run_projection.py`（cost 视图 :66-82、`recompute_spent` :92-105）
- Modify `backend/app/services/ai/runner/folds/deliverables.py`（`fold_deliverable` :20-44）
- Modify `backend/app/repositories/agent_runs_repository.py`（`list_transcript_events` :752-784 之后新增）
- Modify `backend/app/services/issues/issue_chat_stream.py`（`publish_status` :35-36）
- Modify `backend/app/services/issues/issue_agent_executor.py`（:205 上方 + :288）
- Modify `backend/app/workflows/issue_lifecycle.py`（`_run_reply_turns` :534/:581、:626-648、:679-687）
- Modify `backend/app/services/issues/issue_rollup.py`（`compute_rollup` :70-78/:103-115、`load_rollup` :161-180）
- Modify `backend/tests/runner/test_fold_deliverables.py`、`backend/tests/runner/test_budget_hook.py`
- Create `backend/tests/services/issues/test_turn_done_seq.py`

**Interfaces:**
- Produces `view.cost.media_cents: float`
- Produces wire `status{type:"status", phase, run_id: str|null, seq: int|null, outputs: [{kind: str, ref_id: str}]}`（`outputs` = 该 run 在 `run_deliverables` 里登记过的 distinct `(kind, ref_id)`，无 run 时 `[]`；前端 done 时按键失效血缘缓存——WS 今天只中继 chunk/message/status，不另开 deliverable 帧）（Redis `issue:{id}` → `/ws/issue/{id}`，`ws_router.py:145-192` 原样转发）
- Produces wire `progress.current_run.last_seq: int | null`
- Produces `AgentRunsRepository.last_transcript_seq(run_id: int) -> int | None`
- Produces `RunDeliverablesRepository.output_keys_for_run(run_id: int) -> list[dict]`（`[{"kind": str, "ref_id": str}]`，distinct，按 `seq` 升序）
- Produces `issue_chat_stream.publish_status(issue_id: int, phase: str, *, run_id: Any = None) -> None`
- Consumes `deliverable` payload 的 `cost_cents`（`registry.py:99-113`，不改）；`compute_rollup(...)` 加一个 keyword-only 参数

- [ ] **Step 1: 失败测试 — 媒体花费折进 run 账**（追加到 `tests/runner/test_fold_deliverables.py`）
  ```python
  def test_media_cost_folds_into_the_run_total():
      """媒体类登记时就带精确价——必须进 spent_cents，否则生图不占预算。"""
      views = replay(
          [
              ("step_end", {"turn": 1, "step": 1, "cost_cents": 0.5, "model": "qwen-max"}),
              (
                  "deliverable",
                  {"kind": "generated_media", "ref_id": "1", "version": 1, "cost_cents": 12.0},
              ),
          ]
      )
      cost = views["cost"]
      assert (cost["own_cents"], cost["media_cents"], cost["spent_cents"]) == (
          0.5, 12.0, 12.5
      )

  def test_the_same_media_deliverable_twice_is_counted_once():
      """去重键与 ``outputs.seen`` 同一个——DBOS 重放不许把账翻倍。"""
      ev = (
          "deliverable",
          {"kind": "generated_media", "ref_id": "1", "version": 1, "cost_cents": 12.0},
      )
      assert replay([ev, ev])["cost"]["media_cents"] == 12.0

  def test_a_text_deliverable_without_a_cost_adds_nothing():
      """文本类登记行 cost_cents 是 NULL（读时才分摊）——不许当 0 也不许当钱。"""
      views = replay([("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 1})])
      assert (views["cost"]["media_cents"], views["cost"]["spent_cents"]) == (0.0, 0.0)
  ```
- [ ] **Step 2: 跑，看红** — `cd backend && uv run pytest tests/runner/test_fold_deliverables.py -v -k "media or text_deliverable"`
  期望 `KeyError: 'media_cents'`。
- [ ] **Step 3: 实现 media 折叠**
  `run_projection.py` 的 cost 视图（:66-82）在 `by_child` 之后加：
  ```python
              # 3b §3.3：媒体产出的精确价（登记时就知道）。与 own/by_child 并列的
              # 第三个分量，不混进 own_cents——那是 LLM 每步的钱，来源不同。
              "media_cents": 0.0,
  ```
  `recompute_spent`（:92-105）首行 docstring 改成
  ``spent_cents = own_cents + Σ by_child + media_cents``（并说明三个分量各有自己的
  折叠 `step_end` / `subagent_done` / `deliverable`，三者都调这里，总额永远等于三部分），
  函数体：
  ```python
      children = sum(float(v or 0) for v in (cost.get("by_child") or {}).values())
      media = float(cost.get("media_cents") or 0.0)
      cost["spent_cents"] = round(
          float(cost.get("own_cents") or 0.0) + children + media, 4
      )
  ```
  `folds/deliverables.py`：import 改成
  `from app.services.ai.runner.run_projection import recompute_spent, register`；
  在 `views["view"]["outputs"] = outputs` 之前插（复用上面刚做完的去重判断）：
  ```python
      cents = payload.get("cost_cents")
      if isinstance(cents, (int, float)) and not isinstance(cents, bool):
          cost = views["cost"]
          cost["media_cents"] = round(
              float(cost.get("media_cents") or 0.0) + float(cents), 4
          )
          recompute_spent(cost)
  ```
  模块 docstring 补一段：「3b §3.3：媒体类登记行带精确价，同一个 ``seen`` 去重键
  同时护住计数和账——重复到达既不多计一件，也不多计一次钱。」
- [ ] **Step 4: 跑绿** — `cd backend && uv run pytest tests/runner/test_fold_deliverables.py tests/runner/test_outputs_refold_from_transcript.py -v` → 全绿。
- [ ] **Step 5: 失败测试 — 预算钩子看得见媒体花费**（追加到 `tests/runner/test_budget_hook.py`）
  ```python
  @pytest.mark.asyncio
  async def test_a_media_deliverable_pushes_the_gate_over_budget():
      """生图的钱必须进 spent_cents，否则预算拦不住一条只生图的 run。"""
      views = rp.replay(
          [
              ("step_end", {"turn": 1, "step": 1, "cost_cents": 10.0, "model": "qwen-max"}),
              (
                  "deliverable",
                  {"kind": "generated_media", "ref_id": "1", "version": 1, "cost_cents": 95.0},
              ),
          ]
      )
      assert views["cost"]["spent_cents"] == 105.0

      rec = _Rec()
      rec.views = views
      rec.views["view"]["question"] = None
      ctx = StepContext(turn=1, step=2, recorder=rec)
      assert await _hook(budget=100).before_llm_call(ctx) is StepDecision.STOP
      assert [e[0] for e in rec.events] == ["budget_check", "question_asked"]
      assert rec.events[0][1]["action"] == "halt"
  ```
- [ ] **Step 6: 跑** — `cd backend && uv run pytest tests/runner/test_budget_hook.py -v -k media`
  Step 3 之前红在 `assert 10.0 == 105.0`；Step 3 之后直接绿。这条是**证据**：fold 的改动
  真的穿过了预算钩子这个消费方，而不是只改了视图字典。
- [ ] **Step 7: 失败测试 — done 帧与 progress 的 seq**（`backend/tests/services/issues/test_turn_done_seq.py`）
  ```python
  """回合结束信号的水位（3b §4）。

  两个源：WS ``status{phase:'done'}`` 与 ``useIssueProgress`` 的 ``current_run``。
  前端按 seq 丢重复与乱序帧，所以两边都要报得出「这条 run 的 transcript 走到哪
  了」。报不出时是 ``None``——「不知道」不能伪装成 0，0 会把最新的一帧当最旧的丢掉。
  """

  import pytest

  from app.services.issues.issue_rollup import compute_rollup

  pytestmark = pytest.mark.unit
  ISSUE = {"id": 1, "status": "in_progress", "budget_cents": None}
  RUNS = [{"id": 777, "status": "running", "started_at": None, "model": "m"}]

  def _sink(published):
      async def _pub(_issue_id, payload):
          published.append(payload)

      return _pub

  async def test_done_frame_carries_the_run_and_its_last_seq(monkeypatch):
      import app.services.issues.issue_chat_stream as st

      published: list[dict] = []

      async def _last_seq(run_id):
          assert run_id == 777
          return 42

      async def _keys(run_id):
          assert run_id == 777
          return [{"kind": "script_shot", "ref_id": "9"}]

      monkeypatch.setattr(st, "_publish", _sink(published))
      monkeypatch.setattr(st, "_last_transcript_seq", _last_seq)
      monkeypatch.setattr(st, "_run_output_keys", _keys)

      await st.publish_status(1, "done", run_id="777")

      assert published == [
          {
              "type": "status",
              "phase": "done",
              "run_id": "777",
              "seq": 42,
              "outputs": [{"kind": "script_shot", "ref_id": "9"}],
          }
      ]

  async def test_a_frame_without_a_run_still_goes_out(monkeypatch):
      """两个键恒定存在、未知时为 null——有时缺席的字段会被读成 seq 0。"""
      import app.services.issues.issue_chat_stream as st

      published: list[dict] = []
      monkeypatch.setattr(st, "_publish", _sink(published))

      await st.publish_status(1, "running")

      assert published == [
          {"type": "status", "phase": "running", "run_id": None, "seq": None, "outputs": []}
      ]

  def test_current_run_reports_its_last_seq():
      out = compute_rollup(ISSUE, RUNS, [], 0, {}, last_seq=42)
      assert out["current_run"]["last_seq"] == 42

  def test_current_run_last_seq_is_none_when_unknown():
      out = compute_rollup(ISSUE, RUNS, [], 0, {})
      assert out["current_run"]["last_seq"] is None
  ```
- [ ] **Step 8: 跑，看红** — `cd backend && uv run pytest tests/services/issues/test_turn_done_seq.py -v`
  期望 `TypeError: publish_status() got an unexpected keyword argument 'run_id'` 与
  `TypeError: compute_rollup() got an unexpected keyword argument 'last_seq'`。
- [ ] **Step 9: 实现水位读取 + 两个生产者**
  `agent_runs_repository.py`（`list_transcript_events` :752-784 之后，`func` 若未 import 则补）：
  ```python
      async def last_transcript_seq(self, run_id: int) -> Optional[int]:
          """这条 run 的 transcript 水位（``MAX(seq)``），没有事件就是 None。

          前端按它丢重复与乱序的回合结束信号（3b §4）。「没有事件」与「seq 0」
          是两件事，所以空表回 None。"""
          from app.models import AgentRunTranscriptEvents as TE

          async with read_scope() as session:
              return (
                  await session.execute(
                      select(func.max(TE.seq)).where(TE.run_id == int(run_id))
                  )
              ).scalar_one_or_none()
  ```
  `run_deliverables_repository.py`（`lineage_for` 之后；`select`/`distinct` 若未 import 则补）：
  ```python
      async def output_keys_for_run(self, run_id: int) -> list[dict]:
          """这条 run 登记过的 distinct (kind, ref_id)，按首次登记 seq 升序。
          WS done 帧用它让前端按键失效血缘缓存；没登记过就是 []。"""
          from app.models import RunDeliverables as RD

          async with read_scope() as session:
              rows = (
                  await session.execute(
                      select(RD.kind, RD.ref_id, func.min(RD.seq))
                      .where(RD.run_id == int(run_id))
                      .group_by(RD.kind, RD.ref_id)
                      .order_by(func.min(RD.seq))
                  )
              ).all()
          return [{"kind": kind, "ref_id": str(ref_id)} for kind, ref_id, _ in rows]
  ```
  `issue_chat_stream.py`：把 :35-36 换成
  ```python
  async def _last_transcript_seq(run_id: Any) -> Optional[int]:
      """best-effort：水位读不到就是 None，绝不让一次遥测失败拖垮回合。"""
      try:
          from app.repositories.agent_runs_repository import get_agent_runs_repository

          return await get_agent_runs_repository().last_transcript_seq(int(run_id))
      except Exception as e:  # noqa: BLE001
          logger.warning(f"[issue_chat_stream] last seq read failed (run={run_id}): {e}")
          return None

  async def _run_output_keys(run_id: Any) -> list[dict]:
      """这条 run 登记过的产出坐标——前端 done 时按 (kind, ref_id) 精确失效血缘缓存。
      读失败回 []（只是少一次精确失效，TTL 兜底），绝不让状态帧发不出去。"""
      try:
          from app.repositories.run_deliverables_repository import (
              get_run_deliverables_repository,
          )

          return await get_run_deliverables_repository().output_keys_for_run(int(run_id))
      except Exception as e:  # noqa: BLE001
          logger.warning(f"[issue_chat_stream] output keys read failed (run={run_id}): {e}")
          return []

  async def publish_status(issue_id: int, phase: str, *, run_id: Any = None) -> None:
      """回合状态帧。``done`` 帧带 run、它的 transcript 水位与登记过的产出坐标——前端
      按 seq 丢重复帧（轮询边沿与它说的是同一件事）与乱序帧，按 outputs 精确失效
      血缘缓存（3b §4）。三个键**恒定存在**、未知时为 null / []：有时缺席的字段会被
      读成「seq 0」，那会把最新一帧当最旧的丢掉。"""
      seq = await _last_transcript_seq(run_id) if run_id is not None else None
      outputs = await _run_output_keys(run_id) if run_id is not None else []
      await _publish(
          issue_id,
          {
              "type": "status",
              "phase": phase,
              "run_id": str(run_id) if run_id is not None else None,
              "seq": seq,
              "outputs": outputs,
          },
      )
  ```
  三个 `done` 生产者：
  - `issue_agent_executor.py`：`publish_status(iid, "running")`（:205）上方加
    `result: dict[str, Any] | None = None`；:288 改成
    `await publish_status(iid, "done", run_id=(result or {}).get("run_id"))`
    （异常路径下 result 仍是 None → 帧带 null）。
  - `issue_lifecycle.py::_run_reply_turns`：循环外初始化 `last_run_id = None`，
    :534 的 `result = await run_turn(...)` 之后加
    `last_run_id = (result or {}).get("run_id") or last_run_id`；返回值（:581）改成
    `{"issue_id": issue_id, "executed": True, "run_id": last_run_id}`。
  - `issue_lifecycle.py:626-648`：
    ```python
            turns_out: dict[str, Any] | None = None
            try:
                turns_out = await _run_reply_turns(...)
                return turns_out
            finally:
                await publish_status(
                    issue_id, "done", run_id=(turns_out or {}).get("run_id")
                )
    ```
  - `issue_lifecycle.py:679-687`（`run_issue_reply_for_wait`）同形，run_id 取
    `run_issue_reply_step` 返回 dict 的 `run_id`。
  `issue_rollup.py`：`compute_rollup` 签名（:70-78）在 `now` 之后加
  `last_seq: Optional[int] = None`；`current_run` 字典（:104-114）加
  `"last_seq": last_seq,`（注释：该 run 的 transcript 水位，轮询边沿用它去重，
  3b §4；读不到就是 None）。`load_rollup`（:161-180）末尾改成：
  ```python
      current = next((r for r in runs if r.get("status") == "running"), None)
      last_seq = (
          await get_agent_runs_repository().last_transcript_seq(int(current["id"]))
          if current
          else None
      )
      return compute_rollup(issue, runs, children, pending, origin, last_seq=last_seq)
  ```
- [ ] **Step 10: 跑绿** — `cd backend && uv run pytest tests/services/issues/test_turn_done_seq.py -v` → 4 passed。
- [ ] **Step 11: 回归** — `cd backend && uv run pytest tests/runner tests/services/issues tests/workflows -q` → 全绿。
  （现有测试若断言 `_run_reply_turns` 的返回 dict 相等，按新增的 `run_id` 键更新——那是新事实，不是测试写错。）
- [ ] **Step 12: 记录一次突变** — 把 `recompute_spent` 里的 `+ media` 去掉，跑
  `cd backend && uv run pytest tests/runner/test_fold_deliverables.py tests/runner/test_budget_hook.py -v -k media`，
  确认两条转红（`assert (0.5, 12.0, 0.5) == (0.5, 12.0, 12.5)` 与 `assert CONTINUE is STOP`），
  抄进 PR 的「突变记录」后 `git checkout -- backend/app/services/ai/runner/run_projection.py`。
- [ ] **Step 13: 提交**
  `git add backend/app/services/ai/runner/run_projection.py backend/app/services/ai/runner/folds/deliverables.py backend/app/services/issues/issue_chat_stream.py backend/app/services/issues/issue_agent_executor.py backend/app/services/issues/issue_rollup.py backend/app/workflows/issue_lifecycle.py backend/app/repositories/agent_runs_repository.py backend/tests/runner backend/tests/services/issues/test_turn_done_seq.py && git commit -m "feat(runner): 媒体花费折进 run 账并计入预算，回合结束信号带 seq 水位（3b Task 4b）"`

> 前端 `RunCost.media_cents`、`BudgetBlock` 的 `Media ¢x.xx` 行、`issueTurnSignal` 的水位去重、`≈¢0.09` / `¢12.00` / `—` 三态显示都是 Part C 的活。本段只产出 wire 字段：`view.cost.media_cents`、`OutputVersion.cost_kind`、`OutputLineageResponse.as_of_seq`、`status{run_id, seq}`、`current_run.last_seq`。

---

### Task 5: 前端——回退 UI + 花费显示（3b §3.4 / §5 稿一稿二）

**Files:**
- Modify: `frontend/services/outputsService.ts`（`OutputVersion` :37-63、`OutputLineage` :64-69、`OutputsError` :127-137、`reject` :139-159、尾部 :258-260 追加 `post`/`revertOutput`）
- Modify: `frontend/services/outputsService.test.ts`
- Create: `frontend/components/agentActivity/outputCost.ts` + `outputCost.test.ts`
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/foldEvents.ts`（`OutputCard` :75-86、`deliverable` :612-644、`stampResultSummaries(nodes)` :708 旁加第二遍）+ `foldEvents.test.ts`
- Modify: `frontend/components/Todolist/OutputDiffDialog.tsx`（`errorText` :61-72 下方、`SideHead` :99-105、组件体 :137-151、底栏 :329-368）+ `OutputDiffDialog.test.tsx`
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx`（`OutputCards` 元信息行 :430-436）+ `nodes/outputCards.test.tsx`
- Modify: `frontend/components/Todolist/blocks/OutputsBlock.tsx`（行尾 :105-107）
- Modify: `frontend/components/agentActivity/OutputProvenance.tsx`（元信息行 :149-159）
- Modify: `frontend/components/TaskCenter/runView.ts`（`RunCost` :114-120）
- Modify: `frontend/components/Todolist/blocks/BudgetBlock.tsx`（:73-85 之间插入 Media 行）+ 新建 `BudgetBlock.test.tsx`
- Modify: `frontend/public/locales/en.json` / `zh.json`（`outputs.*`，en :5872-5890 附近）

**Interfaces:**
- Consumes（T3/T4）：`POST /api/v1/outputs/{kind}/{ref_id}/revert`，body `{to_version:int, expected_latest:int}`，201 `{version: OutputVersion, kept_version: OutputVersion|null}`；错误体是 `ErrorResponse` 外壳，类型码在 `details.code`（同 `apiClient.ts:195-204`）。`OutputVersion` 新字段 `actor_user_id/reverted_from_version/cost_kind`、`run_id` 转 nullable；`OutputLineageResponse.as_of_seq`。
- Produces：
  - `outputsService.revertOutput(kind: string, refId: string, opts: { toVersion: number; expectedLatest: number }): Promise<RevertResult>`，`RevertResult = { version: OutputVersion; kept_version: OutputVersion | null }`
  - `OutputsError.details: Record<string, unknown> | null`
  - `outputCost.ts`：`type CostKind = 'allocated' | 'exact' | null`；`formatOutputCost(cents: number|null, kind: CostKind): string`；`outputCostTitle(cents: number|null, kind: CostKind, o: { deliverableKind: string; model: string|null }): string | undefined`
  - `OutputCard.costKind: CostKind`；`RunCost.media_cents: number`

- [ ] **Step 1: service 回退（红）**

```ts
// 追加进 frontend/services/outputsService.test.ts 的 describe（:15 的 import 加 revertOutput）。
// 形状照 backend/app/schemas/outputs.py:40-59 —— 每个 id 都是 string。
const reverted = { ...version, id: '347786145852739099', version: 5, parent_version: 4,
  run_id: null, actor_user_id: '6f1c1b64-2b3f-4a5e-9a10-1f2c3d4e5f60',
  reverted_from_version: 1, cost_kind: null, cost_cents: null };
it('reverts to a version and returns both rows', async () => {
  fetchMock.mockResolvedValueOnce(json(201, { version: reverted, kept_version: null }));
  const out = await revertOutput('script_shot', '9', { toVersion: 1, expectedLatest: 4 });
  expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/outputs/script_shot/9/revert');
  expect(fetchMock.mock.calls[0][1].method).toBe('POST');
  expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ to_version: 1, expected_latest: 4 });
  expect(out.version.version).toBe(5);
  expect(out.version.run_id).toBeNull();               // 人手版没有 run
  expect(out.version.reverted_from_version).toBe(1);
  expect(out.kept_version).toBeNull();
});
it('a conflict arrives as a typed code WITH its payload', async () => {
  // latest_version 在 details 里，文案要拿它拼 —— 所以 details 必须穿过来，
  // 只留 code 就只能说「冲突了」，读者无从知道是谁写的。
  fetchMock.mockResolvedValueOnce(json(409, { success: false, error: 'Conflict', code: 'http_409',
    request_id: 'r1', details: { code: 'version_conflict', latest_version: 4 } }));
  await expect(revertOutput('script_shot', '9', { toVersion: 1, expectedLatest: 3 }))
    .rejects.toMatchObject({ code: 'version_conflict', status: 409, details: { latest_version: 4 } });
});
it('keeps the kept version when the backend registered one', async () => {
  fetchMock.mockResolvedValueOnce(json(201, { version: reverted,
    kept_version: { ...reverted, id: '347786145852739098', version: 4, parent_version: 3, reverted_from_version: null } }));
  expect((await revertOutput('script_shot', '9', { toVersion: 1, expectedLatest: 3 })).kept_version?.version).toBe(4);
});
```
```bash
cd frontend && npx vitest run services/outputsService.test.ts   # 红：revertOutput is not a function ×3
```

- [ ] **Step 2: 实现 service + 类型（绿）**

`outputsService.ts`：`OutputVersion`（:37-63）改 `run_id: string | null` 并新增

```ts
  /** 谁写的，当它不是 run。人手登记（回退）才有值 —— run_id 与它至少有一个非空（T1 CHECK）。 */
  actor_user_id: string | null;
  /** 这一版回退自哪一版；非回退为 null。 */
  reverted_from_version: number | null;
  /** 花费怎么来的：allocated = 从 step 花费均摊（文本类，参考值），exact = 登记时的目录价。 */
  cost_kind: 'allocated' | 'exact' | null;
```
`OutputLineage`（:64-69）加 `as_of_seq: number`；`OutputsError`（:127-137）加
`readonly details: Record<string, unknown> | null`（构造参数第四个，默认 null），`reject`（:139-159）
在 detail 是对象时把整包传进去。尾部追加：

```ts
export interface RevertResult {
  version: OutputVersion;
  /** 回退前把未登记的人手编辑登记成的那一版；没有就是 null。
   *  弹层调用前无从得知它会不会出现，所以确认文案不提它，成功后才说。 */
  kept_version: OutputVersion | null;
}
async function post<T>(path: string, body: unknown): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}${path}`, { method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) return reject(res);
  return (await res.json()) as T;
}
/** 把对象回退到 `toVersion`，写出新的一版。`expectedLatest` 是乐观锁：与服务端当前
 *  最新版不等就 409 `version_conflict`（detail 带 `latest_version`），绝不覆盖别人刚登记的版本。 */
export async function revertOutput(kind: string, refId: string,
  opts: { toVersion: number; expectedLatest: number }): Promise<RevertResult> {
  return post<RevertResult>(`/api/v1/outputs/${encodeURIComponent(kind)}/${ref(refId)}/revert`,
    { to_version: opts.toVersion, expected_latest: opts.expectedLatest });
}
```
```bash
cd frontend && npx vitest run services/outputsService.test.ts   # 绿
```

- [ ] **Step 3: 花费格式化器（红）**

```ts
// frontend/components/agentActivity/outputCost.test.ts
import { describe, expect, it } from 'vitest';
import { formatOutputCost, outputCostTitle } from './outputCost';
describe('outputCost', () => {
  it('marks an allocated cost as approximate', () => {
    expect(formatOutputCost(0.09, 'allocated')).toBe('≈¢0.09');
    expect(outputCostTitle(0.09, 'allocated', { deliverableKind: 'script_shot', model: 'x' }))
      .toBe('Allocated from step cost');
  });
  it('an exact price carries no ≈ and no title', () => {
    expect(formatOutputCost(12, 'exact')).toBe('¢12.00');
    expect(outputCostTitle(12, 'exact', { deliverableKind: 'generated_media', model: 'gpt-6-astra' })).toBeUndefined();
  });
  it('no price reads as unknown, and says why only for media', () => {
    // 0 不是「免费」：到过模型的调用在价表配齐时永远不是 0
    // （builtins.tsx:40-47 的 fmtChildCents 写下的同一条规则）。
    expect(formatOutputCost(null, null)).toBe('—');
    expect(formatOutputCost(0, 'exact')).toBe('—');
    expect(outputCostTitle(null, null, { deliverableKind: 'generated_media', model: 'gpt-6-astra' }))
      .toBe('No price configured for gpt-6-astra');
    expect(outputCostTitle(null, null, { deliverableKind: 'script_shot', model: 'qwen-max' })).toBeUndefined();
  });
  it('keeps sub-cent costs readable', () => expect(formatOutputCost(0.004, 'allocated')).toBe('≈¢0.004'));
});
```
```bash
cd frontend && npx vitest run components/agentActivity/outputCost.test.ts   # 红：模块不存在
```

- [ ] **Step 4: 实现 outputCost.ts（绿）**

```ts
/**
 * 一个产出版本的花费，四个面共用一份写法（线程卡 / 右栏产出行 / 差异弹层两侧头 / 来源块）。
 * 分开写四遍的结果必然是四种精度和四种「没有价」的说法。
 *
 * `allocated` 带 `≈`：文本类花费是读时从 `step_end` 按该 (turn, step) 下的登记行数
 * 均摊出来的（3b §3.1），是参考值不是计费输入 —— 显示得跟目录价一样精确，就是在
 * 邀请别人拿它对账。
 */
export type CostKind = 'allocated' | 'exact' | null;
const cents = (c: number): string => `¢${c < 0.01 ? c.toFixed(3) : c.toFixed(2)}`;
export function formatOutputCost(c: number | null, kind: CostKind): string {
  if (c === null || c === 0) return '—';
  return kind === 'allocated' ? `≈${cents(c)}` : cents(c);
}
export function outputCostTitle(c: number | null, kind: CostKind,
  o: { deliverableKind: string; model: string | null }): string | undefined {
  if (kind === 'allocated') return 'Allocated from step cost';
  // 只有媒体类能断言「该有价却没有」：它的价来自 ai_model_prices 的目录行，缺行就是缺配置。
  // 文本类没值只说明这一步的 step_end 还没到。
  if ((c === null || c === 0) && o.deliverableKind === 'generated_media') {
    return `No price configured for ${o.model ?? 'this model'}`;
  }
  return undefined;
}
```
```bash
cd frontend && npx vitest run components/agentActivity/outputCost.test.ts   # 绿
```

- [ ] **Step 5: foldEvents 均摊（红）**

```ts
// 追加进 foldEvents.test.ts（ev(...) 用该文件顶部既有的事件工厂，不新造）
it('splits a step cost across the deliverables that step registered', () => {
  const nodes = foldEvents([
    ev('step_start', 1, { turn: 1, step: 2, model: 'doubao-seed-2-0-lite' }),
    ev('deliverable', 2, { kind: 'script_shot', ref_id: '9', version: 1 }, 1, 2),
    ev('deliverable', 3, { kind: 'script_shot', ref_id: '10', version: 1 }, 1, 2),
    ev('step_end', 4, { turn: 1, step: 2, cost_cents: 0.18 }),
  ]);
  const step = nodes.find((n) => n.kind === 'step') as StepNode;
  expect(step.outputs.map((o) => o.costCents)).toEqual([0.09, 0.09]);
  expect(step.outputs.map((o) => o.costKind)).toEqual(['allocated', 'allocated']);
});
it('a deliverable that lands after step_end still gets its share', () => {
  // DBOS 登记会晚于本步结束到达（foldEvents.ts:369-372 的既有事实）。均摊写进
  // step_end 的分支就只覆盖先到的卡，而份额取决于卡的总数 —— 顺序依赖即错误依赖。
  const nodes = foldEvents([
    ev('step_start', 1, { turn: 1, step: 2 }),
    ev('step_end', 2, { turn: 1, step: 2, cost_cents: 0.18 }),
    ev('deliverable', 3, { kind: 'script_shot', ref_id: '9', version: 1 }, 1, 2),
  ]);
  expect((nodes.find((n) => n.kind === 'step') as StepNode).outputs[0].costCents).toBe(0.18);
});
it('an exact媒体价 is never overwritten by the allocation', () => {
  const nodes = foldEvents([
    ev('step_start', 1, { turn: 1, step: 3 }),
    ev('deliverable', 2, { kind: 'generated_media', ref_id: '77', version: 1, cost_cents: 12 }, 1, 3),
    ev('step_end', 3, { turn: 1, step: 3, cost_cents: 0.2 }),
  ]);
  const o = (nodes.find((n) => n.kind === 'step') as StepNode).outputs[0];
  expect([o.costCents, o.costKind]).toEqual([12, 'exact']);
});
```
```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/foldEvents.test.ts   # 红
```

- [ ] **Step 6: 实现均摊第二遍（绿）**

`foldEvents.ts`：`OutputCard`（:75-86）加 `costKind: CostKind`（`import type { CostKind } from '../outputCost'`）；
`deliverable` 分支（:640-643）push 时 `costKind: num(p.cost_cents) === null ? null : 'exact'`；
`stampResultSummaries(nodes)`（:708）后加 `allocateStepCosts(nodes);`，并在其下方新增：

```ts
/**
 * 把每一步的 step 花费均摊到这一步登记的产出卡上（3b §3.1，与后端
 * `lineage_view.allocate_step_costs` 同一公式 `stepCost / cards.length`）。
 *
 * 第二遍而不是 `step_end` 的分支：登记会晚于本步结束到达（见 `stepAt` 的注释），
 * 在分支里算就只覆盖先到的那几张卡。只填没有自己价的卡 —— 媒体类登记时就带目录价，
 * 拿参考值盖掉精确价是把账做坏。
 */
function allocateStepCosts(nodes: TrajectoryNode[]): void {
  for (const n of nodes) {
    if (n.kind !== 'step' || n.summary.costCents === null) continue;
    const share = n.outputs.filter((o) => o.costCents === null);
    if (share.length === 0) continue;
    const each = n.summary.costCents / share.length;
    for (const o of share) { o.costCents = each; o.costKind = 'allocated'; }
  }
}
```
```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/foldEvents.test.ts   # 绿
```

- [ ] **Step 7: 弹层回退（红）**

```tsx
// OutputDiffDialog.test.tsx：v() 工厂（:38-43）补 actor_user_id: null、
// reverted_from_version: null、cost_kind: 'allocated'；lineage 补 as_of_seq: 2。
const revertOutput = vi.fn();     // 加进既有的 vi.mock('../../services/outputsService') 工厂
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useOptionalToast: () => ({ addToast }) }));
const openTextDiff = () => { /* getOutputLineage→lineage, getOutputDiff→text diff, render */ };
it('offers Revert only for a script kind on a non-latest version', async () => {
  openTextDiff();                                   // from=1, to=2, latest=2
  await waitFor(() => expect(screen.getByTestId('output-diff-revert')).not.toBeDisabled());
});
it('never offers Revert for media', async () => { /* kind=generated_media → toBeDisabled() */ });
it('never offers Revert on the latest version', async () => { /* from=2=latest → toBeDisabled() */ });
it('confirms before reverting and says what it will create', async () => {
  openTextDiff();
  fireEvent.click(await screen.findByTestId('output-diff-revert'));
  // 调用前无从知道有没有未登记编辑，所以确认句里不提 kept。
  expect(screen.getByTestId('output-revert-confirm').textContent).toContain('Revert to v1? This creates v3.');
  expect(revertOutput).not.toHaveBeenCalled();
});
it('reverts, shows the new version and says the edits were kept', async () => {
  openTextDiff();
  revertOutput.mockResolvedValue({
    version: { ...v(4, 3), reverted_from_version: 1, run_id: null, actor_user_id: '6f1c1b64-2b3f-4a5e-9a10-1f2c3d4e5f60' },
    kept_version: { ...v(3, 2), run_id: null, actor_user_id: '6f1c1b64-2b3f-4a5e-9a10-1f2c3d4e5f60' } });
  fireEvent.click(await screen.findByTestId('output-diff-revert'));
  fireEvent.click(screen.getByTestId('output-revert-go'));
  await waitFor(() => expect(revertOutput).toHaveBeenCalledWith('script_shot', '9', { toVersion: 1, expectedLatest: 2 }));
  expect(addToast).toHaveBeenCalledWith('Reverted to v1 as v4', 'success');
  await waitFor(() => expect(screen.getByTestId('output-reverted-chip').textContent).toContain('v4 ↩ v1'));
  expect(screen.getByTestId('output-revert-kept').textContent)
    .toContain('Your edits before the revert were kept as v3.');
});
it('a conflict names the version that appeared', async () => {
  openTextDiff();
  revertOutput.mockRejectedValue(new OutputsError('version_conflict', 409, 'c', { latest_version: 4 }));
  fireEvent.click(await screen.findByTestId('output-diff-revert'));
  fireEvent.click(screen.getByTestId('output-revert-go'));
  await waitFor(() => expect(addToast).toHaveBeenCalledWith(
    'Someone registered v4 meanwhile — reopen to see it', 'error'));
});
it('an unrebuildable version says so', async () => {
  // revertOutput 抛 OutputsError('content_unavailable', 409, '', { reason: 'no_ledger' })
  await waitFor(() => expect(addToast).toHaveBeenCalledWith("v1 can't be rebuilt (no ledger)", 'error'));
});
it('shows each side cost with the ≈ its kind earns', async () => {
  openTextDiff();   // v() 的 cost_kind='allocated'、cost_cents=0.42
  await waitFor(() => expect(screen.getByTestId('output-diff-cost-to').textContent).toBe('≈¢0.42'));
});
```
```bash
cd frontend && npx vitest run components/Todolist/OutputDiffDialog.test.tsx   # 红
```

- [ ] **Step 8: 实现弹层（绿）**

`OutputDiffDialog.tsx` 新 import：`revertOutput`、`invalidateOutputLineage`、`type OutputVersion`（outputsService）、
`useOptionalToast`（`../Toast`，弹层也从画布挂载，那里没有 ToastProvider）、
`formatOutputCost`/`type CostKind`（`../agentActivity/outputCost`）、`RotateCcw`。模块级：

```ts
// 只有文本类有账本 / 逆操作批可以重放；媒体回退与媒体版本链是另立的一期（3b §6）。
const REVERTIBLE = new Set(['script_shot', 'script_scene']);
/** 回退失败的话术：每个码说清「发生了什么」和「接下来做什么」。 */
function revertErrorText(err: unknown, from: number | null): string {
  if (!(err instanceof OutputsError)) return 'Revert failed.';
  const d = err.details ?? {};
  switch (err.code) {
    case 'version_conflict': return `Someone registered v${d.latest_version ?? '?'} meanwhile — reopen to see it`;
    case 'content_unavailable': return `v${from ?? '?'} can't be rebuilt (no ledger)`;
    case 'kind_not_revertible': return "This kind can't be reverted";
    case 'version_not_found': return 'That version is not in this object’s chain.';
    default: return `Revert failed (${err.code})`;
  }
}
```
组件体（:137-151 之后）：

```tsx
const toast = useOptionalToast();
const [confirming, setConfirming] = useState(false);
const [reverting, setReverting] = useState(false);
const [kept, setKept] = useState<OutputVersion | null>(null);
const latestVersion = versions.length ? Math.max(...versions.map((x) => x.version)) : 0;
const canRevert = REVERTIBLE.has(kind) && from !== null && from < latestVersion && !reverting;
const row = (n: number | null) => (n === null ? null : versions.find((x) => x.version === n) ?? null);
const costOf = (n: number) => formatOutputCost(row(n)?.cost_cents ?? null, (row(n)?.cost_kind ?? null) as CostKind);
const doRevert = async () => {
  if (from === null) return;
  setReverting(true);
  try {
    const res = await revertOutput(kind, refId, { toVersion: from, expectedLatest: latestVersion });
    // 用响应里的新版就地更新，不等重拉（3b §4）；同时让缓存的链作废，
    // 别处（来源块、右栏）下一次读才拿到真答案。
    setVersions((prev) => [res.version, ...(res.kept_version ? [res.kept_version] : []), ...prev]);
    setKept(res.kept_version);
    setPinnedFrom(null);
    setTo(res.version.version);
    setConfirming(false);
    invalidateOutputLineage(kind, refId);
    toast?.addToast(`Reverted to v${from} as v${res.version.version}`, 'success');
  } catch (err) {
    console.error('[OutputDiffDialog] revert failed', err);
    toast?.addToast(revertErrorText(err, from), 'error');
    setConfirming(false);
  } finally {
    setReverting(false);
  }
};
```
`SideHead`（:99-105）加 props `cost: string; costTitle?: string; testId: string`，在 `side.model` 后渲染
`<span data-testid={testId} title={costTitle}>{cost}</span>`（两侧 `output-diff-cost-from` / `-to`）。
底栏 :359-367 的 disabled 占位整段替换：

```tsx
{/* 回退版的身份：v4 ↩ v1 · Reverted · You。info 色（不是 ok/warn）——
    回退是一次导航，既不是成功也不是告警。 */}
{row(to)?.reverted_from_version != null && (
  <span data-testid="output-reverted-chip"
        className="rounded border border-info-line bg-info-soft px-1.5 py-0.5 text-[11px] text-info tabular-nums">
    {t('outputs.revertedChip', 'v{{n}} ↩ v{{from}}', { n: to, from: row(to)?.reverted_from_version })}
    <span className="ml-1.5">{t('outputs.revertedBy', 'Reverted · You')}</span>
  </span>
)}
{confirming ? (
  <span data-testid="output-revert-confirm" className="ml-auto flex items-center gap-2 text-[12px] text-ink-300">
    {t('outputs.revertConfirm', 'Revert to v{{from}}? This creates v{{next}}.', { from, next: latestVersion + 1 })}
    <button type="button" data-testid="output-revert-go" disabled={reverting} onClick={() => void doRevert()}
            className="rounded border border-info-line px-2 py-1 text-info disabled:opacity-50">
      {t('outputs.revertGo', 'Revert')}
    </button>
    <button type="button" data-testid="output-revert-cancel" onClick={() => setConfirming(false)}
            className="rounded border border-ink-700 px-2 py-1 text-ink-400">{t('common.cancel', 'Cancel')}</button>
  </span>
) : (
  <button type="button" data-testid="output-diff-revert" disabled={!canRevert}
          title={canRevert ? undefined : t('outputs.revertHint', 'Only an older script version can be reverted')}
          onClick={() => setConfirming(true)}
          className="ml-auto inline-flex items-center gap-1 rounded border border-ink-700 px-3 py-1.5 text-[13px] text-ink-300 hover:border-info-line hover:text-info disabled:cursor-not-allowed disabled:opacity-50">
    <RotateCcw size={12} />{t('outputs.revert', 'Revert To v{{n}}', { n: from ?? 1 })}
  </button>
)}
{kept && (
  <p data-testid="output-revert-kept" className="mt-2 w-full text-[11px] text-info">
    {t('outputs.revertKept', 'Your edits before the revert were kept as v{{n}}.', { n: kept.version })}
  </p>
)}
```
```bash
cd frontend && npx vitest run components/Todolist/OutputDiffDialog.test.tsx   # 绿
```

- [ ] **Step 9: 花费上另外三个面 + Budget 的 Media 行（红）**

```tsx
// outputCards.test.tsx
it('shows the model, the allocated cost and the step', async () => {
  // outputs[0] = { costCents: 0.09, costKind: 'allocated', model: 'doubao-seed-2-0-lite' }，node.step = 2
  const c = screen.getByTestId('output-card-cost');
  expect(c.textContent).toBe('≈¢0.09');
  expect(c.getAttribute('title')).toBe('Allocated from step cost');
  expect(screen.getByTestId('output-card-step').textContent).toBe('step 2');
});
it('an unpriced media card says why', async () => {
  const c = screen.getByTestId('output-card-cost');
  expect([c.textContent, c.getAttribute('title')]).toEqual(['—', 'No price configured for gpt-6-astra']);
});
// OutputsBlock.test.tsx
it('shows each object’s latest cost', async () => {
  expect((await screen.findByTestId('outputs-row-cost')).textContent).toBe('≈¢0.42');
});
// BudgetBlock.test.tsx（新建，ctx 字面量照 OutputsBlock.test.tsx 的写法）
it('adds a Media row only when media spend exists', () => {
  const ctx = { rollup: { budget: { budget_cents: null, spent_cents: 30, pct: null, state: 'ok' },
    current_run: { cost: { spent_cents: 30, media_cents: 24 } } }, issue: { raw: {} }, env: {} };
  render(<BudgetBlockView ctx={ctx as unknown as IssueBlockContext} />);
  expect(screen.getByTestId('budget-media').textContent).toContain('Media ¢24.00');
});
it('draws no Media row at zero', () => { /* media_cents: 0 → queryByTestId('budget-media') 为 null */ });
```
```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/nodes/outputCards.test.tsx \
  components/Todolist/blocks/OutputsBlock.test.tsx components/Todolist/blocks/BudgetBlock.test.tsx   # 红
```

- [ ] **Step 10: 实现四个面（绿）**

- `builtins.tsx:434-435`：把 `{o.model && …}` 与 `fmtChildCents(o.costCents)` 换成三段
  `model · <cost> · step {node.step}`，testId 分别 `output-card-model|cost|step`；cost 用
  `formatOutputCost(o.costCents, o.costKind)` + `title={outputCostTitle(o.costCents, o.costKind, { deliverableKind: o.kind, model: o.model })}`。
- `OutputsBlock.tsx:105-107`：版本 chip 前插 `outputs-row-cost` 一段，取 `item.versions[0]` 的
  `cost_cents` / `cost_kind`，同一对函数。
- `OutputProvenance.tsx:149-159`：元信息行在 `latest.model` 后补同一对（testId `output-provenance-cost`）。
- `runView.ts:114-120`：`RunCost` 加 `media_cents: number`（后端 T4 fold 写入；旧行没有这个键，
  读处一律 `?? 0` —— 缺字段读成 0 而不是 NaN）。
- `BudgetBlock.tsx`：`const media = selectRunCost(rollup?.current_run ? { cost: rollup.current_run.cost } : null)?.media_cents ?? 0;`，
  `media > 0` 时在规则行（:85）上方插
  `<div data-testid="budget-media" className="text-[11px] text-ink-500">{t('issueDetail.mediaSpend', 'Media {{c}}', { c: formatOutputCost(media, 'exact') })}</div>`。

```bash
cd frontend && npx vitest run components/agentActivity components/Todolist/blocks   # 绿
```

- [ ] **Step 11: i18n + tsc**

`en.json`/`zh.json` 的 `outputs` 段新增 `revertConfirm`/`revertGo`/`revertKept`/`revertedChip`/`revertedBy`，
改写 `revertHint`（原值 "Arrives with 3b" 已失效）；新增 `issueDetail.mediaSpend`。

```bash
cd frontend && npx vitest run && npx tsc --noEmit -p .    # 触碰文件零新增错误
```

- [ ] **Step 12: 突变记录**

| 突变 | 应转红 |
|---|---|
| `allocateStepCosts` 去掉 `o.costCents === null` 过滤 | Step 5 第三例 |
| `formatOutputCost` 对 `allocated` 不加 `≈` | Step 3 第一例 |
| `revertErrorText` 的 `version_conflict` 分支改读 `err.message` | Step 7 冲突用例 |
| `canRevert` 去掉 `from < latestVersion` | Step 7「latest 上禁用」用例 |

- [ ] **Step 13: Commit**

```bash
git commit -m "feat(ui): 产出回退弹层 + 花费四面显示 + Budget 媒体行（harness 三期 3b Task 5）"
```

---

---

### Task 6: 前端——实时刷新（回合信号 + 按键失效 + 三个消费方）

**Files:**
- Create: `frontend/components/Todolist/issueTurnSignal.ts` + `issueTurnSignal.test.ts`
- Modify: `frontend/services/outputsService.ts`（`lineageCache` :197-199、`invalidateOutputLineage` :202-216、**删** `clearOutputLineageCache` :218-221、`getOutputLineage` :223-256）+ `outputsService.test.ts`（C10）
- Modify: `frontend/services/issueChatSocket.ts`（`IssueChatEvent` :22-25）
- Modify: `frontend/services/issuesService.ts`（`IssueProgress.current_run` :410-417）
- Modify: `frontend/components/Todolist/useIssueProgress.ts`（`refresh` :35-49）+ 新建 `useIssueProgress.test.ts`
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（WS 分发 :381-407）+ `IssueDetailView.test.tsx`
- Modify: `frontend/components/Todolist/blocks/OutputsBlock.tsx`（effect :52-68）+ 测试
- Modify: `frontend/components/chat/useMentionOutputsTab.ts`（`requested` :84-115）+ `useMentionOutputsTab.test.ts`
- Modify: `frontend/components/agentActivity/OutputProvenance.tsx`（effect :78-100）+ 测试
- Modify: `frontend/components/Todolist/OutputDiffDialog.tsx`（lineage effect :168-188、Task 5 的 `doRevert`）

**Interfaces:**
- Consumes：WS `status{phase:'done', seq:int}`（T6 后端加 `seq`）；done 帧的 `outputs: [{kind, ref_id}]`（Task 4b 产出；WS 今天只中继 chunk/message/status，不另开 deliverable 帧）；`GET /issues/{id}/progress` 的 `current_run.last_seq: number|null`；血缘响应 `as_of_seq`。
- Produces：
  - `issueTurnSignal.ts`：`type TurnSignal = { runId: string | null; seq: number }`；`notifyTurn(issueId: string, s: TurnSignal): void`；`subscribeTurn(issueId: string, cb: (s: TurnSignal) => void): () => void`；`useTurnSignal(issueId: string): TurnSignal | null`；`__resetTurnSignals(): void`（测试专用）
  - `outputsService`：`invalidateOutputLineage(kind?: string, refId?: string): void`；`subscribeLineageChange(cb: () => void): () => void`；`lineageGeneration(): number`；`LINEAGE_TTL_MS = 60_000`；**删除** `clearOutputLineageCache`

- [ ] **Step 1: 信号 store（红）**

```ts
// frontend/components/Todolist/issueTurnSignal.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { notifyTurn, subscribeTurn, __resetTurnSignals } from './issueTurnSignal';
beforeEach(() => __resetTurnSignals());
const RUN = '727145299382534100';
describe('issueTurnSignal', () => {
  it('fires once for a seq, and drops the replay of the same seq', () => {
    // 轮询边沿与 WS done 描述的是同一个回合结束，先后到达两次。
    const cb = vi.fn(); subscribeTurn('5', cb);
    notifyTurn('5', { runId: RUN, seq: 5 });
    notifyTurn('5', { runId: RUN, seq: 5 });
    expect(cb).toHaveBeenCalledTimes(1);
  });
  it('drops a stale frame that arrives out of order', () => {
    const cb = vi.fn(); subscribeTurn('5', cb);
    notifyTurn('5', { runId: RUN, seq: 5 });
    notifyTurn('5', { runId: RUN, seq: 4 });
    expect(cb).toHaveBeenCalledTimes(1);
  });
  it('keeps two issues independent', () => {
    const a = vi.fn(); const b = vi.fn();
    subscribeTurn('5', a); subscribeTurn('6', b);
    notifyTurn('5', { runId: 'r1', seq: 9 });
    notifyTurn('6', { runId: 'r2', seq: 1 });   // 比 issue 5 的水位低，但不是同一条链
    expect([a.mock.calls.length, b.mock.calls.length]).toEqual([1, 1]);
  });
  it('a local action (no run) never raises a run lane watermark', () => {
    // 回退用响应新版的 id 当 seq —— 那是 Snowflake，比任何 transcript seq 大十个
    // 数量级。一条全 issue 共用的水位会被它顶到天上，之后该 issue 的真实回合信号
    // 全被当成陈旧帧丢掉，实时就此静默死亡。
    const cb = vi.fn(); subscribeTurn('5', cb);
    notifyTurn('5', { runId: null, seq: 347786145852739099 });
    notifyTurn('5', { runId: RUN, seq: 3 });
    expect(cb).toHaveBeenCalledTimes(2);
  });
  it('one bad subscriber never starves the next', () => {
    const bad = vi.fn(() => { throw new Error('boom'); }); const good = vi.fn();
    subscribeTurn('5', bad); subscribeTurn('5', good);
    notifyTurn('5', { runId: 'r1', seq: 1 });
    expect(good).toHaveBeenCalledTimes(1);
  });
});
```
```bash
cd frontend && npx vitest run components/Todolist/issueTurnSignal.test.ts   # 红：模块不存在
```

- [ ] **Step 2: 实现 store（绿）**

```ts
// frontend/components/Todolist/issueTurnSignal.ts
/**
 * 「这个议题的一个回合结束了」——跨 React 树的一次广播（3b §4）。
 *
 * 照 `outputHighlight.ts` 的微 store：右栏产出块、@ 页签、画布来源块分属三棵树，
 * 上方没有共同 provider，而 `useSyncExternalStore` 让订阅在 React 里是正确的。
 *
 * ⚠️ **水位按 (issueId, runId) 记，不是按 issueId。** transcript 的 `seq` 本来就是
 * 每个 run 自己的计数器，全 issue 共用一条水位会把「新 run 的 seq 3」判成「旧 run 的
 * seq 40」的陈旧帧；回退成功时传的又是 Snowflake 量级的 id（`runId: null`），同一条
 * 水位必然被它顶死。分车道之后，轮询边沿与 WS done（同一个 runId）照样互相去重 ——
 * 那才是这条规则真正要吃掉的重复。
 */
import { useCallback, useSyncExternalStore } from 'react';
export type TurnSignal = { runId: string | null; seq: number };
const listeners = new Map<string, Set<(s: TurnSignal) => void>>();
const lastSeq = new Map<string, number>();
const latest = new Map<string, TurnSignal>();
const lane = (issueId: string, runId: string | null): string => `${issueId}|${runId ?? '@local'}`;
export function notifyTurn(issueId: string, signal: TurnSignal): void {
  const key = lane(issueId, signal.runId);
  const seen = lastSeq.get(key);
  if (seen !== undefined && signal.seq <= seen) return;
  lastSeq.set(key, signal.seq);
  latest.set(issueId, signal);
  for (const fn of listeners.get(issueId) ?? []) {
    try { fn(signal); } catch (err) {
      // 一个坏订阅者永远不该饿死排在它后面的（CLAUDE.md 分发器要容纳回调异常）。
      console.error('[issueTurnSignal] listener failed', err);
    }
  }
}
export function subscribeTurn(issueId: string, cb: (s: TurnSignal) => void): () => void {
  const set = listeners.get(issueId) ?? new Set();
  set.add(cb); listeners.set(issueId, set);
  return () => { set.delete(cb); };
}
/** 最后一个信号。快照返回 map 里的同一个引用 —— 每次新建对象会让
 *  `useSyncExternalStore` 判定「又变了」并无限重渲染。 */
export function useTurnSignal(issueId: string): TurnSignal | null {
  const subscribe = useCallback((fn: () => void) => subscribeTurn(issueId, () => fn()), [issueId]);
  return useSyncExternalStore(subscribe, () => latest.get(issueId) ?? null, () => null);
}
/** 测试专用：模块级状态活得比一个用例长。 */
export function __resetTurnSignals(): void { listeners.clear(); lastSeq.clear(); latest.clear(); }
```
```bash
cd frontend && npx vitest run components/Todolist/issueTurnSignal.test.ts   # 绿
```

- [ ] **Step 3: 缓存按键失效 + TTL + generation（红，含 C10）**

```ts
// outputsService.test.ts：beforeEach 的 clearOutputLineageCache() → invalidateOutputLineage()
// （无参=整表），:15 的 import 去掉 clearOutputLineageCache，加 subscribeLineageChange /
// LINEAGE_TTL_MS。所有 lineage 响应体补 as_of_seq。
it('invalidates ONE object and leaves its neighbour cached', async () => {
  fetchMock.mockResolvedValue(json(200, { kind: 'script_shot', ref_id: '9', latest_version: 2,
    as_of_seq: 12, versions: [version] }));
  await getOutputLineage('script_shot', '9');
  await getOutputLineage('script_shot', '10');
  invalidateOutputLineage('script_shot', '9');
  await getOutputLineage('script_shot', '9');
  await getOutputLineage('script_shot', '10');
  expect(fetchMock).toHaveBeenCalledTimes(3);            // 9 两次、10 一次
});
it('bumps a generation every consumer can subscribe to', async () => {
  const seen = vi.fn(); const off = subscribeLineageChange(seen);
  invalidateOutputLineage('script_shot', '9');
  expect(seen).toHaveBeenCalledTimes(1); off();
});
it('re-reads an entry older than the TTL when the tab comes back', async () => {
  vi.useFakeTimers();
  fetchMock.mockResolvedValue(json(200, { kind: 'script_shot', ref_id: '9', latest_version: 1,
    as_of_seq: 3, versions: [version] }));
  await getOutputLineage('script_shot', '9');
  vi.advanceTimersByTime(LINEAGE_TTL_MS + 1);
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
  await getOutputLineage('script_shot', '9');
  expect(fetchMock).toHaveBeenCalledTimes(2);
  vi.useRealTimers();
});
it('a fresh entry survives the same visibilitychange', async () => { /* 不 advance → 仍 1 次 */ });
```
```bash
cd frontend && npx vitest run services/outputsService.test.ts   # 红
```

- [ ] **Step 4: 实现缓存（绿）**

```ts
// outputsService.ts —— 条目从裸 Promise 变成带时间戳的记录
/** 没有 WS 的页面（画布节点、资源面板）靠它兜底：60 秒内回到这个标签页仍用缓存，
 *  超了就在下一次读时重来。议题页不靠 TTL —— 它有精确事件。 */
export const LINEAGE_TTL_MS = 60_000;
interface LineageEntry {
  data: Promise<OutputLineage>;
  fetchedAt: number;
  /** 拿到答案时那条链的最新 seq（`as_of_seq`）—— 收到更新的信号时，不发请求就能
   *  判断手上这份是不是已经过期。 */
  asOfSeq: number;
}
const lineageCache = new Map<string, LineageEntry>();
let generation = 0;
const genListeners = new Set<() => void>();
export function lineageGeneration(): number { return generation; }
export function subscribeLineageChange(cb: () => void): () => void {
  genListeners.add(cb); return () => { genListeners.delete(cb); };
}
function bump(): void {
  generation += 1;
  for (const fn of genListeners) {
    try { fn(); } catch (err) { console.error('[outputsService] lineage listener failed', err); }
  }
}
/**
 * 忘掉一个对象，让下一次读重新问。**不带参数才整表** —— 整表失效会把打开 40 个
 * 分镜的画布变成 40 个请求，正是这个缓存存在的理由。按键是常态：回退只动一个对象，
 * 议题页收到的 `deliverable` 事件自带 `kind/ref_id`。
 */
export function invalidateOutputLineage(kind?: string, refId?: string): void {
  if (kind !== undefined && refId !== undefined) lineageCache.delete(lineageKey(kind, refId));
  else lineageCache.clear();
  bump();
}
if (typeof document !== 'undefined') {
  // 回到标签页时扔掉过期条目。定时器不行：后台标签页的 timer 被节流，
  // 而「我离开了十分钟」正是最该重读的时刻。
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    const now = Date.now();
    let dropped = false;
    for (const [k, e] of lineageCache) {
      if (now - e.fetchedAt > LINEAGE_TTL_MS) { lineageCache.delete(k); dropped = true; }
    }
    if (dropped) bump();
  });
}
```
`getOutputLineage`（:236-256）读 `entry`，命中但 `Date.now() - entry.fetchedAt > LINEAGE_TTL_MS` 视为未命中；
存入 `{ data: pending, fetchedAt: Date.now(), asOfSeq: 0 }`，`pending.then` 里回填 `asOfSeq = chain.as_of_seq ?? 0`。
删除 `clearOutputLineageCache`（:218-221）及其 doc（C10）。

```bash
cd frontend && npx vitest run services/outputsService.test.ts   # 绿
```

- [ ] **Step 5: 信号生产方（红）**

```ts
// components/Todolist/useIssueProgress.test.ts（新建；vi.mock('./issueTurnSignal') 暴露 notifyTurn）
it('signals the turn that just ended, once, with the run it ended', async () => {
  // IssueProgress 里没有「回合结束」这个事实，只能从边沿推 —— 而轮询每回合触发多次，
  // 所以推导必须在 hook 内部、拿着上一份 progress 做，不能交给每个消费方各推一遍。
  getIssueProgress.mockResolvedValueOnce({ ...base, phase: 'running', current_run: {
    id: '727145299382534100', last_seq: 42, status: 'running', started_at: null, model: null, view: {}, cost: {} } });
  getIssueProgress.mockResolvedValueOnce({ ...base, phase: 'idle', current_run: null });
  const { result } = renderHook(() => useIssueProgress(5, 's1'));
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(async () => { await result.current.refresh(); });
  expect(notifyTurn).toHaveBeenCalledWith('5', { runId: '727145299382534100', seq: 42 });
});
it('the first load signals nothing', async () => { /* 只有一份 progress → notifyTurn 零次 */ });
```
```tsx
// IssueDetailView.test.tsx：WS 两帧（测试从 openIssueChatSocket 的 mock 拿到 onEvent 回调）
it('a done frame signals the turn with its seq', () => {
  onEvent({ type: 'status', phase: 'done', run_id: '777', seq: 42, outputs: [] });
  expect(notifyTurn).toHaveBeenCalledWith('5', { runId: '777', seq: 42 });
});
it('a done frame invalidates exactly the objects it registered', () => {
  onEvent({ type: 'status', phase: 'done', run_id: '777', seq: 43, outputs: [{ kind: 'script_shot', ref_id: '9' }] });
  expect(invalidateOutputLineage).toHaveBeenCalledTimes(1);
  expect(invalidateOutputLineage).toHaveBeenCalledWith('script_shot', '9');
});
it('a legacy done frame without seq/outputs still signals', () => {
  onEvent({ type: 'status', phase: 'done' });
  expect(notifyTurn).toHaveBeenCalledWith('5', { runId: null, seq: 0 });
  expect(invalidateOutputLineage).not.toHaveBeenCalled();
});
```
```bash
cd frontend && npx vitest run components/Todolist/useIssueProgress.test.ts components/Todolist/IssueDetailView.test.tsx   # 红
```

- [ ] **Step 6: 实现生产方（绿）**

- `issuesService.ts:410-417`：`current_run` 加 `last_seq: number | null`。
- `useIssueProgress.ts`：加 `const prev = useRef<IssueProgress | null>(null);`，`refresh` 里 `setProgress(next)` 前：

```ts
const before = prev.current;
prev.current = next;
const endedRun = before?.current_run?.id ?? null;
// 只有「上一份有 run，而这一份换了 run 或没了 run」才是一次结束。
// 少了 `endedRun &&`，首次加载就会误发一次。
if (endedRun && next.current_run?.id !== endedRun) {
  notifyTurn(String(issueId), { runId: String(endedRun), seq: before?.current_run?.last_seq ?? 0 });
}
```
- `issueChatSocket.ts:22-25`：

```ts
export type IssueChatEvent =
  | { type: 'chunk'; delta: string }
  | { type: 'message'; message: IssueMessage }
  // `run_id` / `seq` / `outputs` 由 Task 4b 的 done 帧带来（恒定存在、未知时 null / []）。
  // 旧后端不带，所以全部可选——缺了 seq 用 0，水位车道按 runId 分开，不会把真实帧顶掉。
  | {
      type: 'status';
      phase: 'running' | 'done' | string;
      run_id?: string | null;
      seq?: number | null;
      outputs?: Array<{ kind: string; ref_id: string }>;
    };
```
- `IssueDetailView.tsx`：`done` 分支（:399-403）末尾加

```ts
for (const o of event.outputs ?? []) invalidateOutputLineage(o.kind as DeliverableKind, o.ref_id);
notifyTurn(String(issue.id), { runId: event.run_id ?? null, seq: event.seq ?? 0 });
```
（先失效再发信号，订阅方重拉时缓存已空；`run_id` 缺失时走本地车道。）

```bash
cd frontend && npx vitest run components/Todolist   # 绿
```

- [ ] **Step 7: 三个消费方（红）**

```tsx
// OutputsBlock.test.tsx
it('re-reads when the issue signals a finished turn', async () => {
  listIssueOutputs.mockResolvedValue([obj]);
  render(<OutputsBlockView ctx={ctx} />);
  await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(1));
  act(() => notifyTurn(String(ISSUE_ID), { runId: 'r1', seq: 7 }));
  await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId('outputs-updated')).toBeTruthy();
});
// useMentionOutputsTab.test.ts
it('forgets its one-shot guard when a turn ends', async () => {
  // 守卫（:84）让页签一个 mention 会话只拉一次；回合结束后不复位，刚产出的东西
  // 就永远 @ 不到。激活 → 1 次 → notifyTurn → 重新激活 → 2 次。
});
// OutputProvenance.test.tsx
it('re-reads after the lineage is invalidated', async () => {
  getOutputLineage.mockResolvedValue(chain);
  render(<OutputProvenance kind="script_shot" refId="9" />);
  await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(1));
  act(() => invalidateOutputLineage('script_shot', '9'));
  await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(2));
});
```
```bash
cd frontend && npx vitest run components/Todolist/blocks/OutputsBlock.test.tsx \
  components/chat/useMentionOutputsTab.test.ts components/agentActivity/OutputProvenance.test.tsx   # 红
```

- [ ] **Step 8: 接线三个消费方（绿）**

- `OutputsBlock.tsx`：`const signal = useTurnSignal(String(issueId));`，effect 依赖加 `signal`；
  拉到结果后若 `signal !== null` 则 `setJustUpdated(true)` + `window.setTimeout(… , 1500)` 清掉，渲染
  `{justUpdated && <span data-testid="outputs-updated" className="text-[11px] text-info">Updated</span>}`。
  ⚠️ `outputsBlock.match` **保持 `() => true`**（spec §7 C12 的原话：仍每页一拉，只是在信号后重拉）——
  改成按 rollup 猜 `has_outputs` 会在产出刚出现时把整块藏掉。
- `useMentionOutputsTab.ts`：
  ```ts
  const signal = useTurnSignal(issueId == null ? '' : String(issueId));
  useEffect(() => { if (!signal) return; requested.current = false; setObjects(null); }, [signal]);
  ```
- `OutputProvenance.tsx` / `OutputDiffDialog.tsx`：
  `const gen = useSyncExternalStore(subscribeLineageChange, lineageGeneration, () => 0);`
  加进各自 lineage effect 的依赖（`[kind, refId, gen]`）。
- Task 5 的 `doRevert` 成功分支补：
  ```ts
  // 回退版自己没有 issue 时借链上已有的那个；都没有就不发 —— 没有议题页要刷新。
  const issueId = res.version.issue_id ?? versions[0]?.issue_id ?? null;
  if (issueId) notifyTurn(String(issueId), { runId: null, seq: Number(res.version.id) });
  ```

```bash
cd frontend && npx vitest run && npx tsc --noEmit -p .    # 绿 + 触碰文件零新增错误
```

- [ ] **Step 9: 突变记录**

| 突变 | 应转红 |
|---|---|
| 水位键改回只用 issueId | Step 1「local action」用例 |
| `invalidateOutputLineage(kind, refId)` 改成整表 `clear()` | Step 3 第一例 |
| `useIssueProgress` 的边沿条件去掉 `endedRun &&` | Step 5 第二例 |
| `OutputsBlock` 的 effect 依赖去掉 `signal` | Step 7 第一例 |

- [ ] **Step 10: Commit**

```bash
git commit -m "feat(ui): 回合信号 seq 水位 + 血缘按键失效 + 三个消费方实时重拉（harness 三期 3b Task 6）"
```

---

---

### Task 7: 资源来源反查端点（后端；面板块见 Task 7b）

**Files:**
- Modify: `backend/app/repositories/generated_media_repository.py` — 在 `get_by_id`（:491-503）之后加 `find_by_promoted_resource`，复用既有 `_registered_resource_lookup_stmt`（:217-231）
- Create: `backend/app/api/resources_provenance_router.py`（≤90 行）
- Modify: `backend/app/api/resources_router.py:34-48`（include 顺序：静态/子路径 router 必须先于 `crud_router`）
- Create: `backend/tests/db/test_generated_media_promoted_lookup_integration.py`（照 `backend/tests/db/test_run_deliverables_repository_integration.py:80-110` 的 `orm_dsn` / `pg` fixture）
- Modify: `.github/workflows/schema-drift.yml`（`paths:` 清单 :66-72 加一行；步骤照 :355-368 的范式新增一步）
- Create: `backend/tests/api/test_resource_provenance_router.py`（照 `backend/tests/api/test_outputs_router.py:104-138` 的 `_client` 桩范式）

**Interfaces:**
- Produces: `GeneratedMediaRepository.find_by_promoted_resource(self, resource_id: int) -> Optional[dict]`（归一化行；`id` 已是 string）
- Produces: `GET /api/v1/resources/{resource_id}/provenance -> OutputLineageResponse`（`kind="generated_media"`）
- Consumes: `run_deliverables_repository.lineage_for(kind=, ref_id=)`、`lineage_view.version_of` / `redact_foreign_issue_links`、`issue_visibility.visible_issue_ids`、`app.api.media_permissions.check_media_access`、`ResourcesRepository.get_resource_by_id`
- 依赖：T4（`OutputLineageResponse.as_of_seq`）。本 Task **不**等 T4：pydantic v2 BaseModel 默认 `extra="ignore"`，T4 未合并时多传的 `as_of_seq` 被忽略，合并后自动生效。开工前 `git -C <worktree> rebase origin/master` 一次即可。

**索引裁定（写进 PR 描述，并通知 T1）**：mig 456 已建 `uq_genmedia_promoted_resource`（`supabase/migrations/456_generated_media_promoted_unique.sql:10-13`，partial unique，`WHERE promoted_resource_id IS NOT NULL`），反查已经有索引可走。**T1 的 mig 466 不要再加 `idx_generated_media_promoted_resource`** —— 那会是同一列同一谓词的第二份索引，只多一份写放大。契约里那行的「先核是否已有」核出来的答案就是「已有」。

- [ ] **Step 1: 红（仓库层，真 PG）** — 新建 `backend/tests/db/test_generated_media_promoted_lookup_integration.py`。头注释写明：`promoted_resource_id` **无 FK**（mig 307:30），所以本文件不需要 `resources` 行，fixture 只建 `generated_media`（NOT NULL 列只有 `scope_id/creator_id/media_kind/file_path/origin_kind`）。四个用例：

  ```python
  """``find_by_promoted_resource`` 打在真 Postgres 上（mig 307 + 456）。

  单测里 session 是桩的，编译得过不代表服务器接受：``promoted_resource_id``
  的 IS NULL 语义（NULL 不匹配任何等值）、``_normalize`` 把 BIGINT id 变成
  string、以及 456 的 partial unique 到底存不存在，只有这里会说话。

      INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
        uv run pytest tests/db/test_generated_media_promoted_lookup_integration.py -v
  """
  from __future__ import annotations

  import os
  import uuid

  import asyncpg
  import pytest

  pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
  _TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
  pytest.importorskip("asyncpg")
  _skip = pytest.mark.skipif(
      not _TEST_DSN,
      reason="INTEGRATION_DATABASE_URL not set — promoted-resource lookup needs a DB.",
  )

  # orm_dsn / pg fixtures: 逐字照抄 tests/db/test_run_deliverables_repository_integration.py:80-108


  @pytest.fixture
  async def gm(pg):
      """两行 generated_media：一行已 promote 到 resource_a，一行没 promote。"""
      scope_id = 331438215859255
      creator = uuid.uuid4()
      resource_a = 900_000_000_000_000 + int(uuid.uuid4().int % 1_000_000)
      made = []

      async def _mk(promoted):
          gen_id = await pg.fetchval(
              """
              INSERT INTO public.generated_media
                  (scope_id, creator_id, media_kind, file_path, origin_kind,
                   promoted_resource_id, model, provider)
              VALUES ($1,$2,'image',$3,'agent_run',$4,'gpt-6-astra','openai-images')
              RETURNING id
              """,
              scope_id, creator, f"gm/{uuid.uuid4().hex}.png", promoted,
          )
          made.append(gen_id)
          return gen_id

      promoted_id = await _mk(resource_a)
      loose_id = await _mk(None)
      try:
          yield {"promoted": promoted_id, "loose": loose_id, "resource_a": resource_a}
      finally:
          await pg.execute(
              "DELETE FROM public.generated_media WHERE id = ANY($1::bigint[])", made
          )


  def _repo():
      from app.repositories.generated_media_repository import GeneratedMediaRepository
      return GeneratedMediaRepository()


  @_skip
  async def test_finds_the_inbox_row_of_a_promoted_resource(orm_dsn, gm):
      row = await _repo().find_by_promoted_resource(gm["resource_a"])
      assert row is not None
      assert row["id"] == str(gm["promoted"]), "Snowflake id 必须是 string"
      assert row["model"] == "gpt-6-astra"


  @_skip
  async def test_a_resource_nobody_promoted_into_is_none(orm_dsn, gm):
      assert await _repo().find_by_promoted_resource(gm["resource_a"] + 7) is None


  @_skip
  async def test_a_null_promoted_pointer_never_matches(orm_dsn, gm, pg):
      """NULL 不等于任何值——没有这条，一个写错成 IS NOT DISTINCT FROM 的
      谓词会把每个未 promote 的生成都认领成某个资源的来源。"""
      row = await _repo().find_by_promoted_resource(gm["resource_a"])
      assert row["id"] != str(gm["loose"])


  @_skip
  async def test_the_partial_unique_index_is_what_makes_one_row_the_answer(orm_dsn, gm, pg):
      """mig 456 的 partial unique 存在 → 一个资源至多一行，``ORDER BY id ASC``
      只为 456 之前可能残留的一对行兜底（所以本测试断言索引，而不是造重复行）。"""
      assert await pg.fetchval(
          "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
          "AND indexname='uq_genmedia_promoted_resource'"
      ) == 1
      with pytest.raises(asyncpg.exceptions.UniqueViolationError):
          await pg.execute(
              "INSERT INTO public.generated_media "
              "(scope_id, creator_id, media_kind, file_path, origin_kind, promoted_resource_id) "
              "VALUES (331438215859255, gen_random_uuid(), 'image', 'x.png', 'agent_run', $1)",
              gm["resource_a"],
          )
  ```

  跑：`cd backend && INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift uv run pytest tests/db/test_generated_media_promoted_lookup_integration.py -v`
  期望失败：`AttributeError: 'GeneratedMediaRepository' object has no attribute 'find_by_promoted_resource'`（前三条）。第四条应当直接绿——它断言的是既有 schema，绿是正确的。

- [ ] **Step 2: 绿（仓库方法）** — `backend/app/repositories/generated_media_repository.py`，`get_by_id`（:503）之后：

  ```python
      async def find_by_promoted_resource(self, resource_id: int) -> Optional[dict]:
          """一个 promoted 资源背后的那一行收件箱记录，没有则 None（3b spec §2.4）。

          语句直接复用 ``insert_registered_resource`` 的幂等 SELECT
          （``_registered_resource_lookup_stmt``）：同一个问题「哪一行为这个
          资源负责」只许有一个答案，两处各写一遍迟早会分叉。

          ``promoted_resource_id`` **没有外键**（mig 307:30），资源删除时不清；
          本方法因此从不断言资源存在。这条路上不可达（调用方拿着活资源进来），
          但那正是它不该假设的原因。

          不做 scope 过滤：可见性由 ``resources_router`` 的资源 ACL 承担，
          这里再加一层 scope 只会让「能看资源却读不到来源」这种半可见状态出现。
          """
          async with read_scope() as session:
              row = (
                  (
                      await session.execute(
                          _registered_resource_lookup_stmt(int(resource_id))
                      )
                  )
                  .mappings()
                  .first()
              )
          return _normalize(dict(row)) if row else None
  ```

  重跑 Step 1 命令 → 4 passed。

- [ ] **Step 3: schema-drift 接线** — `.github/workflows/schema-drift.yml`：`paths:` 清单（:66-72 那一段）加 `- 'backend/tests/db/test_generated_media_promoted_lookup_integration.py'`；在「Exercise the deliverables repository…」步骤（:355-368）之后照同一范式加：

  ```yaml
        - name: Exercise the promoted-resource reverse lookup (mig 307/456)
          working-directory: backend
          env:
            INTEGRATION_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/drift
          run: |
            set -euo pipefail
            # 资源信息面板「谁做的我」唯一的解析口。单测的 session 是桩的，
            # 所以 NULL 指针不匹配、BIGINT id 出闸变 string、以及 456 的
            # partial unique 还在，只有这里会被 Postgres 真正回答。
            bash "$GITHUB_WORKSPACE/.github/scripts/pytest-no-full-skip.sh" tests/db/test_generated_media_promoted_lookup_integration.py -v
  ```

  本地自查：`cd /Volumes/... && /tmp/actionlint`（无输出 + exit 0）。⚠️ 必须走 `pytest-no-full-skip.sh`，裸 `uv run pytest` 在全 skip 时 exit 0（该文件 :47-54 的告诫）。

- [ ] **Step 4: 红（路由单测）** — 新建 `backend/tests/api/test_resource_provenance_router.py`，桩法照 `tests/api/test_outputs_router.py:104-138`（`FastAPI()` + `register_exception_handlers` + `dependency_overrides[get_auth]` + `monkeypatch.setattr(mod, …)`），**断言生产错误外壳**（`body["details"]["code"]`）：

  ```python
  RESOURCE_ID = "912345678901234"
  GEN_ID = "337650953731886"
  ISSUE_ID = "349426708244346"


  def _client(monkeypatch, *, resource=True, access=True, gen=True, rows=None):
      app = FastAPI()
      register_exception_handlers(app)
      app.include_router(mod.router, prefix="/api/v1")
      from app.core.deps import get_auth
      app.dependency_overrides[get_auth] = lambda: SimpleNamespace(user_id=ME)
      from app.core.scope_dep import ScopedRequestDep  # noqa: F401
      for dep in mod.router.dependencies:
          app.dependency_overrides[dep.dependency] = lambda: None
      monkeypatch.setattr(
          mod, "ResourcesRepository",
          lambda: SimpleNamespace(get_resource_by_id=AsyncMock(
              return_value={"id": RESOURCE_ID} if resource else None)),
      )
      monkeypatch.setattr(mod, "check_media_access", AsyncMock(return_value=access))
      monkeypatch.setattr(
          mod, "GeneratedMediaRepository",
          lambda: SimpleNamespace(find_by_promoted_resource=AsyncMock(
              return_value={"id": GEN_ID} if gen else None)),
      )
      repo = SimpleNamespace(lineage_for=AsyncMock(return_value=rows if rows is not None else [_row(1)]))
      monkeypatch.setattr(mod, "get_run_deliverables_repository", lambda: repo)
      monkeypatch.setattr(mod, "visible_issue_ids", AsyncMock(return_value={ISSUE_ID}))
      return TestClient(app), repo
  ```

  用例（`_row(version, **over)` 照 `test_outputs_router.py:41-68` 那份，`kind="generated_media"`、`ref_id=GEN_ID`、`issue_id=ISSUE_ID`、`issue_key="MH-96"`、`team_id="331438215859255"`）：
  1. `test_promoted_resource_answers_with_the_generation_chain`：200，`body["kind"] == "generated_media"`、`body["ref_id"] == GEN_ID`、`body["versions"][0]["issue_key"] == "MH-96"`；且 `repo.lineage_for.await_args.kwargs == {"kind": "generated_media", "ref_id": GEN_ID}`（证明用的是 generation id 而不是 resource id）。
  2. `test_a_resource_no_generation_points_at_is_404_not_registered`：`gen=False` → 404 且 `body["details"]["code"] == "not_registered"`。
  3. `test_an_empty_chain_is_also_not_registered`：`rows=[]` → 404 `not_registered`（「有收件箱行但没人登记过」与「没行」对读者是同一件事）。
  4. `test_an_invisible_issue_keeps_the_coordinates_and_loses_the_link`：`visible_issue_ids` 返回 `set()` → 200，`versions[0]["issue_id"] == ISSUE_ID` 仍在，而 `issue_key is None and deep_link is None`（spec §5 稿四：坐标保留、按钮禁用）。**这条是本端点与 `/outputs/{kind}/{ref_id}` 唯一的行为差异**：那边议题不可见就整体 404，这边资源 ACL 已经放行，再 404 等于把「你有权看的文件」说成不存在。
  5. `test_a_resource_the_caller_cannot_access_is_403`：`access=False` → 403 `forbidden`，且 `find_by_promoted_resource` 未被 await（守卫在解析之前）。
  6. `test_a_missing_resource_is_404_not_found`：`resource=False` → 404 `not_found`。
  7. `test_ids_stay_strings_on_the_wire`：`versions[0]["run_id"]` / `["id"]` 均为 `str`。

  跑：`cd backend && uv run pytest tests/api/test_resource_provenance_router.py -q` → 期望 `ModuleNotFoundError: No module named 'app.api.resources_provenance_router'`。

- [ ] **Step 5: 绿（路由）** — 新建 `backend/app/api/resources_provenance_router.py`：

  ```python
  """``GET /api/v1/resources/{resource_id}/provenance`` —— 资源反查产出它的 run（3b spec §2.4）。

  **为什么不挂在 ``/outputs`` 下**：那个 router 整体骑着 ``todolist`` 模块门
  （``outputs_router.py:52-57``），而这条路的读者是资源库用户，可能根本没开
  todolist。门控与可见性都跟着**资源**走，路由就该长在资源这边。

  **可见性口径与 ``/outputs/{kind}/{ref_id}`` 有意不同一处**：那边用产出 run
  的议题规则决定整条链是否存在；这边资源 ACL 已经回答了「你能不能看这个文件」，
  再用议题规则 404 一次，就是把用户有权看的文件说成不存在。所以链**给**，链里
  每一版的议题链接仍按 ``visible_issue_ids`` 逐条置空——坐标留下，按钮禁用
  （spec §5 稿四）。置空逻辑是 ``redact_foreign_issue_links`` 原物，不另写一份。
  """

  from __future__ import annotations

  from fastapi import APIRouter, HTTPException, status

  from app.api.media_permissions import check_media_access
  from app.core.deps import AuthDep
  from app.core.scope_dep import ScopedRequestDep
  from app.repositories.generated_media_repository import GeneratedMediaRepository
  from app.repositories.resources_repository import ResourcesRepository
  from app.repositories.run_deliverables_repository import (
      get_run_deliverables_repository,
  )
  from app.schemas.outputs import OutputLineageResponse
  from app.services.deliverables.lineage_view import (
      redact_foreign_issue_links,
      version_of,
  )
  from app.services.issues.issue_visibility import visible_issue_ids

  router = APIRouter(prefix="/resources", tags=["resources", "Outputs"])


  def _reject(status_code: int, code: str, message: str) -> HTTPException:
      """``detail`` 必须是 dict：生产把每个 ``HTTPException`` 包进
      ``ErrorResponse``，只有 dict 会原样落到 ``details``（CLAUDE.md 2026-09-09）。"""
      return HTTPException(
          status_code=status_code, detail={"code": code, "message": message}
      )


  @router.get("/{resource_id}/provenance", response_model=OutputLineageResponse)
  async def get_resource_provenance(
      resource_id: str, auth: AuthDep, _scope: ScopedRequestDep
  ) -> OutputLineageResponse:
      """这个资源是谁做的：run / issue / step / 花费，与画布上同一个来源块同形。"""
      resource = await ResourcesRepository().get_resource_by_id(resource_id)
      if not resource:
          raise _reject(status.HTTP_404_NOT_FOUND, "not_found", "resource not found")
      if not await check_media_access(resource_id, auth.user_id, None):
          raise _reject(status.HTTP_403_FORBIDDEN, "forbidden", "access denied")

      gen = await GeneratedMediaRepository().find_by_promoted_resource(
          int(resource["id"])
      )
      if not gen:
          raise _reject(
              status.HTTP_404_NOT_FOUND,
              "not_registered",
              "no generation was promoted into this resource",
          )
      rows = await get_run_deliverables_repository().lineage_for(
          kind="generated_media", ref_id=str(gen["id"])
      )
      if not rows:
          raise _reject(
              status.HTTP_404_NOT_FOUND,
              "not_registered",
              f"generated_media/{gen['id']} is not in the deliverable registry",
          )
      visible = await visible_issue_ids({row.get("issue_id") for row in rows}, auth)
      versions = redact_foreign_issue_links(
          [version_of(row) for row in rows], visible_issue_ids=visible
      )
      newest = rows[0]
      return OutputLineageResponse(
          kind="generated_media",
          ref_id=str(gen["id"]),
          latest_version=versions[0]["version"],
          versions=versions,
          # T4 的字段；T4 未合并时 pydantic 的 extra="ignore" 默认把它丢掉，
          # 所以两个 Task 的合并顺序不构成依赖。
          as_of_seq=int(newest.get("seq") or newest.get("id")),
      )


  __all__ = ["router"]
  ```

  `backend/app/api/resources_router.py`（:34-48 那组 include 之间，`crud_router` 之前）加：

  ```python
  # GET /resources/{id}/provenance —— 与 assets_router 同理：它是 crud_router
  # 那个 catch-all id 段下的静态子路径，必须先注册。
  router.include_router(provenance_router)
  ```
  并在文件头 import 段加 `from app.api.resources_provenance_router import router as provenance_router`，docstring 的子模块清单补一行。

  跑 `cd backend && uv run pytest tests/api/test_resource_provenance_router.py -q` → 7 passed。

- [ ] **Step 6: 突变（记进 PR）** — ① 把 Step 5 的 `visible_issue_ids` 那两行换成直接 `versions = [version_of(r) for r in rows]` → 用例 4 红（跨议题链接泄漏）；② 把 `int(resource["id"])` 换成 `int(gen["id"])` 式的笔误（传 generation id 进反查）→ 用例 1 红；③ 把 `_reject` 的 `detail` 改成字符串 → 用例 2/3/5/6 读 `details.code` 全红。三条各跑一次并还原。

- [ ] **Step 7: 后端提交 + PR** — `cd backend && uv run ruff check app tests && uv run black --check app tests && uv run pytest tests/api tests/db/test_generated_media_promoted_lookup_integration.py -q`；提交：`feat(outputs): 资源来源反查端点 GET /resources/{id}/provenance（3b Task 7）`

  之后 `git push -u origin <branch>` 并 `gh pr create`（标题 `feat(outputs): 资源来源反查 GET /resources/{id}/provenance（harness 三期 3b Task 7）`）。前端面板块在 Task 7b 单独 PR。

---

### Task 7b: 前端——资源信息面板来源块（3b §5 稿四）

**Files:**
- Modify: `frontend/services/resourceService.ts`（import :10-12 补 `ApiError`；函数追加到文件尾，现 2482 行）
- Create: `frontend/services/resourceService.provenance.test.ts`
- Modify: `frontend/components/agentActivity/OutputProvenance.tsx`（props :39-59、effect :78-100、按钮组 :161-225）+ `OutputProvenance.test.tsx`
- Modify: `frontend/components/ResourceInfoPanel.tsx`（import :1-7、组件体 :138-160、Properties 块 :486-514 之后）+ `ResourceInfoPanel.test.tsx`
- Modify: `frontend/public/locales/en.json` / `zh.json`（`outputs.provenanceRedacted`、`resources.infoPanel.provenance`）

**Interfaces:**
- Consumes：`GET /api/v1/resources/{resource_id}/provenance` → `OutputLineageResponse`（`kind="generated_media"`），404 `not_registered`；链接字段按 issue 可见性置空（坐标保留）。
- Produces：`resourceService.getResourceProvenance(resourceId: string): Promise<OutputLineage | null>`；`OutputProvenanceProps` 新增 `lineage?: OutputLineage | null`、`allowDiff?: boolean`（默认 true）。

- [ ] **Step 1: service（红）**

```ts
// frontend/services/resourceService.provenance.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./apiClient', () => ({ apiClient: { get: vi.fn() },
  ApiError: class ApiError extends Error { status: number; code?: string;
    constructor(m: string, s: number, o: { code?: string } = {}) { super(m); this.status = s; this.code = o.code; } } }));
const { apiClient, ApiError } = await import('./apiClient');
const { getResourceProvenance } = await import('./resourceService');
// 真实 wire 形状：血缘响应里每个 id 都是 string（backend/app/schemas/outputs.py:40-46
// 的 `id: str` / `run_id: str` / `issue_id: Optional[str]`）。
const body = { kind: 'generated_media', ref_id: '347786145852739', latest_version: 1, as_of_seq: 31,
  versions: [{ id: '347786145852739011', version: 1, parent_version: null, run_id: '727145299382534100',
    issue_id: '727145299382534000', issue_key: 'MH-94', deep_link: '/team/424242424242/todolist/MH-94?step=3',
    seq: 31, turn: 1, step: 3, title: 'Cover', model: 'gpt-6-astra', cost_cents: 12, cost_kind: 'exact',
    actor_user_id: null, reverted_from_version: null, created_at: '2026-09-12T02:00:00Z' }] };
describe('getResourceProvenance', () => {
  beforeEach(() => vi.mocked(apiClient.get).mockReset());
  it('reads the chain for a promoted资源', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(body);
    const out = await getResourceProvenance('347786145852739000');
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/resources/347786145852739000/provenance');
    expect(out?.versions[0].run_id).toBe('727145299382534100');
  });
  it('a human upload is null, not an error', async () => {
    // 404 not_registered 是答案不是故障：库里大多数资源都是人传的。
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError('nope', 404, { code: 'not_registered' }));
    await expect(getResourceProvenance('1')).resolves.toBeNull();
  });
  it('any other failure propagates', async () => {
    // 500 静默成 null 会让「读不到」长得跟「人传的」一模一样。
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError('boom', 500, {}));
    await expect(getResourceProvenance('1')).rejects.toThrow('boom');
  });
});
```
```bash
cd frontend && npx vitest run services/resourceService.provenance.test.ts   # 红
```

- [ ] **Step 2: 实现 service（绿）**

```ts
// resourceService.ts 尾部（:11 的 import 改成 `import { apiClient, ApiError } from './apiClient';`）
import type { OutputLineage } from './outputsService';
/**
 * 这个资源是谁做出来的 —— `null` 表示没有 run 登记过它，也就是人传的。
 *
 * 404 收成 null 而不是抛：库里绝大多数行都是人传的，把常态渲染成错误等于在每一页
 * 挂一个永久假警报（`OutputProvenance` 顶部写的同一条规则）。其它失败一律外抛 ——
 * 静默成 null 会让「读不到」与「人传的」不可区分。
 *
 * 反查在后端：链接在 `generated_media.promoted_resource_id` 一侧，`resources` 没有
 * 反向列（recon B6）。
 */
export async function getResourceProvenance(resourceId: string): Promise<OutputLineage | null> {
  try {
    return await apiClient.get<OutputLineage>(`/api/v1/resources/${resourceId}/provenance`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
```
```bash
cd frontend && npx vitest run services/resourceService.provenance.test.ts   # 绿
```

- [ ] **Step 3: 来源块两个新入口（红）**

```tsx
// OutputProvenance.test.tsx
it('renders a lineage the host already read, without asking again', async () => {
  render(<OutputProvenance kind="generated_media" refId="347786145852739" lineage={chain} />);
  expect(await screen.findByTestId('output-provenance')).toBeTruthy();
  expect(getOutputLineage).not.toHaveBeenCalled();
});
it('hides Diff when the host forbids it', async () => {
  // 媒体没有可比的版本文本（3b §6 明确不做媒体版本链）。
  render(<OutputProvenance kind="generated_media" refId="9" lineage={twoVersions} allowDiff={false} />);
  await screen.findByTestId('output-provenance');
  expect(screen.queryByTestId('output-provenance-diff')).toBeNull();
});
it('disables both controls when the issue was redacted', async () => {
  // 能看到已 promote 的资源 ≠ 能看到产出它的 issue。后端保留坐标、抹掉链接
  // （deep_link / issue_key 为 null，issue_id 还在），前端据此说出原因，
  // 而不是画一个点了没反应的按钮。
  const redacted = { ...chain, versions: [{ ...chain.versions[0], issue_key: null, deep_link: null }] };
  render(<OutputProvenance kind="generated_media" refId="9" lineage={redacted} />);
  expect((await screen.findByTestId('output-provenance-issue-unlinked')).getAttribute('title'))
    .toBe('Issue not visible to you');
  expect(screen.getByTestId('output-provenance-run')).toBeDisabled();
});
it('an issue that simply has none keeps the old wording', async () => {
  const noIssue = { ...chain, versions: [{ ...chain.versions[0], issue_id: null, issue_key: null, deep_link: null }] };
  render(<OutputProvenance kind="generated_media" refId="9" lineage={noIssue} />);
  expect((await screen.findByTestId('output-provenance-issue-unlinked')).getAttribute('title'))
    .toBe('The issue that produced this is not linked');
});
```
```bash
cd frontend && npx vitest run components/agentActivity/OutputProvenance.test.tsx   # 红
```

- [ ] **Step 4: 实现来源块（绿）**

`OutputProvenance.tsx` props（:39-59）新增：

```tsx
  /** 宿主已经读到的链 —— 传了就不自取。资源面板走的是另一个端点
   *  （`GET /resources/{id}/provenance`，键是资源 id 不是 generated_media id），
   *  同一块 UI 不该为此长出第二条取数分支。`undefined` = 自取；
   *  `null` = 宿主查过、没有来源（人手上传），渲染 null。 */
  lineage?: OutputLineage | null;
  /** 允许 Diff 按钮。媒体在资源面板上没有可比的版本文本（3b §6）。 */
  allowDiff?: boolean;
```
effect（:78-100）首行插 `if (lineageProp !== undefined) { setLineage(lineageProp); setErrorCode(null); return; }`
（prop 与局部 state 同名，重命名 prop 为 `lineage: lineageProp`），依赖加 `lineageProp`。渲染处：

```tsx
// 坐标还在、链接没了 = 被可见性抹掉，而不是「这个 run 本来就没有议题」。
// 两种情形读者要采取的行动完全不同（去要权限 vs 没什么可去）。
const redacted = latest.issue_id !== null && !issueUrl;
```
`output-provenance-issue-unlinked` 的 `title` 改成
`redacted ? t('outputs.provenanceRedacted', 'Issue not visible to you') : t('outputs.provenanceNoLink', 'The issue that produced this is not linked')`；
`output-provenance-run` 的 `disabled` 改成 `!childRun || redacted`、`title` 同理；
Diff 的渲染条件改成 `allowDiff !== false && versions >= 2`。

```bash
cd frontend && npx vitest run components/agentActivity/OutputProvenance.test.tsx   # 绿
```

- [ ] **Step 5: 面板挂块（红）**

```tsx
// ResourceInfoPanel.test.tsx：既有的 vi.mock('../services/resourceService') 工厂加 getResourceProvenance: vi.fn()
vi.mock('./agentActivity/OutputProvenance', () => ({
  OutputProvenance: (p: { refId: string; allowDiff?: boolean }) =>
    <div data-testid="provenance" data-ref={p.refId} data-diff={String(p.allowDiff)} />,
}));
it('shows where an agent-made resource came from', async () => {
  vi.mocked(getResourceProvenance).mockResolvedValue(chain);
  render(<ResourceInfoPanel {...props} resource={{ ...resource, id: '347786145852739000', source_type: 'generated' }} />);
  const block = await screen.findByTestId('provenance');
  // ref 是 generated_media 的 id（链上的 ref_id），不是资源 id。
  expect([block.getAttribute('data-ref'), block.getAttribute('data-diff')]).toEqual(['347786145852739', 'false']);
});
it('a human upload shows nothing', async () => {
  vi.mocked(getResourceProvenance).mockResolvedValue(null);
  render(<ResourceInfoPanel {...props} />);
  await waitFor(() => expect(getResourceProvenance).toHaveBeenCalled());
  expect(screen.queryByTestId('provenance')).toBeNull();
});
it('a failed read never breaks the panel', async () => {
  vi.mocked(getResourceProvenance).mockRejectedValue(new Error('boom'));
  render(<ResourceInfoPanel {...props} />);
  await waitFor(() => expect(getResourceProvenance).toHaveBeenCalled());
  expect(screen.getByText('a.png')).toBeTruthy();
  expect(screen.queryByTestId('provenance')).toBeNull();
});
```
```bash
cd frontend && npx vitest run components/ResourceInfoPanel.test.tsx   # 红
```

- [ ] **Step 6: 实现面板（绿）**

`ResourceInfoPanel.tsx` 组件体（:160 附近）：

```tsx
// 这个资源的来源链（3b §5 稿四）。面板手上只有 resource.id —— 链接在
// generated_media 一侧，resources 没有反向列，所以反查在后端。
const [provenance, setProvenance] = useState<OutputLineage | null>(null);
useEffect(() => {
  let live = true;
  setProvenance(null);
  getResourceProvenance(String(resource.id))
    .then((chain) => { if (live) setProvenance(chain); })
    .catch((err) => {
      // 读不到来源不该让整个信息面板残缺；说出来（日志），但不画错误块 ——
      // 这一块在库里的常态就是不存在。
      console.error('[ResourceInfoPanel] provenance read failed', err);
    });
  return () => { live = false; };
}, [resource.id]);
```
渲染：Properties 块（:486-514）之后、底部留白（:516）之前插入

```tsx
{provenance && (
  <div className={`px-4 mt-4 border-t ${cBorderSection} pt-3`}>
    <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
      {t('resources.infoPanel.provenance', 'Source')}
    </h4>
    {/* 画布分镜节点挂的是同一个组件 —— 一块 UI，两个宿主。
        allowDiff={false}：媒体没有版本链可比（3b §6）。 */}
    <OutputProvenance kind="generated_media" refId={provenance.ref_id}
                      lineage={provenance} allowDiff={false} />
  </div>
)}
```
```bash
cd frontend && npx vitest run && npx tsc --noEmit -p .    # 绿 + 触碰文件零新增错误
```

- [ ] **Step 7: 突变记录**

| 突变 | 应转红 |
|---|---|
| `getResourceProvenance` 把所有失败都收成 null | Step 1 第三例 |
| `redacted` 判定去掉 `latest.issue_id !== null` | Step 3 第四例 |
| 面板不传 `allowDiff={false}` | Step 5 第一例 |
| 面板用 `resource.id` 当 `refId` | Step 5 第一例（`data-ref` 对不上） |

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(ui): 资源信息面板来源块 + 资源来源反查 service（harness 三期 3b Task 7b）"
```

---

### Task 8: 顺手小票 C17 / C5 / C7

**Files:**
- Modify: `backend/app/api/issue_messages_router.py:636-648`（legacy 无 agent 分支）
- Test: `backend/tests/api/test_output_ref_attachment.py`（`_wire` 加一个无 agent 的变体 + 两条用例）
- Create: `backend/app/services/deliverables/README.md`（C5 + C7 两条口径；该目录今天没有 README——已核 `ls backend/app/services/deliverables/`）
- Modify: `backend/app/services/ai/undo/run_undo_service.py:1-13`（docstring 补一句指向 README）

**Interfaces:**
- Produces: `POST /api/v1/issues/{id}/messages` 在无 assignee agent 且带 `output_ref` 附件时 → `HTTPException(409, detail={"code": "citations_need_agent", ...})`
- Consumes: 既有 `verdict.agent_id`（`compute_comment_trigger` / `apply_suppression`）、`payload.attachments`

**现状（已核）**：`issue_messages_router.py:638-648` 是

```python
    if verdict.agent_id is None:
        if payload.answer_to is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "no_open_question",
                        "message": "this issue has no agent to answer"},
            )
        return await _insert_legacy_comment(issue_id, payload, auth)
```

而 `output_ref` 的解析与类型化拒绝发生在 :665-675，**在这个 return 之后**。于是无 agent 的 issue 上带引用发帖 → 附件被 `_insert_legacy_comment` 原样丢掉（它只写 `body` + `meta={}`，:466-472），用户看到发帖成功、引用消失，没有任何地方说得出来。这正是 3a 终审记下的 C17，与「触发路径必须类型化失败回显」同族。

**改法（最小）**：不动 `output_ref` 解析的位置（它需要 `attachments_payload`，且要在 dispatch 之前），只在 legacy 分支里加一个**同形的守卫**——与 `no_open_question` 并排，同一个「有东西要交给 agent，而这个 issue 没有 agent」的判断。

- [ ] **Step 1: 红** — `backend/tests/api/test_output_ref_attachment.py` 加一个 `_wire_no_agent(monkeypatch)`（复制 `_wire` :60-87，把 `issue_row["assignee_agent_id"]` 设为 `None`，并 `monkeypatch.setattr(r, "_insert_legacy_comment", AsyncMock())`），两条用例：

  ```python
  async def test_a_citation_on_an_agentless_issue_is_a_typed_409(monkeypatch):
      """C17：legacy 评论路径在 3a 之后成了唯一静默吃附件的分支。引用只有
      agent 读得懂，没有 agent 就该当面拒绝，而不是发帖成功、引用消失。"""
      r, legacy = _wire_no_agent(monkeypatch)
      with pytest.raises(HTTPException) as exc:
          await _post(r, [_att()])
      assert exc.value.status_code == 409
      assert isinstance(exc.value.detail, dict)
      assert exc.value.detail["code"] == "citations_need_agent"
      legacy.assert_not_awaited()  # 一行都不许落库


  async def test_an_ordinary_comment_on_an_agentless_issue_still_posts(monkeypatch):
      """守卫只认 output_ref：普通评论、文件附件都照旧走 legacy 插入。"""
      r, legacy = _wire_no_agent(monkeypatch)
      await _post(r, [{"kind": "file", "url": "https://x/y.png"}])
      legacy.assert_awaited_once()
      await r.post_issue_message(ISSUE_ID, IssueMessagePost(body="hi"), AUTH)
      assert legacy.await_count == 2
  ```

  跑 `cd backend && uv run pytest tests/api/test_output_ref_attachment.py -q` → 第一条红（`_insert_legacy_comment` 被 await、无异常）。

- [ ] **Step 2: 绿** — `issue_messages_router.py` legacy 分支改为：

  ```python
      if verdict.agent_id is None:
          if payload.answer_to is not None:
              # A typed answer with nobody to wake is not a comment; say so.
              raise HTTPException(
                  status_code=409,
                  detail={
                      "code": "no_open_question",
                      "message": "this issue has no agent to answer",
                  },
              )
          # 3a 小票 C17：引用是写给 agent 看的。legacy 插入只落 body，
          # 附件会被原样丢掉——而「发帖成功、引用消失」是本仓明令禁止的静默
          # no-op（「触发路径必须类型化失败回显」）。与上面的 no_open_question
          # 同形：有东西要交给 agent，而这个 issue 没有 agent。
          if any(
              (a.kind if hasattr(a, "kind") else a.get("kind")) == "output_ref"
              for a in (payload.attachments or [])
          ):
              raise HTTPException(
                  status_code=409,
                  detail={
                      "code": "citations_need_agent",
                      "message": "this issue has no agent to read a citation",
                  },
              )
          return await _insert_legacy_comment(issue_id, payload, auth)
  ```

  重跑 → 2 passed（连同文件里既有用例全绿）。

- [ ] **Step 3: 突变** — 把守卫条件改成 `if False:` → 第一条红；把 `detail` 改成字符串 → 第一条的 `isinstance(..., dict)` 红。记进 PR。

- [ ] **Step 4: C5 + C7 文档（同一个提交）** — 新建 `backend/app/services/deliverables/README.md`：

  ```markdown
  # 产出登记（run_deliverables）的两条常被问错的口径

  ## C5 — undo 不占版本号
  `services/ai/undo/run_undo_service.py` 的回滚是**逆操作批**：它按 `run_id`
  读 `script_shot_ops` / `script_ops` 并写回逆操作，**不调 `register_deliverable`**，
  所以血缘链上不会多出一版。理由：undo 撤销的是某个 run 干过的事，语义是
  「那次产出不算数了」，再登记一版等于把撤销本身记成一次新产出。
  与 3b 的 **revert** 正相反——revert 是人手发起的新版本（`actor_user_id` 占号、
  `reverted_from_version` 指回目标版），因为它产生的是一个**新的当前状态**。
  两者共用账本，不共用版本号。

  ## C7 — `generated_media` 恒 v1
  媒体类没有版本链：重新生成产生的是**新的 `ref_id`**（新 `generated_media`
  行），两代之间只共享 `node_id`。所以 `GET /outputs/generated_media/{id}` 永远
  只回一版，`revert` 对它是 400 `kind_not_revertible`，来源块的 Diff 按钮
  （`versions >= 2` 才渲染）在媒体上永不出现。要把两代媒体串成一条链需要
  `shot_id + slot` 或 `parent_resource_id` 作键——不在 3b 范围（spec §6）。
  ```

  `run_undo_service.py` 的 docstring 末尾加一行：``版本语义见 ``services/deliverables/README.md``（undo 不占号，revert 占）。``

- [ ] **Step 5: lint + 提交 + PR** — `cd backend && uv run ruff check app tests && uv run black --check app tests && uv run pytest tests/api -q`；提交 `fix(issues): 无 agent 的评论路径拒绝 output_ref 引用（409 citations_need_agent，3a 小票 C17/C5/C7）`；PR 同标题。

---

---

### Task 9: 真栈验收 + 完成账

**Files:**
- Modify: `docs/superpowers/plans/2026-09-14-harness-p4-phase3b-revert-cost-realtime.md`（末尾追加「完成账」节）
- 临时（**永不提交**）：`frontend/e2e-prod/tmp-3b-realtime.spec.ts`、`frontend/e2e-prod/tmp-3b-resource.spec.ts`

**固定 fixture**：MH-96（issue id `349426708244346`）在 team `331438215859255`，分镜 `337650953731886`。token：`TOK=$(cat /tmp/nous_token.txt)`；下面所有 curl 均带 `-H "Authorization: Bearer $TOK"`。DB 证据一律：`ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "<SQL>"'`。

- [ ] **Step 1: 盯部署链（前置，两条都要绿才开始验收）**
  - 后端：`gh run list -w deploy-gpu.yml -L 5` → 最新一条 `success`；符号在容器里：`ssh ubuntu 'docker exec nous-backend grep -rl find_by_promoted_resource /app/app/repositories/ && docker exec nous-backend grep -rl citations_need_agent /app/app/api/'`（两行都要有输出）；`ssh ubuntu 'docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz'` → `dbos` 字段不是 `configured_but_disabled`。
  - 前端：`curl -s https://app.nous.ink/version.json | jq -r .commitSha | cut -c1-7` == 本轮最后一个前端 PR 合并 SHA 的前 7 位。

- [ ] **Step 2: 逐条跑 spec §8 的验收表**。⚠️ spec §8 的圈号有重复（⑦⑧⑨ 各出现两次，实为 11 条）；下表按**正文顺序**重新编号 1–11，第二列给出 spec 原文的圈号，逐条一一对应，不合并、不丢项。

| # | spec §8 原号 | 判据 | 探针 | 期望观察 |
|---|---|---|---|---|
| 1 | ① | 分镜回退占号 | agent 先把 `337650953731886` 改到 v2；然后 `curl -sS -X POST "https://cn.nous.ink/api/v1/outputs/script_shot/337650953731886/revert" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"to_version":1,"expected_latest":2}'`；再 `ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "SELECT version,run_id,actor_user_id,reverted_from_version FROM run_deliverables WHERE kind='\''script_shot'\'' AND ref_id='\''337650953731886'\'' ORDER BY version"'` | 201；SQL 末行 `3||<我的 uuid>|1`（`run_id` 空）；`SELECT actor FROM script_shot_ops WHERE shot_id=337650953731886 ORDER BY id DESC LIMIT 1` → `revert:<uuid>` |
| 2 | ① 续 | 内容真回到 v1 | `curl -sS "https://cn.nous.ink/api/v1/outputs/script_shot/337650953731886/diff?from=1&to=3" -H "Authorization: Bearer $TOK" \| jq '.from.text == .to.text'` | `true`（六字段文本两侧相同） |
| 3 | ② | 场次同形 | `SCENE=$(ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "SELECT scene_id FROM script_shots WHERE id=337650953731886"')`；对 `script_scene/$SCENE` 发同样的 revert | 201；`SELECT content_version FROM script_scenes WHERE id=$SCENE` 比回退前 +1；`SELECT actor FROM script_ops WHERE scene_id=$SCENE ORDER BY id DESC LIMIT 1` → `revert:<uuid>` |
| 4 | ③ | 并发保护 | 把 #1 那条 curl **原样再发一次**（`expected_latest` 仍是 2） | 409，`.details.code == "version_conflict"`，`.details.latest_version == 3` |
| 5 | ④ | 媒体不可回退 | `GEN=$(ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "SELECT ref_id FROM run_deliverables WHERE kind='\''generated_media'\'' ORDER BY id DESC LIMIT 1"')`；`POST /api/v1/outputs/generated_media/$GEN/revert -d '{"to_version":1,"expected_latest":1}'` | 400，`.details.code == "kind_not_revertible"` |
| 6 | ⑤ | 媒体价填值并进 run 账 | 先配价：`ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -c "INSERT INTO ai_model_prices(model,provider,prompt_price,completion_price,per_call_cents,effective_at) VALUES ('\''gpt-image-2.5'\'','\''openai-images'\'',0,0,12.0,now())"'`；在 MH-96 上让 agent 生一张图；然后 `SELECT g.cost_cents, d.cost_cents FROM generated_media g JOIN run_deliverables d ON d.ref_id=g.id::text AND d.kind='generated_media' ORDER BY g.id DESC LIMIT 1` | 两列都 `12.0000`；`curl .../api/v1/runs/<run>/view \| jq '.cost.media_cents, .cost.spent_cents'` → `media_cents == 12`，`spent_cents` 含它 |
| 7 | ⑥ | 文本类读时分摊 | 一步生成两个分镜后，对两个 ref 各 `curl .../api/v1/outputs/script_shot/<ref> \| jq '.versions[0] \| {cost_cents, cost_kind}'`；再 `SELECT cost_cents FROM run_deliverables WHERE kind='script_shot' AND ref_id IN (...)` | 两个 `cost_cents` 相等且合计 == 该 `(turn,step)` 的 `step_end` 花费；`cost_kind == "allocated"`；SQL 两行都是 `NULL`（读时折叠，不回写） |
| 8 | ⑧(首次) | 回退前的人手编辑不被销毁 | 在画布上手改分镜文本（不触发 agent）→ 立即对该 shot revert 到 v1 → 查 `run_deliverables` | 多出**两**行：`v(n)` `actor_user_id=我 / reverted_from_version IS NULL`（内容 == 我刚改的），`v(n+1)` `reverted_from_version=1`；`diff?from=<n>&to=<n>` 的 `from.text` == 我改的文本 |
| 9 | ⑨(首次) | 实时去重与按键失效 | 临时 spec `frontend/e2e-prod/tmp-3b-realtime.spec.ts`：登录后打开 MH-96，`page.on('request')` 统计一次回合期间 `/api/v1/issues/349426708244346/outputs` 的命中数，并统计 `/api/v1/outputs/` 下按键的请求路径 | 一次回合 → 该 URL 恰 **1** 次重拉（轮询边沿与 WS done 被 seq 水位去重）；血缘重拉只出现在 `deliverable` 事件带的那个 `(kind, ref_id)` 上，其它键零请求。跑完 `rm frontend/e2e-prod/tmp-3b-realtime.spec.ts` |
| 10 | ⑦ | 回合结束 ≤5 s 三面自刷新 | 同一个临时 spec：右栏产出块行数 `toBeVisible` 断言新行在 5 s 内出现；关闭再打开 @ 页签断言新版可见；另开画布页断言分镜来源块 `data-versions` 从 `2` 变 `3` | 三处都在 5 s 内可见（全部用可见性断言，不用 `toHaveCount`——2026-08-12 的教训） |
| 11 | ⑧(二次) | 资源面板来源块 | promote #6 那张图到资源库 → 临时 spec `tmp-3b-resource.spec.ts` 打开该资源信息面板；另取一个人手上传的资源同样打开；再 `curl -sS "https://cn.nous.ink/api/v1/resources/<promoted id>/provenance" -H "Authorization: Bearer $TOK" \| jq '{kind, ref_id, issue_key: .versions[0].issue_key}'` | agent 图：`output-provenance` 块可见，Open Issue 可点回 MH-96；人手上传：块不存在（curl 该资源 → 404 `not_registered`）；curl agent 图 → `kind=="generated_media"`、`issue_key=="MH-96"`。跑完 `rm frontend/e2e-prod/tmp-3b-resource.spec.ts` |
| 12 | ⑨(二次) | 常规走查不回归 | `cd frontend && npm run e2e:prod` | 全绿 |

- [ ] **Step 3: 清理临时件** — `git -C <worktree> status --porcelain frontend/e2e-prod/` 必须**零输出**（两个 tmp spec 已删）。⚠️ 它们用真账号跑真栈，进仓库就会被 CI 之外的人误跑。

- [ ] **Step 4: 写完成账** — 在 `docs/superpowers/plans/2026-09-14-harness-p4-phase3b-revert-cost-realtime.md` 末尾追加（表格逐行填实测值，空格不许留 TBD——某项没跑就写「未跑，原因 X」）：

  ```markdown
  **完成账（2026-09-14）**

  九个 Task 各一个 worktree（从 `origin/master` 建）+ 独立 PR + TDD + 突变 + opus 对抗评审；
  合并后逐条盯部署链。SDD 工作区 `.superpowers/sdd/2026-09-13-harness-p4-phase3a-followups/`
  （3b 勘察两份 + 本轮 briefs / reports / 评审包）。

  | Task | PR | 合并 SHA | 评审轮次 | 部署验证 |
  |---|---|---|---|---|
  | T1 mig 466 + ORM 镜像 | #… | … | … | run-migration success；`\d run_deliverables` 见 `actor_user_id` |
  | T2 登记口扩参 + ledger_ref | #… | … | … | deploy-gpu success；容器内 `reverted_from_version`；readyz ready |
  | T3 回退端点 | #… | … | … | deploy-gpu success；容器内 `revert_output`；readyz ready |
  | T0 图片登记归因 | #… | … | … | deploy-gpu success；容器内 `result.provider` |
  | T4 花费（分摊 + 媒体价 + run 账） | #… | … | … | deploy-gpu success；容器内 `media_price_cents` |
  | T5 前端回退 + 花费显示 | #… | … | … | version.json … |
  | T6 实时信号 + 按键失效 | #… | … | … | version.json … |
  | T7 资源来源反查 | #… | … | … | deploy-gpu success + version.json … |
  | T8 小票 C17/C5/C7 | #… | … | … | deploy-gpu success；容器内 `citations_need_agent` |

  **真栈验收（spec §8 十二条）**：逐条结论 + 证据一行（PASS / UNVERIFIED + 原因）。

  **本轮新记的票**：…（进 3c 或下一轮 followups）。
  ```

- [ ] **Step 5: 文档 PR** — 完成账单独走一个 worktree：`git -C <worktree> add docs/superpowers/plans/2026-09-14-harness-p4-phase3b-revert-cost-realtime.md && git -C <worktree> commit -m "docs(plan): 三期 3b 完成账 —— 九个 Task 的 PR / SHA / 评审轮次 / 部署证据"`，推送开 PR（标题同 commit）。合并后 `gh run list -w ci.yml -L 3` 确认纯文档 PR 只跑到应跑的 job。

---

**完成账（2026-09-14）**

全部在同一天内完成：spec（#2279）→ 计划（#2280，四段并行作者 + 拼装自审）→ 11 个代码 Task 各自独立 worktree（`origin/master` 建，合并后 `rebase --onto`）+ TDD + opus 对抗评审 + 突变记录 + 合并后盯部署链（后端：deploy-gpu + 容器符号 + readyz；前端：version.json 7 位 SHA）→ 真栈验收 → 两个验收修复。SDD 工作区 `.superpowers/sdd/2026-09-14-harness-p4-phase3b-revert-cost-realtime/`（ledger 含全部裁定、briefs、reports、评审包、Task 9 证据表）。

| Task | PR | 合并 SHA | 评审轮次 | 部署验证 |
|---|---|---|---|---|
| 0 图片登记归因（两条路径写 adapter 解析出的 provider/model，provider 统一协议族键） | #2282 | 5797457c | 2 轮修复后复审 ✅ | deploy-gpu；worker 含 `_canonical_provider` / `gen_attribution.py`；readyz |
| 1 mig 466（run_id 可空 + actor/reverted_from/ledger_ref/per_call_cents）+ ORM + 真 PG | #2281 | 697b308b | 1 轮 Minor 修复 | run-migration + deploy-gpu；information_schema 三列在、`script_shot_ops.run_id` 可空 |
| 2 登记口扩参 + `ledger_ref` + `lineage_for` outer join + 人手版 issue 沿链 | #2283 | 173df184 | 1 轮修复后复审 ✅ | deploy-gpu；真栈 lineage 带 `actor_user_id` 等字段 |
| 8 无 agent 评论拒任何附件 409 `citations_need_agent` + deliverables README | #2284 | 943baf3b | 1 轮修复（守卫扩到全部附件） | deploy-gpu；backend 含 `citations_need_agent` |
| 7 `GET /resources/{id}/provenance`（不 404 不可见议题、人手版借链上议题、`as_of_seq` 字符串） | #2285 | f257ab44 | 1 轮修复后 ✅ | deploy-gpu；路由在；探针 404 `not_registered` |
| 3 Revert To vN（两臂 + 保留版 + 同事务 + 23505→409 + 真 PG） | #2287 | fc74b0df | 2 轮修复 + 1 轮 CI（choke-point 白名单）后复审 ✅ | deploy-gpu；真栈媒体对象回退 → 400 `kind_not_revertible` |
| 4 文本花费读时分摊 + 媒体目录价 + `as_of_seq: str` + `cost_kind` | #2286 | e4308abf | 1 轮修复后 ✅（真 PG RED 抓出 token-only 价目行遮蔽真价） | deploy-gpu；真栈 lineage `as_of_seq:"348431095414717"`、v7 `0.0527 / allocated` |
| 4b 媒体花费进 run 账（持久化列同口径）+ WS done 帧 `run_id/seq/outputs` + progress `last_seq` | #2289 | bc163d5b | 1 轮修复后复审 ✅ | deploy-gpu；worker 含 `output_keys_for_run` / `media_cents` |
| 5 回退弹层 + 花费四面显示 + Budget 媒体行 + `citations_need_agent` 前端半边 | #2288 | 2faedfc5 | 1 轮修复后复审 ✅ | deploy-pages；version.json 2faedfc |
| 6 seq 水位信号 + 按键失效 + 三个消费方 + 在途读不回写 | #2290 | 2602caef | 1 轮修复后复审 ✅ | deploy-pages；version.json 2602cae |
| 7b 资源信息面板来源块（类型化 404） | #2291 | cc77518f | 1 轮修复 | deploy-pages；version.json cc77518 |
| 验收修复 #9：run 车道首信号封口 | #2292 | 1c66cc36 | 自审 + CI 两次抖动重跑 | deploy-pages；version.json 1c66cc3 |
| 验收修复 A：分镜三档重建（不再抹掉未进账本的参数）+ diff 侧脱敏 + 借链上议题 | #2293 | c7f619b9 | 1 轮修复后复审 ✅ | deploy-gpu；diff.py 含 `_pre_image`；真栈 revert 无多余保留版 |

**真栈验收（Task 9，`task-9-report.md`）**：首轮 12 项中 9 PASS（分镜/场次回退占号、内容真回、并发 409、媒体 400、文本读时分摊、保留未登记编辑、三面 ≤5 s 自刷新、常规走查全绿、资源来源反查 curl）。#9 首轮 FAIL（一回合重拉两遍）→ #2292 修后 复验 5/5 PASS（每回合恰 1 次重拉，无串键请求）。最终 10 PASS / 0 FAIL / 2 BLOCKED。#6 与 #11 正例首轮 BLOCKED（生产没有能走 agent 工具链的出图 provider：`jimeng-cli` 要会员，`openai-images` 两行无 key，`codex-local` 刻意不做图，`GENMEDIA_DEFAULT_IMAGE_PROVIDER` 未设）→ 用户按 3a 同形补了 owner 私有目录行 `codex-image`（349722376420455，`actual_provider=codex` / `gpt-6-astra`）与价目行 `(gpt-6-astra, codex, per_call_cents 12)` 后重跑：**#6 PASS**（run 349737046933547 → generated_media 349737204961352 `model=gpt-6-astra provider=codex cost_cents=12`；登记行 12.0000；fold `media_cents=12`、`spent_cents=12.0762`；持久化 `agent_runs.cost_cents=12.0762`）；**#11 正例 PASS**（promote → 资源 349737450414183，`/provenance` → `generated_media` / MH-98 / `12.0 exact`；面板 Source 块「Made By An Agent · gpt-6-astra · ¢12.00」，Open Issue SPA 落到 `MH-98?step=1&turn=1`）。**最终 12 PASS / 0 FAIL / 0 BLOCKED**。探针 agent `9d38ea16-…` 刻意不删（删 agent 会级联抹掉它的 run 与血缘）。

**验收撞出的问题**：A 分镜回退抹掉未进账本的五个字段（真缺陷，#2293 修；fixture 分镜 337650953731886 的 v9 keep op 349591363508654 带修复前坏代码写的假前像，行已按原值 PATCH 补回，账本行需人手 UPDATE）；B 改 issue assignee 不重绑 session（记票）；C diff 端点人手版不借议题（#2293 顺带修）；D `/api/v1/inbox` 500（kind 枚举漂移，无关，记票）。

**裁定**（全文在 ledger，每条带代价）：`as_of_seq` = 登记行 id 字符串；文本花费读时折不回写、媒体价咽喉点填且持久化列同口径；WS 不另开帧、done 帧带 outputs；run 车道封口、seq 只在本地车道；回退前保留当前内容（只保账本可见编辑）；`revert.py`/`diff.py` 进 choke-point 白名单（照 undo 先例）；Task 1/0、3/4/7/8、4b、5 分批并发；`LATEST_MIGRATION` 不动；分摊分母含媒体卡与后端一致。

**记票**：`PATCH /shots` 应写 `script_shot_ops` 行（否则保留臂看不见人手编辑）；删 agent 级联删 run 与血缘（应软删或拒绝）；改 assignee 不重绑；`/api/v1/inbox` 500；画布等其它登记车道仍写请求侧 provider/model；`seen` 50 窗口现在也管钱；前端分母按去重卡数 vs 后端按事件数；前端 vitest 在托管 runner 偶发单例失败（AssetShelf）；第三档读在事务外与 FOR UPDATE 有窗口；资源库页底部批量操作栏（z-50）在 ≤900px 视口高度盖住信息面板来源块；`GET /api/v1/runs/{id}/view` 不存在（真实是 `/ai-library/runs/{id}/view-at?seq=`，计划文本写错）。
