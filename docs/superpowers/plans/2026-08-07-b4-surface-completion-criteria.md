# B4 — Surface 节点完成判据接线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** surface 节点（script/storyboard）在产物写入回流点自动完成（含 mirror issue 关闭与 autopilot 级联），并顺带交付：surface 回填 migration、`_deliverable_present` fallback fail-closed、episodes/progress 的节点级真数据（B5 占位数据来源）。

**Architecture:** 判据计算收敛为 `episode_repository` 的一条按集聚合查询 + 两个纯函数；完成动作走「关 mirror issue → 既有 `_fire_stage_node_sync` 钩子投影节点状态 + 入队 autopilot tick」，无镜像时才走仓库唯一写入口 `set_node_status`。回流点接线采用仓库现成的范式 A（`issue_repository._fire_stage_node_sync` 式 post-commit best-effort hook：函数内 import、try/except 吞错、绝不影响主写）。产物删除只导致派生值回退（progress 端点可见），**不**回退已存储的节点状态、不动游标、不重开 issue（父 spec §5 ①）。

**Tech Stack:** FastAPI + SQLAlchemy ORM（async）+ asyncpg 集成测试（INTEGRATION_DATABASE_URL gated）。

**父 spec:** `2026-08-04-episode-level-workflow-design.md` §5（在 docs PR #1734，未合入 master；本计划自带所需口径）。

## 用户已拍板（2026-08-07）

1. **renders 本期不映射**：只回填 Script→`script`、Storyboard→`storyboard`；renders 留 NULL 按交付物型。理由：判据 `renders_count` 数的是 `script_shots` 的 image_url/video_url（镜头出图），与成片表 `generated_media` 口径分裂，且 Canvas 节点 B6 要退役。
2. **B4 包含 B5 真数据端点**：episodes/progress 返回每集节点级计数 + needs_input 计数；B5 前端换数据另开小 PR，本计划**零前端改动**。

## Global Constraints

- 全程 ORM，禁止新增 `text()` 裸 SQL（2026-08-04 立约；本计划全部查询可用 ORM 表达）。
- `project_stage_nodes.status` 单一写入者纪律：业务代码只能经 `set_node_status`（repo 唯一写入口）或经 issue `transition_status` 的投影钩子改它，禁止直接 UPDATE。
- autopilot 硬线同族：**review 闸只有人** — `review_required=True` 的 surface 节点不自动完成。
- 回流 hook 一律 best-effort：函数内 import、整体 try/except + `logger.warning`、主写操作绝不因 hook 失败而失败或回滚（照抄 `issue_repository.py` `_fire_stage_node_sync` 范式）。
- 不新增任何轮询/定时任务；只挂既有写入路径。
- 集成测试 gated on `INTEGRATION_DATABASE_URL`，CI 无 DB 时干净 skip（照抄 `tests/integration/test_episode_instantiation_db.py` 头部范式）。
- migration 号 **410**（2026-08-07 已 fetch origin/master 核对，最新为 409；**提交前须再 fetch 复核一次**，撞号则顺延并同步改文件内注释）。
- 分支 `feat/b4-completion-criteria`（已基于 428bf5d，含 B3）。每个 Task 独立 commit，信息用中文、风格对齐仓库近期历史。
- 前端零改动（含 `types.ts` — 留给 B5 换数据的 PR）。

## 关键既有事实（实施者不需要重新考古）

| 事实 | 位置 |
|---|---|
| 推导阶梯 `_derive_episode_status`（`scene_count` 是死参数、OMITTED 场次计入 scene_count） | `backend/app/repositories/episode_repository.py:116-138`，喂数 `_progress_stmt` `:82-113` |
| 节点 status 唯一写入口 `set_node_status(node_id, status)` | `backend/app/repositories/project_stage_nodes_repository.py`（B3 后行号有漂移，按函数名找） |
| issue→node 投影钩子（关 issue=done 会把节点刷 done 并入队 autopilot tick） | `backend/app/repositories/issue_repository.py::_fire_stage_node_sync`，映射表 `_ISSUE_TO_NODE_STATUS` |
| `transition_status(issue_id: int, new_status: str, *, dbos_workflow_id=None)` | `backend/app/repositories/issue_repository.py:436` |
| `list_by_origin(origin_kind: str, origin_id: str)` | `backend/app/repositories/issue_repository.py:406` |
| `ORIGIN_KIND = "project_stage"`、`build_stage_origin_id(project_id, stage_id)` → `"project_stage:{project_id}:{node_id}"` | `backend/app/services/library/project_stage_issues.py:50-61` |
| `enqueue_autopilot_tick(project_id: str)`（best-effort 入队） | `backend/app/workflows/autopilot.py:704` |
| `list_nodes_by_episode(project_id: str, episode_id: str)`（已排除 episode_id IS NULL 遗留节点） | `backend/app/repositories/project_stage_nodes_repository.py:917` |
| needs_input 口径：`Issues.status == "needs_followup" AND execution_state->>'agent_outcome' == 'needs_input'`，谓词 `needs_input_predicate()` | `backend/app/repositories/issue_repository.py:71-82` |
| `script_shots.status` ∈ empty/generating/done/failed；置 done 的唯一生产路径 `mark_shot_done` → `update_status`；agent 车道 `set_shot_status` 绕过 repo 直写 ORM | `backend/app/repositories/script_shot_repository.py:336-370`、`backend/app/workflows/script_shot_generate.py:264-273`、`backend/app/services/ai/scope/scoped_script_gateway.py:898-930` |
| `script_scenes.omitted_at`（锁号后删场=OMITTED 软删，行保留）；`content` Text default ''、`content_json` JSONB default '[]' | `backend/app/models/scripts.py:473-509` |
| `script_shots_scene_id_fkey` 是 ON DELETE CASCADE（spec §5 ② 定案：只需回归测试） | migration 已实测 |
| 每集游标 `episodes.current_node_id`；写入口 `EpisodeRepository.set_current_node_id` | `backend/app/repositories/episode_repository.py:306-323` |
| `_deliverable_present` 的 fail-open fallback（无 folder_id、无同名文件夹 → **任意**项目文件即算交付） | `backend/app/services/workflow/advance_service.py:137-209`，B2 计划文档点名 B4 改 fail-closed（`docs/superpowers/plans/2026-08-05-b2-episode-advance-execution.md:8`） |
| 回流点上现在**零联动**（scripts/scenes/shots 写完即返回，无触发器） | 已全量 grep 验证 |
| 集成测试引导：pgvector:pg17 临时容器 + `supabase/ci_bootstrap.sql` + `supabase/schema_baseline.sql` + migrations ≥365（应用顺序照 `.github/workflows/schema-drift.yml`） | B3 同款 |

---

### Task 1: migration 410 — 模板与实例节点 surface 回填

**Files:**
- Create: `supabase/migrations/410_surface_backfill_script_storyboard.sql`

**Interfaces:**
- Produces: 生产模板 329856111474111 的 Script/Storyboard 节点 surface 非 NULL；「个人项目测试 1」44 实例节点中 8 个（4集×2）surface 非 NULL。后续 Task 的集成测试自建数据，不依赖此 migration 的具体行。

- [ ] **Step 1: 写 migration**

```sql
-- 410_surface_backfill_script_storyboard.sql
--
-- B4 前置(spec §5 / B3 遗留):模板早于 B1 建立,11 节点 surface 全 NULL,
-- 导致 44 个已实例化节点全按交付物型保守降级,B4 自动完成与 B5 surface 导航
-- 均不激活。本迁移按节点名回填 script/storyboard 两档。
--
-- renders 本期不映射(用户拍板 2026-08-07):判据 renders_count 数的是
-- script_shots 的 image_url/video_url(镜头出图),与成片表 generated_media
-- 口径分裂;且 Canvas (AI Generation) 节点 B6 退役。等成片链路理清再定。
--
-- 幂等:只填 NULL,不覆盖已有值;按 lower(name) 精确匹配,对全部模板生效
-- (当前生产只有一个模板,名字是模板 seeder 的固定英文名)。
-- surface 冻结语义(spec §5 ③)不受影响:这是一次性数据修复,不是运行时 join。

UPDATE public.workflow_template_nodes
   SET surface = 'script'
 WHERE surface IS NULL AND lower(name) = 'script';

UPDATE public.workflow_template_nodes
   SET surface = 'storyboard'
 WHERE surface IS NULL AND lower(name) = 'storyboard';

-- 实例节点:仅剧集绑定的(episode_id IS NOT NULL)。遗留项目级节点(NULL)
-- 保持 surface NULL = 交付物型降级(spec §5 ③),UI 本就不展示它们。
UPDATE public.project_stage_nodes
   SET surface = 'script', updated_at = now()
 WHERE surface IS NULL AND episode_id IS NOT NULL AND lower(name) = 'script';

UPDATE public.project_stage_nodes
   SET surface = 'storyboard', updated_at = now()
 WHERE surface IS NULL AND episode_id IS NOT NULL AND lower(name) = 'storyboard';
```

- [ ] **Step 2: 本地 ephemeral 库验证（含幂等：跑两遍）**

起一次性容器并按 schema-drift 同顺序应用（端口 55498 与 B3 文档一致，被占则换）：

```bash
docker run -d --name b4-mig-check -e POSTGRES_PASSWORD=postgres -p 55498:5432 pgvector/pgvector:pg17
sleep 5
DSN="postgresql://postgres:postgres@127.0.0.1:55498/postgres"
psql "$DSN" -v ON_ERROR_STOP=1 -q -f supabase/ci_bootstrap.sql
psql "$DSN" -v ON_ERROR_STOP=1 -q -f supabase/schema_baseline.sql
for f in $(ls supabase/migrations/*.sql | awk -F/ '{print $NF"\t"$0}' | sort -n | awk -F'\t' '$1+0>=365 {print $2}'); do
  psql "$DSN" -v ON_ERROR_STOP=1 -q -f "$f" || { echo "FAILED: $f"; break; }
done
# 幂等复跑
psql "$DSN" -v ON_ERROR_STOP=1 -q -f supabase/migrations/410_surface_backfill_script_storyboard.sql
psql "$DSN" -c "SELECT name, surface FROM workflow_template_nodes ORDER BY sort_order"
```

Expected: 两遍都 exit 0；seeder 模板（若 baseline/seed 里有）的 Script/Storyboard 行 surface 已填，其余 NULL。**容器先别删——Task 2/4/6 的集成测试直接用这个 DSN。**

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/410_surface_backfill_script_storyboard.sql
git commit -m "feat(db): mig 410 — 模板/实例节点 surface 回填(script/storyboard) — B4 前置,renders 本期不映射"
```

---

### Task 2: 判据纯函数 + 按集判据查询

**Files:**
- Modify: `backend/app/repositories/episode_repository.py`
- Test: `backend/tests/test_surface_criteria.py`（新建，纯函数单测）
- Test: `backend/tests/integration/test_surface_criteria_db.py`（新建，真-DB）

**Interfaces:**
- Produces:
  - `script_criterion_met(script_count: int, scene_content_count: int) -> bool`（module-level，episode_repository.py）
  - `storyboard_criterion_met(shots_total: int, shots_done: int) -> bool`（同上）
  - `EpisodeRepository.surface_criteria_for_episode(episode_id: str) -> Dict[str, bool]` — 返回 `{"script": bool, "storyboard": bool}`
- Consumes: 既有 `_derive_episode_status` **不改**（展示阶梯与完成判据口径刻意分离，spec §5 表格）。

- [ ] **Step 1: 写失败的纯函数单测**

```python
# backend/tests/test_surface_criteria.py
"""B4 spec §5 判据纯函数。与 _derive_episode_status 的展示阶梯口径独立:
阶梯不查 scene_count(死参数)且把 OMITTED 场次计入;判据必须查非 OMITTED
的有内容场次。"""

from app.repositories.episode_repository import (
    script_criterion_met,
    storyboard_criterion_met,
)


def test_script_criterion_requires_script_and_scene_content():
    assert not script_criterion_met(0, 0)
    # 有剧本但无场次内容 —— 现有阶梯会给 drafting,判据必须不满足(spec §5 ❌ 行)
    assert not script_criterion_met(1, 0)
    assert script_criterion_met(1, 1)
    assert script_criterion_met(2, 3)


def test_storyboard_criterion_requires_all_shots_done():
    assert not storyboard_criterion_met(0, 0)  # 没有镜头 ≠ 完成
    assert not storyboard_criterion_met(5, 4)
    assert storyboard_criterion_met(5, 5)
    assert storyboard_criterion_met(1, 1)
```

- [ ] **Step 2: 跑单测确认失败**

Run: `cd backend && uv run pytest tests/test_surface_criteria.py -v`
Expected: FAIL — `ImportError: cannot import name 'script_criterion_met'`

- [ ] **Step 3: 实现纯函数 + 查询**

在 `episode_repository.py` 的 `_derive_episode_status` 附近加 module-level 纯函数：

```python
def script_criterion_met(script_count: int, scene_content_count: int) -> bool:
    """B4 spec §5 script 档完成判据:该集有剧本且有场次内容。

    与 ``_derive_episode_status`` 的展示阶梯刻意分离 —— 阶梯只判
    ``script_count == 0``(scene_count 是死参数),且把 OMITTED 场次计入;
    完成判据要求至少一个非 OMITTED、内容非空的场次。
    """
    return script_count > 0 and scene_content_count > 0


def storyboard_criterion_met(shots_total: int, shots_done: int) -> bool:
    """B4 spec §5 storyboard 档:镜头全部出卡(boarded 档口径,可复用)。"""
    return shots_total > 0 and shots_done == shots_total
```

在 `EpisodeRepository` 上加查询方法（import 对齐本文件既有的 `select/func/and_/or_` 与模型 import；session 获取方式照抄本文件 `progress_by_project` 用的同一套）：

```python
async def surface_criteria_for_episode(self, episode_id: str) -> Dict[str, bool]:
    """One episode's surface-completion criteria (B4 spec §5).

    scene_content_count = 非 OMITTED 且内容非空(content 文本或 content_json
    数组任一非空)的场次数 —— 这是与 _progress_stmt.scene_count 的两点口径差。
    刻意不吞异常(对齐 progress_by_project 的口径,让写路径 hook 的外层
    try/except 记 warning)。
    """
    stmt = (
        select(
            func.count(func.distinct(ScriptProjects.id)).label("script_count"),
            func.count(func.distinct(ScriptScenes.id))
            .filter(
                ScriptScenes.omitted_at.is_(None),
                or_(
                    ScriptScenes.content != "",
                    func.jsonb_array_length(ScriptScenes.content_json) > 0,
                ),
            )
            .label("scene_content_count"),
            func.count(func.distinct(ScriptShots.id)).label("shots_total"),
            func.count(func.distinct(ScriptShots.id))
            .filter(ScriptShots.status == "done")
            .label("shots_done"),
        )
        .select_from(ScriptProjects)
        .outerjoin(ScriptScenes, ScriptScenes.script_id == ScriptProjects.id)
        .outerjoin(ScriptShots, ScriptShots.scene_id == ScriptScenes.id)
        .where(
            ScriptProjects.episode_id == int(episode_id),
            ScriptProjects.status != "deleted",
        )
    )
    # session 获取:照抄本文件 progress_by_project 的写法
    row = (await session.execute(stmt)).one()
    return {
        "script": script_criterion_met(
            int(row.script_count or 0), int(row.scene_content_count or 0)
        ),
        "storyboard": storyboard_criterion_met(
            int(row.shots_total or 0), int(row.shots_done or 0)
        ),
    }
```

- [ ] **Step 4: 单测通过**

Run: `cd backend && uv run pytest tests/test_surface_criteria.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 写真-DB 集成测试（夹具照抄 `tests/integration/test_episodes_progress_db.py` 的 patched-engine + asyncpg seed 范式；表名/列以该文件为准）**

```python
# backend/tests/integration/test_surface_criteria_db.py
"""True-DB test for EpisodeRepository.surface_criteria_for_episode (B4).

覆盖两点 SQL 口径(纯函数测不到的):OMITTED 场次被排除、空内容场次被排除。

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" \\
        uv run pytest tests/integration/test_surface_criteria_db.py -v -m integration
"""
# 模块头 gate/夹具:照抄 test_episodes_progress_db.py(pytestmark、_TEST_DSN、
# integration_db_url、patched_engine、seed 辅助),前缀用 "__test_b4crit_"。


async def test_script_criterion_excludes_omitted_and_empty_scenes(...):
    # seed: project + episode + script(episode_id 绑定)
    #   scene A: omitted_at = now(), content = 'x'   → 不计
    #   scene B: omitted_at IS NULL, content = ''    → 不计
    # assert criteria == {"script": False, "storyboard": False}
    # 再 seed scene C: content = 'INT. 客厅 - 日'
    # assert criteria["script"] is True


async def test_storyboard_criterion_all_done(...):
    # seed: scene C 下 shots ×2, status 'empty'/'done' → storyboard False
    # UPDATE 第一个 shot status='done' → storyboard True


async def test_deleted_script_excluded(...):
    # script.status='deleted' → {"script": False, "storyboard": False}
```

（测试体用 asyncpg 直插 seed + 调 repository 方法断言，具体 INSERT 列照抄 test_episodes_progress_db.py 已有的 seed 语句——该文件已包含 episodes/script_projects/script_scenes/script_shots 全套插入样例。）

- [ ] **Step 6: 集成测试通过（用 Task 1 留下的容器）**

Run: `cd backend && INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" uv run pytest tests/integration/test_surface_criteria_db.py -v -m integration`
Expected: PASS；同时不设 env 跑一遍确认干净 skip。

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/episode_repository.py backend/tests/test_surface_criteria.py backend/tests/integration/test_surface_criteria_db.py
git commit -m "feat(workflow): B4 判据 — script/storyboard 纯函数 + 按集判据查询(排除 OMITTED/空内容场次)"
```

---

### Task 3: surface_completion 服务（完成动作 + 决策纯函数 + fire seams）

**Files:**
- Create: `backend/app/services/workflow/surface_completion.py`
- Test: `backend/tests/test_surface_completion.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `surface_criteria_for_episode`；既有 `list_nodes_by_episode` / `list_by_origin` / `transition_status` / `set_node_status` / `enqueue_autopilot_tick` / `ORIGIN_KIND` / `build_stage_origin_id`。
- Produces（Task 4/6/7 依赖，签名逐字）:
  - `should_auto_complete(node: Dict[str, Any], criterion_met: bool) -> bool`（纯函数）
  - `sync_surface_completion(project_id: str, episode_id: str, surfaces: Iterable[str] = _AUTO_SURFACES) -> None`
  - `sync_project_surface_completion(project_id: str) -> Dict[str, int]`（返回 `{"episodes": N}`）
  - `fire_surface_sync_for_script(script_id: str, surfaces: Iterable[str] = _AUTO_SURFACES) -> None`
  - `fire_surface_sync_for_scene(scene_id: str, surfaces: Iterable[str] = _AUTO_SURFACES) -> None`
  - `fire_surface_sync_for_shot(shot_id: str, surfaces: Iterable[str] = ("storyboard",)) -> None`
  - 四个 `fire_*`/`sync_*` **全部保证永不 raise**（内部吞错记 warning），caller 无需再包 try/except。

- [ ] **Step 1: 写失败的决策纯函数单测**

```python
# backend/tests/test_surface_completion.py
"""B4 自动完成决策矩阵(纯函数)。"""

from app.services.workflow.surface_completion import should_auto_complete


def _node(**kw):
    base = {
        "surface": "script",
        "skipped": False,
        "review_required": False,
        "status": "in_progress",
    }
    base.update(kw)
    return base


def test_should_auto_complete_matrix():
    assert should_auto_complete(_node(), True)
    assert should_auto_complete(_node(status="pending"), True)  # 未到达组也可先完成
    assert not should_auto_complete(_node(), False)
    assert not should_auto_complete(_node(surface=None), True)       # 交付物型
    assert not should_auto_complete(_node(surface="renders"), True)  # 本期不映射
    assert not should_auto_complete(_node(review_required=True), True)  # review 闸只有人
    assert not should_auto_complete(_node(status="done"), True)       # 幂等
    assert not should_auto_complete(_node(status="in_review"), True)  # 人工评审车道不抢
    assert not should_auto_complete(_node(status="skipped"), True)
    assert not should_auto_complete(_node(skipped=True), True)
```

- [ ] **Step 2: 跑单测确认失败**

Run: `cd backend && uv run pytest tests/test_surface_completion.py -v`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现服务模块**

```python
# backend/app/services/workflow/surface_completion.py
"""B4 (spec §5): surface 节点的自动完成 —— 判据满足时在产物写入回流点把
节点推到 done。

完成动作的优先路径是「关 mirror issue」:transition_status(issue, 'done')
会经 issue_repository._fire_stage_node_sync 把节点投影为 done 并入队
autopilot tick(级联推进由既有机器完成,本模块不直接动游标)。只有镜像
尚未创建(节点在未到达的组)时才走 repo 唯一写入口 set_node_status。

产物删除:本模块【刻意】只做正向(未完成→完成)。派生值回退是事实层面的
(progress 端点重算自然为假,spec §5 ①),存储的节点状态/游标/issue 一律
不回退 —— 想真退,走显式 retreat。

所有公开函数永不 raise(范式 A:函数内 import + try/except + warning),
caller 直接调,不需要再包。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from loguru import logger
from sqlalchemy import select

_AUTO_SURFACES: Tuple[str, ...] = ("script", "storyboard")  # renders 本期不映射(2026-08-07 拍板)
_AUTO_COMPLETABLE = frozenset({"pending", "in_progress"})
_TERMINAL_ISSUE = frozenset({"done", "cancelled"})


def should_auto_complete(node: Dict[str, Any], criterion_met: bool) -> bool:
    """纯决策:这个节点现在该不该被自动推到 done。

    in_review 不碰(评审车道属于人,autopilot 硬线同族);done/skipped 幂等
    跳过;review_required 的 surface 节点保留人工完成路径。
    """
    return bool(
        criterion_met
        and node.get("surface") in _AUTO_SURFACES
        and not node.get("skipped")
        and not node.get("review_required")
        and node.get("status") in _AUTO_COMPLETABLE
    )


async def sync_surface_completion(
    project_id: str, episode_id: str, surfaces: Iterable[str] = _AUTO_SURFACES
) -> None:
    try:
        from app.repositories.episode_repository import get_episode_repository
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        wanted = set(surfaces) & set(_AUTO_SURFACES)
        if not wanted:
            return
        criteria = await get_episode_repository().surface_criteria_for_episode(
            episode_id
        )
        nodes = await get_project_stage_nodes_repository().list_nodes_by_episode(
            project_id, episode_id
        )
        for node in nodes:
            surf = node.get("surface")
            if surf not in wanted:
                continue
            if not should_auto_complete(node, bool(criteria.get(surf))):
                continue
            await _complete_node(project_id, node)
    except Exception as exc:  # noqa: BLE001 — 回流 hook 绝不影响主写
        logger.warning(
            f"[surface-completion] sync failed for project {project_id} "
            f"episode {episode_id}: {exc!r}"
        )


async def _complete_node(project_id: str, node: Dict[str, Any]) -> None:
    from app.repositories.issue_repository import get_issue_repository
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
    )

    node_id = str(node["id"])
    issues = await get_issue_repository().list_by_origin(
        ORIGIN_KIND, build_stage_origin_id(project_id, node_id)
    )
    open_issues = [i for i in issues if i.get("status") not in _TERMINAL_ISSUE]
    if open_issues:
        for issue in open_issues:
            # 投影钩子会刷节点 status=done 并自行入队 autopilot tick
            await get_issue_repository().transition_status(int(issue["id"]), "done")
        logger.info(
            f"[surface-completion] node {node_id} auto-completed via "
            f"{len(open_issues)} mirror issue(s) (project {project_id})"
        )
        return
    # 镜像未创建(节点在未到达的组):走唯一写入口,tick 自己入队
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    await get_project_stage_nodes_repository().set_node_status(node_id, "done")
    logger.info(
        f"[surface-completion] node {node_id} auto-completed directly "
        f"(no mirror issue yet, project {project_id})"
    )
    await _enqueue_tick(project_id)


async def _enqueue_tick(project_id: str) -> None:
    try:
        from app.workflows.autopilot import enqueue_autopilot_tick

        await enqueue_autopilot_tick(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] tick enqueue failed: {exc!r}")


async def sync_project_surface_completion(project_id: str) -> Dict[str, int]:
    """全项目一次性重算(部署点火/修复用,Task 7 的端点调它)。"""
    from app.repositories.episode_repository import get_episode_repository

    episodes = await get_episode_repository().list_by_project(project_id)
    for ep in episodes:
        await sync_surface_completion(project_id, str(ep["id"]))
    return {"episodes": len(episodes)}


# ---- 写路径 seams:各回流点只知道自己手里的 id,这里解析归属 ----

async def _scope_for_script(script_id: str) -> Optional[Tuple[str, str]]:
    from app.db.session import read_scope
    from app.models.scripts import Episodes, ScriptProjects

    async with read_scope() as s:
        row = (
            await s.execute(
                select(Episodes.project_id, ScriptProjects.episode_id)
                .join(Episodes, Episodes.id == ScriptProjects.episode_id)
                .where(ScriptProjects.id == int(script_id))
            )
        ).first()
    if not row or row.episode_id is None:
        return None
    return (str(row.project_id), str(row.episode_id))


async def _scope_for_scene(scene_id: str) -> Optional[Tuple[str, str]]:
    from app.db.session import read_scope
    from app.models.scripts import Episodes, ScriptProjects, ScriptScenes

    async with read_scope() as s:
        row = (
            await s.execute(
                select(Episodes.project_id, ScriptProjects.episode_id)
                .select_from(ScriptScenes)
                .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
                .join(Episodes, Episodes.id == ScriptProjects.episode_id)
                .where(ScriptScenes.id == int(scene_id))
            )
        ).first()
    if not row or row.episode_id is None:
        return None
    return (str(row.project_id), str(row.episode_id))


async def _scope_for_shot(shot_id: str) -> Optional[Tuple[str, str]]:
    from app.db.session import read_scope
    from app.models.scripts import Episodes, ScriptProjects, ScriptScenes, ScriptShots

    async with read_scope() as s:
        row = (
            await s.execute(
                select(Episodes.project_id, ScriptProjects.episode_id)
                .select_from(ScriptShots)
                .join(ScriptScenes, ScriptScenes.id == ScriptShots.scene_id)
                .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
                .join(Episodes, Episodes.id == ScriptProjects.episode_id)
                .where(ScriptShots.id == int(shot_id))
            )
        ).first()
    if not row or row.episode_id is None:
        return None
    return (str(row.project_id), str(row.episode_id))


async def fire_surface_sync_for_script(
    script_id: str, surfaces: Iterable[str] = _AUTO_SURFACES
) -> None:
    try:
        scope = await _scope_for_script(script_id)
        if scope:
            await sync_surface_completion(scope[0], scope[1], surfaces)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] fire(script={script_id}) failed: {exc!r}")


async def fire_surface_sync_for_scene(
    scene_id: str, surfaces: Iterable[str] = _AUTO_SURFACES
) -> None:
    try:
        scope = await _scope_for_scene(scene_id)
        if scope:
            await sync_surface_completion(scope[0], scope[1], surfaces)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] fire(scene={scene_id}) failed: {exc!r}")


async def fire_surface_sync_for_shot(
    shot_id: str, surfaces: Iterable[str] = ("storyboard",)
) -> None:
    try:
        scope = await _scope_for_shot(shot_id)
        if scope:
            await sync_surface_completion(scope[0], scope[1], surfaces)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] fire(shot={shot_id}) failed: {exc!r}")
```

（注意：模型 import 路径以 `episode_repository.py` 顶部实际写法为准——若它从 `app.models` 聚合导入则对齐。）

- [ ] **Step 4: 单测通过**

Run: `cd backend && uv run pytest tests/test_surface_completion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workflow/surface_completion.py backend/tests/test_surface_completion.py
git commit -m "feat(workflow): B4 surface 自动完成服务 — 关镜像 issue 投影节点 + 无镜像走唯一写入口,决策纯函数化"
```

---

### Task 4: 回流点接线 + 真-DB 端到端回归

**Files:**
- Modify: `backend/app/repositories/script_repository.py`（`create` / `get_or_create_for_episode` / `update` / `soft_delete` 尾部）
- Modify: `backend/app/repositories/script_scene_repository.py`（`create` / `create_with_content` / `create_after_lock` / `apply_element_ops` / `delete` 尾部）
- Modify: `backend/app/repositories/script_shot_repository.py`（`update_status` / `delete` 尾部）
- Modify: `backend/app/services/ai/scope/scoped_script_gateway.py`（`set_shot_status` 尾部）
- Test: `backend/tests/integration/test_surface_completion_db.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `fire_surface_sync_for_script` / `fire_surface_sync_for_scene` / `fire_surface_sync_for_shot`（永不 raise，caller 不包 try/except）。

**接线原则**：hook 调用放在**事务提交之后**（即各方法的 `async with write_scope()...` 块退出后、return 前）；只挂**可能产生正向转变**的路径。逐点清单：

| 位置 | 挂哪个 seam | 触发条件 | 为什么 |
|---|---|---|---|
| `script_repository.create` / `get_or_create_for_episode` 成功返回前 | `fire_surface_sync_for_script(str(row["id"]))` | 无条件 | 新剧本落集 |
| `script_repository.update` 成功返回前 | 同上 | 无条件（含 episode_id 重挂载：重挂后按**新** episode 重算） | 重挂可能让目标集判据变真 |
| `script_repository.soft_delete` 成功返回前 | 同上（script 行还在，能解析归属） | 无条件 | 多剧本剧集删掉未完成的那本 → storyboard 判据可能变真 |
| `script_scene_repository.create` / `create_with_content` / `create_after_lock` / `apply_element_ops` 成功返回前 | `fire_surface_sync_for_scene(str(scene_id))` | 无条件 | 场次内容出现 → script 判据 |
| `script_scene_repository.delete` | **进方法时先取 `script_id`**（该方法本就先读行判断锁号），提交后 `fire_surface_sync_for_script(str(script_id))` | 无条件（硬删行已消失，用 script 归属解析） | 删场级联删镜头 → storyboard 判据可能变真（spec §5 ② 回归） |
| `script_shot_repository.update_status` 成功返回前 | `fire_surface_sync_for_shot(str(shot_id))` | 仅当 `status == "done"` | 唯一正向转变 |
| `script_shot_repository.delete` | 进方法时先取 `scene_id`，提交后 `fire_surface_sync_for_scene(str(scene_id), surfaces=("storyboard",))` | 无条件 | 删掉未完成镜头 → 判据可能变真 |
| `scoped_script_gateway.set_shot_status` 成功返回前 | `fire_surface_sync_for_shot(str(shot.id))` | 仅当 `status == "done"` | agent 车道绕过 repo，必须显式接（「触发路径必须类型化回显」同族教训） |

**刻意不挂**：`create`/`create_many`（shots，新增只会让判据变假）、`update`（shot 内容车道，白名单不含 status）、`update_video_url`（renders 未映射）、`move_scene`/`move_shot`（不改计数与内容）、`update_meta`（场次元数据白名单不含 content）。

- [ ] **Step 1: 写失败的真-DB 集成测试（夹具照抄 `test_episode_instantiation_db.py`：pytestmark/integration gate/patched_engine/seed 前缀 `__test_b4wire_`）**

场景清单（每个一个 test 函数；seed 统一为：project + episode + 两个 surface 节点【Script surface='script' 组1、Storyboard surface='storyboard' 组2】+ episodes.current_node_id 指向 Script 节点 + Script 节点的 mirror issue【origin_kind='project_stage'，origin_id=`project_stage:{pid}:{node_id}`，status='in_progress'】）：

```python
async def test_scene_content_completes_script_node(...):
    # ScriptProjectRepository.get_or_create_for_episode + ScriptSceneRepository
    # .create_with_content(content='INT. 客厅 - 日 ...')
    # assert: Script 节点 status=='done';mirror issue status=='done'

async def test_all_shots_done_completes_storyboard_node(...):
    # seed 场次+2 shots(empty);逐个 update_status(shot, 'done')
    # 第一个 done 后 storyboard 节点仍非 done;第二个 done 后=='done'
    # (Storyboard 节点无镜像 → 走 set_node_status 直写路径)

async def test_scene_delete_cascades_and_recomputes_storyboard(...):
    # spec §5 ② 回归:场 A shots 全 done,场 B shots 有 empty → 非 done
    # ScriptSceneRepository.delete(场 B)(未锁号 → 硬删,FK CASCADE 删镜头)
    # assert: storyboard 判据重算 → 节点 done;script_shots 里场 B 的行已消失

async def test_omitted_scene_does_not_satisfy_script(...):
    # 唯一场次 omitted_at=now() → Script 节点保持 in_progress

async def test_review_required_surface_node_not_auto_completed(...):
    # Script 节点 review_required=True → 场次内容写入后 status 不变

async def test_product_delete_does_not_revert(...):
    # 先让 Script 节点 done;再 ScriptProjectRepository.soft_delete(script)
    # assert: 节点仍 done;episodes.current_node_id 未变;issue 仍 done

async def test_unbound_script_is_noop(...):
    # episode_id IS NULL 的剧本走 create → 不炸、无节点变化
```

- [ ] **Step 2: 跑集成测试确认失败**

Run: `cd backend && INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" uv run pytest tests/integration/test_surface_completion_db.py -v -m integration`
Expected: FAIL — 节点状态没有被刷（hook 还没接）

- [ ] **Step 3: 按上面清单逐点接线**

每处两行式样（以 `update_status` 为例，放在事务块退出后）：

```python
        # B4 回流点:镜头置 done 可能让本集 storyboard 判据变真(spec §5)。
        # fire_* 永不 raise,不影响主写。
        if status == "done":
            from app.services.workflow.surface_completion import (
                fire_surface_sync_for_shot,
            )

            await fire_surface_sync_for_shot(str(shot_id))
```

`delete` 类先在方法开头把归属 id 存局部变量（行删除后无法解析），提交后调 seam。

- [ ] **Step 4: 集成测试通过 + 相关既有单测回归**

Run: `cd backend && INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" uv run pytest tests/integration/test_surface_completion_db.py tests/integration/test_surface_criteria_db.py -v -m integration && uv run pytest tests/test_script_repository* tests/test_script_scenes* tests/test_episode_progress.py -q`
Expected: 全 PASS（既有单测里 repo 方法被 mock/Fake 的，若 Fake 缺方法导致 hook 内 import 后调用失败——不会，seam 自吞错并 warning，主断言不受影响）

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/script_repository.py backend/app/repositories/script_scene_repository.py backend/app/repositories/script_shot_repository.py backend/app/services/ai/scope/scoped_script_gateway.py backend/tests/integration/test_surface_completion_db.py
git commit -m "feat(workflow): B4 回流点接线 — 剧本/场次/镜头写路径挂 surface 自动完成(含 agent 车道),spec §5 ② 级联回归"
```

---

### Task 5: `_deliverable_present` fallback 改 fail-closed

**Files:**
- Modify: `backend/app/services/workflow/advance_service.py`（`_deliverable_present` 尾部 + docstring）
- Test: `backend/tests/test_advance_predicate.py`（改既有 + 加一条）

**Interfaces:**
- 行为变化：无 `folder_id` 且无同名文件夹时，从「任意非回收站项目文件即算交付」改为 **False**。`folder_id` 主路径与同名文件夹 fallback 保持不变。

- [ ] **Step 1: 写失败测试**

在 `test_advance_predicate.py` 中（monkeypatch 范式照抄同文件 `:320` 的 `test_deliverable_present_allows_forward`）：

```python
async def test_deliverable_fallback_fail_closed(monkeypatch):
    """B4:无 folder_id、无同名文件夹 → 即便项目里有别的文件,也判未交付。
    (B2 计划文档点名的 fail-closed;按集节点的 stage 文件夹带 episode 前缀,
    同名匹配本就不命中,任意文件兜底等于给按集门禁开了后门。)"""
    # 安排:node 无 folder_id;get_folders 返回 [](无同名);get_project_files
    # 返回 [一个文件]
    # 断言:await _deliverable_present("p1", node) is False
```

- [ ] **Step 2: 确认失败**

Run: `cd backend && uv run pytest tests/test_advance_predicate.py -v -k fail_closed`
Expected: FAIL — 当前返回 True

- [ ] **Step 3: 实现**

`advance_service.py` `_deliverable_present` 最后一行 `return len(files) > 0` 改为：

```python
    # B4 fail-closed:无显式 folder_id 且无同名文件夹 → 视为未交付。
    # 旧行为(任意非回收站文件即算)对按集节点是后门:episode 前缀文件夹
    # (node_folders._episode_scoped_folder_name)不会同名命中,于是全落到
    # 这个兜底,任何一集的任何文件都能替所有集过 Gate2。
    return False
```

同步删掉此前专为兜底做的 `files = await repo.get_project_files(...)` 读取（不再需要就别查），并更新 docstring 的 Fallback 段落。

- [ ] **Step 4: 全部 advance 谓词测试回归**

Run: `cd backend && uv run pytest tests/test_advance_predicate.py tests/test_workflow_flow_rules.py -q`
Expected: PASS（若既有某条测试依赖旧 fail-open 行为，改它的安排为「有同名文件夹」或断言反转——那条测试钉的就是本次要改的行为）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workflow/advance_service.py backend/tests/test_advance_predicate.py
git commit -m "fix(workflow): B4 交付物门禁 fallback 改 fail-closed — 去掉「任意项目文件即算交付」后门"
```

---

### Task 6: episodes/progress 真数据（B5 T-B5.4/T-B5.5 数据源）

**Files:**
- Modify: `backend/app/repositories/episode_repository.py`（`_progress_stmt` 加列、`_progress_row` 加字段、`progress_by_project` 加两个批量 rollup 查询）
- Test: `backend/tests/test_episode_progress.py`（`_progress_row` 形状单测跟改）
- Test: `backend/tests/integration/test_episodes_progress_db.py`（加新字段断言）

**Interfaces:**
- Produces：progress 每行**新增**键（旧键全部保留，前端零破坏）：

```python
"workflow": {
    "nodes_total": int,        # 本集节点数(排除 skipped 布尔与 status='skipped')
    "nodes_done": int,         # 其中 status='done'
    "current_node_id": Optional[str],   # episodes.current_node_id,str 化防 BIGINT 精度
    "needs_input_count": int,  # 本集节点镜像 issue 中 needs_input 的数量
},
"surface_state": {"script": bool, "storyboard": bool},  # 判据当前值(派生,可与节点 status 不一致=产物被删)
```

- [ ] **Step 1: 写失败测试**

单测（`test_episode_progress.py` 补一条形状断言）：

```python
def test_progress_row_carries_workflow_and_surface_state():
    row = _progress_row(_fake_row(...), workflow_rollup={...}, )  # 按实现后的新签名
    assert row["workflow"]["nodes_total"] == 3
    assert row["workflow"]["needs_input_count"] == 1
    assert row["surface_state"] == {"script": True, "storyboard": False}
```

集成（`test_episodes_progress_db.py` 补）：seed 本集 3 节点（1 done）+ 1 条 needs_input 镜像 issue（`status='needs_followup'`, `execution_state={"agent_outcome":"needs_input"}`, `origin_kind='project_stage'`, `origin_id='project_stage:{pid}:{node_id}'`）→ 断言上述形状。

- [ ] **Step 2: 确认失败**

Run: `cd backend && uv run pytest tests/test_episode_progress.py -v -k workflow_and_surface`
Expected: FAIL

- [ ] **Step 3: 实现**

1. `_progress_stmt` 的 select 里加两列：`Episodes.current_node_id`、`scene_content_count`（filter 口径与 Task 2 完全一致——从 Task 2 的查询提取成模块级 helper `_scene_content_count_col()` 复用，两处不许各写一份）。
2. `progress_by_project` 加两个按项目批量查询（一次 group by episode_id，避免 N+1；全 ORM）：

```python
# nodes rollup
select(
    ProjectStageNodes.episode_id,
    func.count(ProjectStageNodes.id).label("nodes_total"),
    func.count(ProjectStageNodes.id)
    .filter(ProjectStageNodes.status == "done")
    .label("nodes_done"),
).where(
    ProjectStageNodes.project_id == int(project_id),
    ProjectStageNodes.episode_id.isnot(None),
    ProjectStageNodes.skipped.is_(False),
    ProjectStageNodes.status != "skipped",
).group_by(ProjectStageNodes.episode_id)

# needs_input rollup(origin_id 由节点行拼出,谓词复用 needs_input_predicate)
select(
    ProjectStageNodes.episode_id,
    func.count(Issues.id).label("needs_input_count"),
).select_from(ProjectStageNodes).join(
    Issues,
    and_(
        Issues.origin_kind == ORIGIN_KIND,
        Issues.origin_id
        == func.concat(
            ORIGIN_KIND + ":",
            func.cast(ProjectStageNodes.project_id, Text),
            ":",
            func.cast(ProjectStageNodes.id, Text),
        ),
    ),
).where(
    ProjectStageNodes.project_id == int(project_id),
    ProjectStageNodes.episode_id.isnot(None),
    needs_input_predicate(),
).group_by(ProjectStageNodes.episode_id)
```

3. `_progress_row` 组装新键；`surface_state` 用 Task 2 纯函数对 `script_count`/`scene_content_count`/`shots_total`/`shots_done` 现算，**不**另发查询。
4. `episodes_router.get_episodes_progress` 不动（dict 透传）。

- [ ] **Step 4: 测试通过**

Run: `cd backend && uv run pytest tests/test_episode_progress.py -q && INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" uv run pytest tests/integration/test_episodes_progress_db.py -v -m integration`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/episode_repository.py backend/tests/test_episode_progress.py backend/tests/integration/test_episodes_progress_db.py
git commit -m "feat(workflow): B4 episodes/progress 真数据 — 每集节点计数/游标/needs_input/判据态,B5 占位数据源就位"
```

---

### Task 7: 点火端点 — 全项目 surface 完成态重算

**Files:**
- Modify: `backend/app/api/projects_router.py`（放在 B3 的 `reinstantiate-per-episode` 端点旁）
- Test: `backend/tests/integration/test_surface_completion_db.py`（补一条）

**Interfaces:**
- Produces: `POST /api/v1/projects/{project_id}/workflow/surface-completion/sync` → `{"success": true, "data": {"episodes": N}}`，守卫 `verify_project_write_access`。

- [ ] **Step 1: 写失败测试**

```python
async def test_project_wide_sync_completes_satisfied_episodes(...):
    # seed 两集:Ep1 判据已满足但节点 pending(模拟部署前就有产物),Ep2 空
    # 直接调 sync_project_surface_completion(project_id)
    # assert 返回 {"episodes": 2};Ep1 Script 节点 done;Ep2 不变
```

（端点本体薄到只做守卫+透传，路由声明照同文件 `reinstantiate-per-episode` 的既有写法，不单独起 TestClient。）

- [ ] **Step 2: 确认失败 → 实现**

```python
@router.post("/projects/{project_id}/workflow/surface-completion/sync")
async def sync_surface_completion_for_project(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_write_access),
) -> Dict[str, Any]:
    """B4 点火/修复:重算全项目每集的 surface 完成态(幂等,只正向)。
    部署后既有产物不会自己触发回流 hook,owner 调一次此端点补齐。"""
    from app.services.workflow.surface_completion import (
        sync_project_surface_completion,
    )

    try:
        data = await sync_project_surface_completion(project_id)
        return {"success": True, "data": data}
    except Exception as exc:
        logger.error(
            f"[Workflow] surface-completion sync for {project_id} failed: {exc}"
        )
        raise HTTPException(status_code=500, detail="Surface completion sync failed")
```

- [ ] **Step 3: 测试通过**

Run: `cd backend && INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" uv run pytest tests/integration/test_surface_completion_db.py -v -m integration`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/projects_router.py backend/tests/integration/test_surface_completion_db.py
git commit -m "feat(workflow): B4 点火端点 — 全项目 surface 完成态一次性重算(owner 守卫,幂等)"
```

---

### Task 8: 全量验证 + 发 PR

- [ ] **Step 1: 后端全量单测 + 集成套件**

Run: `cd backend && uv run pytest -q -m "not integration" && INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" uv run pytest tests/integration -q -m integration`
Expected: 与 master 基线一致的通过面（无新增失败）；跑完 `docker rm -f b4-mig-check`。

- [ ] **Step 2: migration 撞号复核**

Run: `git fetch origin master && ls supabase/migrations/ | sort -n | tail -3`
Expected: 410 仍是最大号；否则重命名并同步文件内注释。

- [ ] **Step 3: 用 `/ship` 发 PR**（自动 merge base + tests + review + VERSION/CHANGELOG）

PR 描述要点：B4 = spec §9 第④期；四件套（判据接线 / surface 回填 mig 410 / deliverable fail-closed / progress 真数据）；renders 不映射与 B5 数据源为用户 2026-08-07 拍板；产物删除只回退派生值不回退游标（spec §5 ①）。

- [ ] **Step 4: 合并后生产点火（人工步骤，写进 PR 描述的 checklist）**

1. migration 410 由 `run-migration.yml` 自动落库 → `SELECT name, surface FROM project_stage_nodes WHERE project_id=291022264100262 AND surface IS NOT NULL` 应有 8 行。
2. Owner 调 `POST /api/v1/projects/291022264100262/workflow/surface-completion/sync`（⚠️ B3 教训：调试账号非 owner 过不了 `verify_project_write_access`，个人项目无成员——要么用户亲自调，要么容器内 python 直调 `sync_project_surface_completion`）。
3. 验收口径：有剧本内容的集，Script 节点 done + 该集游标经 autopilot 级联推进到下一组；`application_logs` 无 `[surface-completion]` ERROR。

---

## Self-Review 记录

- **Spec 覆盖**：§5 表格 script/storyboard 判据（Task 2）、null=交付物型（`should_auto_complete` 的 surface gate）、①删除回退语义（Task 4 `test_product_delete_does_not_revert` + progress 的 `surface_state` 派生展示）、②级联回归测试（Task 4）、③冻结拷贝（B3 已做，Task 1 只回填数据）、回流点不轮询（Task 4 清单）、B2 计划点名的 deliverable fail-closed（Task 5）、用户拍板两条（Task 1 注释 + Task 6）。renders 判据行为本期不激活（不映射），已在 mig 410 注释与 PR 描述中声明。
- **类型一致性**：`fire_surface_sync_for_*`/`sync_surface_completion`/`sync_project_surface_completion`/`should_auto_complete`/`script_criterion_met`/`storyboard_criterion_met` 各任务引用与定义签名逐字一致。
- **已知边界（写进 PR 描述，不在本期修）**：video-only 镜头永远进不了 `shots_done`（既有行为，`tests/test_episode_progress.py:60` 钉死）；`_storyboard_progress_stmt` 第二套聚合口径未合并；展示阶梯的 `scene_count` 死参数维持原样。
