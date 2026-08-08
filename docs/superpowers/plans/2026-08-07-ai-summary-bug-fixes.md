# ai_summary 链路四 bug 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-08-07 ai_summary 链路诊断发现的四个真 bug：workflow 失败被 DBOS 记成 SUCCESS、团队共享资源摘要"先扣款再失败"的身份错配、resource_summaries/resource_transcripts 两张表的 RLS join 错列、persist_summary 不写 llm_model/llm_provider 遥测。

**Architecture:** 全部后端改动 + 一个 SQL migration，单 worktree 单 PR。修复按严重度排序：A（假成功 raise，涉及共享 failure handler 的 3 个调用方）→ B（身份错配，路由派发 + 错误文案拆分 + 转写链）→ C（RLS migration）→ D（遥测列）。每个 task 独立可测、独立可 review。

**Tech Stack:** FastAPI + SQLAlchemy（异步 ORM，禁 `text()` 裸 SQL）、DBOS durable workflows、PostgreSQL RLS、pytest + aiosqlite。

## Global Constraints

- 沟通/commit message 用中文，代码与注释英文；commit 尾行 `Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP`
- 新代码禁 `text()` 裸 SQL（例外仅 `dbos.*` 与 PG 系统目录；见 `docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md`）
- 路线 C 纪律：DBOS workflow 失败必须 `raise`，不得 `return {"status":"failed"}`（CLAUDE.md「任务系统架构纪律」第 4 条——本计划 Task 1 正是修此违约）
- migration 禁 `SET ROLE service_role`（CI ephemeral role 无 grants，会自废权限）
- migration 取号前必须 `git fetch origin master` 后看 `supabase/migrations/` 最新号（worktree 存活期 master 会前进；见 memory `reference-migration-number-collision-check`）
- worktree 流程：`./scripts/worktree-manager.sh create fix/ai-summary-hardening` → `bash scripts/sync-worktree.sh` → `cd backend && uv sync`
- 后端全量测试基线：本机约 87 个 `socksio` 环境性失败不算回归（对照干净 master 的失败集）
- black / isort / flake8 对所有触碰的 py 文件必须 clean（PR #1715 曾因新增测试文件漏跑 black 挂 CI）

## 诊断背景（实证，2026-08-07/08 生产实测）

- E2E 探针触发 `ai_summary_workflow` 两次，均在 `run_summarize_agent` 撞 doubao 429 重试耗尽 → `record_workflow_failure` 返回 failed dict → **`dbos.workflow_status.status = SUCCESS`**（output pickle 里是 `status:failed`），而 `task_tracking` 显示 failed——双表不一致，正是路线 C 第 4 条警告的形态。
- `resources.creator_id` 过滤 + 派发传 `auth.user_id` + 积分按 `creator_id` 扣：三处口径不一致。
- 生产 `pg_policy` 实查：`resource_summaries` 和 `resource_transcripts` 两条策略都是 `resources.media_id = <table>.resource_id`（resource_id 存的是 `resources.id`）→ 谓词恒 false。当前后端 service_role 绕过 RLS 所以潜伏。
- `resource_summaries.llm_model` / `llm_provider` 两列从建表起恒 NULL。

---

### Task 1: workflow 失败必须 raise（Bug A — 最高优先级）

**Files:**
- Modify: `backend/app/workflows/ai_summary.py`（workflow 尾部 except 块，约 278-288 行）
- Modify: `backend/app/workflows/ai_transcription.py`（同款 `return await record_workflow_failure(...)` 尾部）
- Modify: `backend/app/workflows/visual_analysis*.py` 或 analyze_l1 所在文件（第 3 个调用方，Step 1 定位）
- Modify: `backend/app/workflows/_failure_handler.py`（docstring 的 Use 示例改为 record 后 raise）
- Test: `backend/tests/workflows/test_workflow_failure_raises.py`（新建）

**Interfaces:**
- Consumes: `record_workflow_failure(*, workflow_id, error, context) -> dict`（保持签名不变——它仍负责标 task_tracking failed + 解包 DBOSMaxStepRetriesExceeded + 日志；只是调用方不再把它的返回值当 workflow 返回值）
- Produces: 三个 workflow 在失败时 **抛出原异常**；DBOS 将 workflow 记为 ERROR，mirror trigger 把 task_tracking 同步为 failed（与 mgr.fail 幂等重合，无冲突）

- [ ] **Step 1: 定位全部调用方与下游消费者**

```bash
grep -rn "record_workflow_failure" backend/app/ backend/tests/
# 预期 3 个 workflow 调用方（ai_summary / ai_transcription / analyze_l1）+ helper 自身 + 若干测试
# 另查有没有人 await 这三个 workflow 的 handle 结果并按 dict["status"]=="failed" 分支：
grep -rn "get_result\|get_workflow_result" backend/app/ | grep -iv test
```

若发现消费者解析 failed dict：把该消费者改成 `try/except` 捕获异常（异常类型不限定，记录后按原 failed 分支处理）。若无消费者（预期如此——三条链都是 fire-and-forget 派发），直接进行下一步。

- [ ] **Step 2: 写失败测试（先红）**

```python
# backend/tests/workflows/test_workflow_failure_raises.py
"""Route-C rule 4: a DBOS workflow failure must RAISE, not return a failed
dict. Returning a dict makes DBOS record SUCCESS while task_tracking says
failed — the split-brain observed live on 2026-08-08 (wf 5a872175 / 1e63f80b).
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_ai_summary_workflow_reraises_after_recording(monkeypatch):
    from app.workflows import ai_summary as m

    async def boom(*a, **k):
        raise RuntimeError("provider 429")

    monkeypatch.setattr(m, "load_summary_inputs", boom)

    fake_mgr = AsyncMock()
    with patch(
        "app.services.infra.unified_task_manager.get_task_manager",
        return_value=fake_mgr,
    ), patch(
        "app.workflows._failure_handler.record_workflow_failure",
        new=AsyncMock(return_value={"status": "failed"}),
    ) as rec:
        with pytest.raises(RuntimeError, match="provider 429"):
            await m.ai_summary_workflow(1, "u-1")
        rec.assert_awaited_once()
```

注意：本仓库 workflow 单测有既有 harness（DBOS 装饰器在测试环境的处理方式）——先 `grep -rn "ai_summary_workflow" backend/tests/` 找现有测试文件，照它的 mock/夹具方式适配上面的测试（断言不变：**record 被调用一次 + 原异常穿透**）。为 ai_transcription 与 analyze_l1 各写一条同构测试（同文件内，mock 各自第一个 step 抛错）。

- [ ] **Step 3: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/workflows/test_workflow_failure_raises.py -v
```

预期：FAIL——当前代码 `return` 了 dict，`pytest.raises` 不满足。

- [ ] **Step 4: 修改三个调用方**

以 `ai_summary.py` 为例（另两处同构）：

```python
    except Exception as e:  # noqa: BLE001
        # Route-C rule 4: record for task_tracking/UI, then RE-RAISE so
        # DBOS records ERROR — returning the dict made DBOS mark this
        # workflow SUCCESS while task_tracking said failed (observed live
        # 2026-08-08, wf 5a872175/1e63f80b).
        await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_summary",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
        raise
```

同步更新 `_failure_handler.py` docstring 的 Use 示例（`return await record_workflow_failure(...)` → `await record_workflow_failure(...)` + `raise`），并在 docstring 第 2 点"Returns a uniform failure dict"处补一句：调用方必须 re-raise，返回值仅供日志/测试断言用。

- [ ] **Step 5: 跑测试确认通过 + 全量 workflow 套件**

```bash
cd backend && uv run pytest tests/workflows/ -v
```

预期：新测试 PASS；若 Step 1 发现的既有测试断言了旧的 return-dict 行为，改为断言 raise（这是行为修正，不是测试将就实现）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/ backend/tests/workflows/test_workflow_failure_raises.py
git commit -m "fix(workflows): 失败路径 record 后 re-raise — DBOS 不再把失败记成 SUCCESS

路线 C 第 4 条违约修复:ai_summary/ai_transcription/analyze_l1 三个
workflow 的尾部 catch 曾 return failed dict,DBOS 视为 SUCCESS,与
task_tracking 的 failed 形成双表分裂(2026-08-08 生产实测)。

Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP"
```

---

### Task 2: 摘要派发身份对齐资源属主（Bug B-1）

**Files:**
- Modify: `backend/app/api/ai_router.py`（`trigger_summary_by_resource`，派发 kwargs 约 406 行）
- Test: 既有 ai_router 端点测试文件（Step 1 定位；若无则新建 `backend/tests/api/test_ai_router_summary_identity.py`）

**Interfaces:**
- Consumes: 端点内已有的 `resource_owner = resource.get("creator_id") or auth.user_id`（积分口径，约 349 行）
- Produces: `ai_summary_workflow` 的 `user_id` kwarg == 资源属主（与积分扣费、`load_summary_inputs` 的 `creator_id` 过滤三方一致）

- [ ] **Step 1: 定位既有端点测试**

```bash
grep -rln "trigger_summary_by_resource\|summarize/resource" backend/tests/
```

- [ ] **Step 2: 写失败测试（先红）**

```python
# 加入 Step 1 找到的测试文件（或新建）
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_summary_dispatch_uses_resource_owner_not_caller(client_as_team_member):
    """Caller (team member) != resource creator. Points were already charged
    to the OWNER's team; the workflow must run as the owner too, or
    load_summary_inputs' creator_id filter finds nothing → charged-then-fail
    (the bug observed in the 2026-08-07 diagnosis)."""
    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        new=AsyncMock(),
    ) as swr:
        resp = await client_as_team_member.post(
            "/api/v1/ai/summarize/resource/900000000000002"
        )
    assert resp.status_code == 200
    kwargs = swr.await_args.kwargs["dbos_workflow_kwargs"]
    assert kwargs["user_id"] == "owner-uuid"  # 资源 creator_id,不是调用者
```

夹具 `client_as_team_member` 按 Step 1 找到的既有测试的认证/夹具模式搭：mock `_resolve_resource_to_platform_id` 返回 `creator_id="owner-uuid"` 的 resource、mock 积分与 dedup 查询。核心断言只有一个：**派发 kwargs 的 user_id 是 creator_id**。

- [ ] **Step 3: 跑测试确认失败**

```bash
cd backend && uv run pytest <Step1文件> -k owner_not_caller -v
```

预期：FAIL——当前传的是 `auth.user_id`。

- [ ] **Step 4: 修改派发行**

```python
            await start_workflow_routed(
                "ai_summary",
                dbos_workflow_callable=ai_summary_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media["id"]),
                    # Run as the resource OWNER, matching the points charge
                    # above and load_summary_inputs' creator_id filter — a
                    # team member triggering summary on a shared resource
                    # used to charge the owner then fail "no transcript"
                    # (identity mismatch, 2026-08-07 diagnosis).
                    "user_id": resource_owner,
                },
                workflow_id=wf_id,
            )
```

`tracker.create(user_id=auth.user_id, ...)` **保持不变**——task_tracking 行归触发者（任务中心显示给点按钮的人），workflow 身份归属主，两者语义不同。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd backend && uv run pytest <Step1文件> -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/ai_router.py backend/tests/
git commit -m "fix(ai): 摘要 workflow 以资源属主身份运行 — 消除共享资源'先扣款再失败'

积分按 creator_id 扣、workflow 却传 auth.user_id、查询又按
creator_id==user_id 过滤——三方口径不一致,团队成员对共享资源触发
摘要会扣了属主积分然后 no transcript 失败。对齐到属主。

Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP"
```

---

### Task 3: load_summary_inputs 报错文案拆分（Bug B-2）

**Files:**
- Modify: `backend/app/workflows/ai_summary.py`（`load_summary_inputs` 的 `if not row:` 分支，约 88-91 行；新增伴生查询函数 `_summary_inputs_exists_any_owner_stmt`）
- Test: 既有 `_summary_inputs_select_stmt` 的 aiosqlite 测试文件（`grep -rln "_summary_inputs_select_stmt" backend/tests/` 定位）

**Interfaces:**
- Consumes: 既有 `_summary_inputs_select_stmt(parsed_media_id, user_id)`
- Produces: 新纯函数 `_summary_inputs_exists_any_owner_stmt(parsed_media_id: int)` —— 同 join 链但**无** `creator_id` 过滤，`select(Resources.id)` 即可；`load_summary_inputs` 在查空时用它区分两种失败

- [ ] **Step 1: 写失败测试（先红）**

```python
# 加入既有 aiosqlite 测试文件
@pytest.mark.asyncio
async def test_no_row_distinguishes_foreign_owner_from_missing_transcript(
    session_factory,
):
    """'no transcript' and 'owned by someone else' used to share one
    message, sending the 2026-08-07 diagnosis down the wrong path
    (transcript existed; the tenant filter hid it)."""
    # fixture: transcript EXISTS but resource.creator_id = "other-user"
    async with session_factory() as s:
        await _seed_media_resource_transcript(s, creator_id="other-user")

    from app.workflows.ai_summary import load_summary_inputs

    with pytest.raises(RuntimeError, match="not owned by"):
        await load_summary_inputs(SEEDED_PM_ID, "caller-user")


@pytest.mark.asyncio
async def test_no_row_when_transcript_truly_missing(session_factory):
    async with session_factory() as s:
        await _seed_media_resource_without_transcript(s, creator_id="caller-user")

    from app.workflows.ai_summary import load_summary_inputs

    with pytest.raises(RuntimeError, match="no transcript"):
        await load_summary_inputs(SEEDED_PM_ID, "caller-user")
```

seed 帮助函数按该测试文件既有的建行方式写（它已有 ParsedMedia/Resources/ResourceTranscripts 的 aiosqlite 建表与插行逻辑——照抄结构改值）。

- [ ] **Step 2: 跑测试确认失败**

预期：第一条 FAIL（现在两种情况同一个 "no transcript" 文案）。

- [ ] **Step 3: 实现拆分**

```python
def _summary_inputs_exists_any_owner_stmt(parsed_media_id: int):
    """Same join chain as _summary_inputs_select_stmt but WITHOUT the
    creator filter — used only to tell 'transcript missing' apart from
    'resource owned by someone else' in the error path."""
    from app.models import ParsedMedia, Resources, ResourceTranscripts

    return (
        select(Resources.id)
        .join(ParsedMedia, Resources.media_id == ParsedMedia.id)
        .join(ResourceTranscripts, ResourceTranscripts.resource_id == Resources.id)
        .where(ParsedMedia.id == parsed_media_id)
        .where(ResourceTranscripts.full_text.is_not(None))
        .limit(1)
    )
```

`load_summary_inputs` 的空行分支替换为：

```python
    if not row:
        # Two failure modes shared one message and misled the 2026-08-07
        # diagnosis. Probe once without the tenant filter to tell them
        # apart. SYSTEM scope: this cross-tenant existence check is the
        # whole point of the probe; it leaks nothing beyond a boolean.
        from contextlib import nullcontext

        from app.db.scope import is_enforced, system_request_scope

        probe_cm = (
            system_request_scope(
                reason="ai-summary error diagnostics: distinguish missing "
                "transcript from foreign-owned resource"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with probe_cm:
            async with read_scope() as session:
                foreign = (
                    await session.execute(
                        _summary_inputs_exists_any_owner_stmt(parsed_media_id)
                    )
                ).first()
        if foreign:
            raise RuntimeError(
                f"resource for parsed_media={parsed_media_id} exists but is "
                f"not owned by user={user_id} — dispatch identity mismatch, "
                f"not a missing transcript"
            )
        raise RuntimeError(
            f"no transcript for parsed_media={parsed_media_id} user={user_id}"
        )
```

注意 import 对齐该文件现状：`request_scope, Scope` 已从 `app.db.scope` 导入，`is_enforced` / `system_request_scope` 与 `persist_summary` 里的用法同源（照抄它的 import 位置）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest <该测试文件> -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/ai_summary.py backend/tests/
git commit -m "fix(ai): 摘要输入查空时区分'无转写'与'资源属他人' — 终结误导性报错

Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP"
```

---

### Task 4: 转写链摘要派发也对齐属主（Bug B-3）

**Files:**
- Modify: `backend/app/tasks/download_helpers.py`（`chain_summary_for_tags`，337 行起）
- Test: 既有 chain_summary_for_tags 测试（`grep -rln "chain_summary_for_tags" backend/tests/` 定位；无则在 `backend/tests/tasks/` 新建）

**Interfaces:**
- Consumes: `ResourcesRepository` —— Step 1 先查有没有"按 media_id 不限 creator"的查法；没有就在 repo 加 `get_resource_by_media_id(media_id: str) -> dict | None`（ORM select，`where(Resources.media_id == int(media_id)).limit(1)`，字段集与 `get_resource_by_media_id_and_creator` 相同）
- Produces: `chain_summary_for_tags` 派发 `ai_summary_workflow` 时 `user_id=resource["creator_id"]`；resource 查不到时打 info 日志（不再完全静默）

- [ ] **Step 1: 侦察 repo 现有方法与测试**

```bash
grep -n "def get_resource_by" backend/app/repositories/resources_repository.py
grep -rln "chain_summary_for_tags" backend/tests/
```

- [ ] **Step 2: 写失败测试（先红）**

```python
@pytest.mark.asyncio
async def test_chain_dispatches_as_resource_creator(monkeypatch):
    """Transcription can be triggered by a teammate; the summary chain must
    still run as the resource CREATOR (whose creator_id filter the workflow
    applies), not the transcription caller."""
    # mock: media exists; resource creator_id="owner-1"; tag list 含 "Summary"
    # mock start_workflow_routed,断言 kwargs["user_id"] == "owner-1"
    #(mock 方式照该测试文件既有 chain 测试的形式;无既有则用 unittest.mock.patch
    # 对 download_helpers 内 import 的各依赖打点)
    ...
    assert swr.await_args.kwargs["dbos_workflow_kwargs"]["user_id"] == "owner-1"
```

Step 1 若发现既有 chain 测试，在其文件内照它的 mock 风格补全上述断言（核心只有一条：派发身份 == creator）；若无既有测试，完整 patch `MediaRepository.get_by_id` / resource 查询 / `read_resource_tag_names` / `get_task_manager` / `start_workflow_routed`。

- [ ] **Step 3: 实现**

`chain_summary_for_tags` 内：

```python
        res_repo = ResourcesRepository()
        resource = await res_repo.get_resource_by_media_id(str(parsed_media_id))

        if not resource:
            logger.info(
                f"[AI] summary chain: no resource for parsed_media "
                f"{parsed_media_id}; skipping summary"
            )
            return
        resource_id = str(resource["id"])
        # Dispatch as the resource creator — the workflow's creator_id
        # filter and the points ledger both use this identity. The chain's
        # inbound user_id (the transcription caller) may be a teammate.
        owner_id = str(resource.get("creator_id") or user_id)
```

后续派发处 `ai_summary_workflow` 的 kwargs `"user_id": owner_id`（原来是 `user_id`）。若 Step 1 发现 repo 无按 media_id 的方法，新增（ORM，无 `text()`）：

```python
    async def get_resource_by_media_id(self, media_id: str) -> dict | None:
        """Resource row by parsed_media id, creator-agnostic — for chains
        that must resolve the OWNER rather than assume the caller is the
        owner (chain_summary_for_tags)."""
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(Resources)
                        .where(Resources.media_id == int(media_id))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        return _resource_to_dict(row) if row else None
```

（行转 dict 的方式照该 repo 既有方法——如果它们用 `row.__dict__` 或专用序列化函数，照抄同款；`read_scope` 若在该 repo 受 scope 约束，参考 `get_resource_by_media_id_and_creator` 的 scope 处理方式并按其形式声明 system/reason。）

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest <chain 测试文件> -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/tasks/download_helpers.py backend/app/repositories/resources_repository.py backend/tests/
git commit -m "fix(ai): 转写链摘要派发以资源属主身份运行,查不到资源不再静默跳过

Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP"
```

---

### Task 5: RLS join 错列修复 migration（Bug C — 两张表）

**Files:**
- Create: `supabase/migrations/<下一号>_fix_resource_ai_rls_join.sql`（取号见 Step 1）

**Interfaces:**
- Consumes: 生产现状两条策略（083 建，join 用了 `resources.media_id`）
- Produces: 两条同名策略，join 改为 `resources.id = <table>.resource_id`

- [ ] **Step 1: 取号（必须先 fetch）**

```bash
git fetch origin master
ls supabase/migrations/ | grep -oP '^\d+' | sort -n | tail -1
# 取"最新号+1"。写入文件名与文件头注释;若执行时已被占用,重取并连带改注释。
```

（计划撰写时最新为 408，预期取 409——以执行时实际为准。）

- [ ] **Step 2: 写 migration**

```sql
-- <N>_fix_resource_ai_rls_join.sql
-- 083 建的两条 RLS 策略 join 错列:resource_summaries.resource_id 与
-- resource_transcripts.resource_id 存的都是 resources.id,策略却拿去比
-- resources.media_id → 谓词恒 false(2026-08-07 生产 pg_policy 实查确认)。
-- 当前潜伏:后端走 service_role 绕过 RLS;一旦前端直连 Supabase 读这两表,
-- 属主将看不到自己的任何摘要/转写。
-- 修法:join 改回主键列。保留生产已有的 (SELECT auth.uid()) initplan 形式。

BEGIN;

DROP POLICY IF EXISTS "Manage summaries of owned resources" ON resource_summaries;
CREATE POLICY "Manage summaries of owned resources" ON resource_summaries
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.id = resource_summaries.resource_id
      AND resources.creator_id = (SELECT auth.uid())
  ));

DROP POLICY IF EXISTS "Manage transcripts of owned resources" ON resource_transcripts;
CREATE POLICY "Manage transcripts of owned resources" ON resource_transcripts
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.id = resource_transcripts.resource_id
      AND resources.creator_id = (SELECT auth.uid())
  ));

COMMIT;
```

- [ ] **Step 3: 本地对一次性库验证**

```bash
# 起一次性 pg 按 schema-drift workflow 口径 apply(ci_bootstrap + baseline +
# watermark 以上全部 migration + 本文件),确认无报错;或最低限度:
docker run --rm -d --name migprobe -e POSTGRES_PASSWORD=x -p 55499:5432 pgvector/pgvector:pg17
# apply baseline 后跑本 migration,然后:
psql -h 127.0.0.1 -p 55499 -U postgres -c \
  "SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy
   WHERE polrelid IN ('resource_summaries'::regclass,'resource_transcripts'::regclass)"
# 断言输出里是 resources.id = ...,不再有 media_id
docker rm -f migprobe
```

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/
git commit -m "fix(db): resource_summaries/transcripts RLS join 错列修复 — media_id 改回 id

083 起两条策略谓词恒 false,service_role 掩盖至今;前端直连即暴露。

Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP"
```

---

### Task 6: persist_summary 补写 llm_model / llm_provider（Bug D）

**Files:**
- Modify: `backend/app/workflows/ai_summary.py`（`run_summarize_agent` 返回 dict、`persist_summary` 签名与 insert、workflow 透传）
- Test: 既有 persist_summary / run_summarize_agent 测试文件（`grep -rln "persist_summary" backend/tests/` 定位）

**Interfaces:**
- Consumes: `run_summarize_agent` 的入参 `provider_key: str` 与 `provider_config: dict`（`provider_config["model"]` 即实际使用的模型名，来自 `resolve_summarization_config`）
- Produces: `run_summarize_agent` 返回 dict 新增 `llm_model: str` / `llm_provider: str`；`persist_summary` 新增同名 keyword 参数并写入 `ResourceSummaries.llm_model` / `llm_provider`（含 on_conflict 更新集）

- [ ] **Step 1: 写失败测试（先红）**

```python
@pytest.mark.asyncio
async def test_persist_summary_writes_llm_telemetry(session_factory):
    """llm_model / llm_provider were NULL since the table was created —
    the summarize step knows both; persist must record them."""
    from app.workflows.ai_summary import persist_summary

    await persist_summary(
        SEEDED_PM_ID,
        resource_id=str(SEEDED_RESOURCE_ID),
        summary="s",
        key_points=[],
        topics=[],
        llm_model="doubao-seed-2-0-pro-260215",
        llm_provider="doubao",
    )
    async with session_factory() as s:
        row = (
            await s.execute(
                select(ResourceSummaries).where(
                    ResourceSummaries.resource_id == SEEDED_RESOURCE_ID
                )
            )
        ).scalar_one()
    assert row.llm_model == "doubao-seed-2-0-pro-260215"
    assert row.llm_provider == "doubao"
```

（seed 与 session_factory 照该测试文件既有 persist 测试的夹具。）

- [ ] **Step 2: 跑测试确认失败**

预期：FAIL——`persist_summary() got an unexpected keyword argument 'llm_model'`。

- [ ] **Step 3: 实现**

`run_summarize_agent` 返回 dict 追加两 key：

```python
    return {
        "summary": result.summary,
        "key_points": result.key_points,
        "topics": result.topics,
        # Telemetry for resource_summaries.llm_model/llm_provider — both
        # columns were NULL since the table was created (2026-08-07 diag).
        "llm_model": (provider_config or {}).get("model", "") or "",
        "llm_provider": provider_key or "",
    }
```

`persist_summary` 签名追加 `llm_model: str = "", llm_provider: str = ""`；insert values 追加 `llm_model=llm_model or None, llm_provider=llm_provider or None`；`on_conflict_do_update` 的 `set_` 追加两列（`insert_stmt.excluded.llm_model` / `.llm_provider`）。workflow 调用处透传：

```python
        result = await persist_summary(
            parsed_media_id,
            resource_id=inputs["resource_id"],
            summary=agent_out["summary"],
            key_points=agent_out["key_points"],
            topics=agent_out["topics"],
            llm_model=agent_out.get("llm_model", ""),
            llm_provider=agent_out.get("llm_provider", ""),
        )
```

（`.get()` 兜底：DBOS step 结果可能来自旧版本缓存重放，缺 key 不许炸。）

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest <该测试文件> tests/workflows/ -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/ai_summary.py backend/tests/
git commit -m "feat(ai): persist_summary 记录 llm_model/llm_provider — 补齐建表以来恒 NULL 的遥测列

Claude-Session: https://claude.ai/code/session_01B8wVTvyCuwgmnAsG9iWrAP"
```

---

### Task 7: 全量验证 + PR

**Files:** 无新改动——验证与交付。

- [ ] **Step 1: lint 三件套（对本 PR 全部触碰 py 文件，用 CI 口径选文件）**

```bash
cd backend
FILES=$(git diff --name-only --diff-filter=AMR origin/master...HEAD -- 'backend/**' | grep '\.py$' | sed 's|^backend/||')
uv run black --check $FILES && uv run isort --check-only $FILES && uv run flake8 $FILES
# zsh 下 $FILES 不按空格拆词——用 sh -c 或数组(PR #1725 实测教训)
```

- [ ] **Step 2: 后端全量**

```bash
uv run pytest 2>&1 | tail -5
# 失败集须与干净 master 一致(~87 条 socksio 环境性失败);不一致的每一条都要解释
```

- [ ] **Step 3: push + PR（不合并）**

```bash
git push -u origin fix/ai-summary-hardening
gh pr create --title "fix(ai): ai_summary 链路四 bug 修复 — 假成功/身份错配/RLS 错列/遥测缺口" \
  --body "$(cat <<'EOF'
2026-08-07 ai_summary 链路诊断的四个真 bug 修复(诊断报告见会话记录):

1. **workflow 假成功**(路线C第4条违约):ai_summary/ai_transcription/analyze_l1
   失败路径 record 后 re-raise,DBOS 不再把失败记成 SUCCESS
2. **身份错配**:摘要派发(路由+转写链)以资源属主身份运行,消除团队共享
   资源"先扣款再失败";报错文案拆分"无转写"vs"资源属他人"
3. **RLS join 错列**:resource_summaries/resource_transcripts 两条策略
   media_id→id(083 起谓词恒 false,service_role 掩盖至今)
4. **遥测缺口**:persist_summary 补写 llm_model/llm_provider

⚠️ 含 migration:run-migration.yml 与 deploy-gpu.yml 无顺序保证,但本
migration 仅改 RLS 策略,与代码无相互依赖,先后皆可。
EOF
)"
```

- [ ] **Step 4: 报告 main 会话（CI 判别真假红后合并部署）**

---

## Self-Review 记录

- **覆盖**：诊断报告四个 bug ↔ Task 1(A)/2+3+4(B 三面)/5(C 两张表)/6(D)，逐一对应；诊断附带的"B 修复方向推荐第一个（派发传属主）"被 Task 2/4 采纳，"RuntimeError 文案拆开"被 Task 3 采纳。
- **占位扫描**：Task 2 Step 2 与 Task 4 Step 2 的测试留有"照既有夹具适配"的指令——这是刻意的（该仓库 workflow/endpoint 测试 harness 复杂且在演进，硬编码夹具反而会错），但断言本体都已写死。其余无 TBD/TODO。
- **类型一致**：`llm_model/llm_provider` 在 Task 6 三处（step 返回 dict key、persist 签名、透传）拼写一致；`resource_owner`（Task 2）与 `owner_id`（Task 4）各自局部命名，接口面（workflow kwarg `user_id`）一致。
- **风险注记**：Task 1 改的是共享 failure handler 的行为契约，Step 1 的消费者扫描是安全网；DBOS ERROR 状态由 mirror trigger 同步 task_tracking，与 mgr.fail 幂等重合已在设计里确认。
