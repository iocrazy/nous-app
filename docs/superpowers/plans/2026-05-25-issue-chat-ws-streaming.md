# Issue Chat Realtime + Token Streaming over WebSocket — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Replace #349's polling with a backend WebSocket that pushes issue-chat events — including **token-by-token streaming** of the agent's reply — using the proven `ws_router` + Redis pub/sub + JWT-ticket pattern.

**Architecture:** The agent turn runs on the **worker** (gateway/worker split). The worker publishes events to Redis channel `issue:{issue_id}` (chunk deltas during generation, a final mapped IssueMessage, and status). The **gateway** hosts a WS endpoint `/ws/issue/{id}` that authenticates + checks issue visibility, subscribes to that Redis channel, and forwards events to the browser. Redis bridges worker→gateway exactly like `/ws/task-progress`. The frontend renders a live streaming assistant bubble and finalizes it on the message event.

**Tech Stack:** FastAPI WebSocket, Redis pub/sub (`app/core/redis`), DBOS workflow steps, `AILibraryChatService.run_session_turn(chunk_callback=…)` (already streams via `runner.stream_turn`), React.

**Branch:** `feature/issue-chat-ux` (already has: input-width fix `4d0d27d7`, polling+status-events `3453a990`). This plan **removes the polling** from `3453a990` and **keeps** the input-width fix + the `SystemStatusEvent` rendering.

**Spec / review:** decided in chat 2026-05-25 (option 2 + streaming) after `/plan-eng-review` confirmed all building blocks exist.

---

## Key facts (verified)

- `ws_router.py` (`/ws/task-progress`): auth via `?ticket=` (`consume_ticket` from `ws_ticket_router`) or legacy `?token=` (`_authenticate_ws`→`verify_jwt`); then `redis.pubsub().subscribe(channel)` → `async for message in pubsub.listen(): websocket.send_json(json.loads(data))`. Registered in `main.py:253` (`app.include_router(ws_router)`).
- `download_progress.py`: worker publishes via `redis.publish(channel, json.dumps(payload))`.
- `AILibraryChatService.run_session_turn(session_id, *, user_id, content, trigger, chunk_callback=…)` — when `chunk_callback` is set, drives `runner.stream_turn` and `await chunk_callback(delta_text)` per token (ai_library_chat_service.py:662-706). Returns `{assistant_message:{id,content,agent_id,…}, run_id, …}`.
- Issue turn entry points (both currently call `run_session_turn` WITHOUT a chunk_callback):
  - `issue_lifecycle.py:121 run_issue_reply_step(*, session_id, user_id, reply_text)` (reply)
  - `issue_agent_executor.py:50 run_issue_agent(*, issue, agent_id, user_id)` (dispatch)
- `issue_messages_router.py:69 _map_ai_message_to_issue_message(row, *, issue_id, session_user_id)` — maps a raw `ai_messages` row → `IssueMessage`. Pure function.
- Frontend WS pattern: `services/wsTicketService.ts` (`fetchWsTicket()` → POST `/api/v1/ws/ticket`) + `contexts/TaskManagerContext.tsx:509` (`new WebSocket(`${getWsBaseUrl()}/ws/task-progress?ticket=${ticket}`)`). `getWsBaseUrl()`/`getApiUrl()` helpers exist.
- Issue visibility = `created_by_user_id == user_id OR assignee_user_id == user_id` (`issue_messages_router._assert_issue_visible`).

## File structure

- **Create** `backend/app/services/issues/issue_message_mapper.py` — the moved pure mapper (shared by the GET endpoint + the worker publisher).
- **Create** `backend/app/services/issues/issue_chat_stream.py` — Redis publishers: `publish_chunk` / `publish_message` / `publish_status` for channel `issue:{id}`.
- **Modify** `backend/app/api/issue_messages_router.py` — import the mapper from the new module (drop the local copy).
- **Modify** `backend/app/workflows/issue_lifecycle.py` — `run_issue_reply_step` passes a chunk_callback + publishes final message/status.
- **Modify** `backend/app/services/issues/issue_agent_executor.py` — `run_issue_agent` passes a chunk_callback + publishes final message/status.
- **Modify** `backend/app/api/ws_router.py` — add `/ws/issue/{issue_id}` (auth + visibility + Redis forward).
- **Create** `frontend/services/issueChatSocket.ts` — ticket + WS open + event parse.
- **Modify** `frontend/components/Todolist/IssueDetailView.tsx` — replace polling with the WS client; streaming bubble state.
- **Modify** `frontend/components/Todolist/IssueChatThread.tsx` — render the live streaming bubble.
- Tests: `backend/tests/test_issue_chat_stream.py`, `backend/tests/test_issue_ws_auth.py`, plus mapper-move regression in existing `test_issue_messages_endpoint.py`.

---

## Task 1: Extract the IssueMessage mapper to a shared module

**Files:**
- Create: `backend/app/services/issues/issue_message_mapper.py`
- Modify: `backend/app/api/issue_messages_router.py` (import the mapper, drop the local def)
- Test: `backend/tests/test_issue_message_mapper.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_issue_message_mapper.py`:

```python
from uuid import UUID

from app.schemas.issue_message import IssueMessageKind
from app.services.issues.issue_message_mapper import map_ai_message_to_issue_message


def test_assistant_row_maps_to_agent_run():
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "assistant",
        "content": "hello",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "metadata_json": {"run_id": "33333333-3333-3333-3333-333333333333"},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.kind == IssueMessageKind.AGENT_RUN
    assert m.body == "hello"
    assert m.author_agent_id == UUID("22222222-2222-2222-2222-222222222222")
    assert m.agent_run_id == UUID("33333333-3333-3333-3333-333333333333")


def test_user_row_maps_to_comment_with_session_user():
    row = {
        "id": "44444444-4444-4444-4444-444444444444",
        "role": "user",
        "content": "hi",
        "metadata_json": {},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    su = UUID("55555555-5555-5555-5555-555555555555")
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=su)
    assert m.kind == IssueMessageKind.COMMENT
    assert m.author_user_id == su
```

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_issue_message_mapper.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement — move the function**

Create `backend/app/services/issues/issue_message_mapper.py` with the body **copied verbatim** from `issue_messages_router.py:64-123`, renamed to the public `map_ai_message_to_issue_message` (drop the leading underscore):

```python
"""Pure mapper: a raw ai_messages row → the IssueMessage UI shape.
Shared by the messages GET endpoint and the WS chat-stream publisher."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.schemas.issue_message import IssueMessage, IssueMessageKind


def map_ai_message_to_issue_message(
    row: dict[str, Any],
    *,
    issue_id: int,
    session_user_id: Optional[UUID],
) -> IssueMessage:
    # ... exact body from issue_messages_router._map_ai_message_to_issue_message ...
```
(Copy the existing logic exactly — role→kind, metadata_json→from/to_status/run_id, etc.)

In `issue_messages_router.py`: delete the local `_map_ai_message_to_issue_message` and add
`from app.services.issues.issue_message_mapper import map_ai_message_to_issue_message`, then
replace the call site `_map_ai_message_to_issue_message(` → `map_ai_message_to_issue_message(`.

- [ ] **Step 4: Run → pass**

`cd backend && uv run pytest tests/test_issue_message_mapper.py tests/test_issue_messages_endpoint.py -v` → all pass (the endpoint regression confirms the move didn't break the GET path).

- [ ] **Step 5: lint + commit**

```bash
cd backend && uv run black app/services/issues/issue_message_mapper.py app/api/issue_messages_router.py tests/test_issue_message_mapper.py && uv run isort app/services/issues/issue_message_mapper.py app/api/issue_messages_router.py && uv run flake8 app/services/issues/issue_message_mapper.py app/api/issue_messages_router.py
git add -A && git commit -m "refactor(issues): extract IssueMessage mapper to shared module"
```

---

## Task 2: Redis publishers for issue chat events

**Files:**
- Create: `backend/app/services/issues/issue_chat_stream.py`
- Test: `backend/tests/test_issue_chat_stream.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_issue_chat_stream.py`:

```python
import json
from unittest.mock import AsyncMock

import pytest

from app.services.issues import issue_chat_stream as s


@pytest.mark.asyncio
async def test_publish_chunk(monkeypatch):
    r = AsyncMock()
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    await s.publish_chunk(7, "hel")
    r.publish.assert_awaited_once()
    chan, raw = r.publish.call_args.args
    assert chan == "issue:7"
    p = json.loads(raw)
    assert p["type"] == "chunk" and p["delta"] == "hel"


@pytest.mark.asyncio
async def test_publish_status(monkeypatch):
    r = AsyncMock()
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    await s.publish_status(7, "running")
    p = json.loads(r.publish.call_args.args[1])
    assert p["type"] == "status" and p["phase"] == "running"


@pytest.mark.asyncio
async def test_publish_message_maps_shape(monkeypatch):
    r = AsyncMock()
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "assistant", "content": "done",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "metadata_json": {}, "created_at": "2026-05-25T00:00:00+00:00",
    }
    await s.publish_message(7, row, session_user_id=None)
    p = json.loads(r.publish.call_args.args[1])
    assert p["type"] == "message"
    assert p["message"]["kind"] == "agent_run"
    assert p["message"]["body"] == "done"


@pytest.mark.asyncio
async def test_publish_never_raises(monkeypatch):
    # publishing is best-effort: a redis failure must not break the turn
    r = AsyncMock()
    r.publish = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    await s.publish_chunk(7, "x")  # must not raise
```

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_issue_chat_stream.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

Create `backend/app/services/issues/issue_chat_stream.py`:

```python
"""Publish issue-chat events to Redis `issue:{id}` for the WS forwarder.
Best-effort: a publish failure must never break the agent turn."""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from loguru import logger


async def _get_redis():
    from app.core.redis import get_async_redis

    return await get_async_redis()


def _channel(issue_id: int) -> str:
    return f"issue:{issue_id}"


async def _publish(issue_id: int, payload: dict[str, Any]) -> None:
    try:
        r = await _get_redis()
        await r.publish(_channel(issue_id), json.dumps(payload, default=str))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] publish failed (issue={issue_id}): {e}")


async def publish_chunk(issue_id: int, delta: str) -> None:
    await _publish(issue_id, {"type": "chunk", "delta": delta})


async def publish_status(issue_id: int, phase: str) -> None:
    await _publish(issue_id, {"type": "status", "phase": phase})


async def publish_message(
    issue_id: int, ai_message_row: dict[str, Any], *, session_user_id: Optional[UUID]
) -> None:
    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    msg = map_ai_message_to_issue_message(
        ai_message_row, issue_id=issue_id, session_user_id=session_user_id
    )
    await _publish(issue_id, {"type": "message", "message": msg.model_dump(mode="json")})
```

- [ ] **Step 4: Run → pass**

`cd backend && uv run pytest tests/test_issue_chat_stream.py -v` → 4 passed.

- [ ] **Step 5: lint + commit**

```bash
cd backend && uv run black app/services/issues/issue_chat_stream.py tests/test_issue_chat_stream.py && uv run isort app/services/issues/issue_chat_stream.py && uv run flake8 app/services/issues/issue_chat_stream.py
git add -A && git commit -m "feat(issues): redis publishers for issue chat stream events"
```

---

## Task 3: Wire streaming into the issue turn (reply + dispatch)

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py` (`run_issue_reply_step`)
- Modify: `backend/app/services/issues/issue_agent_executor.py` (`run_issue_agent`)
- Test: `backend/tests/test_issue_reply_workflow.py` (extend), `backend/tests/test_issue_agent_executor.py` (extend)

- [ ] **Step 1: Write the failing test (reply step streams + publishes)**

Add to `backend/tests/test_issue_reply_workflow.py`:

```python
@pytest.mark.asyncio
async def test_run_issue_reply_step_passes_chunk_callback_and_publishes(monkeypatch):
    from app.workflows import issue_lifecycle as m

    captured = {}

    async def fake_run_session_turn(session_id, *, user_id, content, trigger,
                                    chunk_callback=None, **kw):
        captured["has_cb"] = chunk_callback is not None
        if chunk_callback:
            await chunk_callback("hel")
            await chunk_callback("lo")
        return {"assistant_message": {"id": "m1", "role": "assistant",
                                      "content": "hello", "agent_id": "a1",
                                      "metadata_json": {},
                                      "created_at": "2026-05-25T00:00:00+00:00"}}

    fake_chat = type("C", (), {"run_session_turn": staticmethod(fake_run_session_turn)})()
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)

    chunks, messages = [], []
    monkeypatch.setattr(m, "publish_chunk",
                        AsyncMock(side_effect=lambda iid, d: chunks.append(d)))
    monkeypatch.setattr(m, "publish_message",
                        AsyncMock(side_effect=lambda iid, row, **k: messages.append(row)))

    out = await m.run_issue_reply_step.__wrapped__(
        issue_id=7,
        session_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        reply_text="hi",
    )
    assert out == "hello"
    assert captured["has_cb"] is True
    assert chunks == ["hel", "lo"]
    assert messages and messages[0]["content"] == "hello"
```

> Note: `run_issue_reply_step` gains an `issue_id` param (needed for the channel). Update its caller in `respond_to_issue_reply` / `_run_reply_turns` to pass `issue_id`.

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_issue_reply_workflow.py -k chunk_callback -v` → FAIL.

- [ ] **Step 3: Implement — reply step**

In `issue_lifecycle.py` add imports at module top:
```python
from app.services.issues.issue_chat_stream import (  # noqa: F401
    publish_chunk,
    publish_message,
    publish_status,
)
```
Rewrite `run_issue_reply_step` to take `issue_id`, stream, and publish:
```python
@DBOS.step()
# no step retry: run_session_turn is non-idempotent (appends user msg + charges)
async def run_issue_reply_step(
    *, issue_id: int, session_id: str, user_id: str, reply_text: str
) -> Optional[str]:
    """Run one reply turn, streaming token deltas + the final message to Redis."""
    from uuid import UUID

    async def _cb(delta: str) -> None:
        await publish_chunk(issue_id, delta)

    result = await AILibraryChatService().run_session_turn(
        UUID(session_id),
        user_id=UUID(user_id),
        content=reply_text,
        trigger="issue_reply",
        chunk_callback=_cb,
    )
    assistant = result.get("assistant_message") or {}
    await publish_message(issue_id, assistant, session_user_id=None)
    return assistant.get("content") or ""
```
Update the workflow body: `respond_to_issue_reply` already has `issue_id`; thread it into `_run_reply_turns`'s `run_turn` call so `run_issue_reply_step(issue_id=issue_id, session_id=…, user_id=…, reply_text=…)`. (Adjust `_run_reply_turns`'s `run_turn` invocation to include `issue_id`, and `respond_to_issue_reply` to pass it.) Publish status `running` before the turn and `done` after (in `respond_to_issue_reply`, around the `_run_reply_turns` call: `await publish_status(issue_id, "running")` … `await publish_status(issue_id, "done")` in a finally).

- [ ] **Step 4: dispatch path — `run_issue_agent`**

In `issue_agent_executor.py`, mirror it: build `_cb` that `publish_chunk(int(issue["id"]), delta)`, pass `chunk_callback=_cb` to `run_session_turn`, then `publish_message(int(issue["id"]), assistant, session_user_id=None)`. Wrap with `publish_status(issue_id, "running"/"done")`. Add a test in `test_issue_agent_executor.py` mirroring Step 1 (chunk_callback passed + message published).

- [ ] **Step 5: Run → pass + commit**

```bash
cd backend && uv run pytest tests/test_issue_reply_workflow.py tests/test_issue_agent_executor.py -v
uv run black app/workflows/issue_lifecycle.py app/services/issues/issue_agent_executor.py tests/test_issue_reply_workflow.py tests/test_issue_agent_executor.py
uv run isort app/workflows/issue_lifecycle.py app/services/issues/issue_agent_executor.py
uv run flake8 app/workflows/issue_lifecycle.py app/services/issues/issue_agent_executor.py
git add -A && git commit -m "feat(issues): stream agent turn deltas + final message to redis (reply + dispatch)"
```

---

## Task 4: Gateway WS endpoint `/ws/issue/{id}`

**Files:**
- Modify: `backend/app/api/ws_router.py`
- Test: `backend/tests/test_issue_ws_auth.py`

- [ ] **Step 1: Write the failing test (auth + visibility helper)**

Factor the auth+visibility into a testable async helper `_resolve_issue_ws_user(issue_id, ticket, token) -> Optional[str]` (returns user_id only if authed AND can see the issue). Create `backend/tests/test_issue_ws_auth.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.api import ws_router as w


@pytest.mark.asyncio
async def test_resolve_denies_when_not_visible(monkeypatch):
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value="user-9"))
    monkeypatch.setattr(
        w.issue_repository, "get_by_id",
        AsyncMock(return_value={"created_by_user_id": "other", "assignee_user_id": None}),
    )
    assert await w._resolve_issue_ws_user(5, None, "jwt") is None


@pytest.mark.asyncio
async def test_resolve_allows_creator(monkeypatch):
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value="user-9"))
    monkeypatch.setattr(
        w.issue_repository, "get_by_id",
        AsyncMock(return_value={"created_by_user_id": "user-9", "assignee_user_id": None}),
    )
    assert await w._resolve_issue_ws_user(5, None, "jwt") == "user-9"


@pytest.mark.asyncio
async def test_resolve_denies_bad_auth(monkeypatch):
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value=None))
    assert await w._resolve_issue_ws_user(5, None, "bad") is None
```

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_issue_ws_auth.py -v` → FAIL.

- [ ] **Step 3: Implement**

In `ws_router.py` add `from app.repositories.issue_repository import issue_repository` and:

```python
async def _resolve_issue_ws_user(
    issue_id: int, ticket: str | None, token: str | None
) -> str | None:
    """Authenticate (ticket preferred, token legacy) AND check the user can
    see the issue (creator or assignee). Returns user_id or None."""
    user_id: str | None = None
    if ticket:
        from app.api.ws_ticket_router import consume_ticket

        user_id = await consume_ticket(ticket)
    elif token:
        user_id = await _authenticate_ws(token)
    if not user_id:
        return None
    row = await issue_repository.get_by_id(issue_id)
    if not row:
        return None
    if user_id in (row.get("created_by_user_id"), row.get("assignee_user_id")):
        return user_id
    return None


@router.websocket("/ws/issue/{issue_id}")
async def ws_issue_chat(
    websocket: WebSocket,
    issue_id: int,
    ticket: str | None = Query(None),
    token: str | None = Query(None),
):
    """Stream issue-chat events (chunk / message / status) from Redis
    channel issue:{issue_id} to the browser. Mirrors ws_task_progress."""
    user_id = await _resolve_issue_ws_user(issue_id, ticket, token)
    if not user_id:
        await websocket.close(code=4001, reason="Authentication/visibility failed")
        return
    await websocket.accept()
    redis = await get_async_redis()
    pubsub = redis.pubsub()
    channel = f"issue:{issue_id}"
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                await websocket.send_json(json.loads(data))
            except WebSocketDisconnect:
                break
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[WS issue] send error: {e}")
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[WS issue] connection error: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
```
(No `main.py` change — same router is already `include_router`'d.)

- [ ] **Step 4: Run → pass + commit**

```bash
cd backend && uv run pytest tests/test_issue_ws_auth.py -v
uv run black app/api/ws_router.py tests/test_issue_ws_auth.py && uv run isort app/api/ws_router.py && uv run flake8 app/api/ws_router.py
git add -A && git commit -m "feat(issues): /ws/issue/{id} websocket forwarder (auth + visibility)"
```

---

## Task 5: Frontend — WS client + streaming render, remove polling

**Files:**
- Create: `frontend/services/issueChatSocket.ts`
- Modify: `frontend/components/Todolist/IssueDetailView.tsx` (remove polling, add WS)
- Modify: `frontend/components/Todolist/IssueChatThread.tsx` (live streaming bubble)

- [ ] **Step 1: WS client service**

Create `frontend/services/issueChatSocket.ts` mirroring `wsTicketService` usage:
```ts
import { fetchWsTicket } from './wsTicketService';
import { getWsBaseUrl } from './...'; // same helper TaskManagerContext uses

export type IssueChatEvent =
  | { type: 'chunk'; delta: string }
  | { type: 'message'; message: any }   // IssueMessage shape
  | { type: 'status'; phase: string };

export async function openIssueChatSocket(
  issueId: number,
  onEvent: (e: IssueChatEvent) => void,
): Promise<WebSocket> {
  const ticket = await fetchWsTicket();
  const ws = new WebSocket(`${getWsBaseUrl()}/ws/issue/${issueId}?ticket=${encodeURIComponent(ticket)}`);
  ws.onmessage = (ev) => {
    try { onEvent(JSON.parse(ev.data)); } catch { /* ignore malformed */ }
  };
  return ws;
}
```
(Match the exact `getWsBaseUrl` import path used in `TaskManagerContext.tsx`.)

- [ ] **Step 2: IssueDetailView — replace polling with WS**

Remove the polling block added in `3453a990` (the `setInterval`/`startPolling`/`stopPolling`/refs). Add: on mount (and when `issue.id` changes), `openIssueChatSocket(issue.id, onEvent)`; close on unmount. `onEvent`:
- `chunk` → append `delta` to a `streamingText` state (the live bubble).
- `message` → push the finalized `IssueMessage` into `messages`, clear `streamingText`, and set `isAgentWorking=false`.
- `status` → `phase==='running'` → `isAgentWorking=true` + clear stale `streamingText`; `phase==='done'` → `isAgentWorking=false`.
Keep `refresh()` for the initial load + after `handleReply`/`handleDispatch` (post the user's own message immediately). Keep the input-width fix and `SystemStatusEvent` rendering untouched.

- [ ] **Step 3: IssueChatThread — live streaming bubble**

Add an optional `streamingText?: string` prop. When non-empty, render a transient assistant bubble at the bottom (agent avatar + the accumulating text + a blinking cursor), styled like a normal agent message. Hide when empty.

- [ ] **Step 4: Build**

`cd frontend && npm run build` → ✓ 0 TS errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/services/issueChatSocket.ts frontend/components/Todolist/IssueDetailView.tsx frontend/components/Todolist/IssueChatThread.tsx
git commit -m "feat(todolist): issue chat over websocket with token streaming (replaces polling)"
```

---

## Task 6: Final verification

- [ ] `cd backend && uv run pytest tests/ -q` → all pass.
- [ ] Chain import: `uv run python -c "import app.api.ws_router, app.workflows.issue_lifecycle, app.services.issues.issue_agent_executor, app.services.issues.issue_chat_stream, app.services.issues.issue_message_mapper; print('OK')"`.
- [ ] `cd frontend && npm run build` → ✓.
- [ ] Confirm polling is gone: `grep -n "setInterval\|startPolling" frontend/components/Todolist/IssueDetailView.tsx` → no matches.
- [ ] **Deploy note (PR body):** gateway/worker split — the worker publishes to Redis, the gateway hosts the WS. Both already share Redis (`app/core/redis`). No migration. After deploy: dispatch/reply on an issue → the agent reply should stream in token-by-token live.

## Self-Review checklist
- Streaming: `run_session_turn(chunk_callback)` already drives `runner.stream_turn` ✓ (Task 3). Worker→Redis→gateway-WS→frontend ✓ (Tasks 2/4/5). Mapping shared ✓ (Task 1). Polling removed, input-width + status-events kept ✓ (Task 5).
- Type consistency: `run_issue_reply_step(*, issue_id, session_id, user_id, reply_text)` — caller updated in `respond_to_issue_reply` (Task 3). `publish_chunk/message/status(issue_id, …)` signatures match call sites. `IssueChatEvent` union matches the backend `{type: chunk|message|status}` payloads.
- Known limitations: WS forward loop is integration-tested only at the auth/visibility helper level (the `pubsub.listen` loop is exercised in prod, not unit-tested — standard for WS). No reconnect/backoff on the frontend WS in v1 (note as follow-up); the initial `refresh()` covers a missed-connect.
