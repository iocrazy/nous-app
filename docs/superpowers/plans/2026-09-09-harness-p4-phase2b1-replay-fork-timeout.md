# harness 二期 2b-1「回放 + fork + 逐工具超时」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** issue 详情页能把一次 run 拖到任意 step 回看、从那一步分叉出新 run；每次工具调用有独立时限，超时是工具结果而非 run 结果。

**Architecture:** 回放 = 把事件流切到 seq 再折叠，后端用已有 `run_projection.replay` 出「那一刻的 view」，前端用已有 `foldEvents` 出「那一刻的轨迹」；fork = 用 `events[:at_seq]` 里的 user/assistant/compaction 消息给 issue 开一个新会话、切指针、派发新 run（`fork_of_run_id/fork_at_seq` 列已在）；逐工具超时 = runner 两条工具循环里所有 handler 的 `await` 经唯一包法 `run_tool_with_timeout`，结果带 `timed_out`，折叠成 `view.tools`。

**Tech Stack:** FastAPI + SQLAlchemy async（新 SQL 一律 ORM；`RunRecorder._insert_row` 是既有 `text()` 例外，只加两列）、DBOS（沿用 `_start_execute_issue`）、pytest；React 19 + vitest/RTL、i18n `t(key, fallback)` en/zh 同改、语义色 token（回放/分叉 info、超时 danger）。

**Spec:** `docs/superpowers/specs/2026-09-09-harness-p4-phase2b1-replay-fork-timeout-design.md`

## Global Constraints

- 每 Task 独立 worktree（`bash scripts/worktree-manager.sh create feat/p4-2b1-tN`，创建后立即 `git fetch && git rebase origin/master`；前端 worktree 需 `ln -s <主检出>/frontend/node_modules frontend/node_modules`）+ 独立 PR；PR 描述必有「复用 / 删除了什么」「偏差」「对抗评审」「突变记录」「测试」。
- TDD：每个关键断言先红后绿；每个 Task 至少一处突变让测试转红并记进 PR。
- 对抗评审用 `Agent`（model: opus），发现全修。
- 后端全量 `uv run pytest -q -p no:cacheprovider --deselect tests/api/test_distribution_music_search.py --deselect tests/api/test_distribution_topic_suggest.py tests`（qishui-audio 一例为本机环境性失败）；前端全量 **在 worktree 的 `frontend/` 目录下** `npx vitest run`。
- lint：后端 isort + black + ruff 改动文件；前端 `npx eslint <改动文件>`。
- **给 run_turn 结果或工具结果加任何新标志，必须在 `tests/runner/test_turn_end_reasons.py` 用「adapter 无 `stream` 属性」用例证明穿过缓冲回退分支**（CLAUDE.md 已知陷阱）。
- 事件类型新增必须先迁移（白名单 CHECK），消费代码后续 PR；迁移不 `SET ROLE`，DROP/ADD 幂等。
- UI 文案英文、Title Case；新 key en/zh 同加；不引入 amber/rose/emerald 等旧色相类名。
- 偏离本计划或 spec 之处，回写两处「实施记录」。

**与 spec 的两处已定偏差（勘察后）**：① spec §1 说前端自己折 `viewAtSeq` 并与后端快照比对——改为后端 `GET /runs/{id}/view-at?seq=` 直接调 `run_projection.replay`，前端零折叠逻辑（一个折叠注册表，不复制到 TS）；② spec §2.2 的 tool_call → tool 消息——`build_history_messages` 只保留 user/assistant/system，tool 结果本来就不在跨轮上下文里，重建只做 user / assistant / compaction_summary。③ spec §4 的「issue 时间线系统行」——`system_status` 行由 DB trigger 写、无 body，改为分叉 run 自己的气泡带 `Forked from …` 芯片即时间线入口。T1 把这三条回写 spec。

---

### Task 1: 迁移 460（`fork` 事件类型）+ 事件端点 `upto_seq` + `view-at` 端点 + spec 回写

**Files:**
- Create: `supabase/migrations/460_transcript_event_type_fork.sql`
- Modify: `backend/app/api/ai_library_router.py:2367-2430`（`list_run_events` 加 `upto_seq`；紧随其后新增 `get_run_view_at`）
- Modify: `docs/superpowers/specs/2026-09-09-harness-p4-phase2b1-replay-fork-timeout-design.md`（§1/§2.2/§4 实施记录）
- Test: `backend/tests/api/test_agent_run_events_router.py`（追加）、`backend/tests/api/test_run_view_at.py`（新）
- Test: `backend/tests/db/test_migration_460_event_type_fork.py`（新，源码守卫）

**Interfaces:**
- Consumes: `app.services.ai.runner.run_projection.replay(events: list[tuple[str, dict]], *, seqs: list[int] | None) -> Views`（已有）。
- Produces: `GET /ai-library/runs/{run_id}/events?after_seq=&upto_seq=&types=&limit=`；`GET /ai-library/runs/{run_id}/view-at?seq=N` → `{"seq": N, "view": {...}, "cost": {...}}`。

- [ ] **Step 1: 写迁移**

```sql
-- 460: harness 二期 2b-1 —— fork 落到 transcript
-- spec: docs/superpowers/specs/2026-09-09-harness-p4-phase2b1-replay-fork-timeout-design.md §2.4
-- 只做一件事：白名单放行 fork（分叉 run 的首条事件 {of_run_id, at_seq, steer}）。
-- 逐工具超时不加类型：timed_out 走 tool_call.payload.result。
-- 照 459 的 DROP/ADD 幂等写法；不 SET ROLE。
BEGIN;

ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;
ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text, 'assistant'::text, 'tool_call'::text, 'error'::text, 'system'::text,
    'llm_retry'::text, 'todo_write'::text,
    'compaction_start'::text, 'compaction_summary'::text, 'compaction_end'::text,
    'turn_end'::text,
    'step_start'::text, 'step_end'::text, 'inbox_claimed'::text,
    'deliverable'::text, 'budget_check'::text,
    'question_asked'::text, 'question_answered'::text,
    'capability_denied'::text,
    'fork'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436: llm_retry. 443: todo_write, compaction_*, turn_end. '
  '453: step_start/step_end, inbox_claimed, deliverable, budget_check. '
  '459: question_asked, question_answered, capability_denied. '
  '460: fork (first event of a forked run: {of_run_id, at_seq, steer}).';

COMMIT;
```

- [ ] **Step 2: 源码守卫测试（红）**

```python
# backend/tests/db/test_migration_460_event_type_fork.py
"""460 must admit 'fork' and keep every type 459 admitted (a DROP/ADD that
forgets one silently rejects that family's best-effort inserts)."""
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"


def _types(path):
    body = path.read_text()
    return set(re.findall(r"'([a-z_]+)'::text", body))


def test_460_is_a_superset_of_459_plus_fork():
    prev = _types(MIG / "459_transcript_event_types_phase2a.sql")
    cur = _types(MIG / "460_transcript_event_type_fork.sql")
    assert "fork" in cur
    assert prev <= cur, prev - cur
```

- [ ] **Step 3: 跑红** `cd backend && uv run pytest -q tests/db/test_migration_460_event_type_fork.py`（文件不存在 → 红）；写 Step 1 的文件 → 绿。

- [ ] **Step 4: `upto_seq` 测试（红）**，追加到 `tests/api/test_agent_run_events_router.py`（照该文件的 `_app()` / `_capturing_read_scope` / `_compiled` 装配）：

```python
def test_upto_seq_adds_an_inclusive_upper_bound():
    captured: list = []
    with patch.object(router_mod, "get_agent_runs_repository") as g, patch(
        "app.db.session.read_scope", _capturing_read_scope(captured)
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        client = TestClient(_app())
        r = client.get(f"/api/v1/ai-library/runs/{RUN_ID}/events?after_seq=3&upto_seq=9")
    assert r.status_code == 200
    sql, params = _compiled(captured[0])
    assert "seq > " in sql and "seq <= " in sql
    assert 3 in params.values() and 9 in params.values()


def test_upto_seq_absent_keeps_the_old_query_shape():
    captured: list = []
    with patch.object(router_mod, "get_agent_runs_repository") as g, patch(
        "app.db.session.read_scope", _capturing_read_scope(captured)
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/events")
    sql, _ = _compiled(captured[0])
    assert "seq <= " not in sql
```

- [ ] **Step 5: 实现 `upto_seq`**（`list_run_events` 签名加 `upto_seq: int | None = None`，查询里 `.where(TE.seq > after_seq)` 后加）：

```python
            stmt = (
                select(TE.seq, TE.event_type, TE.payload, TE.created_at, TE.turn, TE.step)
                .where(TE.run_id == int(run_id))
                .where(TE.seq > after_seq)
                .where(*_event_type_filter(TE, types))
            )
            if upto_seq is not None:
                stmt = stmt.where(TE.seq <= upto_seq)
            rows = (
                (await session.execute(stmt.order_by(TE.seq.asc()).limit(max(1, min(limit, 1000)))))
                .mappings()
                .all()
            )
```

docstring 加一句：`upto_seq` 是含上界，回放刮擦条用它取 `events[:seq]`。

- [ ] **Step 6: `view-at` 测试（红）** `tests/api/test_run_view_at.py`：

```python
"""GET /ai-library/runs/{id}/view-at?seq=N — the folded view AS OF seq (phase 2b-1 replay).
Server-side fold through run_projection.replay so the frontend never duplicates a fold."""
from __future__ import annotations

import contextlib
import importlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

router_mod = importlib.import_module("app.api.ai_library_router")
pytestmark = pytest.mark.unit
USER_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "310819108761481"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    from app.core.deps import get_auth

    class _Auth:
        user_id = USER_ID
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant
    return app


def _rows_scope(rows):
    class _R:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _S:
        async def execute(self, stmt):
            return _R()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    return _rs


EVENTS = [
    {"seq": 1, "event_type": "user", "payload": {"content": "go"}},
    {"seq": 2, "event_type": "step_start", "payload": {"turn": 1, "step": 1, "model": "m"}},
    {"seq": 3, "event_type": "step_end", "payload": {"turn": 1, "step": 1, "model": "m", "usage": {"prompt": 10, "completion": 5}, "cost_cents": 0.5, "duration_ms": 100, "finish_reason": "stop"}},
    {"seq": 4, "event_type": "budget_check", "payload": {"pct": 90.0, "action": "warn", "spent_cents": 9, "budget_cents": 10}},
]


def test_view_at_folds_only_events_up_to_seq():
    with patch.object(router_mod, "get_agent_runs_repository") as g, patch(
        "app.db.session.read_scope", _rows_scope(EVENTS[:3])
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=3")
    assert r.status_code == 200
    body = r.json()
    assert body["seq"] == 3
    assert body["view"]["budget"] is None  # seq 4 not folded
    assert body["cost"]["spent_cents"] == 0.5


def test_view_at_requires_seq_and_404s_foreign_runs():
    with patch.object(router_mod, "get_agent_runs_repository") as g:
        g.return_value.get_by_id = AsyncMock(return_value=None)
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=3")
    assert r.status_code == 404
    with patch.object(router_mod, "get_agent_runs_repository") as g:
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at")
    assert r.status_code == 422
```

- [ ] **Step 7: 实现 `view-at`**（放在 `list_run_events` 之后）：

```python
@router.get(
    "/runs/{run_id}/view-at",
    summary="Folded run.view / run.cost AS OF seq (phase 2b-1 replay)",
)
async def get_run_view_at(run_id: str, auth: AuthDep, seq: int) -> Dict[str, Any]:
    """The same fold registry the recorder runs live, applied to
    ``events[:seq]``. One fold, never copied to TS — the scrubber calls this
    per tick (steps are few; no cache)."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRunTranscriptEvents as TE
    from app.services.ai.runner.run_projection import replay

    runs_repo = get_agent_runs_repository()
    row = await runs_repo.get_by_id(run_id, user_id=_coerce_user_uuid(auth.user_id))
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(TE.seq, TE.event_type, TE.payload)
                    .where(TE.run_id == int(run_id))
                    .where(TE.seq <= seq)
                    .order_by(TE.seq.asc())
                )
            )
            .mappings()
            .all()
        )
    views = replay(
        [(r["event_type"], r["payload"] or {}) for r in rows],
        seqs=[int(r["seq"]) for r in rows],
    )
    return {"seq": seq, "view": views["view"], "cost": views["cost"]}
```

- [ ] **Step 8: 跑绿 + lint** `uv run pytest -q tests/api/test_agent_run_events_router.py tests/api/test_run_view_at.py tests/db/test_migration_460_event_type_fork.py`；isort/black/ruff 三个改动文件。
- [ ] **Step 9: spec 回写**：在 spec §1 末尾加「实施记录：viewAtSeq 改为后端 `view-at`」，§2.2 加「tool_call 不进消息（历史本就只有 user/assistant/system）」，§4 第 2 页加「时间线入口 = 分叉 run 气泡上的芯片，不写 system_status 行」。
- [ ] **Step 10: 突变**：把 `upto_seq` 的 `.where(TE.seq <= upto_seq)` 改成 `<` → `test_upto_seq_adds_an_inclusive_upper_bound` 红（断言 `seq <= `）；还原。
- [ ] **Step 11: Commit**

```bash
git add supabase/migrations/460_transcript_event_type_fork.sql backend/app/api/ai_library_router.py backend/tests docs
git commit -m "feat(db,api): 迁移 460 放行 fork 事件 + 事件端点 upto_seq + view-at 折叠端点（harness 二期 2b-1 Task 1）"
```

---

### Task 2: 事件 → 消息重建 + `fork` 折叠 + run 行记 fork 列 + `fork` 事件落地

**Files:**
- Create: `backend/app/services/ai/runner/replay.py`
- Create: `backend/app/services/ai/runner/folds/fork.py`
- Modify: `backend/app/services/ai/runner/run_projection.py`（`empty_views` 加 `"fork": None`；底部 import 列表加 `fork`）
- Modify: `backend/app/services/ai/runner/run_recorder.py:87-135`（两个字段）与 `_insert_row`（两列）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:552-600`（`run_session_turn` 加 `fork_of: tuple[int, int] | None = None`）、`:1325-1340`（RunRecorder 传两字段；进入 recorder 后先 emit `fork`）
- Modify: `backend/app/services/issues/issue_agent_executor.py:140-160`（从 `execution_state.forked_from` 取 `fork_of` 传下去）
- Test: `backend/tests/runner/test_replay_messages.py`、`backend/tests/runner/test_fold_fork.py`、`backend/tests/runner/test_run_recorder_fork_columns.py`、`backend/tests/services/ai/chat/test_fork_event_emitted.py`

**Interfaces:**
- Produces: `replay.messages_from_events(events: list[dict]) -> list[dict]`（`events` 是 `{seq, event_type, payload}` 列表，已按 seq 升序；返回 `[{role, content}]`）；`replay.is_step_boundary(events, at_seq) -> bool`；`RunRecorder(fork_of_run_id: int | None = None, fork_at_seq: int | None = None)`；`run_session_turn(..., fork_of=(of_run_id, at_seq))`；事件 `fork{of_run_id: int, at_seq: int, steer: bool}`；`view.fork = {of_run_id, at_seq} | None`。
- Consumes: `execution_state.forked_from = {"run_id": int, "at_seq": int, "steer": bool}`（Task 3 写入）。

- [ ] **Step 1: 重建测试（红）** `tests/runner/test_replay_messages.py`：

```python
"""Phase 2b-1: events[:at_seq] → the messages a forked run starts from.
Mirrors build_history_messages: user / assistant / system only; a
compaction_summary replaces everything before it; tool_call never becomes a
message (it is not in cross-turn history either)."""
import pytest

from app.services.ai.runner import replay as r

pytestmark = pytest.mark.unit


def _ev(seq, t, **p):
    return {"seq": seq, "event_type": t, "payload": p}


def test_user_and_assistant_become_messages_in_order():
    evs = [
        _ev(1, "user", content="write act 1"),
        _ev(2, "step_start", turn=1, step=1),
        _ev(3, "tool_call", tool="Skill", args={"skill": "x"}, result={"ok": True}),
        _ev(4, "assistant", content="Act 1 …"),
        _ev(5, "step_end", turn=1, step=1),
    ]
    assert r.messages_from_events(evs) == [
        {"role": "user", "content": "write act 1"},
        {"role": "assistant", "content": "Act 1 …"},
    ]


def test_compaction_summary_replaces_everything_before_it():
    evs = [
        _ev(1, "user", content="a"),
        _ev(2, "assistant", content="b"),
        _ev(3, "compaction_summary", summary="a and b, condensed"),
        _ev(4, "user", content="c"),
    ]
    assert r.messages_from_events(evs) == [
        {"role": "system", "content": "a and b, condensed"},
        {"role": "user", "content": "c"},
    ]


def test_empty_content_events_are_skipped_and_unknown_types_ignored():
    evs = [_ev(1, "user", content=""), _ev(2, "fork", of_run_id=1, at_seq=2), _ev(3, "assistant", content="x")]
    assert r.messages_from_events(evs) == [{"role": "assistant", "content": "x"}]


def test_is_step_boundary_accepts_step_start_and_turn_end_only():
    evs = [_ev(1, "user"), _ev(2, "step_start", turn=1, step=1), _ev(3, "step_end", turn=1, step=1), _ev(4, "turn_end", reason="completed")]
    assert r.is_step_boundary(evs, 2) is True
    assert r.is_step_boundary(evs, 4) is True
    assert r.is_step_boundary(evs, 3) is False
    assert r.is_step_boundary(evs, 99) is False
```

- [ ] **Step 2: 跑红** `uv run pytest -q tests/runner/test_replay_messages.py`（ImportError）。
- [ ] **Step 3: 实现 `replay.py`**

```python
"""Phase 2b-1 replay/fork: rebuild the model-visible history from a run's
transcript. Only what ``build_history_messages`` would show a fresh turn —
user / assistant / system — comes back; tool_call rows never do (they are
not in cross-turn history either). A compaction_summary replaces everything
before it, exactly as the runtime compaction does."""
from __future__ import annotations

from typing import Any

BOUNDARY_TYPES = ("step_start", "turn_end")


def messages_from_events(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ev in events:
        t = ev.get("event_type")
        p = ev.get("payload") or {}
        if t == "compaction_summary":
            summary = str(p.get("summary") or "").strip()
            out = [{"role": "system", "content": summary}] if summary else []
            continue
        if t in ("user", "assistant"):
            content = str(p.get("content") or "").strip()
            if content:
                out.append({"role": t, "content": content})
    return out


def is_step_boundary(events: list[dict[str, Any]], at_seq: int) -> bool:
    """A fork point must be a step boundary so the last message is whole."""
    for ev in events:
        if int(ev.get("seq", -1)) == int(at_seq):
            return ev.get("event_type") in BOUNDARY_TYPES
    return False
```

- [ ] **Step 4: 折叠测试（红）** `tests/runner/test_fold_fork.py`：

```python
import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def test_fork_event_folds_into_view_fork():
    views = rp.apply(rp.empty_views(), "fork", {"of_run_id": 42, "at_seq": 7, "steer": True})
    assert views["view"]["fork"] == {"of_run_id": 42, "at_seq": 7}


def test_empty_views_has_fork_none_and_bad_payload_is_ignored():
    assert rp.empty_views()["view"]["fork"] is None
    views = rp.apply(rp.empty_views(), "fork", {"of_run_id": "x"})
    assert views["view"]["fork"] is None
```

- [ ] **Step 5: 实现 `folds/fork.py`** + 注册：

```python
from app.services.ai.runner.run_projection import register


@register("fork")
def fold_fork(views, payload):
    of_run_id, at_seq = payload.get("of_run_id"), payload.get("at_seq")
    if not isinstance(of_run_id, int) or not isinstance(at_seq, int):
        return None
    views["view"]["fork"] = {"of_run_id": of_run_id, "at_seq": at_seq}
    return views
```

`run_projection.empty_views()` 的 `"view"` 里加 `"fork": None`（放在 `"last_answer"` 后）；文件底部 import 列表加 `fork`（照 `budget` 的写法）。跑 `tests/runner/test_fold_fork.py` 与 `tests/runner/test_run_projection*.py` 绿。

- [ ] **Step 6: RunRecorder 两列测试（红）** `tests/runner/test_run_recorder_fork_columns.py`：

```python
"""RunRecorder writes fork_of_run_id / fork_at_seq when given (columns from mig 453)."""
import contextlib
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


async def test_insert_row_carries_fork_columns(monkeypatch):
    captured = {}

    class _Row:
        def __getitem__(self, i):
            return 777

    class _S:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params

            class _R:
                def first(self):
                    return _Row()

            return _R()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="issue_dispatch", fork_of_run_id=42, fork_at_seq=7)
    monkeypatch.setattr(rec, "_link_task", _noop)
    await rec._insert_row()
    assert "fork_of_run_id" in captured["sql"] and "fork_at_seq" in captured["sql"]
    assert captured["params"]["fork_of_run_id"] == 42 and captured["params"]["fork_at_seq"] == 7


async def test_insert_row_omits_fork_columns_when_not_forked(monkeypatch):
    captured = {}

    class _S:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)

            class _R:
                def first(self):
                    return None

            return _R()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    await rec._insert_row()
    assert "fork_of_run_id" not in captured["sql"]


async def _noop():
    return None
```

- [ ] **Step 7: 实现**：`RunRecorder` 数据类在 `attribution` 后加

```python
    # Phase 2b-1: set only on a forked run (mig 453 columns).
    fork_of_run_id: Optional[int] = None
    fork_at_seq: Optional[int] = None
```

`_insert_row` 的 `payload` 里 `"metadata_json"` 之后加 `"fork_of_run_id": self.fork_of_run_id, "fork_at_seq": self.fork_at_seq,`（`None` 会被既有的 `{k: v ... if v is not None}` 过滤掉）。

- [ ] **Step 8: chat service 传参 + emit 测试（红）** `tests/services/ai/chat/test_fork_event_emitted.py`（照 `tests/services/ai/chat/test_stop_reason_passthrough.py` 的 `_chat_env` / `_FakeStore` / `_session_row` 装配）：

```python
"""A forked turn: RunRecorder gets the fork columns and the run's first
event is fork{of_run_id, at_seq, steer} — before any user/step event."""
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.ai.chat import ai_library_chat_service as svc_mod
from tests.test_parity_gap_coverage import _chat_env, _FakeStore, _session_row

pytestmark = pytest.mark.unit


async def test_fork_of_reaches_recorder_and_emits_fork_first():
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    seen = {}
    real_recorder = svc_mod.RunRecorder

    class _Rec(real_recorder):
        def __init__(self, *a, **kw):
            seen["kw"] = kw
            super().__init__(*a, **kw)

    with _chat_env(run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id) as recorder, patch.object(
        svc_mod, "RunRecorder", _Rec
    ):
        await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(), user_id=user_id, content="go", trigger="issue_dispatch", fork_of=(42, 7)
        )
    assert seen["kw"]["fork_of_run_id"] == 42 and seen["kw"]["fork_at_seq"] == 7
    types = [e[0] for e in recorder.events]
    assert types[0] == "fork"
    assert recorder.events[0][1] == {"of_run_id": 42, "at_seq": 7, "steer": False}
```

（`_chat_env` 若不暴露 recorder 的事件列表，按该 fixture 的既有 `recorder` 对象补一个 `events` 采集——先读 `tests/test_parity_gap_coverage.py::_chat_env`，别猜。）

- [ ] **Step 9: 实现**：`run_session_turn` 签名加 `fork_of: Optional[tuple[int, int]] = None`（两处签名都加，见 `:552-600` 的两个重载/包装）；`RunRecorder(...)` 调用加 `fork_of_run_id=fork_of[0] if fork_of else None, fork_at_seq=fork_of[1] if fork_of else None`；`as recorder:` 之后、`try:` 之前：

```python
                if fork_of is not None:
                    from app.services.ai.runner.events import emit

                    await emit(
                        recorder,
                        "fork",
                        {"of_run_id": int(fork_of[0]), "at_seq": int(fork_of[1]), "steer": bool(fork_steer)},
                    )
```

`fork_steer` 由 `run_session_turn` 的新参数 `fork_steer: bool = False` 传入（Task 3 的端点知道有没有 steer）。

- [ ] **Step 10: executor 传参**（`issue_agent_executor.py:152` 的 `run_session_turn(` 调用）：

```python
    forked = (issue.get("execution_state") or {}).get("forked_from") or None
    fork_of = (int(forked["run_id"]), int(forked["at_seq"])) if forked else None
    ...
        result = await AILibraryChatService().run_session_turn(
            session_id,
            ...,
            fork_of=fork_of,
            fork_steer=bool(forked and forked.get("steer")),
        )
```

并在 `tests/services/issues/test_issue_agent_executor*.py`（找既有的 executor 测试文件）追加一例：`execution_state.forked_from={"run_id":42,"at_seq":7,"steer":True}` → `run_session_turn` 收到 `fork_of=(42,7), fork_steer=True`。

- [ ] **Step 11: 缓冲回退守卫**：本 Task 没给 run_turn 结果加标志（`fork` 是事件不是标志），在 PR 里写明；`tests/runner/test_turn_end_reasons.py` 不动。
- [ ] **Step 12: 跑绿 + 突变**：`messages_from_events` 去掉压缩替换（`out = [...]` 改成 `out.append`）→ 红；`fold_fork` 不校验 int → `bad_payload` 例红；还原。
- [ ] **Step 13: Commit**

```bash
git commit -am "feat(runner): 事件→消息重建、fork 折叠、RunRecorder fork 列、分叉 run 首条 fork 事件（harness 二期 2b-1 Task 2）"
```

---

### Task 3: fork 端点 + `issue_fork` 服务 + `/forks` 反查

**Files:**
- Create: `backend/app/services/issues/issue_fork.py`
- Modify: `backend/app/api/ai_library_router.py`（`view-at` 之后加 `POST /runs/{run_id}/fork`、`GET /runs/{run_id}/forks`）
- Modify: `backend/app/repositories/agent_runs_repository.py`（`list_forks(run_id, *, user_id)`）
- Test: `backend/tests/services/issues/test_issue_fork.py`、`backend/tests/api/test_run_fork_router.py`

**Interfaces:**
- Consumes: Task 2 的 `replay.messages_from_events` / `is_step_boundary`；2a 的 `AgentRunsRepository.running_root_run_id(issue_id, conversation_id)`（raise on failure）与 `issues_router._start_execute_issue(issue_id)`（搬到 `issue_fork` 可共用处：把 `_start_execute_issue` 提到 `app/services/issues/issue_dispatch.py::start_execute_issue` 并让 router 与本服务都调它——这是本 Task 唯一的搬迁，PR 单列）；`AILibraryChatService.create_session(user_id, agent_slug, title, project_id, team_id, context_type, context_id)`；`ConversationsAIStore.append_user_message(session_id, user_id, content)` / `append_assistant_message(session_id, agent_id, content, prompt_tokens, completion_tokens, metadata)`；`agent_run_inbox_repository` 的 steer 写入（2a 路径）；`issue_repository.set_paused_at` 与 `execution_state` 更新入口（2a 的 `_persist_workflow_id` 旁边的 `update_execution_state`）。
- Produces: `POST /ai-library/runs/{run_id}/fork {at_seq, steer?}` → `201 {run_id: None, session_id, workflow_id, issue_id, forked_from: {run_id, at_seq}}`（新 run 行由 workflow 打开，响应里 `run_id` 为 null，前端按 `issue_id` 重拉 progress）；`GET /ai-library/runs/{run_id}/forks` → `{items: [{run_id, at_seq, created_at, status}]}`；错误码 `not_an_issue_run` 409、`run_live` 409、`not_a_step_boundary` 400、`issue_terminal` 409、`run_state_unavailable` 503、`dispatch_failed` 503。

- [ ] **Step 1: 服务层测试（红）** `tests/services/issues/test_issue_fork.py`——把所有 I/O 通过可注入的 `deps` 桩掉（照 `tests/runner/test_budget_question_kind.py` 的 `deps` fixture 写法）：

```python
"""issue_fork.fork_run: preconditions → rebuild → new session → pointer switch
→ marker clear → steer inbox → dispatch. Every side effect is a recorded call."""
import pytest

from app.services.issues import issue_fork as f

pytestmark = pytest.mark.unit


class _Deps:
    def __init__(self):
        self.calls = []
        self.run = {"id": 42, "issue_id": 9, "user_id": "u", "agent_id": "a"}
        self.issue = {"id": 9, "status": "in_progress", "ai_session_id": 100, "assignee_agent_id": "a", "created_by_user_id": "u", "title": "T", "team_id": 1, "project_id": None, "hidden_at": None, "execution_state": {"awaiting_input": {"question_id": "q:42:4", "run_id": "42"}}, "paused_at": "x"}
        self.events = [
            {"seq": 1, "event_type": "user", "payload": {"content": "go"}},
            {"seq": 2, "event_type": "step_start", "payload": {}},
            {"seq": 3, "event_type": "assistant", "payload": {"content": "act 1"}},
            {"seq": 4, "event_type": "step_start", "payload": {}},
        ]
        self.live = None

    async def get_run(self, run_id, user_id):
        return self.run

    async def get_issue(self, issue_id):
        return self.issue

    async def list_events(self, run_id):
        return self.events

    async def running_root_run_id(self, issue_id, conversation_id):
        if self.live == "raise":
            raise RuntimeError("db")
        return self.live

    async def create_session(self, **kw):
        self.calls.append(("session", kw))
        return {"id": "200", "agent_slug": "s"}

    async def append_messages(self, session_id, messages, meta):
        self.calls.append(("messages", session_id, messages, meta))

    async def switch_session_and_mark(self, issue_id, session_id, forked_from):
        self.calls.append(("switch", issue_id, session_id, forked_from))

    async def supersede_question(self, issue_id, question_id):
        self.calls.append(("supersede", question_id))

    async def write_steer(self, issue_id, user_id, text):
        self.calls.append(("steer", text))

    async def dispatch(self, issue_id):
        self.calls.append(("dispatch", issue_id))
        return "wf-1"

    async def restore_session(self, issue_id, session_id):
        self.calls.append(("restore", session_id))


async def test_happy_path_records_every_side_effect_in_order():
    d = _Deps()
    out = await f.fork_run(42, at_seq=4, steer="be darker", user_id="u", deps=d)
    kinds = [c[0] for c in d.calls]
    assert kinds == ["session", "messages", "switch", "supersede", "steer", "dispatch"]
    assert d.calls[1][2] == [{"role": "user", "content": "go"}, {"role": "assistant", "content": "act 1"}]
    assert d.calls[1][3] == {"forked_from": {"run_id": 42, "at_seq": 4}}
    assert d.calls[2][3] == {"run_id": 42, "at_seq": 4, "steer": True}
    assert out == {"run_id": None, "session_id": "200", "workflow_id": "wf-1", "issue_id": 9, "forked_from": {"run_id": 42, "at_seq": 4}}


async def test_no_steer_skips_the_inbox_write_and_supersede_only_when_the_question_belongs_to_the_run():
    d = _Deps()
    d.issue["execution_state"] = {"awaiting_input": {"question_id": "q:41:4", "run_id": "41"}}
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    kinds = [c[0] for c in d.calls]
    assert "steer" not in kinds and "supersede" not in kinds


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda d: d.run.update(issue_id=None), "not_an_issue_run"),
        (lambda d: setattr(d, "live", 43), "run_live"),
        (lambda d: setattr(d, "live", "raise"), "run_state_unavailable"),
        (lambda d: d.issue.update(status="done"), "issue_terminal"),
    ],
)
async def test_preconditions(mutate, code):
    d = _Deps()
    mutate(d)
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert ei.value.code == code


async def test_at_seq_must_be_a_step_boundary():
    d = _Deps()
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=3, steer=None, user_id="u", deps=d)
    assert ei.value.code == "not_a_step_boundary"


async def test_dispatch_failure_restores_the_session_pointer():
    d = _Deps()

    async def boom(issue_id):
        raise RuntimeError("dbos down")

    d.dispatch = boom
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert ei.value.code == "dispatch_failed"
    assert d.calls[-1] == ("restore", 100)
```

- [ ] **Step 2: 跑红**，然后实现 `issue_fork.py`：

```python
"""Phase 2b-1 §2: fork an issue run at a step boundary.

Pure orchestration over an injectable ``deps`` object so the order of side
effects is a unit-tested contract; ``default_deps()`` binds the real
repositories. The original run and session are never written."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from app.services.ai.runner.replay import is_step_boundary, messages_from_events

TERMINAL = {"done", "cancelled", "closed"}


class ForkRejected(Exception):
    def __init__(self, code: str, status: int, message: str = ""):
        super().__init__(message or code)
        self.code, self.status = code, status


class ForkDeps(Protocol):
    async def get_run(self, run_id: int, user_id: str) -> Optional[dict]: ...
    async def get_issue(self, issue_id: int) -> Optional[dict]: ...
    async def list_events(self, run_id: int) -> list[dict]: ...
    async def running_root_run_id(self, issue_id: int, conversation_id: Optional[int]) -> Optional[int]: ...
    async def create_session(self, **kw: Any) -> dict: ...
    async def append_messages(self, session_id: str, messages: list[dict], meta: dict) -> None: ...
    async def switch_session_and_mark(self, issue_id: int, session_id: str, forked_from: dict) -> None: ...
    async def supersede_question(self, issue_id: int, question_id: str) -> None: ...
    async def write_steer(self, issue_id: int, user_id: str, text: str) -> None: ...
    async def dispatch(self, issue_id: int) -> str: ...
    async def restore_session(self, issue_id: int, session_id: Optional[int]) -> None: ...


async def fork_run(run_id: int, *, at_seq: int, steer: Optional[str], user_id: str, deps: ForkDeps) -> dict:
    run = await deps.get_run(run_id, user_id)
    if not run:
        raise ForkRejected("not_found", 404)
    issue_id = run.get("issue_id")
    if not issue_id:
        raise ForkRejected("not_an_issue_run", 409, "only issue runs can be forked")
    issue = await deps.get_issue(int(issue_id))
    if not issue or issue.get("hidden_at"):
        raise ForkRejected("not_found", 404)
    if issue.get("status") in TERMINAL:
        raise ForkRejected("issue_terminal", 409, "reopen the issue before forking")
    try:
        live = await deps.running_root_run_id(int(issue_id), issue.get("ai_session_id"))
    except Exception as exc:  # noqa: BLE001 — typed 503, never a silent pass
        raise ForkRejected("run_state_unavailable", 503, str(exc)) from exc
    if live:
        raise ForkRejected("run_live", 409, "pause or cancel the running run first")
    events = await deps.list_events(run_id)
    if not is_step_boundary(events, at_seq):
        raise ForkRejected("not_a_step_boundary", 400, "at_seq must be a step_start or turn_end seq")
    messages = messages_from_events([e for e in events if int(e["seq"]) <= at_seq])

    forked_from = {"run_id": int(run_id), "at_seq": int(at_seq), "steer": bool(steer and steer.strip())}
    session = await deps.create_session(
        user_id=issue.get("created_by_user_id") or issue.get("assignee_user_id") or user_id,
        agent_id=issue.get("assignee_agent_id"),
        title=(issue.get("title") or "Issue")[:200],
        project_id=issue.get("project_id"),
        team_id=issue.get("team_id"),
        context_type="issue",
        context_id=str(issue_id),
    )
    session_id = str(session["id"])
    await deps.append_messages(session_id, messages, {"forked_from": {"run_id": int(run_id), "at_seq": int(at_seq)}})
    previous_session = issue.get("ai_session_id")
    await deps.switch_session_and_mark(int(issue_id), session_id, forked_from)
    awaiting = (issue.get("execution_state") or {}).get("awaiting_input") or {}
    if str(awaiting.get("run_id") or "") == str(run_id) and awaiting.get("question_id"):
        await deps.supersede_question(int(issue_id), str(awaiting["question_id"]))
    if forked_from["steer"]:
        await deps.write_steer(int(issue_id), user_id, steer.strip())
    try:
        workflow_id = await deps.dispatch(int(issue_id))
    except Exception as exc:  # noqa: BLE001
        await deps.restore_session(int(issue_id), previous_session)
        raise ForkRejected("dispatch_failed", 503, str(exc)) from exc
    return {
        "run_id": None,
        "session_id": session_id,
        "workflow_id": workflow_id,
        "issue_id": int(issue_id),
        "forked_from": {"run_id": int(run_id), "at_seq": int(at_seq)},
    }
```

`default_deps()`（同文件）把每个方法绑到真实实现：`get_run` → `get_agent_runs_repository().get_by_id(str(run_id), user_id=UUID(user_id))`；`get_issue` → `issue_repository.get_by_id`；`list_events` → 与 `view-at` 相同的 ORM 读（抽成 `agent_runs_repository.list_transcript_events(run_id)`，Task 1 的两个端点也改用它——**不要**再写第三份查询）；`running_root_run_id` → `get_agent_runs_repository().running_root_run_id`；`create_session` → `AILibraryChatService().create_session(...)`（agent_id → slug 经 `get_agent_repository().get_by_id`）；`append_messages` → 逐条 `ConversationsAIStore.append_user_message` / `append_assistant_message(prompt_tokens=0, completion_tokens=0, metadata=meta)`，`system` 角色用 `append_assistant_message` 并 `metadata={"role_hint": "system", **meta}`（`build_history_messages` 已把 system 当历史）；`switch_session_and_mark` → 一个 `update(Issues).values(ai_session_id=int(session_id), paused_at=None, execution_state=execution_state - 'awaiting_input' || {'forked_from': ...})`（jsonb 用 `func.jsonb_build_object`，照 2a T4 的教训不 `cast("{}")`）；`supersede_question` → 2a 的 `input_gate.mark_question_answered(issue_id, question_id, value=None, superseded=True)`（若签名无 `superseded`，加上并在其单测钉住）；`write_steer` → 2a issue_messages 改投用的 `agent_run_inbox_repository.insert(kind="steer", target_kind="issue", ...)`；`dispatch` → `issue_dispatch.start_execute_issue`（从 `issues_router._start_execute_issue` 搬出，router 改调它）；`restore_session` → `update(Issues).values(ai_session_id=previous)`。

- [ ] **Step 3: 端点测试（红）** `tests/api/test_run_fork_router.py`（照 `test_run_view_at.py` 的 `_app()`）：`fork_run` 桩成 `AsyncMock`，断言 201 透传返回；`ForkRejected("run_live", 409)` → 409 且 `detail == {"code": "run_live", "message": ...}`；`at_seq` 缺 → 422；`GET /forks` 返回 `list_forks` 的行（`run_id` 为 str）。
- [ ] **Step 4: 实现端点**

```python
class ForkRunRequest(BaseModel):
    at_seq: int = Field(ge=1)
    steer: Optional[str] = Field(default=None, max_length=4000)


@router.post("/runs/{run_id}/fork", status_code=201, summary="Fork an issue run at a step boundary (phase 2b-1)")
async def fork_run_endpoint(run_id: str, body: ForkRunRequest, auth: AuthDep) -> Dict[str, Any]:
    from app.services.issues.issue_fork import ForkRejected, default_deps, fork_run

    try:
        return await fork_run(int(run_id), at_seq=body.at_seq, steer=body.steer, user_id=str(auth.user_id), deps=default_deps())
    except ForkRejected as rej:
        raise HTTPException(status_code=rej.status, detail={"code": rej.code, "message": str(rej)})


@router.get("/runs/{run_id}/forks", summary="Runs forked from this run (phase 2b-1)")
async def list_run_forks(run_id: str, auth: AuthDep) -> Dict[str, Any]:
    runs_repo = get_agent_runs_repository()
    if not await runs_repo.get_by_id(run_id, user_id=_coerce_user_uuid(auth.user_id)):
        raise HTTPException(status_code=404, detail="run not found")
    rows = await runs_repo.list_forks(int(run_id))
    return {"items": [{"run_id": str(r["id"]), "at_seq": r["fork_at_seq"], "created_at": r["created_at"], "status": r["status"]} for r in rows]}
```

`list_forks`：`select(AgentRuns.id, AgentRuns.fork_at_seq, AgentRuns.created_at, AgentRuns.status).where(AgentRuns.fork_of_run_id == run_id).order_by(AgentRuns.created_at.asc())`，单测断言编译 SQL 含 `fork_of_run_id = `。

- [ ] **Step 5: 跑绿 + lint + 突变**：`fork_run` 去掉 `run_live` 检查 → 红；`switch_session_and_mark` 的真实实现里去掉 `awaiting_input` 清除 → `tests/services/issues/test_issue_fork_deps.py`（对 `default_deps` 的 SQL 绑定断言：`paused_at = NULL`、`- 'awaiting_input'`）红；还原。
- [ ] **Step 6: Commit**

```bash
git commit -am "feat(issues): POST /runs/{id}/fork + issue_fork 服务 + GET /runs/{id}/forks（harness 二期 2b-1 Task 3）"
```

---

### Task 4: 逐工具超时

**Files:**
- Create: `backend/app/services/ai/runner/tool_timeouts.py`、`backend/app/services/ai/runner/tool_exec.py`、`backend/app/services/ai/runner/folds/tools.py`
- Modify: `backend/app/core/config.py`（`TOOL_TIMEOUTS: dict[str, int] = Field(default_factory=dict)`，放在 `CORS_ORIGINS` 附近，注释指向本文）
- Modify: `backend/app/services/ai/runner/agent_runner.py:859-934` 与 `:1884-1979`（每处 `result = await <handler>` → `result = await self._timed(tool_name, <handler>)`）；新增方法 `_timed`
- Modify: `backend/app/services/ai/runner/run_projection.py`（`empty_views` 加 `"tools": {"timed_out": 0, "last_timed_out": None}`；import 加 `tools`）
- Modify: `backend/app/services/ai/prompts/README.md`（What the model sees：超时错误正文）
- Test: `backend/tests/runner/test_tool_exec.py`、`backend/tests/runner/test_tool_timeouts_table.py`、`backend/tests/runner/test_fold_tools.py`、`backend/tests/runner/test_tool_timeout_both_loops.py`、`backend/tests/runner/test_tool_await_source_guard.py`

**Interfaces:**
- Produces: `tool_timeouts.resolve_timeout(tool_name: str) -> float`；`tool_exec.run_tool_with_timeout(tool_name, coro, *, timeout_s=None) -> dict`（超时返回 `{"error": "timeout", "timed_out": True, "timeout_s": n, "elapsed_s": x, "tool": name}`；handler 抛异常时返回 `{"error": str(exc), "timed_out": False}`——与各 site 既有的 try/except 语义合并：先由 `_timed` 统一，site 内原有的 `except` 保留给 handler 自己的类型化错误）；事件 `tool_call.payload.result.timed_out`；`view.tools = {timed_out: int, last_timed_out: str | None}`。

- [ ] **Step 1: 时限表测试（红）**

```python
# tests/runner/test_tool_timeouts_table.py
import pytest

from app.services.ai.runner import tool_timeouts as tt

pytestmark = pytest.mark.unit


def test_defaults_cover_every_built_in_tool_family():
    assert tt.resolve_timeout("Skill") == 30
    assert tt.resolve_timeout("FinishIssue") == 10 and tt.resolve_timeout("AskUser") == 10
    assert tt.resolve_timeout("ResourceFetch") == 60
    assert tt.resolve_timeout("GenerateImage") == 600 and tt.resolve_timeout("GenerateVideo") == 600
    assert tt.resolve_timeout("Delegate") == 900
    assert tt.resolve_timeout("skill.script-outline") == 120  # MCP-advertised
    assert tt.resolve_timeout("SomethingNew") == 60


def test_config_overrides_win(monkeypatch):
    monkeypatch.setattr(tt.settings, "TOOL_TIMEOUTS", {"ResourceFetch": 15, "SomethingNew": 5})
    assert tt.resolve_timeout("ResourceFetch") == 15
    assert tt.resolve_timeout("SomethingNew") == 5
    assert tt.resolve_timeout("Skill") == 30
```

- [ ] **Step 2: 实现 `tool_timeouts.py`**

```python
"""Per-tool wall-clock limits (phase 2b-1 §3). Defaults by family; config.yml
TOOL_TIMEOUTS overrides by exact tool name. A timeout is a TOOL result
({error:"timeout", timed_out:true}) the model sees — never a run stop."""
from __future__ import annotations

from app.core.config import settings

DEFAULTS: dict[str, float] = {
    "Skill": 30,
    "FinishIssue": 10,
    "AskUser": 10,
    "ResourceFetch": 60,
    "GenerateImage": 600,
    "GenerateVideo": 600,
    "Delegate": 900,
}
MCP_DEFAULT = 120.0
FALLBACK = 60.0


def resolve_timeout(tool_name: str) -> float:
    override = (getattr(settings, "TOOL_TIMEOUTS", None) or {}).get(tool_name)
    if isinstance(override, (int, float)) and override > 0:
        return float(override)
    if tool_name in DEFAULTS:
        return float(DEFAULTS[tool_name])
    if "." in tool_name:  # MCP-advertised names look like skill.x / agent.y / server.tool
        return MCP_DEFAULT
    return FALLBACK
```

`config.py` 加 `TOOL_TIMEOUTS: dict[str, int] = Field(default_factory=dict, description="Per-tool wall-clock seconds; keys are tool names (see runner/tool_timeouts.py)")`。

- [ ] **Step 3: 包法测试（红）** `tests/runner/test_tool_exec.py`

```python
import asyncio

import pytest

from app.services.ai.runner import tool_exec as tx

pytestmark = pytest.mark.unit


async def test_returns_handler_result_when_in_time():
    async def fast():
        return {"ok": True}

    out = await tx.run_tool_with_timeout("Skill", fast(), timeout_s=1)
    assert out == {"ok": True}


async def test_timeout_yields_a_typed_result_and_cancels_the_handler():
    cancelled = {"seen": False}

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise

    out = await tx.run_tool_with_timeout("ResourceFetch", slow(), timeout_s=0.05)
    assert out["error"] == "timeout" and out["timed_out"] is True
    assert out["timeout_s"] == 0.05 and out["tool"] == "ResourceFetch"
    assert 0 < out["elapsed_s"] < 1
    assert cancelled["seen"] is True


async def test_handler_exception_is_a_non_timeout_error():
    async def boom():
        raise RuntimeError("bad")

    out = await tx.run_tool_with_timeout("Skill", boom(), timeout_s=1)
    assert out["error"] == "bad" and out["timed_out"] is False
```

- [ ] **Step 4: 实现 `tool_exec.py`**

```python
"""The ONE way a runner awaits a tool handler (phase 2b-1 §3). Source guard:
tests/runner/test_tool_await_source_guard.py."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Optional

from loguru import logger

from app.services.ai.runner.tool_timeouts import resolve_timeout


async def run_tool_with_timeout(tool_name: str, coro: Awaitable[Any], *, timeout_s: Optional[float] = None) -> Any:
    limit = float(timeout_s if timeout_s is not None else resolve_timeout(tool_name))
    task = asyncio.ensure_future(coro)
    t0 = time.monotonic()
    try:
        return await asyncio.wait_for(task, timeout=limit)
    except asyncio.TimeoutError:
        # wait_for already cancelled the task; await it to quiescence so a
        # subprocess-backed tool has run its CancelledError cleanup (kill_tree).
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 — the cancel itself, or the tool's own cleanup error
            pass
        elapsed = round(time.monotonic() - t0, 3)
        logger.warning(f"[tool_exec] {tool_name} timed out after {elapsed}s (limit {limit}s)")
        return {"error": "timeout", "timed_out": True, "timeout_s": limit, "elapsed_s": elapsed, "tool": tool_name}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a tool's own failure is a tool result
        return {"error": str(exc), "timed_out": False}
```

- [ ] **Step 5: 源码守卫（红）** `tests/runner/test_tool_await_source_guard.py`

```python
"""Every tool handler await in agent_runner goes through self._timed — a
site that awaits a handler directly has no timeout and no timed_out flag."""
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit
SRC = pathlib.Path(__file__).resolve().parents[2] / "app/services/ai/runner/agent_runner.py"
HANDLERS = [
    r"self\.skill_tool\.execute\(",
    r"self\.resource_fetch_handler\(",
    r"self\.generate_image_handler\(",
    r"self\.generate_video_handler\(",
    r"self\.mcp_registry\.call\(",
    r"self\.delegate_tool\.execute\(",
    r"self\._dispatch_screenwriting\(",
    r"self\._dispatch_finish_issue\(",
    r"self\._dispatch_ask_user\(",
]


def test_every_tool_await_is_wrapped():
    text = SRC.read_text()
    bare = []
    for pat in HANDLERS:
        for m in re.finditer(r"await\s+" + pat, text):
            line = text[: m.start()].count("\n") + 1
            # the wrapped form is `await self._timed(name, self.x(...))` — no `await` directly before the handler
            bare.append((pat, line))
    assert bare == [], f"tool handlers awaited without _timed: {bare}"
    assert text.count("self._timed(") >= 2 * 6, "both loops must route their handlers through _timed"
```

- [ ] **Step 6: 两条循环改写**：每个 `result = await self.<handler>(args)` 改成 `result = await self._timed(tool_name, self.<handler>(args))`（`_dispatch_ask_user` / `_dispatch_finish_issue` / `_dispatch_screenwriting` 同样）。新增方法：

```python
    async def _timed(self, tool_name: str, coro):
        """All tool handlers go through here (phase 2b-1 §3): per-tool
        wall-clock limit; a timeout is a tool RESULT the model reads."""
        from app.services.ai.runner.tool_exec import run_tool_with_timeout

        return await run_tool_with_timeout(tool_name, coro)
```

各 site 原有的 `try/except` 保留（handler 自己的类型化错误先于 `_timed` 的通用兜底），只把 `await` 那一行换掉。

- [ ] **Step 7: 两条循环行为测试（红）** `tests/runner/test_tool_timeout_both_loops.py`（照 `tests/runner/test_turn_end_reasons.py` 的 `_composed()` / `_Rec` 装配；adapter 第一轮返回一个 `Skill` tool_call，第二轮返回纯文本 `stop`）：

```python
async def _never(args):
    await asyncio.sleep(60)


@pytest.mark.parametrize("stream", [False, True])
async def test_a_hanging_tool_times_out_and_the_turn_continues(stream, monkeypatch):
    from app.services.ai.runner import tool_timeouts as tt

    monkeypatch.setattr(tt.settings, "TOOL_TIMEOUTS", {"Skill": 0.05})
    runner = _runner_with_skill(_never)  # skill_tool.execute = _never
    rec = _Rec()
    if stream:
        chunks = [c async for c in runner.stream_turn(_composed(), [{"role": "user", "content": "q"}], recorder=rec, auto_recorder=False)]
        assert chunks[-1].finish_reason == "stop"
    else:
        out = await runner.run_turn(_composed(), [{"role": "user", "content": "q"}], recorder=rec)
        assert out["content"]
    tool_events = [e for e in rec.events if e[0] == "tool_call"]
    assert tool_events and tool_events[0][1]["result"]["timed_out"] is True
    assert tool_events[0][1]["result"]["error"] == "timeout"
    assert rec.turn_ends()[-1]["reason"] == "completed"  # a timeout is not a stop
```

`_runner_with_skill` 与假 adapter 的两轮回复照该测试文件既有的 `_StopHook`/`AsyncMock` 写法写全（第一轮 `tool_calls=[{"id":"c1","function":{"name":"Skill","arguments":"{\"skill\":\"x\"}"}}]`，第二轮 `content="done", finish_reason="stop"`）。stream 分支必须用**无 `stream` 属性**的 adapter（缓冲回退分支，Global Constraints）。

- [ ] **Step 8: 折叠测试 + 实现** `tests/runner/test_fold_tools.py`：`apply(empty, "tool_call", {"tool":"ResourceFetch","result":{"error":"timeout","timed_out":True}})` → `view.tools == {"timed_out": 1, "last_timed_out": "ResourceFetch"}`；`result.error="bad", timed_out=False` → 计数不变；再一次超时 → 2。`folds/tools.py`：

```python
from app.services.ai.runner.run_projection import register


@register("tool_call")
def fold_tool_call(views, payload):
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("timed_out") is not True:
        return None
    tools = views["view"].get("tools") or {"timed_out": 0, "last_timed_out": None}
    views["view"]["tools"] = {"timed_out": int(tools["timed_out"]) + 1, "last_timed_out": str(payload.get("tool") or "")}
    return views
```

（若 `tool_call` 已有注册的折叠，把逻辑并进那一个——`register` 拒绝重复注册，测试会告诉你。）

- [ ] **Step 9: prompts README**：`What the model sees` 加一段：超时时工具消息正文为 `{"error":"timeout","timed_out":true,"timeout_s":60,"elapsed_s":60.0,"tool":"ResourceFetch"}`（JSON 原样），并说明 Token effect（一次超时一条 ≤120 字符）与 KV Cache effect（append-only）。
- [ ] **Step 10: 跑绿 + 全量 + 突变**：`run_tool_with_timeout` 去掉 `task.cancel()`/`await task` → `cancelled["seen"]` 例红；`fold_tool_call` 不看 `timed_out` 只看 `error` → 计数例红；还原。
- [ ] **Step 11: Commit**

```bash
git commit -am "feat(runner): 逐工具超时——时限表 + run_tool_with_timeout 唯一包法 + 两循环接入 + view.tools 折叠（harness 二期 2b-1 Task 4）"
```

---

### Task 5: 前端回放刮擦条 + Cockpit「as of」

**Files:**
- Modify: `frontend/services/aiLibraryService.ts:328-340`（`getRunEvents(runId, afterSeq = 0, uptoSeq?: number)`；新增 `getRunViewAt(runId, seq)`）
- Create: `frontend/components/agentActivity/ReplayScrubber.tsx`、`frontend/components/agentActivity/replayTicks.ts`
- Create: `frontend/components/Todolist/replayContext.ts`（`ReplayState = {runId: string; seq: number | null; view: RunView | null; cost: RunCost | null}` + React context）
- Modify: `frontend/components/Todolist/IssueChatThread.tsx:46-60`（`RunTrajectory` 接受 `replay` 并切片 events）
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（持有 ReplayState、读写 `?run&seq`）
- Modify: `frontend/components/Todolist/blocks/CockpitBlock.tsx`（有 replay 且非最新 → 用 `replay.view/cost` 渲染四格并加 `as of step N` 角标）
- Modify: `frontend/public/locales/en.json`、`zh.json`（`replay.live`、`replay.asOf`、`replay.stepOf`、`replay.prev`、`replay.next`）
- Test: `frontend/components/agentActivity/replayTicks.test.ts`、`ReplayScrubber.test.tsx`、`frontend/components/Todolist/blocks/CockpitBlock.test.tsx`（追加）、`IssueDetailView.test.tsx`（追加深链）

**Interfaces:**
- Consumes: Task 1 的 `view-at`；`AgentRunEvent{seq, event_type, payload, turn, step}`；`runView.selectRunView/selectRunCost`。
- Produces: `replayTicks(events: AgentRunEvent[]): Tick[]`（`Tick = {seq, kind: 'step' | 'turn_end', turn, step}`，只含 `step_start` 与 `turn_end`）；`<ReplayScrubber events ticks seq isRunning onSeek(seq | null) onFork?(seq)>`（`seq === null` 表示 Live）；`ReplayContext`。

- [ ] **Step 1: 刻度纯函数测试（红）**

```ts
// replayTicks.test.ts
import { describe, expect, it } from 'vitest';
import { replayTicks } from './replayTicks';
const ev = (seq: number, event_type: string, payload: Record<string, unknown> = {}) => ({ seq, event_type, payload, created_at: '' } as never);

describe('replayTicks', () => {
  it('keeps only step_start and turn_end, in seq order, with coordinates', () => {
    const ticks = replayTicks([ev(1, 'user'), ev(2, 'step_start', { turn: 1, step: 1 }), ev(3, 'tool_call'), ev(4, 'step_start', { turn: 1, step: 2 }), ev(9, 'turn_end', { reason: 'completed' })]);
    expect(ticks).toEqual([
      { seq: 2, kind: 'step', turn: 1, step: 1 },
      { seq: 4, kind: 'step', turn: 1, step: 2 },
      { seq: 9, kind: 'turn_end', turn: null, step: null },
    ]);
  });
  it('is empty for a run with no steps yet', () => {
    expect(replayTicks([ev(1, 'user')])).toEqual([]);
  });
});
```

- [ ] **Step 2: 实现 `replayTicks.ts`**

```ts
import type { AgentRunEvent } from '../../types';
export interface Tick { seq: number; kind: 'step' | 'turn_end'; turn: number | null; step: number | null }
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
export function replayTicks(events: AgentRunEvent[]): Tick[] {
  return events
    .filter((e) => e.event_type === 'step_start' || e.event_type === 'turn_end')
    .sort((a, b) => a.seq - b.seq)
    .map((e) => ({ seq: e.seq, kind: e.event_type === 'step_start' ? 'step' : 'turn_end', turn: num(e.payload?.turn), step: num(e.payload?.step) }));
}
```

- [ ] **Step 3: 刮擦条组件测试（红）** `ReplayScrubber.test.tsx`（mock `react-i18next` 为 `t: (k, f) => f ?? k`）：渲染 4 个刻度 → `getAllByTestId('replay-tick')` 长度 4；点第 2 个 → `onSeek(4)`；`seq=4` 时 `replay-position` 文本 `step 2 / 3`（最后一个 turn_end 不计入分母）；按 `ArrowLeft` → `onSeek(2)`；`isRunning && seq===null` 时 `replay-live` 有 `data-on="true"`；点 Live → `onSeek(null)`；`onFork` 给了且 `seq!==null` → 出 `replay-fork` 按钮，点它 → `onFork(4)`。
- [ ] **Step 4: 实现 `ReplayScrubber.tsx`**（`role="slider"`、`aria-valuenow=seq`，刻度 `button[data-testid=replay-tick][data-kind]`，当前刻度 `data-current="true"`，键盘 ←/→ 在 `onKeyDown` 处理；配色 `border-info-line bg-info-soft text-info`）。
- [ ] **Step 5: 切片与上下文**：`replayContext.ts` 导出 `ReplayContext = createContext<ReplayState | null>(null)`；`IssueDetailView` 持有 `replay` state：`onSeek(seq)` → 若 `seq===null` 清空，否则 `aiLibraryService.getRunViewAt(runId, seq)` 后 `setReplay({runId, seq, view: selectRunView({view}), cost: selectRunCost({cost})})`，并 `setSearchParams({run, seq})`；挂载时若 URL 带 `run&seq` 先 seek。`RunTrajectory`（IssueChatThread）读 context：`replay?.runId === runId && replay.seq != null` → `events.filter(e => e.seq <= replay.seq)`，并把 `isRunning` 传 `false`（冻结）；刮擦条只挂在**当前最新 run** 的轨迹上方。
- [ ] **Step 6: Cockpit「as of」**（`CockpitBlock`）：`const replay = useContext(ReplayContext)`；`const frozen = replay && replay.runId === rollup.current_run?.id && replay.seq != null`；`view = frozen ? replay.view : selectRunView(...)`；`frozen` 时四格右上角 `<span data-testid="cockpit-asof">{t('replay.asOf', 'as of step {{n}}', {n})}</span>`，Pause/Resume/Cancel 按钮 `disabled`（回放态不许控制）。测试：给 context 传 `seq=7, view.step={done:7,total:12}` → `cockpit-steps` 文本含 `7 / 12`，`cockpit-asof` 出现，`cockpit-pause` disabled。
- [ ] **Step 7: i18n**：en `replay: {live: "Live", asOf: "as of step {{n}}", stepOf: "step {{n}} / {{total}}", prev: "Previous step", next: "Next step", fork: "Fork from step {{n}}"}`，zh 对应「实时 / 截至第 {{n}} 步 / 第 {{n}} / {{total}} 步 / 上一步 / 下一步 / 从第 {{n}} 步分叉」；照 `questionI18nParity.test.ts` 加 `replayI18nParity.test.ts`。
- [ ] **Step 8: 跑绿 + eslint + 全量（在 worktree 的 `frontend/`）+ 突变**：`replayTicks` 把 `turn_end` 也当 `step` → 红；`RunTrajectory` 不切片 → `IssueChatThread` 新增用例（context seq=4 时 step 3 节点不渲染）红；还原。
- [ ] **Step 9: Commit**

```bash
git commit -am "feat(ui): 回放刮擦条 + 轨迹切片 + Cockpit as-of + 深链（harness 二期 2b-1 Task 5）"
```

---

### Task 6: 前端 fork 弹窗、芯片与分叉点

**Files:**
- Modify: `frontend/services/aiLibraryService.ts`（`forkRun(runId, {at_seq, steer?})`、`getRunForks(runId)`；非 2xx 抛 `RunForkRejectedError{code,status}`，照 `issuesService.IssueControlError` 的 `_controlJson` 写法）
- Create: `frontend/components/Todolist/ForkRunDialog.tsx`、`frontend/components/Todolist/forkErrors.ts`
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（`onFork(seq)` 开弹窗；成功后清 replay、`onIssueChanged()` 重拉）
- Modify: `frontend/components/Todolist/blocks/CockpitBlock.tsx`（`view.fork` → 头部芯片 `cockpit-fork-chip`，点击 `onSeek` 到原 run + seq）
- Modify: `frontend/components/Todolist/IssueChatThread.tsx`（`RunTrajectory` 拉 `getRunForks`，把 `at_seq → run_id` 传给 `TrajectoryRenderer`）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/TrajectoryRenderer.tsx` + `nodes/builtins.tsx`（`forksBySeq?: Record<number, string[]>` prop；StepNodeView 在 `node` 起始 seq 命中时右侧出 `trajectory-fork-mark` 芯片）
- Modify: `frontend/components/TaskCenter/runView.ts`（`RunView.fork?: {of_run_id: number; at_seq: number} | null`、`RunView.tools?: {timed_out: number; last_timed_out: string | null}`）
- Modify: locales（`fork.title`、`fork.body`、`fork.steerLabel`、`fork.confirm`、`fork.chip`、`fork.mark`、`fork.error.run_live/issue_terminal/not_a_step_boundary/not_an_issue_run/run_state_unavailable/dispatch_failed/generic`）
- Test: `ForkRunDialog.test.tsx`、`forkErrors.test.ts`、`services/aiLibraryService.fork.test.ts`、`CockpitBlock.test.tsx`（追加）、`TrajectoryRenderer` 测试（追加 fork mark）、`forkI18nParity.test.ts`

**Interfaces:**
- Consumes: Task 3 的两个端点；Task 5 的 `ReplayContext`、`ReplayScrubber.onFork`。
- Produces: `forkRun(runId: string, body: {at_seq: number; steer?: string}) => Promise<{run_id: string | null; session_id: string; workflow_id: string; issue_id: number; forked_from: {run_id: number; at_seq: number}}>`；`getRunForks(runId) => Promise<{items: {run_id: string; at_seq: number; created_at: string; status: string}[]}>`；`forkErrorText(err, t)`。

- [ ] **Step 1: service 测试（红）**（照 `services/issuesService.controlError.test.ts`：stub fetch，真实 `{detail:{code,message}}` 体）：409 `run_live` → `RunForkRejectedError.code === 'run_live'`；201 → 返回体透传。
- [ ] **Step 2: 实现 service**（`_forkJson` 解析 `detail.code`，同 `_controlJson`）。
- [ ] **Step 3: 弹窗测试（红）** `ForkRunDialog.test.tsx`：渲染 `seq=7` → 标题 `Fork from step 7`、说明句、textarea；点 `Fork` → `onConfirm('be darker')`（trim）；空 steer → `onConfirm(undefined)`；`pending` 时按钮 disabled；`error` 显示 `fork.error.run_live` 映射文案。
- [ ] **Step 4: 实现弹窗**（`role="dialog"`、`data-testid="fork-dialog"`、`fork-steer`、`fork-confirm`、`fork-cancel`、`fork-error`；Esc 关闭；语义色 info）。
- [ ] **Step 5: 接线**：`IssueDetailView.onFork(seq)` → 弹窗 → `forkRun(runId, {at_seq: seq, steer})` → 成功：`setReplay(null)`、`setSearchParams({})`、`env.onIssueChanged?.()`；失败：`forkErrorText(err, t)` 进弹窗 `fork-error`。
- [ ] **Step 6: 芯片与分叉点**：Cockpit 头部 `view.fork` → `<button data-testid="cockpit-fork-chip">⑂ Forked from run #{short(of_run_id)} @ step {stepOf(at_seq)}</button>`（`stepOf` 用 `replayTicks` 把 seq 换成 step 号；找不到就显示 `seq N`），点击 → `onSeek` 到 `{runId: of_run_id, seq: at_seq}`（原 run 的轨迹已在同一时间线里）；`RunTrajectory` 对每个 run 拉 `getRunForks`（结束的 run 只拉一次，缓存进既有 `settledCache`），`forksBySeq` 传给 `TrajectoryRenderer`，StepNodeView 命中时出 `trajectory-fork-mark`（`Fork → #short(run_id)`，点击滚到那个 run 的气泡：`document.getElementById('run-<id>')?.scrollIntoView`，气泡容器加该 id）。
- [ ] **Step 7: i18n + parity 测试；eslint；全量；突变**：`forkRun` 不解析 `detail.code` → service 例红；弹窗不 trim → `onConfirm` 例红；还原。
- [ ] **Step 8: Commit**

```bash
git commit -am "feat(ui): 从 step 分叉——弹窗、Cockpit 芯片、原 run 分叉点标记（harness 二期 2b-1 Task 6）"
```

---

### Task 7: 前端超时徽标 + Cockpit Tools 格

**Files:**
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/foldEvents.ts:260-275`（`tool_call` 折叠读 `result.timed_out` → `line.detail.timedOut / elapsedS / timeoutS`，`ok=false`）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx:125-135`（`line.detail.timedOut` → `Timed out · {timeoutS}s` danger 徽标，替代通用 `failed`；展开显示 `elapsed`）
- Modify: `frontend/components/TaskCenter/runView.ts`（`toolsState(view)` 选择器）与 `frontend/components/Todolist/blocks/CockpitBlock.tsx`（第五格 `cockpit-tools`，仅 `timed_out > 0` 时渲染）
- Modify: locales（`trajectory.timedOut`、`issueDetail.tools`、`issueDetail.toolsTimedOut_one/_other`）
- Test: `foldEvents.test.ts`（追加）、`builtins`/`TrajectoryRenderer` 测试（追加）、`runView.test.ts`（追加）、`CockpitBlock.test.tsx`（追加）

- [ ] **Step 1: 折叠测试（红）**：`tool_call` payload `{tool:'ResourceFetch', result:{error:'timeout', timed_out:true, timeout_s:60, elapsed_s:60.0}}` → line `{type:'tool', ok:false, detail:{timedOut:true, timeoutS:60, elapsedS:60}}`；`result:{ok:false}` → `detail.timedOut` 为 `false`。
- [ ] **Step 2: 实现折叠**（`const timedOut = result?.timed_out === true;` `ok = !(result !== null && (result.ok === false || timedOut))`；detail 加三个字段）。
- [ ] **Step 3: 渲染测试（红）**：折叠后渲染 → `getByTestId('trajectory-timeout-badge')` 文本 `Timed out · 60s`，无 `failed` 文案；展开 → 含 `elapsed 60.0s`。
- [ ] **Step 4: 实现渲染**（builtins 里 `line.type === 'tool' && line.detail?.timedOut ? <span data-testid="trajectory-timeout-badge" className="text-danger">{t('trajectory.timedOut', 'Timed out · {{s}}s', {s})}</span> : (!line.ok && <span className="text-danger">{t('trajectory.failed')}</span>)`）。
- [ ] **Step 5: Cockpit 格**：`toolsState(view)` 返回 `view.tools ?? null`；`CockpitBlock` 在 Runs 格之后：`tools && tools.timed_out > 0 && <Cell label={t('issueDetail.tools','Tools')} testId="cockpit-tools"><span className="text-danger">{t('issueDetail.toolsTimedOut', {count: tools.timed_out})}</span><div className="text-[11px] text-ink-500">{tools.last_timed_out}</div></Cell>`；测试：`view.tools={timed_out:1,last_timed_out:'ResourceFetch'}` → 格出现且含 `ResourceFetch`；`timed_out:0` → 无 `cockpit-tools`。i18n `toolsTimedOut_one: "{{count}} timed out"`, `_other: "{{count}} timed out"`（zh「{{count}} 个超时」两条）。
- [ ] **Step 6: eslint；全量；突变**：折叠不看 `timed_out` → 徽标例红；Cockpit 不判 `> 0` → `timed_out:0` 例红；还原。
- [ ] **Step 7: Commit**

```bash
git commit -am "feat(ui): 工具超时徽标 + Cockpit Tools 格（harness 二期 2b-1 Task 7）"
```

---

### Task 8: 真栈验收 + 完成账

**Files:**
- Modify: 本计划（末尾「完成账」）；`backend/config.yml`（验收期间临时 `TOOL_TIMEOUTS: {ResourceFetch: 15}`，验收后**同一 PR 内改回**——不许把 15s 留在生产）
- Modify: `CLAUDE.md`（仅当发现新陷阱）

- [ ] **Step 1: 部署确认**：最后一个 PR 合并后 `gh run list --workflow=deploy-gpu.yml` 到 success；`ssh -i ~/.ssh/id_macmini heygo@10.0.0.10 'docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz'`；容器内 `grep -c "def run_tool_with_timeout" /app/app/services/ai/runner/tool_exec.py`；`app.nous.ink/version.json` 的 `commitSha` = 前端 merge SHA。
- [ ] **Step 2: ① 回放**：对 2a 留下的多步 run（MH-67 的 347474259567172）`GET /events?upto_seq=<step2 的 seq>` 只回到那里；`GET /view-at?seq=` 的 `view.step.done` 与事件一致；页面刮到 step 2，轨迹只剩两步、Cockpit 出 `as of step 2`、Pause 置灰；`?run&seq` 深链打开即定位。
- [ ] **Step 3: ② fork**：对同一 run 在 step 2 分叉并带 steer → 201；新 run 首条事件 `fork{of_run_id, at_seq, steer:true}`，事件 2 `inbox_claimed{kind:"steer"}`；`agent_runs.fork_of_run_id/fork_at_seq` 有值；原 run 事件数不变；`GET /forks` 列出；issue `ai_session_id` 已换、原会话消息数不变；页面：新 run 头部芯片可点回原 run 且自动刮到 step 2，原 run 轨迹 step 2 出 `Fork →`。前置条件各验一次：run 活着 → 409 `run_live`；`at_seq` 不在边界 → 400；issue done → 409 `issue_terminal`。
- [ ] **Step 4: ③ 超时**：合入 `TOOL_TIMEOUTS: {ResourceFetch: 15}` 的临时配置 PR 部署后，建 issue 让 `analyze` 抓 `https://httpbin.org/delay/30` → `tool_call.result.timed_out=true, timeout_s=15`；run 继续（`turn_end{completed}` 或模型换路）；`view.tools.timed_out=1`；页面徽标 `Timed out · 15s`、Cockpit `1 timed out · ResourceFetch`；验后改回配置并部署。
- [ ] **Step 5: 前端** `cd frontend && npm run e2e:prod`；三页截图对照 `~/Downloads/control-plane-2b1.html`。
- [ ] **Step 6: 完成账**：表格记每条证据（run id / seq / 端点响应 / 截图），未验项写原因；记忆 `project-harness-p4-phase2a-shipped` 追加 2b-1 状态并写 2b-2 入口（schedule → 收件箱、continuable 子代理，先接活 workforce 链的 4 条待接线）。

---

## 自审记录（写完后对照 spec）

- §1 回放：T1（`upto_seq`、`view-at`）+ T5（刮擦条、切片、as of、深链）。偏差①已写明。
- §2 fork：T2（重建、折叠、列、事件）+ T3（端点、服务、反查、`start_execute_issue` 搬迁）+ T6（弹窗、芯片、分叉点）。偏差②③已写明；§2.3 第 2 步的 supersede 经 `mark_question_answered(superseded=True)`。
- §3 超时：T4（表、包法、两循环、折叠、README）+ T7（徽标、格）。
- §4 三页：T5 / T6 / T7 各一页。
- §5 汇总表全部有 Task；§6 突变每 Task 至少一处；§7 不做项没有任何 Task 触及。
- 类型一致性：`view.fork{of_run_id, at_seq}`（T2 折叠 = T6 读）；`view.tools{timed_out, last_timed_out}`（T4 = T7）；`forked_from{run_id, at_seq, steer}`（T3 写 = T2 读）；`fork_of=(run_id, at_seq)` + `fork_steer`（T2 签名 = T2 executor 调用）；`Tick{seq, kind, turn, step}`（T5 = T6 的 `stepOf`）。
