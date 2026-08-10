# Agent Run 撤销（Run-level Undo）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 agent run 补齐「整 run 一键撤销」：shot 写入记账 + 归属列、run 级逆操作端点（跳过 + 类型化报告）、TurnWriteSummary 的 Undo 按钮；末尾附三个独立小尾巴（strip 解耦、场次卡深链、结构化上下文 payload）。

**Architecture:** 新表 `script_shot_ops` 在 `scoped_script_gateway` 的写事务内记账（不侵入 `script_ops` watermark 语义）；撤销端点按账本逐项 CAS（delete/update 带完整 WHERE 条件，与并发编辑天然互斥），scene 正文走既有 `apply_element_ops` 以 `actor='undo:<run_id>'` 作为新 forward op；前端按钮挂 `TurnWriteSummary` 头部行，报告就地渲染。

**Tech Stack:** FastAPI + SQLAlchemy async ORM（禁裸 SQL）、PostgreSQL（migration 413）、React 19 + vitest、i18next。

**Spec:** `docs/superpowers/specs/2026-08-09-agent-run-undo-design.md`（已拍板）。

## Global Constraints

- 与用户沟通用中文；**UI 文本一律英文**（Title Case），禁 emoji，图标用 lucide。
- i18n：`frontend/public/locales/en.json` 与 `zh.json` 必须同步加 key（camelCase）。
- 后端**禁止新增 `text()` 裸 SQL**——全部走 SQLAlchemy ORM 表达式（2026-08-04 立约）。
- `agent_runs.id` / `script_shots.id` / `script_shot_ops.id` 都是 **BIGINT snowflake**，HTTP 层传字符串，进 ORM 前 `int()` 强转；前端拿到的是 numeric string，**不要** parseInt（2^53 精度）。
- 状态色用语义 token（ok/warn/danger/info/agent），不用 indigo/amber 等旧色相类。
- 前端 `catch` 不许静默吞错：`catch (err) { console.error(...) }`。
- 每个 task 内：先写 RED 测试 → 实现 → GREEN → commit。commit message 用仓库惯例（中文 conventional commit，如 `feat(ai): ...`）。
- 工作目录是 worktree `.worktrees/feat-b4-completion-criteria`（分支 `feat/agent-undo`）；测试命令后端 `cd backend && uv run pytest ...`，前端 `cd frontend && npx vitest run ...`。
- **两个守卫测试是硬约束，不许松绑**：`backend/tests/test_scope_resolver_single_choke_point.py` 的 `test_scoped_script_gateway_calls_only_the_ops_channel`（gateway 只许调 `apply_element_ops` 这一个 repo 方法）与 `test_scoped_script_gateway_takes_only_resolved_handles`（gateway 公开函数只收 Resolved* handle）。本计划的设计已绕开两者（记账用裸 ORM `insert()`，撤销逻辑放新文件），实现时若撞上守卫，是实现走偏了，不是该改守卫。

---

### Task 1: Migration 413 + ORM 模型（归属列 / 账本表 / undone_at）

**Files:**
- Create: `supabase/migrations/413_agent_run_undo.sql`
- Modify: `backend/app/models/scripts.py`（`ScriptShots` 加列 + 新 `ScriptShotOps` 类）
- Modify: `backend/app/models/agents.py`（`AgentRuns` 加 `undone_at`）
- Modify: `backend/app/models/__init__.py`（导出 `ScriptShotOps`）

**Interfaces:**
- Produces: ORM 类 `ScriptShotOps`（列：`id, run_id, shot_id, scene_id, action, before_json, after_json, created_at`）；`ScriptShots.created_by_agent_run_id: Optional[int]`；`AgentRuns.undone_at: Optional[datetime]`。Task 2/4/5 直接 import 这些。

- [ ] **Step 1: fetch 复核 migration 取号**

```bash
git fetch origin master
git log origin/master --oneline -1
ls supabase/migrations/ | sort | tail -5
```

写 spec 时最新是一对 412 撞号（`412_retire_canvas_stage_node` / `412_social_accounts_realtime`），预定取 413。若 origin/master 上已出现 413+，顺延取号并同步改本文件与 SQL 头注释里的编号引用（只改本计划新增的引用，别误伤既有号）。

- [ ] **Step 2: 写 migration SQL**

```sql
-- 413_agent_run_undo.sql
--
-- Agent Run 撤销立项（spec: docs/superpowers/specs/2026-08-09-agent-run-undo-design.md）
-- 三件套：
--   1. script_shots.created_by_agent_run_id — agent 建卡归属列。只在 agent
--      create_shot 时写；人写 / Auto-Storyboard 的卡保持 NULL。
--   2. script_shot_ops — shot 写入账本，与 script_ops 平行、互不侵入：
--      script_ops.op_seq == content_version 的 watermark 语义是 P1 版本控制
--      的核心不变量，shot 写入不碰 content_version，塞进去会破坏全链。
--   3. agent_runs.undone_at — run 级一次性撤销标记，驱动前端按钮态与端点幂等。
--
-- agent_runs.id 是 BIGINT snowflake（mig 232），不是 UUID。
--
-- RLS：script_shot_ops 不启用。它是后端内部账本（不经 PostgREST 暴露，
-- 只有服务端超管连接读写）；剧本域租户策略（mig 408 起的第三层）后续分期
-- 覆盖时再一并处理，现在启用反而会挡住 CI ephemeral 库的非平台角色。

ALTER TABLE public.script_shots
  ADD COLUMN IF NOT EXISTS created_by_agent_run_id BIGINT NULL
  REFERENCES public.agent_runs(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS public.script_shot_ops (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  run_id BIGINT NOT NULL REFERENCES public.agent_runs(id) ON DELETE CASCADE,
  shot_id BIGINT NOT NULL REFERENCES public.script_shots(id) ON DELETE CASCADE,
  -- 冗余列，供 scene 级查询；不加 FK（shot 删除时行随 CASCADE 消失，scene
  -- 维度只做过滤，不需要参照完整性）。
  scene_id BIGINT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('create', 'update')),
  -- update 时旧值快照（仅本次涉及字段）；create 为 NULL。
  before_json JSONB NULL,
  -- 写完后的字段快照：create 为全部 _WRITABLE_SHOT_FIELDS（显式含 NULL），
  -- update 仅本次涉及字段。
  after_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_shot_ops_run
  ON public.script_shot_ops (run_id);

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS undone_at TIMESTAMPTZ NULL;
```

- [ ] **Step 3: ORM 模型同步**

`backend/app/models/scripts.py`：

在 `ScriptShots` 类的 `updated_at` 之后加列（保持既有 mapped_column 风格）：

```python
    created_by_agent_run_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment="mig 413: agent CreateShot 归属；人写 / Auto-Storyboard 为 NULL",
    )
```

同文件 `ScriptOps` 类之后新增（照抄 `ScriptOps` 的 `__table_args__` 风格）：

```python
class ScriptShotOps(Base):
    __tablename__ = "script_shot_ops"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="script_shot_ops_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["shot_id"],
            ["public.script_shots.id"],
            ondelete="CASCADE",
            name="script_shot_ops_shot_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_shot_ops_pkey"),
        Index("idx_script_shot_ops_run", "run_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scene_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
```

`backend/app/models/agents.py`：`AgentRuns` 列区（`ended_at` 附近）加：

```python
    undone_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="mig 413: run 级一次性撤销标记；NULL = 未撤销",
    )
```

`backend/app/models/__init__.py`：在 `ScriptOps` 旁边的 import 列表与 `__all__` 各加一行 `ScriptShotOps`。

- [ ] **Step 4: 验证 import 与既有测试**

```bash
cd backend && uv run python -c "from app.models import ScriptShotOps, AgentRuns, ScriptShots; print(ScriptShotOps.__tablename__, AgentRuns.undone_at is not None, ScriptShots.created_by_agent_run_id is not None)"
uv run pytest tests/test_scope_resolver_single_choke_point.py -x -q
```

Expected: 打印 `script_shot_ops True True`；守卫测试全绿（models/scripts.py 在 ORM 白名单里，加类不违规）。

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/413_agent_run_undo.sql backend/app/models/
git commit -m "feat(db): agent run 撤销三件套 — shot 归属列 + script_shot_ops 账本 + undone_at (mig 413)"
```

---

### Task 2: Gateway 记账（create_shot 归属 + create/update 账本）

**Files:**
- Modify: `backend/app/services/ai/scope/scoped_script_gateway.py`
- Modify: `backend/tests/test_screenwriting_tools.py`（既有 create/update 测试补队列结果）
- Test: `backend/tests/test_script_shot_ops_ledger.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `ScriptShotOps` ORM 类；既有 `AgentRunScope`（`run_id: str`）、`ResolvedScene` / `ResolvedShot`（`shot.scene_id` 可用）。
- Produces: `create_shot` / `update_shot` 的对外签名与返回值**完全不变**（守卫测试 `takes_only_resolved_handles` 不受影响）；副作用新增：同事务写 `script_shot_ops` 行 + `created_by_agent_run_id`。账本行 shape：create → `after_json` = 全部 6 个 `_WRITABLE_SHOT_FIELDS`（含显式 None）；update → `before_json`/`after_json` 仅含本次涉及字段。

**实现要点（先读再写）：**
- 账本 INSERT 用本文件既有裸 ORM 写法 `insert(ScriptShotOps)`，**不新增 repository 调用**——`test_scoped_script_gateway_calls_only_the_ops_channel` AST 白名单不需松绑。
- 现有 `create_shot`/`update_shot` 的 `.returning(...)` 缺 `ScriptShots.lighting`（`_WRITABLE_SHOT_FIELDS` 有它）——两处都补上，`_shot_dict` 不动（多余属性无害）。
- 测试路径的 sentinel run_id 是 `"0"`（`agent_run_scope._SENTINEL_RUN_ID`），必须跳过记账与归属，否则 FK 违约。

- [ ] **Step 1: 写 RED 测试**

新建 `backend/tests/test_script_shot_ops_ledger.py`，复用 `test_screenwriting_tools.py` 的 `_CaptureSession` / `_FakeResult` / `_ScopeCtx` 基建（直接 import 或复制到本文件，以该文件现状为准；若是私有类就复制，别跨文件 import 私有名）：

```python
"""scoped_script_gateway 记账（mig 413）：create/update 同事务写 script_shot_ops。"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.services.ai.scope.scoped_script_gateway as gateway_mod
from app.services.ai.scope.agent_run_scope import AgentRunScope

# ↓ 按 test_screenwriting_tools.py 现有定义复制 _CaptureSession / _FakeResult /
#   _ScopeCtx / _resolved_scene / _resolved_shot 这五样（保持行为一致）。

_RUN_ID = "800100000000000009"

def _scope(run_id=_RUN_ID) -> AgentRunScope:
    return AgentRunScope(run_id=run_id, user_id="u1", project_id=1, team_id=None)

def _returning_row(**over):
    base = dict(id=900, shot_number=1, shot_type="CU", camera_angle=None,
                camera_movement=None, focal_length="85mm", lighting=None,
                description="Her hands.", status="empty")
    base.update(over)
    return SimpleNamespace(**base)

@pytest.mark.asyncio
async def test_create_shot_writes_attribution_and_full_snapshot_ledger_row():
    session = _CaptureSession([
        _FakeResult(scalar=0),                       # MAX(shot_number)
        _FakeResult(scalar=0),                       # MAX(sort_order)
        _FakeResult(first_row=_returning_row()),     # INSERT..RETURNING
        _FakeResult(),                               # ledger INSERT
    ])
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="1")),
    ):
        await gateway_mod.create_shot(
            _scope(), _resolved_scene(),
            {"shot_type": "CU", "focal_length": "85mm", "description": "Her hands."},
        )
    inserts = [s for s in session.statements if s.__class__.__name__ == "Insert"]
    assert len(inserts) == 2
    shot_values = inserts[0].compile().params
    assert shot_values["created_by_agent_run_id"] == int(_RUN_ID)
    ledger = inserts[1].compile().params
    assert ledger["run_id"] == int(_RUN_ID)
    assert ledger["shot_id"] == 900
    assert ledger["action"] == "create"
    assert ledger["before_json"] is None
    # create 快照必须覆盖全部 6 个 writable 字段（未写的显式 None）
    assert ledger["after_json"] == {
        "shot_type": "CU", "camera_angle": None, "camera_movement": None,
        "focal_length": "85mm", "lighting": None, "description": "Her hands.",
    }

@pytest.mark.asyncio
async def test_create_shot_sentinel_run_skips_ledger_and_attribution():
    session = _CaptureSession([
        _FakeResult(scalar=0), _FakeResult(scalar=0),
        _FakeResult(first_row=_returning_row()),
    ])
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="1")),
    ):
        await gateway_mod.create_shot(_scope(run_id="0"), _resolved_scene(), {"description": "x"})
    inserts = [s for s in session.statements if s.__class__.__name__ == "Insert"]
    assert len(inserts) == 1
    assert "created_by_agent_run_id" not in inserts[0].compile().params

@pytest.mark.asyncio
async def test_update_shot_ledger_carries_only_touched_fields_before_and_after():
    session = _CaptureSession([
        # 事务内先 SELECT ... FOR UPDATE 旧值
        _FakeResult(first_row=_returning_row(shot_type="MS", focal_length="35mm")),
        _FakeResult(first_row=_returning_row(shot_type="CU", focal_length="35mm")),  # UPDATE..RETURNING
        _FakeResult(),                               # ledger INSERT
    ])
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for_shot", AsyncMock(return_value="1")),
    ):
        await gateway_mod.update_shot(_scope(), _resolved_shot(), {"shot_type": "CU"})
    ledger = [s for s in session.statements if s.__class__.__name__ == "Insert"][0].compile().params
    assert ledger["action"] == "update"
    assert ledger["before_json"] == {"shot_type": "MS"}
    assert ledger["after_json"] == {"shot_type": "CU"}
    assert ledger["scene_id"] == _resolved_shot().scene_id
```

- [ ] **Step 2: 跑测试确认 RED**

```bash
cd backend && uv run pytest tests/test_script_shot_ops_ledger.py -v
```

Expected: FAIL（`created_by_agent_run_id` 不在 params / 只有一条 Insert / update 无 SELECT 旧值）。

- [ ] **Step 3: 实现 gateway 记账**

`scoped_script_gateway.py`：

① import 区把 `ScriptShotOps` 加进 `from app.models import ...`。

② `_WRITABLE_SHOT_FIELDS` 定义之后加模块私有 helper：

```python
def _ledger_run_id(scope: AgentRunScope) -> Optional[int]:
    """账本/归属用的 BIGINT run id；测试路径的 sentinel run_id="0"（见
    agent_run_scope._SENTINEL_RUN_ID）没有对应 agent_runs 行，写了会 FK
    违约——返回 None 表示这次写入不记账、不归属。"""
    try:
        rid = int(str(scope.run_id))
    except (TypeError, ValueError):
        return None
    return rid if rid > 0 else None
```

③ `create_shot`：`.returning(...)` 补 `ScriptShots.lighting`；`values["sort_order"] = ...` 之后：

```python
        rid = _ledger_run_id(scope)
        if rid is not None:
            values["created_by_agent_run_id"] = rid
```

INSERT 执行后、**仍在 `async with write_scope()` 块内**（同事务——写入失败不留账，记账失败连卡一起回滚）：

```python
        if rid is not None:
            await session.execute(
                insert(ScriptShotOps).values(
                    run_id=rid,
                    shot_id=row.id,
                    scene_id=scene.id,
                    action="create",
                    before_json=None,
                    after_json={
                        f: getattr(row, f) for f in _WRITABLE_SHOT_FIELDS
                    },
                )
            )
```

注意 `row is None` 的 pragma 分支要先于记账（保持现状的先取 row 再用）。

④ `update_shot`：`.returning(...)` 补 `ScriptShots.lighting`；事务内先锁读旧值再 UPDATE：

```python
    values = _writable(fields)
    if not values:
        return None
    rid = _ledger_run_id(scope)
    async with write_scope() as session:
        old = (
            await session.execute(
                select(*[getattr(ScriptShots, f) for f in _WRITABLE_SHOT_FIELDS])
                .where(ScriptShots.id == shot.id)
                .with_for_update()
            )
        ).first()
        row = (
            await session.execute(
                update(ScriptShots)
                .where(ScriptShots.id == shot.id)
                .values(**values)
                .returning(...)  # 现有列 + lighting
            )
        ).first()
        if rid is not None and old is not None and row is not None:
            await session.execute(
                insert(ScriptShotOps).values(
                    run_id=rid,
                    shot_id=shot.id,
                    scene_id=shot.scene_id,
                    action="update",
                    before_json={f: getattr(old, f) for f in values},
                    after_json={f: getattr(row, f) for f in values},
                )
            )
```

- [ ] **Step 4: 修既有测试的结果队列**

`backend/tests/test_screenwriting_tools.py`：
- `test_create_shot_assigns_the_next_scene_internal_integer` / `test_create_shot_ignores_a_model_supplied_shot_number_and_status`：`_CaptureSession` 队列各追加一个 `_FakeResult()`（账本 INSERT），RETURNING 的 `SimpleNamespace` 各补 `lighting=None`；取 shots insert 的断言已用 `next(...)`（第一条 Insert），不受影响。
- `test_update_shot_rejects_status_and_url_writes`：队列头部插一个 `_FakeResult(first_row=<旧值 SimpleNamespace，含 6 字段>)`（FOR UPDATE 读），RETURNING 行补 `lighting=None`，尾部追加 `_FakeResult()`（账本 INSERT）。
- 其余用 `AsyncMock` 直接 patch `create_shot`/`update_shot` 的工具层测试不受影响。

- [ ] **Step 5: 全绿验证**

```bash
cd backend && uv run pytest tests/test_script_shot_ops_ledger.py tests/test_screenwriting_tools.py tests/test_scope_resolver_single_choke_point.py -q
```

Expected: 全部 PASS（含两个守卫测试——没加公开函数、没调新 repo 方法）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai/scope/scoped_script_gateway.py backend/tests/test_script_shot_ops_ledger.py backend/tests/test_screenwriting_tools.py
git commit -m "feat(ai): shot 写入记账 — create/update 同事务落 script_shot_ops + 归属列"
```

---

### Task 3: 撤销纯逻辑模块（shot 归并计划 + scene 选择性 inverse）

**Files:**
- Create: `backend/app/services/ai/undo/__init__.py`（空文件）
- Create: `backend/app/services/ai/undo/run_undo_logic.py`
- Test: `backend/tests/test_run_undo_logic.py`

**Interfaces:**
- Consumes: `app.services.script.version_service` 的 `_ops_of` / `_inverse_of`（ledger 行 shape `{"op_seq": int, "op_json": {"ops": [...], "inverse": [...]}, "actor": str}`）。
- Produces（Task 4 的输入，签名照抄）:

```python
@dataclass(frozen=True)
class ShotUndoPlan:
    shot_id: int
    scene_id: int
    kind: str                      # 'delete' | 'revert'
    expected: dict[str, Any]       # CAS 比对：字段 -> 当前应有值（delete=全 6 字段折叠终值；revert=涉及字段的最后 after 值）
    restore: dict[str, Any]        # 仅 revert：字段 -> 恢复值（第一条 before 值）

def merge_shot_ops(rows: list[dict]) -> list[ShotUndoPlan]: ...

@dataclass(frozen=True)
class SceneUndoPlan:
    scene_id: int
    inverse_ops: list[dict]        # 按 op_seq 降序拼接、仅未跳过 element 的 inverse
    skipped_element_ids: tuple[str, ...]   # edited_after_run 的 element
    expected_version: int          # 全账本 max(op_seq)（== content_version 不变量）
    undone_element_ids: tuple[str, ...]    # inverse_ops 覆盖的 distinct element

def scene_undo_plan(scene_id: int, ops_rows: list[dict], run_actor: str) -> SceneUndoPlan: ...
```

**语义（spec §4.2 / §4.3，写实现前读一遍）：**
- `merge_shot_ops` 输入是该 run 的全部 `script_shot_ops` 行（dict，键 `shot_id/scene_id/action/before_json/after_json/created_at/id`），先按 `(created_at, id)` 升序、按 `shot_id` 分组：
  - 组内含 `create` → `kind='delete'`：`expected` = 从 create 的全 6 字段快照起、按序用每条 update 的 `after_json` 覆盖后的折叠终值（等价于 spec 的「比对用最后一行 after_json」，但对多行改不同字段的情况仍完整）。`restore={}`。
  - 组内只有 update → `kind='revert'`：`expected` = 每个被涉及字段取**最后**一条涉及它的 `after_json` 值；`restore` = 每个字段取**第一**条涉及它的 `before_json` 值（回到 run 前状态）。
- `scene_undo_plan`：`run_rows` = `actor == run_actor` 的行。对每个被 run 碰过的 element `e`（从 `_ops_of(row)` 的 `element_id` 收集），取 run 内最后碰它的 `op_seq` 为 `s_e`；若存在 `actor != run_actor` 且 `op_seq > s_e` 的行碰过 `e` → `e` 进 `skipped_element_ids`。其余：按 `op_seq` 降序遍历 run_rows，取 `_inverse_of(row)` 中 `element_id` 未被跳过的 op 拼接。`expected_version` = 全部行的 `max(op_seq)`（空账本为 0）。

- [ ] **Step 1: 写 RED 测试**

`backend/tests/test_run_undo_logic.py`（用例覆盖，全部纯数据无 IO）：

```python
"""run 撤销纯逻辑：shot 账本归并 + scene 选择性 inverse（spec §4.2/§4.3）。"""

from app.services.ai.undo.run_undo_logic import (
    ShotUndoPlan, merge_shot_ops, scene_undo_plan,
)

def _shot_row(i, shot_id, action, before, after, scene_id=10):
    return {"id": i, "shot_id": shot_id, "scene_id": scene_id, "action": action,
            "before_json": before, "after_json": after,
            "created_at": f"2026-08-09T00:00:{i:02d}Z"}

_FULL = {"shot_type": "CU", "camera_angle": None, "camera_movement": None,
         "focal_length": "85mm", "lighting": None, "description": "a"}

def test_create_then_update_merges_to_one_delete_with_folded_expected():
    rows = [
        _shot_row(1, 900, "create", None, _FULL),
        _shot_row(2, 900, "update", {"description": "a"}, {"description": "b"}),
    ]
    plans = merge_shot_ops(rows)
    assert plans == [ShotUndoPlan(
        shot_id=900, scene_id=10, kind="delete",
        expected={**_FULL, "description": "b"}, restore={},
    )]

def test_update_only_reverts_first_before_compares_last_after():
    rows = [
        _shot_row(1, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"}),
        _shot_row(2, 901, "update", {"shot_type": "CU"}, {"shot_type": "ECU"}),
        _shot_row(3, 901, "update", {"lighting": None}, {"lighting": "low-key"}),
    ]
    (plan,) = merge_shot_ops(rows)
    assert plan.kind == "revert"
    assert plan.expected == {"shot_type": "ECU", "lighting": "low-key"}
    assert plan.restore == {"shot_type": "MS", "lighting": None}

def test_two_shots_two_plans():
    rows = [_shot_row(1, 900, "create", None, _FULL),
            _shot_row(2, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"})]
    assert {p.shot_id: p.kind for p in merge_shot_ops(rows)} == {900: "delete", 901: "revert"}

def _op_row(seq, actor, ops, inverse):
    return {"op_seq": seq, "actor": actor, "op_json": {"ops": ops, "inverse": inverse}}

def _upd(eid, text):
    return {"op": "update", "element_id": eid, "payload": {"text": text}}

_AGENT = "agent:800100000000000009"

def test_scene_plan_skips_elements_a_foreign_actor_touched_after_the_run():
    rows = [
        _op_row(5, _AGENT, [_upd("el_1", "new1")], [_upd("el_1", "old1")]),
        _op_row(6, _AGENT, [_upd("el_2", "new2")], [_upd("el_2", "old2")]),
        _op_row(7, "some-user-uuid", [_upd("el_2", "human")], [_upd("el_2", "new2")]),
    ]
    plan = scene_undo_plan(77, rows, _AGENT)
    assert plan.skipped_element_ids == ("el_2",)
    assert plan.inverse_ops == [_upd("el_1", "old1")]
    assert plan.undone_element_ids == ("el_1",)
    assert plan.expected_version == 7

def test_scene_plan_inverse_is_descending_and_foreign_before_run_is_fine():
    rows = [
        _op_row(3, "some-user-uuid", [_upd("el_1", "human-early")], [_upd("el_1", "genesis")]),
        _op_row(4, _AGENT, [_upd("el_1", "a1")], [_upd("el_1", "human-early")]),
        _op_row(5, _AGENT, [_upd("el_1", "a2")], [_upd("el_1", "a1")]),
    ]
    plan = scene_undo_plan(77, rows, _AGENT)
    assert plan.skipped_element_ids == ()
    assert plan.inverse_ops == [_upd("el_1", "a1"), _upd("el_1", "human-early")]
    assert plan.expected_version == 5

def test_scene_plan_all_skipped_yields_empty_batch():
    rows = [
        _op_row(4, _AGENT, [_upd("el_1", "a")], [_upd("el_1", "z")]),
        _op_row(5, "u", [_upd("el_1", "h")], [_upd("el_1", "a")]),
    ]
    plan = scene_undo_plan(77, rows, _AGENT)
    assert plan.inverse_ops == [] and plan.skipped_element_ids == ("el_1",)
```

- [ ] **Step 2: RED 确认**

```bash
cd backend && uv run pytest tests/test_run_undo_logic.py -v
```

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现 `run_undo_logic.py`**

按上面接口实现。要点：分组排序键 `(created_at, id)`（同秒插入靠 snowflake id 兜底）；`merge_shot_ops` 输出按 shot_id 升序保证确定性；`scene_undo_plan` 里 import `from app.services.script.version_service import _inverse_of, _ops_of`（账本行 shape 的既有解析器，别重写）。模块 docstring 写明「纯逻辑无 IO，IO 在 run_undo_service」。

- [ ] **Step 4: GREEN 确认**

```bash
cd backend && uv run pytest tests/test_run_undo_logic.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/undo/ backend/tests/test_run_undo_logic.py
git commit -m "feat(ai): run 撤销纯逻辑 — shot 账本归并 + scene 选择性 inverse(跳过 foreign-actor-after)"
```

---

### Task 4: 撤销执行服务（CAS 删/复原 + scene apply + B4 回流）

**Files:**
- Create: `backend/app/services/ai/undo/run_undo_service.py`
- Modify: `backend/tests/test_scope_resolver_single_choke_point.py`（两个白名单各加一条）
- Test: `backend/tests/test_run_undo_service.py`

**Interfaces:**
- Consumes: Task 3 的 `merge_shot_ops` / `scene_undo_plan`；`app.db.session.read_scope/write_scope`；`app.models` 的 `ScriptShots/ScriptOps/ScriptShotOps`；`get_script_scene_repository().list_ops_by_scene/apply_element_ops` + `VersionConflict`；`app.services.workflow.surface_completion.fire_surface_sync_for_scene`。
- Produces（Task 5 的输入）:

```python
async def execute_undo(run_id: int) -> dict[str, Any]:
    """执行整 run 撤销（权限/幂等由调用方先行）。返回 spec §4.4 报告体
    （不含 status 字段，由端点补）：
    {"shots_deleted": int, "shots_reverted": int,
     "scene_elements_reverted": int,
     "skipped": [{"kind": "shot"|"scene"|"scene_element", "id": str, "reason": str}]}
    reason 枚举: edited_after_run / rendered / version_conflict
    """
```

**实现要点：**
- **shot delete CAS**（每个 plan 各自一个 `write_scope` 事务）：

```python
conds = [ScriptShots.id == plan.shot_id, ScriptShots.status == "empty",
         func.coalesce(ScriptShots.image_url, "") == "",
         func.coalesce(ScriptShots.thumbnail_url, "") == "",
         func.coalesce(ScriptShots.video_url, "") == ""]
for f, v in plan.expected.items():
    col = getattr(ScriptShots, f)
    conds.append(col.is_(None) if v is None else col == v)
result = await session.execute(delete(ScriptShots).where(*conds))
```

  rowcount>0 → `shots_deleted += 1`，事务外 `await fire_surface_sync_for_scene(str(plan.scene_id), surfaces=("storyboard",))`。**必须用 scene 版而不是 `fire_surface_sync_for_shot`**：卡已删除，shot 维度的 `_scope_for_shot` JOIN 解析必然落空，回流会静默打空（fire_* 永不 raise，所以不会报错，只会漏）。scene 行还在，scene 版能解析到 project/episode。
  rowcount==0 → 读该行分类 reason：行不存在 → `edited_after_run`（已被人删，视为被动过）；`status != 'empty'` 或三个 URL 任一非空 → `rendered`；否则 → `edited_after_run`。
- **shot revert CAS**：`update(ScriptShots).where(id + expected 逐字段比对).values(**plan.restore)`；rowcount==0 → skipped `edited_after_run`（spec 对 revert 不设 rendered 条件）。revert 不触回流（涉及字段全是创作参数，不影响 done 判据）。
- **scene**：`select(ScriptOps.scene_id).where(ScriptOps.actor == f"agent:{run_id}").distinct()` 找被碰 scene；每个 scene 走 `list_ops_by_scene` → `scene_undo_plan` → 有 `inverse_ops` 时 `apply_element_ops(str(scene_id), plan.inverse_ops, expected_version=plan.expected_version, actor=f"undo:{run_id}")`（沿用 rollback 范式：历史 append-only、可再回滚，history 面板可识别非用户手打）。`VersionConflict` → 整 scene 一条 skipped `{"kind":"scene","id":str(scene_id),"reason":"version_conflict"}`，不重试（与 agent 写入「at most once」一致）。`skipped_element_ids` 逐个报 `{"kind":"scene_element","id":eid,"reason":"edited_after_run"}`；成功则 `scene_elements_reverted += len(plan.undone_element_ids)`。
- **白名单**：`test_scope_resolver_single_choke_point.py` 的 `ORM_ALLOWED_PATHS` 与 `REPO_LAYER_ALLOWED_PATHS` 各加：

```python
    "services/ai/undo/run_undo_service.py": (
        "Run 撤销执行器（mig 413 立项）。不是 agent-tool 路径：入口是"
        "人触发的 /runs/{run_id}/undo REST 端点，router 已按 agent_runs."
        "user_id 校验归属 + undone_at CAS 幂等后才调用。它操作的每个 id 都"
        "来自服务端自己的账本（script_shot_ops / script_ops），从不接受"
        "模型或用户供给的 scene/shot id；写入全部带 CAS WHERE（与并发编辑"
        "互斥），scene 正文只走 apply_element_ops 这一条 ops 通道。"
    ),
```

（REPO_LAYER 那份把最后一句换成说明只调 `list_ops_by_scene` + `apply_element_ops` 两个方法。）

- [ ] **Step 1: 写 RED 测试**

`backend/tests/test_run_undo_service.py`，fake-session 风格（照抄 Task 2 基建）。用例：

```python
# 1) delete CAS 条件齐全：编译后的 DELETE WHERE 含 status/'empty'、三个
#    coalesce URL、以及 expected 每个字段（None 字段用 IS NULL）；成功后
#    fire_surface_sync_for_scene 被以 (str(scene_id), surfaces=("storyboard",)) 调用
#    （patch fire_surface_sync_for_scene 为 AsyncMock）。
# 2) delete rowcount=0 + 行有 image_url → skipped reason='rendered'；
#    行不存在 → 'edited_after_run'；两种都不触回流。
# 3) revert rowcount=0 → 'edited_after_run'；rowcount=1 → shots_reverted=1 且不触回流。
# 4) scene: apply_element_ops 抛 VersionConflict(current_version=9, elements=[])
#    → skipped kind='scene' reason='version_conflict'，报告里无 scene_element 计数。
# 5) scene: plan 有 2 个 undone element + 1 个 skipped element →
#    scene_elements_reverted==2 且 skipped 含 {'kind':'scene_element','reason':'edited_after_run'}。
# 6) 报告 shape：所有 id 都是 str（snowflake 精度纪律）。
```

写法：patch `run_undo_service` 模块内的 `read_scope`/`write_scope` 为 fake session、`get_script_scene_repository` 为 MagicMock（`list_ops_by_scene`/`apply_element_ops` 用 AsyncMock）、`fire_surface_sync_for_scene` 为 AsyncMock。shot 账本行喂 fake `select(ScriptShotOps)` 结果（`_FakeResult` 的 mappings/all 按现有基建能力来，必要时给 fake session 加一个 `mappings_rows` 队列——加在测试文件里，不动生产码）。

- [ ] **Step 2: RED 确认**

```bash
cd backend && uv run pytest tests/test_run_undo_service.py -v
```

- [ ] **Step 3: 实现 `run_undo_service.py`**

按上面要点实现。结构建议：

```python
async def execute_undo(run_id: int) -> dict[str, Any]:
    report = {"shots_deleted": 0, "shots_reverted": 0,
              "scene_elements_reverted": 0, "skipped": []}
    await _undo_shots(run_id, report)
    await _undo_scenes(run_id, report)
    return report
```

`_undo_shots`：一个 `read_scope` 读账本行（`select(ScriptShotOps).where(run_id==...).order_by(created_at.asc(), id.asc())`，行转 dict）→ `merge_shot_ops` → 逐 plan 独立 `write_scope` 执行 CAS（单项失败只影响该项，try/except 包住并 `logger.error`，落 skipped `edited_after_run`——报告永不半途丢失）。

`_undo_scenes`：如上。`fire_surface_sync_for_scene` 在 import 区顶部正常 import（本文件不在 agent-tool import 图内，不需要函数内延迟 import）。

- [ ] **Step 4: GREEN + 守卫全绿**

```bash
cd backend && uv run pytest tests/test_run_undo_service.py tests/test_scope_resolver_single_choke_point.py -q
```

Expected: PASS。守卫测试若红，说明白名单条目路径写错或服务里调了白名单外的 repo 方法——修服务，不改守卫断言。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/undo/run_undo_service.py backend/tests/test_run_undo_service.py backend/tests/test_scope_resolver_single_choke_point.py
git commit -m "feat(ai): run 撤销执行器 — 逐项 CAS 删/复原 + scene 选择性回滚 + B4 scene 级回流"
```

---

### Task 5: 幂等 claim + 撤销端点 + RunDetail.undone_at

**Files:**
- Modify: `backend/app/repositories/agent_runs_repository.py`（新 `claim_undo`）
- Modify: `backend/app/schemas/agent_runs.py`（`RunDetail.undone_at` + `UndoReport`）
- Modify: `backend/app/api/ai_library_router.py`（`POST /runs/{run_id}/undo`）
- Test: `backend/tests/test_agent_runs_endpoints.py`（追加用例）

**Interfaces:**
- Consumes: Task 4 `execute_undo(run_id: int)`；Task 1 `AgentRuns.undone_at`。
- Produces: `POST /api/v1/ai-library/runs/{run_id}/undo` → `UndoReport`；`GET /runs/{run_id}` 的响应多 `undone_at`（`_agent_run_to_dict` 是全列映射，模型加列后自动携带，只需 schema 放行）。
- Repository 新方法：

```python
async def claim_undo(self, run_id: str, *, user_id: UUID) -> str:
    """一次性认领撤销。返回 'claimed' | 'already_undone' | 'running' | 'not_found'。"""
```

**设计说明（写进代码注释）：** spec §4.1 写「执行完成设 undone_at」，这里改为**先 claim 后执行**：单条 CAS `UPDATE ... WHERE undone_at IS NULL AND status <> 'running'` 天然挡住双击/并发重入（两个并发执行会把 scene inverse 应用两次）。代价是执行中途崩溃会留下「已标记未做完」——每项操作本身带 CAS，重放也只是全 skip，宁可少撤不可重撤，与「永不销毁别人工作」同向。

- [ ] **Step 1: 写 RED 测试**

`backend/tests/test_agent_runs_endpoints.py` 追加（沿用文件里 `_fake_auth` / patch 风格；`undo_run` 从 router import）：

```python
# ---------------------------- undo run ----------------------------

@pytest.mark.asyncio
async def test_undo_run_404_when_not_found() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="not_found"))
    with patch("app.api.ai_library_router.get_agent_runs_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await undo_run("123", _fake_auth())
        assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_undo_run_409_while_running() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="running"))
    with patch("app.api.ai_library_router.get_agent_runs_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await undo_run("123", _fake_auth())
        assert exc.value.status_code == 409

@pytest.mark.asyncio
async def test_undo_run_second_call_reports_already_undone_without_executing() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="already_undone"))
    execute = AsyncMock()
    with (
        patch("app.api.ai_library_router.get_agent_runs_repository", return_value=repo),
        patch("app.services.ai.undo.run_undo_service.execute_undo", execute),
    ):
        out = await undo_run("123", _fake_auth())
    assert out["status"] == "already_undone"
    assert out["skipped"] == []
    execute.assert_not_awaited()

@pytest.mark.asyncio
async def test_undo_run_claimed_executes_and_returns_typed_report() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="claimed"))
    report = {"shots_deleted": 2, "shots_reverted": 1, "scene_elements_reverted": 3,
              "skipped": [{"kind": "shot", "id": "900", "reason": "rendered"}]}
    with (
        patch("app.api.ai_library_router.get_agent_runs_repository", return_value=repo),
        patch("app.services.ai.undo.run_undo_service.execute_undo",
              AsyncMock(return_value=report)),
    ):
        out = await undo_run("800100000000000009", _fake_auth())
    assert out["status"] == "done"
    assert out["shots_deleted"] == 2
    assert out["skipped"][0]["reason"] == "rendered"
```

- [ ] **Step 2: RED 确认**

```bash
cd backend && uv run pytest tests/test_agent_runs_endpoints.py -v -k undo
```

Expected: FAIL（`ImportError: cannot import name 'undo_run'`——记得在文件顶部 import 区补 `undo_run`）。

- [ ] **Step 3: 实现**

① `agent_runs_repository.py`（`request_cancel` 之后，同风格）：

```python
    async def claim_undo(self, run_id: str, *, user_id: UUID) -> str:
        """Run 级撤销的一次性认领（mig 413）。单条 CAS：undone_at 从 NULL
        置 now() 即认领成功；先 claim 后执行是刻意的——两个并发 undo 把
        scene inverse 应用两次比「崩溃后无法重试」更糟（宁可少撤不可重撤）。
        返回 'claimed' | 'already_undone' | 'running' | 'not_found'。"""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.user_id == user_id)
                    .where(AgentRuns.undone_at.is_(None))
                    .where(AgentRuns.status != "running")
                    .values(undone_at=func.now())
                )
                if (result.rowcount or 0) > 0:
                    return "claimed"
        except Exception as e:
            logger.error(f"Failed to claim undo for run {run_id}: {e}")
            return "not_found"
        row = await self.get_by_id(run_id, user_id=user_id)
        if not row:
            return "not_found"
        if row.get("undone_at"):
            return "already_undone"
        if row.get("status") == "running":
            return "running"
        return "not_found"  # pragma: no cover — 竞态兜底
```

（文件顶部确认已 import `func`；没有则补。）

② `schemas/agent_runs.py`：`RunDetail` 加 `undone_at: Optional[datetime] = None`；文件尾加：

```python
class UndoSkippedItem(BaseModel):
    """撤销报告里一条被跳过的项（spec §4.4：silent no-op 不可接受）。"""

    kind: str  # 'shot' | 'scene' | 'scene_element'
    id: str
    reason: str  # 'edited_after_run' | 'rendered' | 'version_conflict'


class UndoReport(BaseModel):
    """POST /runs/{run_id}/undo 的类型化报告。"""

    status: str  # 'done' | 'already_undone'
    shots_deleted: int = 0
    shots_reverted: int = 0
    scene_elements_reverted: int = 0
    skipped: list[UndoSkippedItem] = Field(default_factory=list)
```

③ `ai_library_router.py`：`cancel_run` 之后（import 区补 `UndoReport`，与 `RunDetail` 同一来源）：

```python
@router.post(
    "/runs/{run_id}/undo",
    response_model=UndoReport,
    summary="Undo everything this run wrote (one-shot, skip + typed report)",
)
async def undo_run(run_id: str, auth: AuthDep) -> Dict[str, Any]:
    """整 run 一键撤销（mig 413 立项）。逐项 CAS：被后续修改碰过的写入
    跳过并报告，永不销毁别人的工作。一次性，无 redo；重复调用返回
    already_undone。运行中的 run 不可撤（先 cancel）。"""
    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    claim = await runs_repo.claim_undo(run_id, user_id=user_uuid)
    if claim == "not_found":
        raise HTTPException(status_code=404, detail="run not found or not owned by you")
    if claim == "running":
        raise HTTPException(status_code=409, detail="run is still running — cancel it first")
    if claim == "already_undone":
        return {"status": "already_undone", "shots_deleted": 0, "shots_reverted": 0,
                "scene_elements_reverted": 0, "skipped": []}
    from app.services.ai.undo import run_undo_service

    report = await run_undo_service.execute_undo(int(run_id))
    return {"status": "done", **report}
```

注意 patch 目标：测试 patch 的是 `app.services.ai.undo.run_undo_service.execute_undo`，所以端点里用 `from app.services.ai.undo import run_undo_service` + `run_undo_service.execute_undo(...)`（模块属性访问，patch 才生效）。

- [ ] **Step 4: GREEN 确认**

```bash
cd backend && uv run pytest tests/test_agent_runs_endpoints.py -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/agent_runs_repository.py backend/app/schemas/agent_runs.py backend/app/api/ai_library_router.py backend/tests/test_agent_runs_endpoints.py
git commit -m "feat(ai): POST /runs/{run_id}/undo — claim-first 幂等 + 类型化报告 + RunDetail.undone_at"
```

---

### Task 6: 前端 Undo 按钮 + 报告渲染 + storyboard 刷新

**Files:**
- Modify: `frontend/types.ts`（`AgentRunDetail.undone_at` + `AgentRunUndoReport`）
- Modify: `frontend/services/aiLibraryService.ts`（`undoRun`）
- Modify: `frontend/components/agentActivity/shotFocusBus.ts`（storyboard 刷新通道）
- Create: `frontend/components/agentActivity/useRunUndo.ts`
- Modify: `frontend/components/agentActivity/TurnWriteSummary.tsx`（按钮 + 报告）
- Modify: `frontend/components/chat/AIChatBubble.tsx`（`runId` prop 透传）
- Modify: `frontend/components/AIChatPanel.tsx`（`extractRunId` + 传参）
- Modify: `frontend/editor/storyboard/StoryboardView.tsx`（订阅刷新）
- Modify: `frontend/public/locales/en.json` + `zh.json`
- Test: `frontend/components/agentActivity/TurnWriteSummary.test.tsx`（追加）、`frontend/components/agentActivity/shotFocusBus.test.ts`（追加）

**Interfaces:**
- Consumes: Task 5 的端点与 `UndoReport` JSON shape；`GET /runs/{id}` 的 `undone_at`。
- Produces:

```ts
// types.ts
export interface AgentRunUndoReport {
  status: 'done' | 'already_undone';
  shots_deleted: number;
  shots_reverted: number;
  scene_elements_reverted: number;
  skipped: { kind: 'shot' | 'scene' | 'scene_element'; id: string;
             reason: 'edited_after_run' | 'rendered' | 'version_conflict' }[];
}
// AgentRunDetail 加: undone_at?: string | null;

// aiLibraryService
async undoRun(runId: string): Promise<AgentRunUndoReport>

// shotFocusBus.ts 新增（同文件同范式）
export function onStoryboardRefresh(listener: () => void): () => void
export function requestStoryboardRefresh(): void

// useRunUndo.ts
export type RunUndoState = 'loading' | 'ready' | 'busy' | 'undone' | 'hidden';
export function useRunUndo(runId: string | null | undefined, enabled: boolean): {
  state: RunUndoState;
  report: AgentRunUndoReport | null;
  undo: () => Promise<void>;
}

// TurnWriteSummary 新 props
runId?: string | null;
```

**设计说明：** spec §5 写「undone_at 随 run 数据进入 useRunToolActivity 数据流」，但按钮唯一的挂载面（chat 面板的 `AIChatBubble`）**刻意不用**那个 hook（会把 trace 渲染两遍，见 `useRunToolActivity.ts` 头注释）；issue timeline 用该 hook 却是 `interactive=false`（无按钮）。所以 undone 态走独立的 `useRunUndo`（`getRun` 一次 + 模块级缓存，仿 `settledCache` 范式）——同效果、不破坏既有分工，实现时在 `useRunUndo.ts` 头注释里写明这个偏离与原因。

- [ ] **Step 1: 写 RED 组件测试**

`TurnWriteSummary.test.tsx` 追加（沿用该文件既有 render/mocks 风格；mock `useRunUndo` 模块）：

```tsx
// 1) interactive + runId + state='ready' → 渲染 data-testid="turn-undo-button"，
//    文案 'Undo'，带 lucide Undo2（断言 button 内有 svg 即可）。
// 2) state='undone' 且无 report → 按钮 disabled、文案 'Undone'。
// 3) 点击按钮 → undo() 被调用一次。
// 4) report 存在（shots_deleted=2, scene_elements_reverted=3,
//    skipped=[{kind:'shot',id:'9',reason:'rendered'}]）→ 渲染
//    data-testid="turn-undo-report"，含计数文案与 reason 文案（英文 UI）。
// 5) interactive=false 或无 runId → 不渲染按钮（快照维持现状）。
```

`shotFocusBus.test.ts` 追加：`requestStoryboardRefresh` 通知订阅者、退订后不再收到、无订阅时静默不抛。

- [ ] **Step 2: RED 确认**

```bash
cd frontend && npx vitest run components/agentActivity/TurnWriteSummary.test.tsx components/agentActivity/shotFocusBus.test.ts
```

- [ ] **Step 3: 实现**

① `types.ts`：如上接口；`AgentRunDetail` 加 `undone_at?: string | null;`。

② `aiLibraryService.ts`（`cancelRun` 之后，同风格）：

```ts
  /**
   * Undo everything a finished run wrote (one-shot; server skips anything a
   * later edit touched and reports it). 409 while the run is still running.
   */
  async undoRun(runId: string): Promise<AgentRunUndoReport> {
    const resp = await fetch(
      `${base()}/runs/${encodeURIComponent(runId)}/undo`,
      { method: 'POST', headers: await getAuthHeaders() },
    );
    return handle<AgentRunUndoReport>(resp);
  },
```

（`handle<T>` 是该文件既有的响应处理器；import 区补 `AgentRunUndoReport`。）

③ `shotFocusBus.ts`：新增第二组 listener 集合，照抄 shot focus 三件套的结构与注释风格（fire-and-forget、无订阅者即丢弃）。

④ `useRunUndo.ts`：模块级 `const undoneCache = new Map<string, boolean>()` + `__clearRunUndoCache()`（测试用，仿 `useRunToolActivity`）。`enabled=false` 或无 `runId` → `'hidden'`。首次：缓存命中直接定态；否则 `getRun(runId)` 读 `undone_at`（404/失败 → `'hidden'`，`console.error`）。`undo()`：置 `'busy'` → `aiLibraryService.undoRun` → 存 report、置 `'undone'`、`undoneCache.set` → `requestStoryboardRefresh()`；失败 `console.error` + 回 `'ready'`。

⑤ `TurnWriteSummary.tsx`：props 加 `runId?: string | null`；组件内 `const { state, report, undo } = useRunUndo(runId, interactive && !onShotClick)`（测试传了 `onShotClick` 的场景不该发请求；判断保持简单：`enabled = interactive && Boolean(runId)` 也可，但 mock 服务时注意）。头部行改为 flex 两端：左侧现有 PencilLine + 文案，右侧：

```tsx
{state === 'ready' || state === 'busy' ? (
  <button
    type="button"
    onClick={() => void undo()}
    disabled={state === 'busy'}
    data-testid="turn-undo-button"
    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-ink-500 transition-colors hover:bg-agent-soft hover:text-agent disabled:opacity-50"
  >
    <Undo2 size={10} aria-hidden />
    {t('agentActivity.undo', 'Undo')}
  </button>
) : state === 'undone' ? (
  <span data-testid="turn-undo-button" aria-disabled="true"
        className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-ink-500 opacity-60">
    <Undo2 size={10} aria-hidden />
    {t('agentActivity.undone', 'Undone')}
  </span>
) : null}
```

report 渲染（卡片列表之后）：

```tsx
{report && (
  <div data-testid="turn-undo-report" className="mt-1 border-t border-agent-line px-2 pt-1 text-[10px] text-ink-500">
    <p>
      {t('agentActivity.undoSummary', {
        deleted: report.shots_deleted,
        reverted: report.shots_reverted,
        elements: report.scene_elements_reverted,
      })}
    </p>
    {report.skipped.map((item, i) => (
      <p key={`${item.kind}-${item.id}-${i}`}>
        {t(`agentActivity.undoKind.${item.kind}`)} {item.id} ·{' '}
        {t(`agentActivity.undoReason.${item.reason}`)}
      </p>
    ))}
  </div>
)}
```

⑥ `AIChatBubble.tsx`：props 加 `runId?: string | null`，`<TurnWriteSummary summary={writeSummary} runId={runId} />`。

⑦ `AIChatPanel.tsx`：`extractToolCalls` 旁加：

```tsx
/** 持久化 assistant 消息 metadata_json.run_id（BIGINT snowflake，保持字符串）。 */
function extractRunId(msg: AIChatMessage): string | null {
  const meta = msg.metadata_json;
  if (!meta || typeof meta !== 'object') return null;
  const raw = (meta as Record<string, unknown>).run_id;
  return typeof raw === 'string' && raw ? raw : null;
}
```

MessageBubble 调用处加 `runId={msg.role === 'assistant' ? extractRunId(msg) : undefined}`。

⑧ `StoryboardView.tsx`：把「mount 时并行加载全部 scene shots」的逻辑抽成 `loadAll` useCallback（内容照旧），mount effect 调它；新增：

```tsx
useEffect(() => onStoryboardRefresh(() => { void loadAll(); }), [loadAll]);
```

（import `onStoryboardRefresh`；`IssueChatThread` 不动——`interactive=false` 分支 `useRunUndo` 拿 `enabled=false` 恒 `'hidden'`。）

⑨ i18n（en.json `agentActivity` 块内，zh.json 同步中文值）：

```json
"undo": "Undo",
"undone": "Undone",
"undoSummary": "Undid {{deleted}} cards, reverted {{reverted}} cards, restored {{elements}} passages",
"undoKind": { "shot": "Shot", "scene": "Scene", "scene_element": "Passage" },
"undoReason": {
  "edited_after_run": "changed since this run — left untouched",
  "rendered": "already rendered — left untouched",
  "version_conflict": "scene was being edited — left untouched"
}
```

zh 值：`"undo": "撤销"` 等按语义翻译（zh.json 是翻译文件，中文值合规）。

- [ ] **Step 4: GREEN + 全量前端基线**

```bash
cd frontend && npx vitest run components/agentActivity/ && npx tsc --noEmit
```

Expected: 全 PASS + 无类型错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/types.ts frontend/services/aiLibraryService.ts frontend/components/agentActivity/ frontend/components/chat/AIChatBubble.tsx frontend/components/AIChatPanel.tsx frontend/editor/storyboard/StoryboardView.tsx frontend/public/locales/
git commit -m "feat(fe): TurnWriteSummary 撤销按钮 — 三态 + 类型化报告就地渲染 + storyboard 刷新"
```

---

### Task 7: 小尾巴 A — strip 非当前节点点击不再闪作业面

**Files:**
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`（`handleSelectNode`）
- Test: `frontend/components/workspace/ProjectWorkspace.test.tsx`（追加/调整）

**背景与拍板语义：** 现在 `handleSelectNode`（`ProjectWorkspace.tsx:486`）对**任意**节点点击都按 surface 路由——点一个非当前（pending/done）的 storyboard 节点，也会把当前集的 storyboard 作业面弹出来，内容跟被点的节点毫无关系（「闪面板」）。解耦：**只有当前节点**的点击进作业面；非当前节点点击一律落到它自己的 Stage Board（`handleOpenStage(node.id)`，即节点详情，与 deliverable-only 节点同路径）。

- [ ] **Step 1: 写 RED 测试**

`ProjectWorkspace.test.tsx` 里找到现有 workflow-strip 节点点击的测试（搜 `workflow-strip-node`），追加：

```tsx
// 1) 点击 current 节点（data-current="true"，surface=storyboard）→ 进 storyboard
//    作业面（现状断言不变）。
// 2) 点击非 current 的 storyboard/script 节点 → 不出现作业面（断言原有的
//    surface panel testid 不在文档中），而是 Stage Board 视图（沿用现有
//    handleOpenStage 的断言方式——参考 deliverable 节点用例）。
```

若既有用例正好断言了「非当前节点也进作业面」，把它改成新语义（这是行为变更，测试改动就是变更记录）。

- [ ] **Step 2: RED 确认**

```bash
cd frontend && npx vitest run components/workspace/ProjectWorkspace.test.tsx
```

- [ ] **Step 3: 实现**

`handleSelectNode` 开头加：

```tsx
      // 拍板（undo 立项附带）：非当前节点点击不再闪当前集的作业面——surface
      // 路由只对 current 节点成立，其余一律看节点自己的 Stage Board。
      if (node.id !== currentNodeId) {
        handleOpenStage(node.id);
        return;
      }
```

（`currentNodeId` 若不在该 callback 依赖里，补进 deps。确认变量名以文件现状为准——strip 的 props 叫 `currentNodeId`，workspace 侧找喂给 `<WorkflowStrip currentNodeId=...>` 的那个源。）

- [ ] **Step 4: GREEN → Commit**

```bash
cd frontend && npx vitest run components/workspace/ProjectWorkspace.test.tsx
git add frontend/components/workspace/ProjectWorkspace.tsx frontend/components/workspace/ProjectWorkspace.test.tsx
git commit -m "fix(fe): workflow strip 非当前节点点击改开节点 Stage Board — 不再闪当前集作业面"
```

---

### Task 8: 小尾巴 B — 场次卡 Open 深链到 scene 级

**Files:**
- Modify: `frontend/editor/components/EditorShell.tsx`（新 prop `initialFocusSceneId`）
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`（透传）
- Test: `frontend/editor/components/EditorShell.test.tsx`（若无则建；先看有没有既有 EditorShell 测试，有就追加）

**背景：** `EpisodeSceneBoard` 场次卡的 Open 已回调 `onOpenScene(sceneId)` → `handleOpenWorkView('storyboard', { sceneId })`，但 `handleOpenWorkView` 收了 `sceneId` 却没用（`ProjectWorkspace.tsx:394` 注释自认「scrolling to the specific scene is deferred」）——编辑器打开后停在整卷 storyboard。补上最后一跳。机制照抄既有 `pendingFocusShotId` 范式（`EditorShell.tsx:522-540`）：storyboard 列已带 `data-scene-id`（`StoryboardView.tsx:359`），script sheet 的 SceneBlock 也带，同一个选择器两个 rail view 都能用。

- [ ] **Step 1: 写 RED 测试**

EditorShell 相关测试文件（先 `ls frontend/editor/components/*.test.tsx` 找现状；无则新建，mock 数据加载沿用同目录已有测试基建；若该组件历史上无测试且基建过重，允许降级为 ProjectWorkspace 集成断言——把判断写进报告）：

```tsx
// mount EditorShell 时传 initialFocusSceneId="200" + initialRailView="storyboard"
// → scenes 数据就绪后，querySelector('[data-scene-id="200"]') 的元素被
//   scrollIntoView（jsdom 下 mock Element.prototype.scrollIntoView 断言调用）。
// 不传 → 不调用。
```

- [ ] **Step 2: RED 确认**

```bash
cd frontend && npx vitest run frontend/editor/components --passWithNoTests=false 2>/dev/null || npx vitest run editor/components
```

（以实际路径解析为准。）

- [ ] **Step 3: 实现**

① `EditorShell.tsx`：props 加

```tsx
  /**
   * 深链：mount 后滚动定位到该 scene（场次卡 Open → scene 级）。storyboard
   * 列与 script sheet 的 SceneBlock 都带 data-scene-id，两个 rail view 通用。
   * 与 pendingFocusShotId 同范式：先等目标渲染出来再滚，一次性消费。
   */
  initialFocusSceneId?: string;
```

组件内加 `const [pendingFocusSceneId, setPendingFocusSceneId] = useState<string | null>(initialFocusSceneId ?? null);`，effect（仿 `pendingFocusShotId` 那个，`EditorShell.tsx:531`）：

```tsx
  useEffect(() => {
    if (!pendingFocusSceneId) return;
    const block = shellRef.current?.querySelector<HTMLElement>(
      `[data-scene-id="${pendingFocusSceneId}"]`,
    );
    if (!block) return; // scenes 还没渲染完——等下一次 deps 变化再试
    block.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setActiveScene(pendingFocusSceneId);
    setPendingFocusSceneId(null);
  }, [pendingFocusSceneId, railView, scenes, setActiveScene]);
```

② `ProjectWorkspace.tsx`：加状态 `const [studioFocusSceneId, setStudioFocusSceneId] = useState<string | null>(null);`。`handleOpenWorkView` 里：

```tsx
      setStudioFocusSceneId(view === 'storyboard' && opts?.sceneId ? opts.sceneId : null);
```

（放在现有 early-return **之后**、`setStudioView` 之前的位置要注意：bare storyboard 分支 return 前也要清掉旧值。）EditorShell mount 处加 `initialFocusSceneId={studioFocusSceneId ?? undefined}`；同时更新 `:388-396` 那段「deferred」注释（缺口已闭）。

- [ ] **Step 4: GREEN + 回归**

```bash
cd frontend && npx vitest run components/workspace/ editor/ && npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add frontend/editor/components/EditorShell.tsx frontend/components/workspace/ProjectWorkspace.tsx <测试文件>
git commit -m "feat(fe): 场次卡 Open 深链到 scene 级 — EditorShell initialFocusSceneId 定位滚动"
```

---

### Task 9: 小尾巴 C — 结构化上下文 payload（设计稿 §5.3）

**Files:**
- Modify: `backend/app/schemas/ai_library_chat.py`（`ScriptContextRequest` + `ChatRequest.script_context`）
- Modify: `backend/app/api/ai_library_router.py`（chat / chat-stream 两个端点透传）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py`（`format_script_context_block` + 注入）
- Modify: `frontend/components/AIChatPanel.tsx`（发送 script_context）
- Modify: `frontend/services/aiLibraryService.ts`（`streamChatMessage` opts 类型）
- Test: `backend/tests/test_script_context_block.py`（新建）；前端在 `AIChatPanel` 相关测试处追加纯函数测试

**背景（ContextCapsule.tsx 头注释原文）：** 胶囊现在只是「presentation half」——scene/element id 已随胶囊携带并展示，但发送时仍把选区**文本**折进 content（`AIChatPanel.tsx:561-574`）。§5.3 要的是 payload 本身成为 `{scene_id, element_ids}` **handle**，agent 能据此精确编辑。

**Scope 决策（写实现前明确）：** content 的文本折叠**保留不动**（用户气泡显示与历史持久化不变），额外把 handle 作为 `script_context` 结构化字段发给后端，后端把它渲染成 `<user_selection>` 块**前置到 request_instructions**（仿 link_injection 范式，`ai_library_chat_service.py:511-537`）——不进持久化消息，不改展示。quoted_text **不重复发**（文本已在 content 里，重复会双份耗 token）。

- [ ] **Step 1: 写 RED 后端测试**

`backend/tests/test_script_context_block.py`：

```python
"""§5.3 结构化上下文：script_context → <user_selection> 指令块。"""

from app.schemas.ai_library_chat import ChatRequest, ScriptContextRequest
from app.services.ai.chat.ai_library_chat_service import format_script_context_block

def test_block_carries_scene_and_element_handles():
    block = format_script_context_block({
        "scene_id": "31415", "element_ids": ["el_1", "el_2"],
        "element_type": "dialogue", "scene_label": "S2", "cross_scene": False,
    })
    assert "<user_selection>" in block and "</user_selection>" in block
    assert "scene_id: 31415" in block
    assert "element_ids: el_1, el_2" in block
    assert "S2" in block
    # handle 指令：让 agent 直接用 id 定位，而不是靠文本再搜一遍
    assert "ReadScene" in block

def test_block_without_elements_still_names_the_scene():
    block = format_script_context_block({"scene_id": "31415", "element_ids": []})
    assert "scene_id: 31415" in block
    assert "element_ids" not in block

def test_empty_or_missing_context_yields_empty_string():
    assert format_script_context_block(None) == ""
    assert format_script_context_block({}) == ""   # 无 scene_id 无 element → 无块

def test_chat_request_accepts_script_context():
    req = ChatRequest(content="tighten this", script_context=ScriptContextRequest(
        scene_id="31415", element_ids=["el_1"], element_type="dialogue",
        scene_label="S2", cross_scene=False,
    ))
    assert req.script_context.scene_id == "31415"
    assert ChatRequest(content="hi").script_context is None
```

- [ ] **Step 2: RED 确认**

```bash
cd backend && uv run pytest tests/test_script_context_block.py -v
```

- [ ] **Step 3: 后端实现**

① `schemas/ai_library_chat.py`（`AttachmentRequest` 之后）：

```python
class ScriptContextRequest(BaseModel):
    """§5.3：随消息携带的剧本选区 handle。文本折叠仍在 content 里（展示/
    持久化不变）；这里只携带 id，让 agent 能用 ReadScene/ProposeEdit 精确
    定位，而不是拿文本再搜一遍。id 全部字符串（BIGINT snowflake 精度）。"""

    scene_id: Optional[str] = None
    element_ids: list[str] = Field(default_factory=list)
    element_type: Optional[str] = None
    scene_label: Optional[str] = None
    cross_scene: bool = False
```

`ChatRequest` 加 `script_context: Optional[ScriptContextRequest] = None`。

② `ai_library_chat_service.py` 模块级函数（放 `_surface_next_session_commitments` 附近）：

```python
def format_script_context_block(script_context: Optional[dict]) -> str:
    """script_context handle → <user_selection> 指令块（空/无效返回 ""）。
    只带 id 与标签，不带文本——文本已折叠在用户消息里，重复注入是双份 token。"""
    sc = script_context or {}
    scene_id = sc.get("scene_id")
    element_ids = [e for e in (sc.get("element_ids") or []) if e]
    if not scene_id and not element_ids:
        return ""
    lines = ["<user_selection>"]
    if sc.get("scene_label"):
        lines.append(f"scene: {sc['scene_label']}")
    if scene_id:
        lines.append(f"scene_id: {scene_id}")
    if element_ids:
        lines.append(f"element_ids: {', '.join(element_ids)}")
    if sc.get("element_type"):
        lines.append(f"element_type: {sc['element_type']}")
    if sc.get("cross_scene"):
        lines.append("spans multiple scenes")
    lines.append(
        "The user's message refers to this selection. Use these ids directly "
        "(ReadScene the scene, then target these element_ids with "
        "ProposeEdit/ApplyEdit) instead of re-locating the text by content."
    )
    lines.append("</user_selection>")
    return "\n".join(lines)
```

③ 线程贯通：`chat()` / `run_session_turn()` / `_run_session_turn_inner()` 各加 kw 参数 `script_context: Optional[dict] = None` 并透传；`_run_session_turn_inner` 里 request_instructions 组好后（link_injection 之前或之后均可，选 link_injection **之前**紧邻默认指令处）：

```python
        selection_block = format_script_context_block(script_context)
        if selection_block:
            request_instructions = selection_block + "\n\n" + request_instructions
```

④ `ai_library_router.py`：chat 与 chat-stream 两个端点把 `body.script_context.model_dump() if body.script_context else None` 传给 service（两处都要，漏 stream 就是 silent no-op）。

- [ ] **Step 4: 后端 GREEN**

```bash
cd backend && uv run pytest tests/test_script_context_block.py tests/ -q -k "chat_service or script_context" 
```

（后半句按既有 chat service 测试文件名收敛跑一轮，确认签名扩参没打破现有 mock。全量 `uv run pytest -q` 在 Task 结束前跑一次。）

- [ ] **Step 5: 前端实现 + 测试**

① `AIChatPanel.tsx`：`handleSend` 里 capsule 折叠逻辑**保留**，在 `opts` 组装处加：

```tsx
        if (capsule?.sceneId || capsule?.elementId) {
          opts.script_context = {
            scene_id: capsule.sceneId ?? null,
            element_ids: capsule.elementId ? [capsule.elementId] : [],
            element_type: capsule.elementType ?? null,
            scene_label: capsule.sceneLabel ?? null,
            cross_scene: Boolean(capsule.crossScene),
          };
        }
```

注意时序：现在 `const capsule = contextCapsule;` 读取在 opts 组装之前，保持读取一次、两处（text 折叠 + script_context）共用同一个 `capsule` 快照。

② `aiLibraryService.ts`：`streamChatMessage` 的 opts 类型加 `script_context?: {...}`（shape 同上，跟随该文件现有内联类型风格），透传进请求 body。

③ 前端测试：从 `AIChatPanel.tsx` 导出纯函数 `buildScriptContext(capsule: ContextCapsuleValue | null)`（把 ① 的对象构造抽出来复用），在 `frontend/components/agentActivity/ContextCapsule.test.tsx` 旁新建或在既有 AIChatPanel 测试文件追加：

```tsx
// buildScriptContext(null) → null
// buildScriptContext({text:'x'}) → null（无 id 无块）
// buildScriptContext({text:'x', sceneId:'31415', elementId:'el_1',
//   elementType:'dialogue', sceneLabel:'S2'}) →
//   {scene_id:'31415', element_ids:['el_1'], element_type:'dialogue',
//    scene_label:'S2', cross_scene:false}
```

- [ ] **Step 6: 全量验证 + Commit**

```bash
cd backend && uv run pytest -q
cd ../frontend && npx vitest run && npx tsc --noEmit
git add backend/app/schemas/ai_library_chat.py backend/app/api/ai_library_router.py backend/app/services/ai/chat/ai_library_chat_service.py backend/tests/test_script_context_block.py frontend/components/AIChatPanel.tsx frontend/services/aiLibraryService.ts <前端测试文件>
git commit -m "feat(ai): 结构化上下文 payload — 胶囊 handle 进 <user_selection> 指令块(§5.3)"
```

---

## 收尾

- [ ] 全量基线：`cd backend && uv run pytest -q`；`cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
- [ ] 用 `/ship` 推分支开 PR（base master）。PR 描述引用 spec 文件路径 + 三个拍板决策。
- [ ] 提醒（PR 描述里写明）：本 PR 同时含 migration 与依赖它的代码，`run-migration.yml` 与 `deploy-gpu.yml` 无顺序保证——merge 后若后端先起，undo 端点在 migration 落库前会 500（`undone_at` 列不存在）；观察两条 workflow 都绿再验收。验收走真实链路：让 agent 建两张卡 → 人改其中一张 → Undo → 报告应为 1 删 1 skip(edited_after_run)。

## Self-Review 记录（写计划时已核）

- spec §2（migration/列类型/取号）→ Task 1；§3（同事务记账/FOR UPDATE/裸 ORM 不松 AST 白名单）→ Task 2；§4.2/4.3（CAS 条件、归并、跳过语义、undo actor、B4 回流）→ Task 3+4；§4.1/4.4（幂等/权限/报告 shape）→ Task 5；§5（按钮三态/报告/刷新/issue timeline 不变）→ Task 6；§7 三个小尾巴 → Task 7/8/9。
- 两处明确偏离 spec 均有记录：① 幂等改 claim-first（Task 5 设计说明）；② undone 态走 `useRunUndo` 而非 `useRunToolActivity`（Task 6 设计说明）。③ B4 回流用 `fire_surface_sync_for_scene` 而非 spec 写的 `fire_surface_sync_for_shot`——卡删除后 shot 维度解析必然落空（Task 4 要点，spec 的意图是「删卡后触发 storyboard 判据重算」，scene 版才做得到）。
- 类型一致性：`ShotUndoPlan`/`SceneUndoPlan`/`execute_undo`/`claim_undo`/`UndoReport`/`AgentRunUndoReport`/`useRunUndo` 的名字与字段在产出方与消费方 Task 间逐一核对过。
