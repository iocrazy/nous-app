# needs_input 一等状态 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent 在 issue dispatch 中声明 `needs_input` 后，DBOS workflow 原地挂起等用户回复；等待状态在 Task Center 醒目可见并发 inbox 通知；用户回复唤醒原 workflow 原地继续；所有失败形态降级为今天的"终结+回复重启"行为。

**Architecture:** `input_gate.py`（`DBOS.recv_async`/`send_async` 封装，仿 `approval_gate.py`）+ `_run_dispatch_with_continuation` 注入等待循环 + reply 端点分流唤醒 + 启动 reaper 清跨版本假活。等待态写 `task_tracking.metadata.awaiting_input`（业务装饰字段，路线 C 合规，不碰 phase）。

**Tech Stack:** Python 3.13 / FastAPI / DBOS（`recv_async`/`send_async` 已确认存在于所装版本）· React 19 前端 · pytest（`asyncio_mode=auto`）

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-07-30-needs-input-first-class-design.md`（含协作面总 spec `2026-08-02-collab-surface-and-ai-library-redesign-design.md` 第一批）
- **范围仅 2️⃣**：spec 里的 4️⃣（静默失败契约）由并行 session 推进，本计划不含
- 挂起 TTL：**72 小时**；每次 dispatch 等待轮数上限：**5**（用户已确认）
- 路线 C 纪律：**禁止 PATCH task_tracking 的 phase/status/progress 列**；只写 metadata（jsonb merge）
- 旧路径（`respond_to_issue_reply`）**必须保留**为兜底，任何失败形态不得比现状更糟
- 测试命令一律 `cd backend && uv run pytest`；async 测试无需装饰器
- UI 文案英文；i18n key camelCase；zh.json 提供中文值
- 提交信息结尾附 `Claude-Session: https://claude.ai/code/session_01JEBdiHMzaE8dPjupQEY6jF`
- 实施前先 `bash scripts/sync-worktree.sh`；分支名 `feature/needs-input-first-class-impl`

## 文件结构

| 动作 | 路径 | 职责 |
|------|------|------|
| 新建 | `backend/app/agent_framework/input_gate.py` | recv/send 封装 + 等待标记读写 + inbox 写入 |
| 新建 | `backend/tests/test_input_gate.py` | gate 单测 |
| 修改 | `backend/app/workflows/issue_lifecycle.py` | 等待循环注入 `_run_dispatch_with_continuation` + 回复回合 step |
| 修改 | `backend/tests/test_issue_continuation.py` | 等待循环 DI 测试 |
| 修改 | `backend/app/api/issue_messages_router.py` | 回复分流：唤醒 or 旧路径 |
| 新建 | `backend/tests/test_issue_reply_wake_routing.py` | 分流单测 |
| 修改 | `backend/app/main.py` | 启动 reaper `_bg_reap_stale_input_waits` |
| 新建 | `backend/tests/test_stale_input_wait_reaper.py` | reaper 单测 |
| 新建 | `supabase/migrations/399_inbox_agent_question_kind.sql`（号被占则顺延，`ls supabase/migrations | tail` 确认） | inbox kind CHECK 扩容 |
| 修改 | `backend/app/core/config.py` | `NEEDS_INPUT_RECV_TTL_HOURS=72`、`NEEDS_INPUT_MAX_WAIT_ROUNDS=5` |
| 修改 | `frontend/components/TaskCenter/taskRowPresentation.ts` + `.test.ts` | awaiting_input 行高亮 |
| 修改 | `frontend/components/notifications/InboxPanel.tsx`、`notificationLink.ts` + `.test.ts` | agent_question kind |
| 修改 | `frontend/public/locales/en.json`、`zh.json` | 文案 |

---

### Task 1: migration — inbox_notifications 扩 kind

**Files:**
- Create: `supabase/migrations/399_inbox_agent_question_kind.sql`

**Interfaces:**
- Produces: `inbox_notifications.kind` 可取值 `'agent_question'`（Task 2 写入依赖）

- [ ] **Step 1: 确认序号**

```bash
ls supabase/migrations | tail -3
```
若 399 已被占用，改用下一空号并同步改文件名（下文按 399 书写）。

- [ ] **Step 2: 写 migration**

```sql
-- 399: needs_input 一等状态（spec 2026-07-30）— inbox 通知新增 agent_question kind。
-- 原 CHECK 是封闭三 kind（mig 373 有意为之的 narrowness）；agent 提问是第四类
-- "需要我行动"的收件箱事件，符合该表"a result landed / needs my action"的定位。
ALTER TABLE public.inbox_notifications
  DROP CONSTRAINT IF EXISTS inbox_notifications_kind_check;
ALTER TABLE public.inbox_notifications
  ADD CONSTRAINT inbox_notifications_kind_check
  CHECK (kind IN ('generation_result', 'publish_result', 'autopilot_output', 'agent_question'));
```

注意：约束名以实际为准——先 `docker exec nous-db psql -U postgres -p 55434 -d postgres -tA -c "SELECT conname FROM pg_constraint WHERE conrelid='public.inbox_notifications'::regclass AND contype='c'"` 核对，不一致则替换 DROP 的名字。

- [ ] **Step 3: 本地库执行验证**

```bash
docker exec -i nous-db psql -U postgres -p 55434 -d postgres < supabase/migrations/399_inbox_agent_question_kind.sql
docker exec nous-db psql -U postgres -p 55434 -d postgres -tA -c \
  "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='inbox_notifications_kind_check'"
```
Expected: 输出含 `agent_question`。

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/399_inbox_agent_question_kind.sql
git commit -m "feat(db): inbox_notifications 增 agent_question kind — needs_input 通知落点"
```

---

### Task 2: input_gate — recv/send 封装 + 等待标记 + inbox 写入

**Files:**
- Create: `backend/app/agent_framework/input_gate.py`
- Create: `backend/tests/test_input_gate.py`

**Interfaces:**
- Consumes: `dbos.DBOS.recv_async/send_async`；`app.db.engine`（task_tracking metadata 与 inbox 写入）
- Produces（后续任务契约）:
  - `await await_user_input(issue_id: int, *, ttl_seconds: int) -> Optional[dict]` — 挂起；超时返 None；payload 形如 `{"reply_text": str, "user_id": str, "attachments": list|None}`；畸形 payload 返 None（视为超时，走兜底）
  - `await signal_user_reply(*, workflow_id: str, issue_id: int, reply_text: str, user_id: str, attachments: Optional[list]) -> bool`
  - `await mark_awaiting_input(*, workflow_id: str, issue_id: int, user_id: str, prompt: str) -> None` — 写 metadata.awaiting_input + inbox 行
  - `await clear_awaiting_input(*, workflow_id: str) -> None` — 删 metadata.awaiting_input（inbox 保留）
  - `TOPIC_PREFIX = "needs_input:"`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_input_gate.py`：

```python
"""input_gate —— needs_input 挂起/唤醒原语（仿 approval_gate 的测试口径）。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework import input_gate


@pytest.mark.asyncio
async def test_await_user_input_returns_payload():
    with patch.object(input_gate, "_recv_async", new=AsyncMock(return_value={
        "reply_text": "摊牌", "user_id": "u1", "attachments": None,
    })) as recv:
        got = await input_gate.await_user_input(123, ttl_seconds=60)
    assert got == {"reply_text": "摊牌", "user_id": "u1", "attachments": None}
    recv.assert_awaited_once_with("needs_input:123", timeout_seconds=60)


@pytest.mark.asyncio
async def test_await_user_input_timeout_returns_none():
    with patch.object(input_gate, "_recv_async", new=AsyncMock(return_value=None)):
        assert await input_gate.await_user_input(123, ttl_seconds=1) is None


@pytest.mark.asyncio
async def test_await_user_input_malformed_payload_returns_none():
    """畸形 payload 不炸 workflow —— 当超时处理，降级旧路径。"""
    with patch.object(input_gate, "_recv_async", new=AsyncMock(return_value="not-a-dict")):
        assert await input_gate.await_user_input(123, ttl_seconds=1) is None


@pytest.mark.asyncio
async def test_await_user_input_missing_reply_text_returns_none():
    with patch.object(input_gate, "_recv_async", new=AsyncMock(return_value={"user_id": "u1"})):
        assert await input_gate.await_user_input(123, ttl_seconds=1) is None


@pytest.mark.asyncio
async def test_signal_user_reply_sends_on_topic():
    with patch.object(input_gate, "_send_async", new=AsyncMock()) as send:
        ok = await input_gate.signal_user_reply(
            workflow_id="wf-1", issue_id=123,
            reply_text="hi", user_id="u1", attachments=None,
        )
    assert ok is True
    send.assert_awaited_once_with(
        "wf-1",
        {"reply_text": "hi", "user_id": "u1", "attachments": None},
        topic="needs_input:123",
    )


@pytest.mark.asyncio
async def test_signal_user_reply_swallow_errors_returns_false():
    """send 失败不是致命——调用方会走旧路径兜底。"""
    with patch.object(input_gate, "_send_async", new=AsyncMock(side_effect=RuntimeError("gone"))):
        ok = await input_gate.signal_user_reply(
            workflow_id="wf-1", issue_id=123,
            reply_text="hi", user_id="u1", attachments=None,
        )
    assert ok is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_input_gate.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.agent_framework.input_gate'`

- [ ] **Step 3: 实现 input_gate.py**

```python
"""needs_input 挂起/唤醒原语 —— approval_gate 的同族兄弟。

approval_gate 用同款 DBOS.recv/send 验证过 pause-resume 可行；本模块把它
接到 issue dispatch 的 needs_input 上：workflow 原地挂起等用户回复，回复
经 DBOS.send 精确唤醒。topic 按 issue 区分，避免同 workflow 多 gate 串扰。

所有对外函数失败都"软"处理（返 None/False），因为调用方永远有旧路径
（respond_to_issue_reply）兜底 —— 本模块任何故障都不得比现状更糟。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

TOPIC_PREFIX = "needs_input:"

# 提问原文进 metadata / inbox 前的截断（spec: 500）
_PROMPT_MAX = 500


def _topic_for(issue_id: int) -> str:
    return f"{TOPIC_PREFIX}{issue_id}"


async def _recv_async(topic: str, *, timeout_seconds: int) -> Any:
    """薄壳，供测试 patch。必须在 @DBOS.workflow 体内被调用。"""
    from dbos import DBOS

    return await DBOS.recv_async(topic, timeout_seconds=timeout_seconds)


async def _send_async(workflow_id: str, payload: dict, *, topic: str) -> None:
    from dbos import DBOS

    await DBOS.send_async(workflow_id, payload, topic=topic)


async def await_user_input(issue_id: int, *, ttl_seconds: int) -> Optional[dict]:
    """挂起当前 workflow 等 issue 的用户回复。超时/畸形 payload 均返 None。"""
    payload = await _recv_async(_topic_for(issue_id), timeout_seconds=ttl_seconds)
    if not isinstance(payload, dict) or not payload.get("reply_text"):
        if payload is not None:
            logger.warning(f"[input_gate] malformed payload for issue {issue_id}: {payload!r}")
        return None
    return {
        "reply_text": str(payload["reply_text"]),
        "user_id": str(payload.get("user_id") or ""),
        "attachments": payload.get("attachments"),
    }


async def signal_user_reply(
    *,
    workflow_id: str,
    issue_id: int,
    reply_text: str,
    user_id: str,
    attachments: Optional[list] = None,
) -> bool:
    """向挂起的 workflow 投递回复。失败返 False（调用方走旧路径）。"""
    try:
        await _send_async(
            workflow_id,
            {"reply_text": reply_text, "user_id": user_id, "attachments": attachments},
            topic=_topic_for(issue_id),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — 软失败是本模块契约
        logger.warning(f"[input_gate] send to wf={workflow_id} failed: {exc}")
        return False


async def mark_awaiting_input(
    *, workflow_id: str, issue_id: int, user_id: str, prompt: str
) -> None:
    """写等待标记（task_tracking.metadata.awaiting_input，路线 C 业务装饰字段）
    并投 inbox 通知。任一失败只记日志 —— 标记失败不阻断挂起，UI 少个高亮而已。"""
    from app.db import engine as db_engine

    clipped = (prompt or "")[:_PROMPT_MAX]
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db_engine.execute_as_service_role(
            """UPDATE public.task_tracking
               SET metadata = COALESCE(metadata, '{}'::jsonb)
                   || jsonb_build_object('awaiting_input', :marker::jsonb)
               WHERE dbos_workflow_id = :wf""",
            {
                "marker": json.dumps({"prompt": clipped, "since": now, "issue_id": issue_id}),
                "wf": workflow_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] mark metadata failed wf={workflow_id}: {exc}")
    try:
        await db_engine.execute_as_service_role(
            """INSERT INTO public.inbox_notifications
                   (user_id, kind, title, body, severity, link_kind, link_id)
               VALUES (:uid, 'agent_question', :title, :body, 'info', 'issue', :iid)""",
            {
                "uid": user_id,
                "title": "Agent needs your input",
                "body": clipped,
                "iid": str(issue_id),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] inbox insert failed issue={issue_id}: {exc}")


async def clear_awaiting_input(*, workflow_id: str) -> None:
    """移除等待标记。inbox 行有意保留（用户稍后仍可从收件箱进入）。"""
    from app.db import engine as db_engine

    try:
        await db_engine.execute_as_service_role(
            """UPDATE public.task_tracking
               SET metadata = metadata - 'awaiting_input'
               WHERE dbos_workflow_id = :wf""",
            {"wf": workflow_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] clear metadata failed wf={workflow_id}: {exc}")
```

实现要求：`db_engine.execute_as_service_role` 若不存在此名（以 `backend/app/db/engine.py` 实际导出为准——issues_router.py 约 356 行有同款用法可对照），替换为该文件中现有的 service-role 执行助手；参数风格（`:name`）也以现有用法为准。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest tests/test_input_gate.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_framework/input_gate.py backend/tests/test_input_gate.py
git commit -m "feat(agent): input_gate —— needs_input 挂起/唤醒原语 + 等待标记/inbox"
```

---

### Task 3: 配置项

**Files:**
- Modify: `backend/app/core/config.py`

**Interfaces:**
- Produces: `settings.NEEDS_INPUT_RECV_TTL_HOURS: int = 72`、`settings.NEEDS_INPUT_MAX_WAIT_ROUNDS: int = 5`

- [ ] **Step 1: 添加设置**

在 `config.py` 的 Settings 类中，找到相邻的 int 型业务配置（如超时类配置）成组的位置，添加：

```python
    # needs_input 一等状态（spec 2026-07-30）：recv 挂起 TTL 与单次 dispatch 等待轮上限
    NEEDS_INPUT_RECV_TTL_HOURS: int = 72
    NEEDS_INPUT_MAX_WAIT_ROUNDS: int = 5
```

- [ ] **Step 2: 快速验证**

```bash
cd backend && uv run python -c "from app.core.config import settings; print(settings.NEEDS_INPUT_RECV_TTL_HOURS, settings.NEEDS_INPUT_MAX_WAIT_ROUNDS)"
```
Expected: `72 5`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/config.py
git commit -m "feat(config): needs_input TTL(72h)/等待轮上限(5) 配置项"
```

---

### Task 4: 等待循环注入 `_run_dispatch_with_continuation`

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`（`_run_dispatch_with_continuation`，现约 350-438 行；新增回复回合 step）
- Test: `backend/tests/test_issue_continuation.py`（追加用例，不改既有用例）

**Interfaces:**
- Consumes: Task 2 的 gate 四函数（经依赖注入传入，便于测试）；`extract_issue_outcome`（`app.services.ai.tools.finish_issue_tool` 已导出）
- Produces:
  - `_run_dispatch_with_continuation(..., wait_for_input=None, mark_waiting=None, clear_waiting=None, run_reply=None)` 四个新可选注入参数；`None` 时行为与现状**完全一致**（needs_input 直接终结）——这保证 gate 未接线/故障时零回归
  - `run_issue_reply_for_wait_step(issue_id, user_id, reply_text, attachments) -> dict`（`@DBOS.step`，返回 `{"outcome": ..., "reason": ...}`）
  - 返回 dict 增加 `"wait_rounds": int`

- [ ] **Step 1: 读现状**

Read `backend/app/workflows/issue_lifecycle.py:150-260`（`run_issue_reply` 与 `_run_reply_turns`）与 `:350-438`（循环本体）。确认 `run_issue_reply` 内部调用 `run_session_turn` 的 result 变量名——Step 3 的 outcome 提取要挂在它上面。

- [ ] **Step 2: 写失败测试（追加到 test_issue_continuation.py）**

沿用该文件既有的 fake 构造风格（先读文件头的现有 fixture / helper，保持一致），追加：

```python
# ---------- needs_input 等待循环（spec 2026-07-30） ----------


def _mk_wait_deps(payloads):
    """payloads: 依次弹出的回复 payload（None=超时）。返回 (deps kwargs, calls 记录)。"""
    calls = {"mark": 0, "clear": 0, "replies": []}
    seq = list(payloads)

    async def wait_for_input(issue_id, ttl_seconds):
        return seq.pop(0) if seq else None

    async def mark_waiting(issue_id, prompt):
        calls["mark"] += 1

    async def clear_waiting(issue_id):
        calls["clear"] += 1

    async def run_reply(issue_id, payload):
        calls["replies"].append(payload["reply_text"])
        return {"outcome": "completed", "reason": "done after reply"}

    return (
        dict(
            wait_for_input=wait_for_input,
            mark_waiting=mark_waiting,
            clear_waiting=clear_waiting,
            run_reply=run_reply,
        ),
        calls,
    )


async def test_needs_input_waits_then_reply_continues_to_completed():
    """needs_input → 挂起 → 收到回复 → 回复回合 completed → in_review。"""
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append((status, kw.get("agent_outcome")))

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"outcome": "needs_input", "reason": "which ending?"}

    deps, calls = _mk_wait_deps([{"reply_text": "摊牌", "user_id": "u1", "attachments": None}])
    result = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn, set_status=set_status,
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        **deps,
    )
    assert result["outcome"] == "completed"
    assert result["wait_rounds"] == 1
    assert calls == {"mark": 1, "clear": 1, "replies": ["摊牌"]}
    # 状态序列：等待时 needs_followup → 回复后 in_progress → 终态 in_review
    assert statuses[0] == ("needs_followup", "needs_input")
    assert statuses[1][0] == "in_progress"
    assert statuses[-1][0] == "in_review"


async def test_needs_input_timeout_terminates_like_today():
    """超时（wait 返 None）→ 清标记 → 停在 needs_followup，与现状终态一致。"""
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append(status)

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"outcome": "needs_input", "reason": "which ending?"}

    deps, calls = _mk_wait_deps([None])
    result = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn, set_status=set_status,
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        **deps,
    )
    assert result["outcome"] == "needs_input"
    assert calls["clear"] == 1
    assert statuses[-1] == "needs_followup"          # 没有回到 in_progress


async def test_needs_input_wait_rounds_capped():
    """agent 连环问人 → 第 NEEDS_INPUT_MAX_WAIT_ROUNDS 轮后强制终结。"""

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"outcome": "needs_input", "reason": "again?"}

    async def run_reply(issue_id, payload):
        return {"outcome": "needs_input", "reason": "and again?"}

    deps, calls = _mk_wait_deps(
        [{"reply_text": f"r{i}", "user_id": "u", "attachments": None} for i in range(10)]
    )
    deps["run_reply"] = run_reply
    result = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn, set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        **deps,
    )
    assert result["wait_rounds"] == 5                 # settings 默认
    assert result["outcome"] == "needs_input"         # 超限按现状终结


async def test_needs_input_without_gate_behaves_as_today():
    """不注入 gate（生产未接线/故障降级）→ 现状行为：直接 needs_followup 终结。"""
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append(status)

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"outcome": "needs_input", "reason": "?"}

    result = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn, set_status=set_status,
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
    )
    assert result["outcome"] == "needs_input"
    assert statuses == ["needs_followup"]


async def test_wait_wakeup_preempted_by_external_close():
    """挂起期间 issue 被人工关闭 → 唤醒后 PREEMPT 复查让路，不再跑回复回合。"""
    load_seq = [{"status": "in_progress"}, {"status": "cancelled"}]

    async def load_issue(_):
        return load_seq.pop(0) if load_seq else {"status": "cancelled"}

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"outcome": "needs_input", "reason": "?"}

    deps, calls = _mk_wait_deps([{"reply_text": "r", "user_id": "u", "attachments": None}])
    result = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn, set_status=AsyncMock(),
        load_issue=load_issue, **deps,
    )
    assert result.get("preempted") is True
    assert calls["replies"] == []                     # 回复回合没有跑
```

（`AsyncMock` 若文件尚未导入则补 `from unittest.mock import AsyncMock`。）

- [ ] **Step 3: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_issue_continuation.py -v -k needs_input
```
Expected: FAIL（新参数不存在 / 行为不符）

- [ ] **Step 4: 改造循环**

`_run_dispatch_with_continuation` 签名追加（全部默认 None）：

```python
    wait_for_input: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
    mark_waiting: Optional[Callable[..., Awaitable[None]]] = None,
    clear_waiting: Optional[Callable[..., Awaitable[None]]] = None,
    run_reply: Optional[Callable[..., Awaitable[dict]]] = None,
```

循环体改造（保留现有 preempt 检查与 continue 计数逻辑，把"终态路由"从循环后移进循环内的判定；伪结构如下，落地时以现文件变量名为准）：

```python
    attempt = 0
    wait_rounds = 0
    outcome: Optional[str] = None
    reason: Optional[str] = None
    gate_ready = all([wait_for_input, mark_waiting, clear_waiting, run_reply])
    from app.core.config import settings

    while True:
        fresh = await load_issue(issue_id)
        fresh_status = (fresh or {}).get("status")
        if fresh_status in PREEMPT_STATUSES:
            # （原样保留现有日志与返回，追加 wait_rounds 字段）
            return {..., "wait_rounds": wait_rounds}

        if outcome == "needs_input" and gate_ready:
            # 上一轮声明了 needs_input：挂起等回复
            await set_status(issue_id, "needs_followup",
                             agent_outcome="needs_input", outcome_reason=reason)
            await mark_waiting(issue_id, reason or "")
            payload = await wait_for_input(
                issue_id, ttl_seconds=settings.NEEDS_INPUT_RECV_TTL_HOURS * 3600
            )
            await clear_waiting(issue_id)
            if payload is None:
                # 超时/畸形：停在 needs_followup，与现状终态一致（不再 set_status）
                return {"outcome": "needs_input", "attempts": attempt,
                        "wait_rounds": wait_rounds}
            wait_rounds += 1
            await set_status(issue_id, "in_progress", agent_outcome=None,
                             outcome_reason=None)
            continue_res = await run_reply(issue_id, payload)
            outcome = (continue_res or {}).get("outcome")
            reason = (continue_res or {}).get("reason")
        else:
            res = await run_turn(issue_row, agent_id, user_id,
                                 is_continuation=(attempt > 0 or wait_rounds > 0))
            outcome = (res or {}).get("outcome")
            reason = (res or {}).get("reason")

        if outcome == "continue" and attempt < max_continuations:
            attempt += 1
            continue
        if (outcome == "needs_input" and gate_ready
                and wait_rounds < settings.NEEDS_INPUT_MAX_WAIT_ROUNDS):
            continue        # 回到循环顶：preempt 复查 → 挂起
        break

    # 现有终态路由原样保留（needs_input/completed/continue/None 四支），
    # 每个 return 补 "wait_rounds": wait_rounds
```

注意两点：① `set_status(issue_id, "in_progress", ...)` 若现有 `set_status` 注入实现不接受 `agent_outcome=None`，按其真实签名调整（读 `execute_issue` 中传入的实现）；② needs_input 且 gate_ready 但 `wait_rounds >= 上限` 时落入 break → 走现有 needs_input 终态路由（等价"超限按现状终结"）。

- [ ] **Step 5: 新增回复回合 step**

在 `issue_lifecycle.py`（`run_issue_reply` 附近）新增：

```python
@DBOS.step()
async def run_issue_reply_for_wait_step(
    issue_id: int, user_id: str, reply_text: str,
    attachments: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """等待循环的回复回合：跑一轮 issue reply 并带回 FinishIssue 声明。

    与 respond_to_issue_reply 的差异：本 step 在原 dispatch workflow 体内被
    调用（挂起唤醒后原地继续），需要 outcome 继续状态机路由；同样串行于
    per-issue turn lock。无 DBOS retry：run_session_turn 非幂等（同
    run_issue_agent_step 的理由）。"""
    from app.services.ai.tools.finish_issue_tool import extract_issue_outcome

    session_id = await ensure_issue_session_step(issue_id)
    # 复用 _run_reply_turns 的锁语义跑一轮；run_turn 换成带 result 回传的闭包
    captured: dict[str, Any] = {}

    async def _turn(**kw):
        # 与 run_issue_reply 相同的调用，但保留 run_session_turn 的完整 result
        # （读 run_issue_reply 现实现，把 result 存入 captured["result"]）
        ...

    await _run_reply_turns(
        issue_id, user_id, reply_text,
        session_id=session_id,
        acquire=acquire_turn_lock, run_turn=_turn,
        release=clear_lock, sleep=DBOS.sleep_async,
        attachments=attachments,
    )
    result = captured.get("result") or {}
    outcome, reason = extract_issue_outcome(result.get("tool_calls"))
    return {"outcome": outcome, "reason": reason}
```

实现要求：`_turn` 体从 `run_issue_reply`（Read `issue_lifecycle.py:174-206`）**原样搬运**其 run_session_turn 调用与 publish_message 逻辑，仅追加 `captured["result"] = result`。`_run_reply_turns` 的 run_turn 参数签名（关键字）以现文件为准对齐。

- [ ] **Step 6: 跑全部 continuation 测试**

```bash
cd backend && uv run pytest tests/test_issue_continuation.py -v
```
Expected: 既有用例 + 5 个新用例全部通过。

- [ ] **Step 7: Commit**

```bash
git add backend/app/workflows/issue_lifecycle.py backend/tests/test_issue_continuation.py
git commit -m "feat(issues): dispatch 循环注入 needs_input 挂起-唤醒（默认关闭,注入即启用）"
```

---

### Task 5: 生产接线 —— execute_issue 传入真实 gate

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`（`execute_issue` 调用 `_run_dispatch_with_continuation` 处）

**Interfaces:**
- Consumes: Task 2 gate + Task 4 新参数与 `run_issue_reply_for_wait_step`

- [ ] **Step 1: 找到调用点**

```bash
cd backend && grep -n "_run_dispatch_with_continuation(" app/workflows/issue_lifecycle.py
```

- [ ] **Step 2: 接线**

在 `execute_issue` 的调用处（workflow 体内）补四个实参。`workflow_id` 用 `DBOS.workflow_id`；`user_id` 沿用该作用域内既有变量：

```python
        from app.agent_framework import input_gate

        wf_id = DBOS.workflow_id

        async def _wait(issue_id_: int, ttl_seconds: int):
            return await input_gate.await_user_input(issue_id_, ttl_seconds=ttl_seconds)

        async def _mark(issue_id_: int, prompt: str):
            await input_gate.mark_awaiting_input(
                workflow_id=wf_id, issue_id=issue_id_, user_id=user_id, prompt=prompt
            )

        async def _clear(issue_id_: int):
            await input_gate.clear_awaiting_input(workflow_id=wf_id)

        async def _reply(issue_id_: int, payload: dict):
            return await run_issue_reply_for_wait_step(
                issue_id_, payload.get("user_id") or user_id,
                payload["reply_text"], payload.get("attachments"),
            )

        result = await _run_dispatch_with_continuation(
            ...,  # 既有实参不动
            wait_for_input=_wait, mark_waiting=_mark,
            clear_waiting=_clear, run_reply=_reply,
        )
```

注意：`DBOS.workflow_id` 的取法以 approval_gate 调用方 / dbos 版本实际 API 为准（可能是 `DBOS.workflow_id` 属性或 `ctx` 取值——grep `workflow_id` 在 workflows/ 下的现有用法照抄）。

- [ ] **Step 3: 全量后端回归**

```bash
cd backend && uv run pytest -q 2>&1 | tail -5
```
Expected: 无新增 FAILED。

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/issue_lifecycle.py
git commit -m "feat(issues): execute_issue 接线 needs_input gate（挂起-唤醒启用）"
```

---

### Task 6: 回复分流 —— 唤醒 or 旧路径

**Files:**
- Modify: `backend/app/api/issue_messages_router.py`（回复入口，调 `_dispatch_respond_to_issue_reply` 之前）
- Create: `backend/tests/test_issue_reply_wake_routing.py`

**Interfaces:**
- Consumes: `input_gate.signal_user_reply`；issue 行的 `dbos_workflow_id`；task_tracking 的 awaiting 标记
- Produces: `_try_wake_waiting_workflow(issue_row: dict, user_id: str, reply_text: str, attachments) -> bool`（True=已唤醒无需 dispatch）

- [ ] **Step 1: 写失败测试**

```python
"""回复分流：有等待标记 → send 唤醒；无标记/唤醒失败/workflow 已终态 → 旧路径。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.api import issue_messages_router as r


def _issue(wf="wf-1"):
    return {"id": 5, "dbos_workflow_id": wf}


@pytest.mark.asyncio
async def test_no_marker_returns_false():
    with patch.object(r, "_load_awaiting_marker", new=AsyncMock(return_value=None)):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is False


@pytest.mark.asyncio
async def test_marker_and_live_workflow_wakes():
    with (
        patch.object(r, "_load_awaiting_marker", new=AsyncMock(return_value={"issue_id": 5})),
        patch.object(r, "_workflow_is_terminal", new=AsyncMock(return_value=False)),
        patch.object(r.input_gate, "signal_user_reply", new=AsyncMock(return_value=True)) as sig,
    ):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is True
    sig.assert_awaited_once()


@pytest.mark.asyncio
async def test_marker_but_terminal_workflow_falls_back():
    """send 与超时竞态：workflow 已终态 → 旧路径（返 False）。"""
    with (
        patch.object(r, "_load_awaiting_marker", new=AsyncMock(return_value={"issue_id": 5})),
        patch.object(r, "_workflow_is_terminal", new=AsyncMock(return_value=True)),
        patch.object(r.input_gate, "signal_user_reply", new=AsyncMock(return_value=True)),
    ):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is False


@pytest.mark.asyncio
async def test_send_failure_falls_back():
    with (
        patch.object(r, "_load_awaiting_marker", new=AsyncMock(return_value={"issue_id": 5})),
        patch.object(r, "_workflow_is_terminal", new=AsyncMock(return_value=False)),
        patch.object(r.input_gate, "signal_user_reply", new=AsyncMock(return_value=False)),
    ):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is False


@pytest.mark.asyncio
async def test_missing_workflow_id_falls_back():
    assert await r._try_wake_waiting_workflow({"id": 5, "dbos_workflow_id": None}, "u", "hi", None) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_issue_reply_wake_routing.py -v
```
Expected: AttributeError（函数不存在）

- [ ] **Step 3: 实现分流**

`issue_messages_router.py` 顶部 `from app.agent_framework import input_gate`，新增：

```python
async def _load_awaiting_marker(workflow_id: str) -> Optional[dict]:
    """按 dbos_workflow_id 取 task_tracking.metadata.awaiting_input；无则 None。"""
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT metadata->'awaiting_input' AS marker FROM public.task_tracking "
        "WHERE dbos_workflow_id = :wf",
        {"wf": workflow_id},
    )
    marker = (row or {}).get("marker")
    if isinstance(marker, str):
        import json
        try:
            marker = json.loads(marker)
        except ValueError:
            return None
    return marker if isinstance(marker, dict) else None


async def _workflow_is_terminal(workflow_id: str) -> bool:
    """DBOS 视角 workflow 是否已终态（SUCCESS/ERROR/CANCELLED…）。
    查询失败按'终态'处理 —— 宁可走旧路径也不投递到虚空。"""
    from app.db import engine as db_engine

    try:
        row = await db_engine.fetch_one(
            "SELECT status FROM dbos.workflow_status WHERE workflow_uuid = :wf",
            {"wf": workflow_id},
        )
        return (row or {}).get("status") not in ("PENDING", "ENQUEUED")
    except Exception:  # noqa: BLE001
        return True


async def _try_wake_waiting_workflow(
    issue_row: dict, user_id: str, reply_text: str, attachments: Optional[list]
) -> bool:
    """回复分流：True=已投递给挂起的 workflow；False=调用方走旧路径 dispatch。
    消息本体在调用方先落库，本函数任何失败形态都不丢消息。"""
    wf_id = issue_row.get("dbos_workflow_id")
    if not wf_id:
        return False
    marker = await _load_awaiting_marker(wf_id)
    if not marker:
        return False
    if await _workflow_is_terminal(wf_id):
        return False
    return await input_gate.signal_user_reply(
        workflow_id=wf_id, issue_id=int(issue_row["id"]),
        reply_text=reply_text, user_id=user_id, attachments=attachments,
    )
```

调用点：在回复端点里 `_dispatch_respond_to_issue_reply(...)` 之前（消息已落库之后）插入：

```python
    if await _try_wake_waiting_workflow(issue_row, user_id, body, attachments):
        logger.info(f"[issue_reply] issue {issue_id}: delivered to waiting workflow")
    else:
        _dispatch_respond_to_issue_reply(...)   # 既有调用原样
```

实现要求：回复端点的变量名（issue_row/body/attachments/user_id）以现文件为准；若该端点当前没查 issue 行，则补一次最小 SELECT（id, dbos_workflow_id）。`dbos.workflow_status` 的 PENDING 枚举拼写以库内实际值为准（`SELECT DISTINCT status FROM dbos.workflow_status LIMIT 10` 核对）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest tests/test_issue_reply_wake_routing.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/issue_messages_router.py backend/tests/test_issue_reply_wake_routing.py
git commit -m "feat(issues): 回复分流 —— 优先唤醒挂起 workflow,失败走旧路径"
```

---

### Task 7: 启动 reaper —— 清跨版本假活挂起

**Files:**
- Modify: `backend/app/main.py`（仿 `_bg_reap_internal_queue` 增加一个后台任务）
- Create: `backend/tests/test_stale_input_wait_reaper.py`

**Interfaces:**
- Consumes: `_resolve_pinned_app_version`（`app.services.infra.dbos_orchestrator`）
- Produces: `reap_stale_input_waits(current_version: str) -> int`（返回清理条数；放 `input_gate.py` 便于测试，main.py 只做调度）

- [ ] **Step 1: 写失败测试**

```python
"""跨版本假活挂起 reaper：old-version 挂起被清标记+取消,当前版本不动。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework import input_gate


@pytest.mark.asyncio
async def test_reaper_clears_stale_and_cancels():
    rows = [
        {"dbos_workflow_id": "wf-old", "application_version": "v1"},
    ]
    with (
        patch.object(input_gate, "_fetch_awaiting_rows", new=AsyncMock(return_value=rows)),
        patch.object(input_gate, "clear_awaiting_input", new=AsyncMock()) as clear,
        patch.object(input_gate, "_cancel_workflow", new=AsyncMock()) as cancel,
    ):
        n = await input_gate.reap_stale_input_waits(current_version="v2")
    assert n == 1
    clear.assert_awaited_once_with(workflow_id="wf-old")
    cancel.assert_awaited_once_with("wf-old")


@pytest.mark.asyncio
async def test_reaper_skips_current_version():
    rows = [{"dbos_workflow_id": "wf-new", "application_version": "v2"}]
    with (
        patch.object(input_gate, "_fetch_awaiting_rows", new=AsyncMock(return_value=rows)),
        patch.object(input_gate, "clear_awaiting_input", new=AsyncMock()) as clear,
        patch.object(input_gate, "_cancel_workflow", new=AsyncMock()) as cancel,
    ):
        n = await input_gate.reap_stale_input_waits(current_version="v2")
    assert n == 0
    clear.assert_not_awaited()
    cancel.assert_not_awaited()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_stale_input_wait_reaper.py -v
```

- [ ] **Step 3: 在 input_gate.py 实现**

```python
async def _fetch_awaiting_rows() -> list[dict]:
    """所有带 awaiting_input 标记的 task 行 + 其 DBOS app_version。"""
    from app.db import engine as db_engine

    return await db_engine.fetch_all(
        """SELECT t.dbos_workflow_id, w.application_version
           FROM public.task_tracking t
           JOIN dbos.workflow_status w ON w.workflow_uuid = t.dbos_workflow_id
           WHERE t.metadata ? 'awaiting_input'
             AND w.status IN ('PENDING', 'ENQUEUED')""",
    )


async def _cancel_workflow(workflow_id: str) -> None:
    from dbos import DBOS

    await DBOS.cancel_workflow_async(workflow_id)


async def reap_stale_input_waits(*, current_version: str) -> int:
    """部署换版本后,旧版本挂起的 workflow 无 worker 认领 —— 假活。
    清标记 + cancel,使回复自动走旧路径;issue 停在 needs_followup 用户无感。"""
    cleaned = 0
    for row in await _fetch_awaiting_rows():
        if row.get("application_version") == current_version:
            continue
        wf = row["dbos_workflow_id"]
        try:
            await clear_awaiting_input(workflow_id=wf)
            await _cancel_workflow(wf)
            cleaned += 1
            logger.info(f"[input_gate] reaped stale wait wf={wf}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[input_gate] reap failed wf={wf}: {exc}")
    return cleaned
```

实现要求：`DBOS.cancel_workflow_async` 的确切名字以所装 dbos 包为准（`grep -n "def cancel" .venv/lib/python*/site-packages/dbos/_dbos.py`），无 async 变体则用同步版包 `asyncio.to_thread`。`fetch_all` 以 `app/db/engine.py` 实际导出为准。

- [ ] **Step 4: main.py 调度**

仿 `_bg_reap_internal_queue`（Read `backend/app/main.py` 中它的定义与注册处）新增 `_bg_reap_stale_input_waits`：启动后延迟约 30s 执行一次（与 internal-queue reaper 同款模式），取 `_resolve_pinned_app_version()` 为 current_version；version 为空则跳过并记日志。在同一处注册后台任务。

- [ ] **Step 5: 跑测试 + 回归**

```bash
cd backend && uv run pytest tests/test_stale_input_wait_reaper.py tests/test_input_gate.py -v
```
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_framework/input_gate.py backend/app/main.py backend/tests/test_stale_input_wait_reaper.py
git commit -m "feat(issues): 启动 reaper 清跨版本假活挂起 —— 回复自动降级旧路径"
```

---

### Task 8: 前端 —— Task Center 高亮 + inbox agent_question

**Files:**
- Modify: `frontend/components/TaskCenter/taskRowPresentation.ts`、`taskRowPresentation.test.ts`
- Modify: `frontend/components/notifications/notificationLink.ts`、`notificationLink.test.ts`、`InboxPanel.tsx`
- Modify: `frontend/public/locales/en.json`、`frontend/public/locales/zh.json`

**Interfaces:**
- Consumes: task_tracking realtime 行的 `metadata.awaiting_input`（`{prompt, since, issue_id}`）；inbox 行 `kind='agent_question'`、`link_kind='issue'`
- Produces: 等待行的展示态（橙色 Waiting for your input + 提问副标题 + 点击深链 issue）

- [ ] **Step 1: 读现状**

Read `taskRowPresentation.ts` 全文与其测试，确认展示态结构（label/color/subtitle 的返回 shape）与 metadata 的可达性；Read `notificationLink.ts` 确认 kind→route 解析模式。

- [ ] **Step 2: 写失败测试（沿用两文件现有测试风格）**

taskRowPresentation 追加：`metadata.awaiting_input` 存在 → 展示 label 为 `Waiting for your input`、副标题为 prompt、语义色为 warn（K1 赭）；不存在 → 原行为不变（快照既有用例守护）。
notificationLink 追加：`kind='agent_question', link_kind='issue', link_id='123'` → 解析到 issue 详情路由（与既有 `issue` link_kind 同路由）。

- [ ] **Step 3: 实现**

- taskRowPresentation：在展示态推导入口处优先判断 `row.metadata?.awaiting_input`，返回 `{label: t('taskCenter.waitingForInput','Waiting for your input'), tone:'warn', subtitle: marker.prompt, link: issueRoute(marker.issue_id)}`——具体字段名对齐 Step 1 读到的 shape。
- notificationLink：`agent_question` 并入 `issue` 的路由分支。
- InboxPanel：kind 图标/文案映射表加一行（icon 用 MessagesSquare 或现有提问类图标；文案 key `inbox.agentQuestion` = `Agent needs your input`）。
- 状态条计数：Read `taskCenterSummary.ts`（含测试），在汇总里加 waiting 计数（`metadata.awaiting_input` 行数），展示样式与既有计数 chip 一致（warn 色）。
- i18n：en/zh 补 `taskCenter.waitingForInput`、`inbox.agentQuestion`（zh：`等你回复`、`Agent 提问`）。

- [ ] **Step 4: 测试 + typecheck**

```bash
cd frontend && npx vitest run components/TaskCenter components/notifications && npx tsc --noEmit 2>&1 | grep -E "TaskCenter|notification" | head -5
```
Expected: 测试全过，无本次文件的 TS 错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TaskCenter frontend/components/notifications frontend/public/locales
git commit -m "feat(fe): 等你回复高亮(Task Center) + inbox agent_question kind"
```

---

### Task 9: E2E 验证 + PR

**Files:** 无新文件（验证 + 提交）

- [ ] **Step 1: 部署前全量回归**

```bash
cd backend && uv run pytest -q 2>&1 | tail -5
cd ../frontend && npx vitest run 2>&1 | tail -5
```
Expected: 无新增失败（前端既有失败基线以 master 为准对照）。

- [ ] **Step 2: 开 PR（合并后自动部署，migration 链独立触发）**

```bash
git push -u origin feature/needs-input-first-class-impl
gh pr create --base master --title "feat(issues): needs_input 一等状态 —— DBOS recv 挂起 + 等你回复醒目化" --body "spec: docs/superpowers/specs/2026-07-30-needs-input-first-class-design.md
实施: 挂起-唤醒(gate)/回复分流/reaper/TaskCenter+inbox 呈现;TTL 72h,等待轮上限 5;
所有失败形态降级现状旧路径。4️⃣ 由并行 session 推进,不在本 PR。

https://claude.ai/code/session_01JEBdiHMzaE8dPjupQEY6jF"
```

- [ ] **Step 3: 合并部署后 E2E（Claude 调试账号，凭证见 memory claude-debug-test-account）**

1. 用调试账号建一个 issue，内容明确要求 agent 提问（如"先问我要哪种风格再动笔，不要直接写"），dispatch 给 script_ai。
2. 观察：`task_tracking` 行出现 `metadata.awaiting_input`；`inbox_notifications` 出现 agent_question 行；issue 状态 needs_followup。
3. 通过回复端点发回复 → 确认 `dbos.workflow_status` 里原 workflow 继续（同 workflow_uuid 完成),issue 进入 in_review/done。
4. 负路径：再造一个挂起，直接 `DBOS cancel`（模拟版本更替后 reaper）→ 回复走旧路径 `respond_to_issue_reply`,行为与现状一致。
5. 清理测试 issue。

- [ ] **Step 4: 观察一周期**

发版后跑一遍 CLAUDE.md 的 application_logs 错误漏斗，确认无 `input_gate` 相关 ERROR 风暴。
