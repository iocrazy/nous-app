# harness 三期 3a「产出与血缘：登记口 + 版本链 + 三处消费面」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent 写出去的每一件东西都经同一个登记口落账，带版本链与血缘（谁 / 哪个 issue / 哪个 run / 第几步 / 模型 / 花费）；用户在三处消费它——线程里的产出卡与 Cockpit 计数、作曲区 @引用产出、对象页与 Generated 卡的来源反查；顺手关掉两张小票（测试顺序假红、`?slug=` 过滤失效）。

**Architecture:** 登记口 `register_deliverable()` 是唯一入口，**叠在**既有的 `register_generated_media()` 与脚本网关的写入点上（不并列、不改调用点签名），一次调用做三件事：算版本 → 插 `run_deliverables` 行 → 经 `events.emit` 在 run 的 transcript 上落一条 `deliverable` 事件。`run_id` 为空即 no-op，所以人手改动与前端生成路径零影响。分镜出图/出视频走 DBOS，run 上下文在派发时就丢了，本期由派发方把 `{run_id, turn, step}` 放进 workflow payload、workflow 内回填 origin；事件写在可能已结束的父 run 上，复用 workforce 写 `subagent_done` 的 `RunEventWriter.for_run` 路径。折叠只落计数（`view.outputs`），清单一律查表——表是唯一真相，不写 `metadata_json.lineage`。前端照 2b-2 子代理卡的整条线：`foldEvents` 新 case → 步内产出卡 → Cockpit 第 5 格 → 右栏「产出」块（order 25，与既有「交付物」块并存）→ 差异弹层；@引用只加一个页签、一种附件类型 `output_ref`、一个映射分支，模型侧新增 `<referenced_outputs>` 框（登记 `OWNED_FRAMES`、标题转义）。

**Tech Stack:** FastAPI + SQLAlchemy async（新 SQL 一律 ORM，禁 `text()`）、DBOS（**绝不在 `@DBOS.step` 内 enqueue**）、pytest；React 19 + vitest/RTL、tiptap（@ 管线）、i18n `t(key, fallback)` en/zh 同改、语义色 token（产出 ok / 修订 warn / 引用 agent）。

**Spec:** `docs/superpowers/specs/2026-09-10-harness-p4-phase3a-outputs-lineage-design.md`

## Global Constraints

- **登记口是唯一入口**：`generated_media` 的 INSERT 只许出现在 `register_generated_media` 内；agent 对 `script_shots` / `script_scenes` / `script_chapters` 的写只许经登记版本。每个 Task 新增写路径都要问一句「它登记了吗」。
- **`run_id` 为空 ⇒ 零行零事件**（人手改动不占版本号）。每个接线点都要有这条负向测试。
- **版本唯一**：`UNIQUE (kind, ref_id, version)`；登记口捕获 `IntegrityError` 后**重算一次**，二次冲突照抛。
- **不写 `metadata_json.lineage`**（对总 spec 的刻意偏差，spec §2.4）；视图只落计数。
- **模型可见面**：新增 `<referenced_outputs>` 框必须同批登记进 `OWNED_FRAMES`（否则 `tests/services/ai/prompts/test_frame_escape_wiring.py` 拒绝），标题走 `escape_frame_attr`；`prompts/README.md` 的 Model Experience 三问补一段；全文 pin `test_system_message_pin.py` 有 diff 就读 diff 再刷新。
- 每 Task 独立 worktree：`cd <主检出> && bash scripts/worktree-manager.sh create feat/p4-3a-tN` **单独一条 Bash**，建完立即 `git -C <wt> fetch origin && git -C <wt> reset --hard origin/master && git -C <wt> branch --unset-upstream`；前端 worktree 需 `ln -s <主检出>/frontend/node_modules <wt>/frontend/node_modules`；写操作一律 `git -C <绝对路径>`，同一轮最多一条依赖 cwd 的 Bash；主检出只读。
- 独立 PR；PR 描述必有「复用 / 删除了什么」「偏差」「对抗评审」「突变记录」「测试」。推送用显式 lease；PR 一出来先看 `gh pr view N --json mergeable,mergeStateStatus`，DIRTY 先修基底。
- TDD：每个关键断言先红后绿；每 Task 至少一处突变让测试转红并记进 PR。对抗评审用 `Agent`（model: opus），发现全修。
- 后端全量 `cd <wt>/backend && uv run pytest -q -p no:cacheprovider --deselect tests/api/test_distribution_music_search.py --deselect tests/api/test_distribution_topic_suggest.py tests`；前端 `cd <wt>/frontend && npx vitest run`。
- lint：后端 `flake8 app tests` 零告警 + isort/black/ruff 改动文件；前端 `npx eslint <改动文件>` + `npx tsc --noEmit`。
- 迁移与消费代码分 PR（ORM 镜像除外——schema-drift 门禁两向零容忍，拆不开）；迁移不 `SET ROLE`；取号前 `git fetch` 重扫，461 之后是 462。
- 边界 mock 用真实 wire 形状（Snowflake id 在不同 router 上 number / string 不同，照抄后端真实返回）；新触发路径必须带类型化失败回显（`details.code`，不是裸 `detail`）。
- UI 文案英文、Title Case；新 key en/zh 同加；不引入旧色相类名（用 ok/warn/danger/info/agent）。
- CI 绿即合并并盯两条部署链（`deploy-gpu.yml` self-hosted + `deploy-pages.yml`），合并后探 `readyz` 与容器内符号。
- 偏离本计划或 spec，回写两处「实施记录」。

---
## 文件地图（先定边界，再切 Task）

**新建**

| 文件 | 职责 | 谁用 |
|---|---|---|
| `supabase/migrations/462_harness_p4_phase3a_deliverables.sql` | `run_deliverables` 加五列 + 两索引；`agent_run_inbox` dedupe 唯一索引（小票） | T1 |
| `backend/app/services/deliverables/__init__.py` | 只导出 `register_deliverable` / `DeliverableKind` | T2 |
| `backend/app/services/deliverables/registry.py` | **唯一入口**：算版本 → 插行 → 落事件；`run_id` 空即 no-op | T2 |
| `backend/app/services/deliverables/kinds.py` | `DeliverableKind` 字面量 + 每类的 `title_for` / `ref_id_of`（纯函数） | T2 |
| `backend/app/repositories/run_deliverables_repository.py` | ORM 读写：`insert_version` / `latest_version` / `list_for_issue` / `lineage_for` | T2、T3 |
| `backend/app/services/ai/runner/folds/deliverables.py` | `deliverable` → `view.outputs{total,revised,last}`，顺序无关、去重 | T2 |
| `backend/app/api/outputs_router.py` | `GET /outputs/{kind}/{ref_id}`、`/diff` | T3 |
| `backend/app/schemas/outputs.py` | `OutputItem` / `OutputVersion` / `OutputLineage` / `OutputDiff` | T3 |
| `backend/app/services/deliverables/diff.py` | 文本类取两版可渲染文本；媒体类取两行 URL | T3 |
| `frontend/components/Todolist/blocks/OutputsBlock.tsx` | 右栏「产出」只读清单（`zone:'context'`, order 25） | T5 |
| `frontend/components/Todolist/OutputDiffDialog.tsx` | 差异弹层（文本并排 / 媒体左右） | T5 |
| `frontend/components/Todolist/outputDiff.ts` | 词级 diff 纯函数（无依赖，自带测试） | T5 |
| `frontend/services/outputsService.ts` | 三个端点的客户端 + `ScopeError` 类型化错误 | T5、T6 |
| `frontend/components/chat/useMentionOutputsTab.ts` | @ 弹层「产出」页签数据源（照 `useMentionAssetsTab.ts`） | T6 |
| `frontend/components/agentActivity/OutputProvenance.tsx` | 对象页通用「来源 / 版本链 / 花费」小块 | T6 |

**修改**

| 文件 | 改什么 | Task |
|---|---|---|
| `backend/app/models/agents.py` | `RunDeliverables` 补五列；`AgentRunInbox.__table_args__` 补唯一索引 | T1 |
| `backend/tests/models/test_transcript_event_types_phase2a.py` | `LATEST_MIGRATION` → 462 | T1 |
| `backend/app/services/library/generated_media_service.py` | 插行成功后调 `register_deliverable`（origin 已有 run/model/cost） | T2 |
| `backend/app/workflows/script_shot_generate.py` / `script_shot_video.py` | 入参接 `run_id/turn/step`，回填 `GenerationOrigin` | T2 |
| 两条 workflow 的派发方（`enqueue` 处） | payload 带 `{run_id, turn, step}` | T2 |
| `backend/app/services/ai/scope/scoped_script_gateway.py` | 四个写点登记 `script_shot` | T2 |
| `backend/app/repositories/script_scene_repository.py` | 两个写点登记 `script_scene` | T2 |
| `backend/app/workflows/script_ai_workflows.py` | 章节改写 / 分支登记 `script_chapter`（需透传 run_id） | T2 |
| `backend/app/services/ai/runner/run_projection.py` | 末尾 import 加 `deliverables` | T2 |
| `backend/app/api/issues_router.py` | `GET /issues/{id}/outputs` | T3 |
| `backend/app/main.py` | 注册 `outputs_router` | T3 |
| `backend/app/services/library/generated_source.py` | `agent_run` 分支补 issue/run/step + deep_link | T3 |
| `backend/app/schemas/generated.py` | `GeneratedSource` 补字段 | T3 |
| `backend/app/api/issue_messages_router.py` + 附件解析 | 新附件类型 `output_ref`，不可解析 → 400 `output_ref_unresolvable` | T4 |
| `backend/app/services/ai/prompts/prompt_composer.py` | 渲染 `<referenced_outputs>` | T4 |
| `backend/app/boundary/frame_markers.py` | `OWNED_FRAMES` 加新框 | T4 |
| `backend/app/services/ai/prompts/README.md` | Model Experience 三问补段 | T4 |
| `frontend/components/agentActivity/TrajectoryRenderer/foldEvents.ts` | `deliverable` case + `StepNode.outputs` | T5 |
| `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx` | 产出卡渲染 | T5 |
| `frontend/components/TaskCenter/runView.ts` | `RunOutputs` 类型 + `outputsState` selector | T5 |
| `frontend/components/Todolist/blocks/CockpitBlock.tsx` | 第 5 格「产出」 | T5 |
| `frontend/components/Todolist/blocks/index.ts` | 注册 `outputsBlock` | T5 |
| `frontend/components/Todolist/IssueReplyBox.tsx` + `composerAttachmentPayload.ts` | 「产出」页签 + `output_ref` 映射分支 | T6 |
| `frontend/services/issueMessageService.ts` | `IssueMessageAttachment` 加 `output_ref` | T6 |
| `frontend/components/resources/generated/GeneratedCard.tsx` | 来源行可点 | T6 |
| `frontend/public/locales/{en,zh}.json` | 新 key | T5、T6 |

**删除**：无（「交付物」块与 `DeliverablesZone` 原样保留，两块并存）。

---
### Task 1: 迁移 462 —— 产出表补列与唯一索引 + 收件箱 dedupe 唯一索引

**Files:**
- Create: `supabase/migrations/462_harness_p4_phase3a_deliverables.sql`
- Modify: `backend/app/models/agents.py`（`RunDeliverables` 补列；`AgentRunInbox.__table_args__` 补唯一索引）
- Modify: `backend/tests/models/test_transcript_event_types_phase2a.py:32`（`LATEST_MIGRATION` → 462）
- Modify: `backend/app/repositories/agent_run_inbox_repository.py`（`enqueue` 捕获唯一冲突 → 回读复用）
- Test: `backend/tests/db/test_migration_462_deliverables.py`、`backend/tests/repositories/test_inbox_dedupe_race.py`

**Interfaces:**
- Consumes: 453 建好的 `run_deliverables`（`id/run_id/seq/kind/ref_id/version/parent_version/created_at`）；461 已放行的 `deliverable` 事件类型。
- Produces: 列 `title/model/cost_cents/turn/step`；约束 `run_deliverables_kind_ref_version_key`（T2 的并发闸门靠它）；索引 `idx_run_deliverables_ref_latest`（T3 的「取最新版」靠它）；`agent_run_inbox_dedupe_live_key`（T2/T4 的投递幂等靠它）。

- [x] **Step 1: 取号并确认 461 仍是最后一个**

```bash
cd /Volumes/program/project-code/repos/nous-app && git fetch origin
ls supabase/migrations | tail -3   # 期望最后一个是 461_harness_p4_phase2b2_orchestration.sql
```
若已出现 462，本 Task 全部编号顺延为下一个空号，并同步改 `LATEST_MIGRATION`。

- [x] **Step 2: 写迁移（照 461 的幂等写法，不 SET ROLE）**

```sql
-- 462: harness 第五轮 · 三期 3a —— 产出登记（列与唯一约束）
-- spec: docs/superpowers/specs/2026-09-10-harness-p4-phase3a-outputs-lineage-design.md §3
-- 表与事件类型在 453/461 已就位；本迁移只补血缘列与两个约束，外加一张 2b-2 遗留小票。
-- 照 459/460/461 的 DROP/ADD 幂等写法；不 SET ROLE（以连接角色 postgres 跑）。
BEGIN;

-- (a) 血缘列 -------------------------------------------------------
ALTER TABLE public.run_deliverables
  ADD COLUMN IF NOT EXISTS title      TEXT,
  ADD COLUMN IF NOT EXISTS model      TEXT,
  ADD COLUMN IF NOT EXISTS cost_cents NUMERIC(12,4),
  ADD COLUMN IF NOT EXISTS turn       INTEGER,
  ADD COLUMN IF NOT EXISTS step       INTEGER;

COMMENT ON COLUMN public.run_deliverables.title IS
  '462: 展示用标题，登记时截断至 120 字符（与 inbox 的 clip 同族，事件与列同一份）。';
COMMENT ON COLUMN public.run_deliverables.cost_cents IS
  '462: 这一版产出自身的花费；run 级总账仍在 agent_runs.cost_cents，两者不互相推导。';

-- (b) 版本唯一：并发登记同一对象必须有一个失败并重算 -----------------
CREATE UNIQUE INDEX IF NOT EXISTS run_deliverables_kind_ref_version_key
  ON public.run_deliverables (kind, ref_id, version);

-- (c) 取最新版 / 读版本链 -------------------------------------------
CREATE INDEX IF NOT EXISTS idx_run_deliverables_ref_latest
  ON public.run_deliverables (kind, ref_id, version DESC);

-- (d) 小票（2b-2 遗留）：收件箱 dedupe 的并发唯一索引 ----------------
-- dedupe_key 住在 content jsonb 里（无列、无迁移是当时的刻意选择）；
-- 唯一性只对 LIVE 行成立：expired_at 非空的行没人消费过，允许重新排队，
-- 与 dedupe_lookup_stmt 的 WHERE expired_at IS NULL 严格同口径。
CREATE UNIQUE INDEX IF NOT EXISTS agent_run_inbox_dedupe_live_key
  ON public.agent_run_inbox (target_kind, target_id, (content->>'dedupe_key'))
  WHERE content ? 'dedupe_key' AND expired_at IS NULL;

COMMIT;
```

- [x] **Step 3: 写会失败的迁移测试**

```python
# backend/tests/db/test_migration_462_deliverables.py
"""462 的形状由这里钉住：列、两个索引、幂等写法、不 SET ROLE。
照 tests/db/test_migration_461_orchestration.py 的读文件断言风格——
不连库，纯读 SQL 文本，CI 的 schema-drift 才是真执行方。"""
from pathlib import Path

MIG = (
    Path(__file__).resolve().parents[3]
    / "supabase/migrations/462_harness_p4_phase3a_deliverables.sql"
)


def test_adds_the_five_lineage_columns():
    sql = MIG.read_text(encoding="utf-8")
    for col in ("title", "model", "cost_cents", "turn", "step"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql.replace("  ", " ")


def test_version_uniqueness_is_scoped_to_the_object():
    sql = MIG.read_text(encoding="utf-8")
    assert "run_deliverables_kind_ref_version_key" in sql
    assert "(kind, ref_id, version)" in sql


def test_inbox_dedupe_index_only_covers_live_rows():
    """一个不带 WHERE 的唯一索引会把「过期后重新排队」这条合法路径变成冲突。"""
    sql = MIG.read_text(encoding="utf-8")
    assert "agent_run_inbox_dedupe_live_key" in sql
    assert "WHERE content ? 'dedupe_key' AND expired_at IS NULL" in sql


def test_no_set_role_and_single_transaction():
    sql = MIG.read_text(encoding="utf-8")
    assert "SET ROLE" not in sql.upper()
    assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1
```

- [x] **Step 4: 跑它，确认红**

Run: `cd backend && uv run pytest tests/db/test_migration_462_deliverables.py -q`
Expected: FAIL（文件不存在 / 断言不满足）→ 写完 Step 2 的文件后再跑，全绿。

- [x] **Step 5: ORM 镜像（同 PR，门禁不许拆）**

`backend/app/models/agents.py` 的 `RunDeliverables` 追加：

```python
    title: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    cost_cents: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 4))
    turn: Mapped[Optional[int]] = mapped_column(Integer)
    step: Mapped[Optional[int]] = mapped_column(Integer)
```
`__table_args__` 里两个新索引（与迁移同名，否则 schema-drift 两向报警）：

```python
        Index(
            "run_deliverables_kind_ref_version_key",
            "kind",
            "ref_id",
            "version",
            unique=True,
        ),
        Index(
            "idx_run_deliverables_ref_latest",
            "kind",
            "ref_id",
            text("version DESC"),
        ),
```
`AgentRunInbox.__table_args__` 追加（表达式索引 + partial，照上面同名）：

```python
        Index(
            "agent_run_inbox_dedupe_live_key",
            "target_kind",
            "target_id",
            text("(content->>'dedupe_key')"),
            unique=True,
            postgresql_where=text("content ? 'dedupe_key' AND expired_at IS NULL"),
        ),
```
`decimal` / `Numeric` 若文件顶部未导入则一并加。

- [x] **Step 6: 迁移号钉住测试推进**

`backend/tests/models/test_transcript_event_types_phase2a.py:32` 改成 462 的路径，并把注释里的「461 admitted…」续写一句「462 只补列，不动白名单」。⚠️ 462 里**没有** `ARRAY[...]`，而 `_first_array` 断言恰好一个——所以这个常量只能指向**最近一次重写白名单的迁移**。结论：**本 Task 不动它**，只在 462 的头注释里写明「不重写事件白名单」，并加一条测试锁住这个前提：

```python
def test_462_does_not_touch_the_event_allowlist():
    """白名单的最新声明仍是 461；462 若重写了它而 LATEST_MIGRATION 没跟上，
    ORM 与迁移的比对就会拿旧集合过关——这里先把前提钉死。"""
    sql = (MIGRATION.parent / "462_harness_p4_phase3a_deliverables.sql").read_text(
        encoding="utf-8"
    )
    assert "agent_run_transcript_events" not in sql
```

- [x] **Step 7: 收件箱 enqueue 捕获唯一冲突（让索引真的有用）**

`agent_run_inbox_repository.py::enqueue`，在 `insert` 外面包一层：

```python
            try:
                row = (
                    await session.execute(
                        insert(AgentRunInbox).values(...).returning(AgentRunInbox)
                    )
                ).scalar_one()
            except IntegrityError:
                # 并发的第二个写手：索引挡下它，回读对方刚插的那一行。
                # 没有 dedupe_key 的插入不可能撞这个索引，所以这里必有 key。
                await session.rollback()
                async with write_scope() as retry:
                    existing = (
                        await retry.execute(
                            dedupe_lookup_stmt(target_kind, int(target_id), dedupe_key)
                        )
                    ).scalar_one_or_none()
                if existing is None:
                    raise
                logger.info(
                    f"[agent_run_inbox] {target_kind} {target_id}: dedupe_key "
                    f"{dedupe_key} lost the race — reusing the winner's item"
                )
                return _row(existing)
```
并把 `dedupe_lookup_stmt` docstring 里「Best-effort by construction… without a unique index two SIMULTANEOUS enqueues can both miss」改写为现状：索引已在（462），查-插仍保留是为了省一次异常路径。

- [x] **Step 8: 竞态测试（先红）**

```python
# backend/tests/repositories/test_inbox_dedupe_race.py
"""两个并发 enqueue 同一个 dedupe_key：必须只落一行，且两个调用方都拿到它。
没有唯一索引时这条会偶发双行——用显式的「两次查都miss」桩把偶发变必然。"""
async def test_two_simultaneous_enqueues_yield_one_row(monkeypatch, inbox_repo):
    ...  # 桩掉 dedupe_lookup 让两次都返回 None，模拟两个写手同时错过
    first = await inbox_repo.enqueue(..., dedupe_key="k1")
    second = await inbox_repo.enqueue(..., dedupe_key="k1")
    assert first["id"] == second["id"]
```
Run: `uv run pytest tests/repositories/test_inbox_dedupe_race.py -q` → 先红（两行不同 id），实现后绿。

- [x] **Step 9: 全量 + lint**

```bash
cd backend && uv run pytest -q -p no:cacheprovider --deselect tests/api/test_distribution_music_search.py --deselect tests/api/test_distribution_topic_suggest.py tests
uv run black --check app tests && uv run isort --check-only app tests && uv run ruff check app tests && uv run flake8 app tests
```

- [x] **Step 10: 突变记录（至少 3 处，各证转红）**

| 突变 | 应转红的测试 |
|---|---|
| 唯一索引去掉 `(version)` 只留 `(kind, ref_id)` | `test_version_uniqueness_is_scoped_to_the_object` |
| dedupe 索引去掉 `WHERE … expired_at IS NULL` | `test_inbox_dedupe_index_only_covers_live_rows` |
| `enqueue` 去掉 `except IntegrityError` 分支 | `test_two_simultaneous_enqueues_yield_one_row` |

- [x] **Step 11: Commit**

```bash
git add supabase/migrations/462_harness_p4_phase3a_deliverables.sql backend/app/models/agents.py backend/app/repositories/agent_run_inbox_repository.py backend/tests
git commit -m "feat(db): 迁移 462 产出血缘列 + 版本唯一索引 + 收件箱 dedupe 唯一索引（harness 三期 3a Task 1）"
```

---
### Task 2: 登记口 + 三类接线 + `deliverable` 事件 + fold

**Files:**
- Create: `backend/app/services/deliverables/__init__.py`、`registry.py`、`kinds.py`
- Create: `backend/app/repositories/run_deliverables_repository.py`
- Create: `backend/app/services/ai/runner/folds/deliverables.py`
- Modify: `backend/app/services/library/generated_media_service.py:359-361`（插行成功后登记）
- Modify: `backend/app/services/ai/runner/agent_runner.py:1447-1461`（`_media_run_context` 带坐标）与 `:921,935,2021,2035`（传 `iteration`）
- Modify: `backend/app/workflows/script_shot_generate.py:233-240`、`script_shot_video.py:169`（origin 回填 run 坐标）及两者的派发方
- Modify: `backend/app/services/ai/scope/scoped_script_gateway.py`（分镜四处）、`backend/app/repositories/script_scene_repository.py:552,577`（场景两处）、`backend/app/workflows/script_ai_workflows.py:67,136`（章节两处）
- Modify: `backend/app/services/ai/runner/run_projection.py:150`（import 加 `deliverables`）
- Test: `backend/tests/services/deliverables/test_registry.py`、`test_version_chain.py`、`test_wiring_generated_media.py`、`test_wiring_script.py`、`backend/tests/runner/test_fold_deliverables.py`、`backend/tests/services/deliverables/test_choke_point_guard.py`

**Interfaces:**
- Consumes: T1 的列与唯一索引；`events.emit(recorder, type, payload, turn=, step=)`（`services/ai/runner/events.py:22`）；`RunEventWriter.for_run(run_id)`（写到已结束的 run 上，2b-2 的 `subagent_done` 同款）；`clip_claimed_text`（`services/ai/runner/inbox.py`）。
- Produces: `register_deliverable(...) -> DeliverableRow | None`；`deliverable` 事件载荷 `{kind, ref_id, version, parent_version, title, model, cost_cents, turn, step}`；`view.outputs = {total, revised, last}`（T3 的端点与 T5 的 UI 都读这两样）。

- [x] **Step 1: 先写会失败的登记口契约测试**

```python
# backend/tests/services/deliverables/test_registry.py
"""登记口的四条契约：no-op、落行、落事件、截断。
真库不在单测里——repository 用桩，断言我们发出去的东西，
真执行留给 schema-drift 的集成套件与 Task 8 的真栈。"""
import pytest

from app.services.deliverables.registry import register_deliverable


@pytest.mark.asyncio
async def test_no_run_id_registers_nothing(repo_spy, emit_spy):
    """人手改动 / 前端生成走同一个函数，必须什么都不做。"""
    out = await register_deliverable(
        run_id=None, kind="generated_media", ref_id="123", title="x"
    )
    assert out is None
    assert repo_spy.inserts == [] and emit_spy.events == []


@pytest.mark.asyncio
async def test_first_registration_is_v1_with_no_parent(repo_spy, emit_spy):
    repo_spy.latest_version_returns = None
    out = await register_deliverable(
        run_id=777, kind="generated_media", ref_id="123", title="Shot 1",
        model="gpt-image-2.5", cost_cents=0.12, turn=1, step=3,
    )
    assert (out.version, out.parent_version) == (1, None)
    ev = emit_spy.events[-1]
    assert ev.type == "deliverable"
    assert ev.payload == {
        "kind": "generated_media", "ref_id": "123", "version": 1,
        "parent_version": None, "title": "Shot 1",
        "model": "gpt-image-2.5", "cost_cents": 0.12, "turn": 1, "step": 3,
    }
    assert (ev.turn, ev.step) == (1, 3)   # 坐标也要进列，不只进载荷


@pytest.mark.asyncio
async def test_second_registration_chains_to_the_previous(repo_spy, emit_spy):
    repo_spy.latest_version_returns = 2
    out = await register_deliverable(run_id=777, kind="script_shot", ref_id="9", title="t")
    assert (out.version, out.parent_version) == (3, 2)


@pytest.mark.asyncio
async def test_title_is_clipped_to_120(repo_spy, emit_spy):
    """标题进事件也进列；不设界，一个超长剧本行就能把 transcript 撑爆。"""
    out = await register_deliverable(run_id=777, kind="script_scene", ref_id="9", title="x" * 400)
    assert len(out.title) == 120
    assert len(emit_spy.events[-1].payload["title"]) == 120
```

- [x] **Step 2: 跑它，确认红**

Run: `cd backend && uv run pytest tests/services/deliverables/test_registry.py -q`
Expected: FAIL `ModuleNotFoundError: app.services.deliverables`

- [x] **Step 3: 写 kinds 与 repository**

```python
# backend/app/services/deliverables/kinds.py
"""产出的四类。新增一类必须同时给出 ref_id 的取法与标题的取法——
没有这两样，血缘端点就只能显示一个裸 id。"""
from typing import Final, Literal

DeliverableKind = Literal[
    "generated_media", "script_shot", "script_scene", "script_chapter"
]

ALL_KINDS: Final[tuple[str, ...]] = (
    "generated_media", "script_shot", "script_scene", "script_chapter",
)

TITLE_MAX: Final[int] = 120
```

```python
# backend/app/repositories/run_deliverables_repository.py
"""run_deliverables 的唯一读写口（ORM，无裸 SQL）。

版本号由 ``latest_version`` + 1 得出，并发靠 462 的
``run_deliverables_kind_ref_version_key`` 兜底——所以这里**不**加锁：
一个短事务里的 SELECT-then-INSERT 比 SELECT FOR UPDATE 便宜，
冲突由登记口重算一次（spec §2.1）。"""
from typing import Any, Optional

from sqlalchemy import desc, insert, select

from app.db.session import read_scope, write_scope
from app.models.agents import AgentRuns, RunDeliverables


class RunDeliverablesRepository:
    async def latest_version(self, *, kind: str, ref_id: str) -> Optional[int]:
        async with read_scope() as session:
            return (
                await session.execute(
                    select(RunDeliverables.version)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id == str(ref_id))
                    .order_by(desc(RunDeliverables.version))
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def insert_version(self, **values: Any) -> dict[str, Any]:
        async with write_scope() as session:
            row = (
                await session.execute(
                    insert(RunDeliverables).values(**values).returning(RunDeliverables)
                )
            ).scalar_one()
            return _row(row)

    async def list_for_issue(self, issue_id: int) -> list[dict[str, Any]]:
        """本 issue 全部产出。issue 归属经 run 反查——
        表上不存 issue_id，`agent_runs.issue_id` 是唯一真相（spec §3）。"""
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(RunDeliverables)
                    .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                    .where(AgentRuns.issue_id == int(issue_id))
                    .order_by(
                        RunDeliverables.kind,
                        RunDeliverables.ref_id,
                        desc(RunDeliverables.version),
                    )
                )
            ).scalars().all()
            return [_row(r) for r in rows]

    async def lineage_for(self, *, kind: str, ref_id: str) -> list[dict[str, Any]]:
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(RunDeliverables, AgentRuns.issue_id)
                    .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id == str(ref_id))
                    .order_by(desc(RunDeliverables.version))
                )
            ).all()
            return [{**_row(r[0]), "issue_id": r[1]} for r in rows]
```
（`_row` 照同目录既有 repository 的写法：`{c.name: getattr(row, c.name) for c in row.__table__.columns}`，`Decimal` 转 `float`，`BIGINT` 转 `str`——**id 一律 str，照 Snowflake 精度纪律**。）

- [x] **Step 4: 写登记口**

```python
# backend/app/services/deliverables/registry.py
"""产出登记的唯一入口（harness p4 §1-④ / 三期 3a spec §1）。

没登记 = 不存在。三件事一次做完：算版本 → 插行 → 在 run 的
transcript 上落一条 ``deliverable`` 事件。

**``run_id`` 为空即 no-op**：人手编辑、画布保存、前端直传都会流经同一个
写入点，它们不是 agent 产出，不占版本号，也不该在任何 run 上留事件。

**事件可能落在已经结束的 run 上**：分镜出图走 DBOS，父 run 早已收工。
这条路与 workforce 写 ``subagent_done`` 的完全一样——``RunEventWriter.for_run``。
"""
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.repositories.run_deliverables_repository import RunDeliverablesRepository
from app.services.ai.runner.events import emit
from app.services.ai.runner.inbox import clip_claimed_text
from app.services.deliverables.kinds import ALL_KINDS, TITLE_MAX


@dataclass(frozen=True)
class DeliverableRow:
    id: str
    run_id: str
    kind: str
    ref_id: str
    version: int
    parent_version: Optional[int]
    title: Optional[str]


async def register_deliverable(
    *,
    run_id: Any,
    kind: str,
    ref_id: Any,
    title: Optional[str] = None,
    model: Optional[str] = None,
    cost_cents: Optional[float] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
    recorder: Any = None,
) -> Optional[DeliverableRow]:
    if run_id in (None, "", 0):
        return None
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown deliverable kind {kind!r}")

    repo = RunDeliverablesRepository()
    clipped = clip_claimed_text(title or "", TITLE_MAX) or None

    row = await _insert_next_version(
        repo,
        run_id=int(run_id),
        kind=kind,
        ref_id=str(ref_id),
        title=clipped,
        model=model,
        cost_cents=cost_cents,
        turn=turn,
        step=step,
    )

    await emit(
        recorder if recorder is not None else _writer_for(run_id),
        "deliverable",
        {
            "kind": kind,
            "ref_id": str(ref_id),
            "version": row.version,
            "parent_version": row.parent_version,
            "title": clipped,
            "model": model,
            "cost_cents": cost_cents,
            "turn": turn,
            "step": step,
        },
        turn=turn,
        step=step,
    )
    return row


async def _insert_next_version(repo, *, kind, ref_id, **values) -> DeliverableRow:
    """SELECT max → INSERT，冲突重算一次。

    第二次一定读得到对方的值（它已提交，正是它让我们撞索引的），
    所以一次重试够用；二次冲突照抛——宁可失败，也不要两个 v2。"""
    for attempt in (1, 2):
        previous = await repo.latest_version(kind=kind, ref_id=ref_id)
        try:
            inserted = await repo.insert_version(
                kind=kind,
                ref_id=ref_id,
                version=(previous or 0) + 1,
                parent_version=previous,
                **values,
            )
            return _as_row(inserted)
        except IntegrityError:
            if attempt == 2:
                raise
            logger.info(
                f"[deliverables] {kind}/{ref_id} v{(previous or 0) + 1} lost the "
                "race — recomputing once"
            )
    raise AssertionError("unreachable")
```
`_writer_for(run_id)` 返回一个最小适配对象，把 `record_event` 代理到 `RunEventWriter.for_run(int(run_id)).append(...)`——这样 `emit` 的「recorder 没有 `record_event` 就静默」语义对两条路都成立。

- [x] **Step 5: 跑 Step 1 的测试，全绿**

Run: `cd backend && uv run pytest tests/services/deliverables -q`

- [x] **Step 6: 接线一——`generated_media`（先写测试）**

```python
# backend/tests/services/deliverables/test_wiring_generated_media.py
@pytest.mark.asyncio
async def test_agent_generation_registers_one_deliverable(register_spy, insert_stub):
    """agent 生图：插行成功后恰好登记一次，标题取 origin 的 prompt 首行。"""
    await register_generated_media(
        user_id="u", scope_id=1, source_url="http://x/y.png", mime="image/png",
        origin=GenerationOrigin(
            kind="agent_run", run_id="777", agent_id="a", model="gpt-image-2.5",
            cost_cents=0.12, prompt="A cafe at dusk", turn=1, step=3,
        ),
    )
    assert register_spy.calls == [
        dict(run_id="777", kind="generated_media", ref_id="<new row id>",
             title="A cafe at dusk", model="gpt-image-2.5", cost_cents=0.12,
             turn=1, step=3, recorder=None)
    ]


@pytest.mark.asyncio
async def test_canvas_generation_registers_nothing(register_spy, insert_stub):
    """画布/上传路径 origin 无 run_id —— 零登记（负向对照）。"""
    await register_generated_media(..., origin=GenerationOrigin(kind="canvas_run"))
    assert register_spy.calls == []
```
实现：`GenerationOrigin` 加 `turn: Optional[int] = None` / `step: Optional[int] = None` 两个字段；`register_generated_media` 在 `return dict(row)` 之前插一段

```python
    out = dict(row) if row is not None else {}
    if out.get("id") is not None:
        # 唯一入口在这里叠上去：五个调用点一个都不用改。
        await register_deliverable(
            run_id=origin.run_id,
            kind="generated_media",
            ref_id=str(out["id"]),
            title=(origin.prompt or "").strip().splitlines()[0] if origin.prompt else None,
            model=origin.model,
            cost_cents=origin.cost_cents,
            turn=origin.turn,
            step=origin.step,
        )
    return out
```
⚠️ `_insert_uploaded_row` **不登记**（上传不是 agent 产出）——用一条测试钉住。

- [x] **Step 7: 接线一续——坐标进 `run_context`**

`agent_runner.py:1447` 改签名并补两个键：

```python
    def _media_run_context(
        self, recorder, composed, step: Optional[int] = None
    ) -> dict:
        return {
            "run_id": recorder.run_id if recorder else None,
            "user_id": str(recorder.user_id) if recorder else None,
            "team_id": recorder.team_id if recorder else None,
            "agent_id": str(composed.agent_id),
            # 产出登记要坐标才能把卡挂到正确的那一步（3a）。turn 与
            # `_step_started` 同源：目前恒为 1，改它要一起改。
            "turn": 1,
            "step": step,
        }
```
四个调用点（`:921,935,2021,2035`）传 `iteration`。`generate_media_tools.py` 的两处 `GenerationOrigin(...)` 补 `turn=run_context.get("turn"), step=run_context.get("step")`。

- [x] **Step 8: 接线二——分镜出图/出视频把 run 坐标穿过 DBOS**

⚠️ 要加宽的是**四个 enqueue 站点**（出图三个、出视频一个），不是两个；agent 工具侧那个 enqueue 手里已经有 `scope.run_id`，今天只写进了 task_tracking 的 metadata。逐个补齐，漏一个就是那条路的产出永远无 run 可挂。

两条 workflow 的 payload 加 `{"run_id": ..., "turn": ..., "step": ...}`（派发方从工具的 `run_context` 取），workflow 内：

```python
                origin=GenerationOrigin(
                    kind="shot_generate",
                    node_id=str(shot_id),
                    prompt=prompt,
                    model=model,
                    provider=provider,
                    derivation_kind="shot_generate",
                    # 3a：run 上下文在派发时就丢了，这里回填，否则这条路
                    # 产出的图永远没有 run 可挂（真栈缺口，spec §1.3）。
                    run_id=payload.get("run_id"),
                    turn=payload.get("turn"),
                    step=payload.get("step"),
                ),
```
测试：`tests/workflows/test_shot_generate_carries_run.py` —— 派发方构造的 payload 必含三个键；workflow 把它们放进 origin。**先红**（今天 payload 里没有）。

- [x] **Step 9: 接线三——分镜 / 场景 / 章节**

`scoped_script_gateway` 的写点里**只有改内容的三处登记**——`set_shot_status` 只改状态，状态不是新版本（它今天连 `script_shot_ops` 账本行都不写，那是另一张小票，不在本期）。三处各调一次：

```python
        await register_deliverable(
            run_id=self.run_id,
            kind="script_shot",
            ref_id=str(shot_id),
            title=_shot_title(after_json),   # 「S3 · 分镜 #1 · 中景」
            turn=1,
            step=self.step,
        )
```
场景两处同形（`kind="script_scene"`，`ref_id=scene_id`，标题取场次号 + 名）；章节两处**整条调用链都取不到 run id**：给两个 workflow 加 keyword-only 的 `run_id/turn/step`，并给 `ScriptService.update_chapter` / `create_chapter` 加一个署名参数（`attributed_to_run_id`），由派发方传入。签名改动波及既有调用方，全部显式改完再跑全量——默认值设 `None` 以免漏改处静默变成「不登记」。
测试 `test_wiring_script.py`：三类各一条「写一次 ⇒ 一行一事件」+ 一条「`run_id=None` ⇒ 零行零事件」。

- [x] **Step 10: fold（先写测试）**

```python
# backend/tests/runner/test_fold_deliverables.py
def test_counts_total_and_revised():
    views = replay([
        ("deliverable", {"kind": "generated_media", "ref_id": "1", "version": 1}),
        ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 3}),
    ])
    assert views["view"]["outputs"] == {
        "total": 2, "revised": 1,
        "last": {"kind": "script_shot", "ref_id": "9", "version": 3, "title": None},
    }


def test_the_same_version_twice_counts_once():
    """跨 DBOS 的事件会重放——重复到达不许把计数翻倍。"""
    ev = ("deliverable", {"kind": "generated_media", "ref_id": "1", "version": 1})
    assert replay([ev, ev])["view"]["outputs"]["total"] == 1


def test_a_payload_without_ref_is_ignored():
    assert replay([("deliverable", {"kind": "generated_media"})])["view"]["outputs"] is None
```

```python
# backend/app/services/ai/runner/folds/deliverables.py
"""``deliverable`` → ``view.outputs``（三期 3a §2.3）。

只落计数，清单查表（spec §2.4：表是唯一真相）。

顺序无关且幂等：这些事件可能来自 DBOS 侧的重放，也可能在父 run 结束
之后才到。见过的 ``(kind, ref_id, version)`` 留一个**有界**的集合
（最近 50 个），重复到达不再计数。"""
from app.services.ai.runner.run_projection import register

_SEEN_MAX = 50
_EMPTY = {"total": 0, "revised": 0, "last": None, "seen": []}


@register("deliverable")
def fold_deliverable(views, payload):
    kind, ref_id = payload.get("kind"), payload.get("ref_id")
    version = payload.get("version")
    if not kind or not ref_id or not isinstance(version, int):
        return None
    outputs = {**_EMPTY, **(views["view"].get("outputs") or {})}
    key = f"{kind}:{ref_id}:{version}"
    if key in outputs["seen"]:
        return None
    outputs["seen"] = [*outputs["seen"], key][-_SEEN_MAX:]
    outputs["total"] += 1
    if version > 1:
        outputs["revised"] += 1
    outputs["last"] = {
        "kind": kind, "ref_id": str(ref_id), "version": version,
        "title": payload.get("title"),
    }
    views["view"]["outputs"] = outputs
    return views
```
`run_projection.py:150` 的显式 import 列表加 `deliverables`。

- [x] **Step 11: 咽喉点守卫（源码扫描）**

```python
# backend/tests/services/deliverables/test_choke_point_guard.py
"""「唯一入口」靠这条测试活着：任何绕过登记口的新写入点都会让它转红。

扫源码而不是跑代码——新写入点通常出现在一条没有测试的路径上，
运行时断言等不到它。"""
import ast, pathlib

APP = pathlib.Path(__file__).resolve().parents[3] / "app"
ALLOWED_INSERTERS = {
    "app/services/library/generated_media_service.py",   # 唯一插 generated_media 的地方
}


def test_generated_media_insert_happens_in_one_file_only():
    offenders = [
        str(p.relative_to(APP.parent))
        for p in APP.rglob("*.py")
        if "_generated_media_insert_stmt(" in p.read_text(encoding="utf-8")
        and str(p.relative_to(APP.parent)) not in ALLOWED_INSERTERS
    ]
    assert offenders == [], f"这些文件绕过了登记口：{offenders}"


def test_register_generated_media_calls_the_registry():
    src = (APP / "services/library/generated_media_service.py").read_text("utf-8")
    assert "register_deliverable(" in src
```

- [x] **Step 12: 全量 + lint + 突变**

| 突变 | 应转红 |
|---|---|
| `generated_media` 的 `cost_cents` 无人填（11 个调用点无一设置），本期血缘里媒体类花费为空——UI 显 `—`，不伪造（小票已记） |（无测试，PR 描述里写明）|
| 登记口去掉 `if run_id in (None, "", 0): return None` | `test_no_run_id_registers_nothing` / `test_canvas_generation_registers_nothing` |
| `_insert_next_version` 去掉 `except IntegrityError` 重算 | 并发用例 |
| fold 去掉 `seen` 去重 | `test_the_same_version_twice_counts_once` |
| shot workflow 的 origin 去掉 `run_id=payload.get("run_id")` | `test_shot_generate_carries_run` |
| `register_generated_media` 把登记挪到 `_insert_uploaded_row` 里 | 上传零登记那条 |

- [x] **Step 13: Commit**

```bash
git add backend/app/services/deliverables backend/app/repositories/run_deliverables_repository.py backend/app/services/ai backend/app/workflows backend/app/services/library backend/tests
git commit -m "feat(harness): 产出登记口叠在既有写入点上——版本链、deliverable 事件与 view.outputs 折叠、分镜派发带 run 坐标（三期 3a Task 2）"
```

---
### Task 3: 三个端点 + Generated 卡来源行

**Files:**
- Create: `backend/app/api/outputs_router.py`、`backend/app/schemas/outputs.py`、`backend/app/services/deliverables/diff.py`
- Modify: `backend/app/api/issues_router.py`（加 `GET /{issue_id}/outputs`，照 `:771` 的 schedules 写法）
- Modify: `backend/app/main.py`（注册 router）
- Modify: `backend/app/services/library/generated_source.py:48`（`agent_run` 分支）、`backend/app/schemas/generated.py:88`
- Test: `backend/tests/api/test_outputs_router.py`、`test_issue_outputs.py`、`backend/tests/services/library/test_generated_source_agent_run.py`

**Interfaces:**
- Consumes: T2 的 `RunDeliverablesRepository.{list_for_issue,lineage_for}`。
- Produces: 三个端点的响应形状（T5/T6 的 service 按它写）；`GeneratedSource` 新字段 `issue_id / run_id / step`（T6 的卡按它渲染）。

- [x] **Step 1: 先写端点测试（红）**

```python
# backend/tests/api/test_outputs_router.py
def test_lineage_returns_versions_newest_first(client, seeded_deliverables):
    r = client.get("/api/v1/outputs/script_shot/9")
    assert r.status_code == 200
    assert [v["version"] for v in r.json()["versions"]] == [3, 2, 1]
    assert r.json()["versions"][0]["issue_id"] == "348087075560200"   # str，不是 number


def test_unregistered_object_is_404_not_empty(client):
    """「没登记」与「没产出」是两件事：空数组会让 UI 显示一个空的来源块，
    而正确的回答是这个对象根本不在登记表里。"""
    r = client.get("/api/v1/outputs/script_shot/999999")
    assert r.status_code == 404
    assert r.json()["details"]["code"] == "not_registered"   # ErrorResponse 外壳


def test_diff_rejects_a_version_that_does_not_exist(client, seeded_deliverables):
    r = client.get("/api/v1/outputs/script_shot/9/diff?from=1&to=7")
    assert r.status_code == 404 and r.json()["details"]["code"] == "version_not_found"
```
⚠️ 错误体是 `ErrorResponse` 外壳，类型化码在 `details.code`（CLAUDE.md 2026-09-09 真栈教训），`raise HTTPException(status_code=404, detail={"code": "not_registered"})`——**detail 必须是 dict**。

- [x] **Step 2: 实现三个端点**

`GET /api/v1/issues/{id}/outputs` 挂在 `issues_router`（与 `/schedules` 同族，复用 `_assert_visibility`）：按 `(kind, ref_id)` 分组，每组 `latest` + `versions[]`。
`GET /api/v1/outputs/{kind}/{ref_id}`、`/diff` 挂新 router；可见性 = 该产出所属 run 的 issue 可见性（无 issue 的 run → 仅本人）。
`diff.py`：`script_*` 从 `script_ops` / `script_shot_ops` 的 `before_json/after_json` 取两版文本；`generated_media` 取两行的 `id` 与封面 URL。

- [x] **Step 3: Generated 来源行**

```python
    elif kind == "agent_run":
        # 3a：这条分支此前只给一句 "Chat generation" 且无链接——
        # 「哪个 run 产出的这张图」在收件箱里无法回答。
        label = f"{agent_name or 'Agent'} · {issue_key or 'Run'}"
        if issue_id:
            deep_link = f"/team/{team_id}/todolist/{issue_key}"
            if step is not None:
                deep_link += f"?step={step}"
```
`issue_id` / `step` 从 `run_deliverables`（`kind='generated_media'`, `ref_id=<row id>`）反查一次；查不到就退回今天的平文本（**不报错**：历史行没有登记是正常的）。

- [x] **Step 4: 全量 + lint + 突变**

| 突变 | 应转红 |
|---|---|
| 未登记对象返回 `{"versions": []}` 而非 404 | `test_unregistered_object_is_404_not_empty` |
| 版本排序改成升序 | `test_lineage_returns_versions_newest_first` |
| `issue_id` 以 int 返回 | 该字段的 str 断言 |

- [x] **Step 5: Commit**

```bash
git commit -m "feat(api): 产出血缘三端点 + Generated 卡来源行反查（三期 3a Task 3）"
```

---

### Task 4: `output_ref` 附件 + `<referenced_outputs>` 框

**Files:**
- Modify: `backend/app/api/issue_messages_router.py` 与附件解析处（新 kind）
- Modify: `backend/app/services/ai/prompts/prompt_composer.py`（渲染新框）
- Modify: `backend/app/boundary/frame_markers.py`（`OWNED_FRAMES`）
- Modify: `backend/app/services/ai/prompts/README.md`（Model Experience 三问）
- Test: `backend/tests/api/test_output_ref_attachment.py`、`backend/tests/services/ai/prompts/test_referenced_outputs_frame.py`

**Interfaces:**
- Consumes: T2 的登记表（校验引用存在）、T3 的 `lineage_for`。
- Produces: 附件 wire 形状 `{kind:"output_ref", ref_kind, ref_id, version}`（T6 的映射分支按它写）；提示词新框。

- [x] **Step 1: 测试（红）**

```python
def test_unresolvable_output_ref_is_a_typed_400(client):
    """静默丢附件是本仓明令禁止的（「触发路径必须类型化失败回显」）。"""
    r = client.post(f"/api/v1/issues/{iid}/messages", json={
        "content": "look at this",
        "attachments": [{"kind": "output_ref", "ref_kind": "script_shot",
                         "ref_id": "999999", "version": 1}],
    })
    assert r.status_code == 400
    assert r.json()["details"]["code"] == "output_ref_unresolvable"


def test_frame_lists_only_ids_and_versions(composed_prompt):
    """引用只给坐标，内容由 agent 自己用既有工具取——
    把整段剧本塞进系统提示词会把每一轮的前缀都撑爆。"""
    assert '<referenced_outputs>' in composed_prompt
    assert '<output kind="script_shot" ref="9" version="2" title="S3 · Shot #1"/>' in composed_prompt
    assert "INT. CAFE" not in composed_prompt


def test_title_with_a_closing_marker_cannot_break_out(composed_prompt_with_hostile_title):
    assert "</referenced_outputs>" not in composed_prompt_with_hostile_title.split(
        "<referenced_outputs>")[1].split("</referenced_outputs>")[0]
```

- [x] **Step 2: 实现**

附件解析：`output_ref` → 查登记表，`(ref_kind, ref_id, version)` 不存在或其 run 不属于本 issue ⇒ 400 类型化；存在则把 `{kind, ref_id, version, title}` 存进消息 `attachments`。
`prompt_composer`：本轮消息带 `output_ref` 时渲染新框；标题走 `escape_frame_attr`。
`frame_markers.OWNED_FRAMES` 加 `referenced_outputs`（不加 `test_frame_escape_wiring.py::test_every_frame_rendered_in_prompt_code_is_registered` 直接拒绝）。

- [x] **Step 3: 全文 pin**

Run: `cd backend && uv run pytest tests/services/ai/prompts/test_system_message_pin.py -q`
无引用的普通轮次**不应**有 diff（新框只在有引用时出现）。若有 diff，说明框无条件渲染了——那是缺陷，不是刷新快照的理由。

- [x] **Step 4: README 三问**

`prompts/README.md` 加一段：What the model sees（贴出框的字面量）、Token effect（每条引用一行、条数由用户选择上限决定、不含内容所以不随产出大小增长）、KV Cache effect（框在缓存边界之后的 request instructions 段，不破坏稳定前缀）。

- [x] **Step 5: 突变 + Commit**

| 突变 | 应转红 |
|---|---|
| 不可解析的引用改成静默丢弃 | `test_unresolvable_output_ref_is_a_typed_400` |
| 框里改成塞完整内容 | `test_frame_lists_only_ids_and_versions` |
| `OWNED_FRAMES` 不登记新框 | `test_frame_escape_wiring.py` |

```bash
git commit -m "feat(harness): @引用产出的 output_ref 附件与 <referenced_outputs> 框（三期 3a Task 4）"
```

---
### Task 5: 前端——线程产出卡 + Cockpit 第 5 格 + 右栏「产出」块 + 差异弹层

**Files:**
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/foldEvents.ts`（`OutputCard` 类型 + `StepNode.outputs` + `deliverable` case）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx`（`OutputCards`，紧跟 `SubagentCards`）
- Modify: `frontend/components/TaskCenter/runView.ts`（`RunOutputs` + `outputsState`）
- Modify: `frontend/components/Todolist/blocks/CockpitBlock.tsx`（第 5 格 + 列数）
- Create: `frontend/components/Todolist/blocks/OutputsBlock.tsx`、`frontend/components/Todolist/OutputDiffDialog.tsx`、`outputDiff.ts`、`frontend/services/outputsService.ts`
- Modify: `frontend/components/Todolist/blocks/index.ts`、`frontend/components/Todolist/issueBlocks.ts`（注册）
- Modify: `frontend/public/locales/{en,zh}.json`
- Test: `foldEvents.test.ts` 增用例、`runView.test.ts`、`OutputsBlock.test.tsx`、`outputDiff.test.ts`、`CockpitBlock.test.tsx`

**Interfaces:**
- Consumes: T2 的事件载荷与 `view.outputs`；T3 的三个端点。
- Produces: `data-testid="output-card" / "cockpit-outputs" / "outputs-block" / "output-diff"`（T8 真栈走查按它抓）。

- [x] **Step 1: fold 用例（先红）**

```ts
// foldEvents.test.ts
it('hangs an output card on the step that produced it', () => {
  const nodes = foldEvents([
    at(1, 'step_start', { turn: 1, step: 1 }, 1),
    at(2, 'deliverable', { kind: 'generated_media', ref_id: '77', version: 1, title: 'S3 · Shot #1' }, 1),
  ]);
  const step = nodes.find((n) => n.kind === 'step') as StepNode;
  expect(step.outputs).toEqual([
    { key: 'generated_media:77:1', kind: 'generated_media', refId: '77',
      version: 1, parentVersion: null, title: 'S3 · Shot #1', model: null, costCents: null },
  ]);
});

it('attaches a late deliverable to its own step, not the last one', () => {
  // 分镜出图走 DBOS，事件可能在 run 结束后才到——它必须回到第 1 步，
  // 而不是堆在最后一步（与 subagent_done 同族的乱序）。
  const nodes = foldEvents([
    at(1, 'step_start', { turn: 1, step: 1 }, 1),
    at(2, 'step_end', { turn: 1, step: 1 }, 1),
    at(3, 'step_start', { turn: 1, step: 2 }, 2),
    at(4, 'deliverable', { kind: 'generated_media', ref_id: '77', version: 1 }, 1),
  ]);
  const steps = nodes.filter((n) => n.kind === 'step') as StepNode[];
  expect(steps[0].outputs).toHaveLength(1);
  expect(steps[1].outputs).toHaveLength(0);
});

it('ignores a deliverable with no ref_id', () => { /* 一张打不开的卡不如没有 */ });
```

```ts
// foldEvents.ts —— 类型与 case
export interface OutputCard {
  key: string;              // `${kind}:${refId}:${version}`，与后端 fold 的 seen 键同形
  kind: string;
  refId: string;
  version: number;
  parentVersion: number | null;
  title: string | null;
  model: string | null;
  costCents: number | null;
}
// StepNode 加：
  /** Outputs this step registered, in registration order (harness 3a §5). */
  outputs: OutputCard[];

      case 'deliverable': {
        const kind = str(p.kind);
        const refId = str(p.ref_id);
        const version = num(p.version);
        // 一张打不开的卡比没有卡更糟：三者缺一即不画。
        if (!kind || !refId || version === null) break;
        // 坐标优先（事件自带 turn/step），因为跨 DBOS 的登记可能在 run
        // 收工后才到——`ensureStep` 会按坐标回到它真正所属的那一步。
        const node = ensureStep(ev, null);
        const key = `${kind}:${refId}:${version}`;
        if (node.outputs.some((o) => o.key === key)) break;
        node.outputs.push({
          key, kind, refId, version,
          parentVersion: num(p.parent_version),
          title: str(p.title),
          model: str(p.model),
          costCents: num(p.cost_cents),
        });
        break;
      }
```
`newStep()` 里给 `outputs: []`（与 `children: []` 并列）。

- [x] **Step 2: 渲染卡（照 `SubagentCards` 的结构与色板）**

`builtins.tsx` 加 `OutputCards`，`StepNodeView` 在 `<SubagentCards …/>` 之后渲染它。两态：`version === 1` 用 `border-ok-line bg-ok-soft/40`，`> 1` 用 `border-warn-line bg-warn-soft/40` 并显示 `v{n} ← v{n-1}`。右侧「Open」；修订才有「Diff」。花费为 0/空显示 `—`（照 7c 的 `fmtChildCents` 教训，`¢0.000` 是骗人的）。

- [x] **Step 3: `runView.ts` 选择器**

```ts
/** Outputs an agent registered this run (harness 3a). `total` includes every
 *  version; `revised` counts the ones that replaced an earlier version. */
export interface RunOutputs {
  total: number;
  revised: number;
  last: { kind: string; ref_id: string; version: number; title: string | null } | null;
}

export function outputsState(view: RunView | null): RunOutputs | null {
  const o = view?.outputs;
  if (!o || !(o.total > 0)) return null;   // 0 件不占格子
  return { total: o.total, revised: o.revised ?? 0, last: o.last ?? null };
}
```
`RunView` 加 `outputs?: RunOutputs | null`（**可选**：旧 run 的 metadata 里没有这个键，必选会让整张 Cockpit 变 null）。

- [x] **Step 4: Cockpit 第 5 格**

```tsx
      <div className={`grid grid-cols-2 gap-2 ${GRID_COLS[4 + (tools && tools.timed_out > 0 ? 1 : 0) + (children ? 1 : 0) + (outputs ? 1 : 0)]}`}>
```
`GRID_COLS` 补 `7: 'sm:grid-cols-7'`（4 + 三个条件格；Tailwind 不扫模板串，必须写全）。新格：

```tsx
        {outputs && (
          <Cell label={t('issueDetail.outputs', 'Outputs')} testId="cockpit-outputs">
            {outputs.total}
            {outputs.revised > 0 && (
              <span className="text-ink-500 text-[12px]">
                {' · '}{t('issueDetail.outputsRevised', '{{n}} revised', { n: outputs.revised })}
              </span>
            )}
          </Cell>
        )}
```

- [x] **Step 5: 右栏「产出」块**

`OutputsBlock.tsx`：`id: 'outputs'`（⚠️ 不能叫 `deliverables`——`registerIssueBlock` 对重复 id **直接抛错**，而那个 id 已被项目阶段文件夹占用），`zone: 'context'`, `order: 25`，`match: (ctx) => true`（任何 issue 都可能有产出；无产出时组件返回 null）。读 `GET /issues/{id}/outputs`，按对象分组，行内显示 `v{n}`，点开差异弹层。

- [x] **Step 6: 差异弹层与 diff 工具**

`outputDiff.ts` 纯函数（词级 LCS，无外部依赖），自带测试：空对空、全增、全删、中间替换、极长文本（截断而不是卡死）。`OutputDiffDialog.tsx` 照既有弹层的 Esc / 焦点陷阱写法。媒体类两栏缩略图；「Revert To v1」按钮 **disabled**，`title` 写明「Arrives with 3b」（与资产库 P2 的占位同族做法，别当坏按钮修）。

- [x] **Step 7: 全量前端 + lint + 突变**

```bash
cd frontend && npx vitest run && npx eslint <改动文件> && npx tsc --noEmit
```

| 突变 | 应转红 |
|---|---|
| `deliverable` case 用 `lastStep()` 代替 `ensureStep` | 「late deliverable 回到自己的步」 |
| `outputsState` 去掉 `total > 0` 判空 | Cockpit 空格用例 |
| `OutputsBlock` 的 id 改成 `deliverables` | 注册重复 id 的抛错用例 |
| 花费 0 显示 `¢0.000` | 卡片 `—` 用例 |

- [x] **Step 8: Commit**

```bash
git commit -m "feat(ui): 线程产出卡 + Cockpit 产出格 + 右栏产出块 + 版本差异弹层（三期 3a Task 5）"
```

---

### Task 6: 前端——@引用产出页签 + 对象页来源块 + Generated 卡来源行

**Files:**
- Create: `frontend/components/chat/useMentionOutputsTab.ts`、`frontend/components/agentActivity/OutputProvenance.tsx`
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx`（第三个页签）、`composerAttachmentPayload.ts`（新分支）、`frontend/services/issueMessageService.ts`（联合类型）
- Modify: `frontend/components/resources/generated/GeneratedCard.tsx`（来源行可点）
- Modify: 分镜 / 场景 / 资源信息面板三处挂 `OutputProvenance`
- Modify: `frontend/public/locales/{en,zh}.json`
- Test: `composerAttachmentPayload.test.ts`、`useMentionOutputsTab.test.ts`、`OutputProvenance.test.tsx`、`GeneratedCard.test.tsx`

**Interfaces:**
- Consumes: T3 的 `GET /issues/{id}/outputs` 与 `GET /outputs/{kind}/{ref_id}`；T4 的附件 wire 形状。
- Produces: `ComposerAttachment` 新成员 `output_ref`。

- [x] **Step 1: 映射器分支（先红）**

```ts
// composerAttachmentPayload.test.ts
it('maps an output reference by kind, id and version', () => {
  // 漏掉这个分支不会抛错——AttachmentRequest 全字段可选，
  // 服务端照收，引用解析成空，用户只看到「附件读不到」。
  expect(toIssueAttachmentPayload([
    { kind: 'output_ref', refKind: 'script_shot', refId: '9', version: 2, name: 'S3 · Shot #1' },
  ])).toEqual([
    { kind: 'output_ref', ref_kind: 'script_shot', ref_id: '9', version: 2, name: 'S3 · Shot #1' },
  ]);
});
```

- [x] **Step 2: 「产出」页签**

`useMentionOutputsTab.ts` 照 `useMentionAssetsTab.ts` 的状态 / 传输 / 键盘路由写；数据源 `GET /issues/{id}/outputs`，默认只列每个对象的最新版，旧版折在下面一组（标 `Older`）。`ResourcePickerSuggestion` 的页签数组加一项——⚠️ 它今天是两页签的硬编码数组，加第三项要同时改键盘左右切换的取模。

- [x] **Step 3: 对象页来源块**

`OutputProvenance.tsx`：入参 `{ kind, refId }`，读 `GET /outputs/{kind}/{ref_id}`；404 `not_registered` ⇒ 渲染 null（人手创建的对象没有来源，这不是错误）；其余错误 ⇒ 一行可读提示，不静默。

- [x] **Step 4: Generated 卡来源行**

`GeneratedCard.tsx` 的 source 区：有 `deep_link` 就渲染成链接（今天只在 canvas 分支有），标签用 T3 的新字段拼「Agent · MH-91 · run #… · step N」。

- [x] **Step 5: 前端全量 + lint + 突变 + Commit**

| 突变 | 应转红 |
|---|---|
| 映射器删掉 `output_ref` 分支 | Step 1 的用例 |
| 页签数组加了项但键盘取模仍按 2 | 键盘切换用例 |
| `not_registered` 时渲染「加载失败」 | `OutputProvenance` 的 null 用例 |

```bash
git commit -m "feat(ui): @引用产出页签 + 对象页来源块 + Generated 卡来源行（三期 3a Task 6）"
```

---

### Task 7: 两张小票（测试顺序假红、`?slug=` 过滤失效）

**Files:**
- Modify: `backend/tests/workflows/conftest.py` 或相关 fixture（隔离，不是关随机）
- Modify: `backend/app/api/ai_library_router.py`（agents 列表的 `slug` 过滤）
- Test: `backend/tests/api/test_agents_list_filters.py`

- [x] **Step 1: 顺序假红查根因**

先复现：`uv run pytest tests/workflows tests/services/issues/test_issue_reply_resume.py -q` 红，单跑各自绿。查共享状态（DBOS 单例、`app.state`、模块级缓存），**修隔离而不是加 `-p no:randomly`**（关掉随机只是把问题藏起来）。根因与修法写进 PR。

- [x] **Step 2: `?slug=` 过滤（先红）**

```python
def test_slug_filter_returns_only_that_agent(client, seeded_agents):
    """验收脚本踩到过：传 slug=script_ai 拿回来的是 analyze。"""
    r = client.get("/api/v1/ai-library/agents?slug=script_ai")
    assert [a["slug"] for a in r.json()["items"]] == ["script_ai"]
```

- [x] **Step 3: 实现 + 全量 + Commit**

```bash
git commit -m "fix(test,api): 工作流测试顺序隔离 + agents 列表 slug 过滤生效（三期 3a Task 7）"
```

---

### Task 8: 真栈验收 + 完成账

- [ ] **Step 1: 等两条部署链**

每个 Task 合并后 `gh run list --workflow=deploy-gpu.yml --limit 2` 与 `deploy-pages.yml` 到 success；探 `readyz`（`dbos: enabled`）+ 容器内符号 `grep -c register_deliverable /app/app/services/library/generated_media_service.py`。

- [ ] **Step 2: 按 spec §8 八项逐条真跑**

建真 issue、花真钱，prompt 要小。逐项记 done / 证据（id、SQL、截图）/ PASS·FAIL·UNVERIFIED 到 `.superpowers/sdd/<workspace>/task-8-evidence.md`。**负向对照必须做**：人手改同一对象后 `SELECT count(*) FROM run_deliverables WHERE kind=… AND ref_id=…` 不变。

- [ ] **Step 3: 缺陷修复轮**

真栈揪出的缺陷按 2b-2 的节奏走（7a/7b/7c 三轮的形状）：独立 PR、opus 评审、合并、再复验，直到一轮零新缺陷。

- [ ] **Step 4: 完成账**

写进本 plan 末尾：八项验收表 + 真栈才暴露的缺陷清单 + 记小票；spec 的「实施记录」同步。

- [ ] **Step 5: 收尾**

`npm run e2e:prod` 绿；记忆改 shipped；销毁 worktree；SDD 工作区保留证据文件。

---
