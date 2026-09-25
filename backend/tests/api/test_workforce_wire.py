"""Workforce routes: wire parity after they gained response models (P9).

Each route is driven over real HTTP against a stub session that hands back
rows carrying every selected column with its native type
(``tests/api/wire_parity.py``). The same handler is then called directly,
with no response model in the way, and the HTTP body must equal
``jsonable_encoder`` of what it returned — what FastAPI sent before.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any, Callable
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.encoders import jsonable_encoder
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import (
    AgentInbox,
    AgentOutbox,
    AgentRuns,
    AgentStateHistory,
    AgentWorkers,
    AiAgents,
    TaskTracking,
)
from tests.api.wire_parity import sample_row

mod = importlib.import_module("app.api.workforce_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
BASE = "/api/v1/workforce"
AGENT = UUID("00000000-0000-0000-0000-0000000000a1")

AGENT_COLS = ["id", "slug", "name", "icon", "model", "persistent", "paused_reason"]
WORKER_COLS = [
    "agent_id",
    "state",
    "current_task_id",
    "state_changed_at",
    "heartbeat_at",
]
BOARD_RUN_COLS = [
    "id",
    "agent_id",
    "status",
    "trigger",
    "started_at",
    "ended_at",
    "cost_cents",
    "prompt_tokens",
    "completion_tokens",
    "model",
]
HISTORY_COLS = [
    "agent_id",
    "from_state",
    "to_state",
    "trigger",
    "task_id",
    "changed_at",
]
INBOX_COLS = [
    "id",
    "sender_kind",
    "sender_user_id",
    "sender_agent_id",
    "message_type",
    "payload",
    "status",
    "priority",
    "created_at",
    "processed_at",
    "reply_to_message_id",
    "dedup_key",
]
OUTBOX_COLS = [
    "id",
    "recipient_kind",
    "recipient_user_id",
    "recipient_agent_id",
    "message_type",
    "payload",
    "task_id",
    "delivered",
    "delivered_at",
    "created_at",
]
DETAIL_RUN_COLS = [
    "id",
    "status",
    "trigger",
    "model",
    "provider",
    "started_at",
    "ended_at",
    "prompt_tokens",
    "completion_tokens",
    "cost_cents",
    "input_summary",
    "output_summary",
    "error_code",
    "error_message",
]


def _nulled(row: dict[str, Any], model: Any) -> dict[str, Any]:
    """The same row with every nullable column NULL."""
    nullable = {c.name for c in model.__table__.columns if c.nullable}
    return {k: (None if k in nullable else v) for k, v in row.items()}


class _Rows:
    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> Any:
        if isinstance(self._rows, list):
            return self._rows[0] if self._rows else None
        return self._rows

    def all(self) -> Any:
        return self._rows

    def scalar(self) -> Any:
        return self._rows


class _Session:
    """Answers each statement from the queue of the first table it names."""

    def __init__(self, answers: list[tuple[str, Any]]) -> None:
        self.answers = list(answers)

    async def execute(self, stmt: Any, *a, **kw) -> _Rows:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        for i, (table, rows) in enumerate(self.answers):
            if f"public.{table}" in sql or f" {table}" in sql:
                self.answers.pop(i)
                return _Rows(rows)
        raise AssertionError(f"unexpected statement: {sql}")


def _script(monkeypatch, make: Callable[[], list[tuple[str, Any]]]) -> None:
    """Every scope opened gets a FRESH copy of the script, so the handler can
    be run twice (over HTTP, then directly) against identical rows."""
    state: dict[str, _Session] = {}

    def _reset() -> None:
        state["s"] = _Session(make())

    _reset()

    @asynccontextmanager
    async def _scope(*a, **kw):
        yield state["s"]

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return _reset


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    class _Role:
        async def execute(self, *a, **kw):
            class _R:
                def first(self):
                    return ("admin",)

            return _R()

    @asynccontextmanager
    async def _role_scope(*a, **kw):
        yield _Role()

    monkeypatch.setattr("app.db.session.read_scope", _role_scope)

    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


AUTH = AuthContext(user_id=USER, auth_type="jwt")


def _board_rows(nulls: bool) -> Callable[[], list[tuple[str, Any]]]:
    def make() -> list[tuple[str, Any]]:
        agent = sample_row(AiAgents, only=AGENT_COLS) | {"id": AGENT}
        worker = sample_row(AgentWorkers, only=WORKER_COLS) | {"agent_id": AGENT}
        run = sample_row(AgentRuns, only=BOARD_RUN_COLS) | {"agent_id": AGENT}
        hist = sample_row(AgentStateHistory, only=HISTORY_COLS) | {"agent_id": AGENT}
        if nulls:
            agent = _nulled(agent, AiAgents)
            agent["id"] = AGENT
            worker = _nulled(worker, AgentWorkers)
            run = _nulled(run, AgentRuns)
            hist = _nulled(hist, AgentStateHistory)
        return [
            ("ai_agents", [agent]),
            ("agent_workers", [worker]),
            ("agent_inbox", [{"recipient_agent_id": AGENT}]),
            ("agent_inbox", [{"recipient_agent_id": AGENT}] * 2),
            ("agent_outbox", [{"sender_agent_id": AGENT}]),
            ("agent_runs", [run]),
            ("agent_state_history", [hist]),
        ]

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_board(client, monkeypatch, nulls):
    reset = _script(monkeypatch, _board_rows(nulls))
    resp = await client.get(f"{BASE}/board")
    reset()
    raw = await mod.get_workforce_board(_auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
    assert resp.json()["agents"][0]["recent_runs"]


class _AgentRepo:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def get_by_slug(self, slug: str) -> dict[str, Any]:
        return self.row

    async def update_fields(self, *a: Any) -> None:
        return None


def _agent(nulls: bool) -> dict[str, Any]:
    row = {
        "id": str(AGENT),
        "slug": "coordinator",
        "name": "Coordinator",
        "icon": "cpu",
        "model": "m",
        "persistent": True,
        "paused_reason": "manual",
    }
    if nulls:
        row |= {"icon": None, "model": None, "paused_reason": None}
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_agent_detail(client, monkeypatch, nulls):
    def make() -> list[tuple[str, Any]]:
        inbox = sample_row(AgentInbox, only=INBOX_COLS)
        outbox = sample_row(AgentOutbox, only=OUTBOX_COLS)
        run = sample_row(AgentRuns, only=DETAIL_RUN_COLS)
        if nulls:
            inbox = _nulled(inbox, AgentInbox)
            outbox = _nulled(outbox, AgentOutbox)
            run = _nulled(run, AgentRuns)
        return [
            ("agent_inbox", [inbox]),
            ("agent_outbox", [outbox]),
            ("agent_runs", [run]),
        ]

    monkeypatch.setattr(mod, "get_agent_repository", lambda: _AgentRepo(_agent(nulls)))
    reset = _script(monkeypatch, make)
    resp = await client.get(f"{BASE}/agents/coordinator/detail")
    reset()
    raw = await mod.get_agent_detail(slug="coordinator", auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
    assert set(resp.json()["inbox"][0]) == set(INBOX_COLS)
    assert set(resp.json()["outbox"][0]) == set(OUTBOX_COLS)
    assert set(resp.json()["runs"][0]) == set(DETAIL_RUN_COLS)


@pytest.mark.asyncio
async def test_pause_resume_and_clear(client, monkeypatch):
    monkeypatch.setattr(mod, "get_agent_repository", lambda: _AgentRepo(_agent(False)))
    _script(monkeypatch, lambda: [("agent_inbox", [(1,), (2,)])])
    resp = await client.post(f"{BASE}/agents/coordinator/pause", json={"reason": "x"})
    assert resp.json() == {
        "slug": "coordinator",
        "paused_reason": "x",
        "status": "paused",
    }
    resp = await client.post(f"{BASE}/agents/coordinator/resume")
    assert resp.json() == {
        "slug": "coordinator",
        "paused_reason": None,
        "status": "resumed",
    }
    resp = await client.post(f"{BASE}/agents/coordinator/clear-inbox")
    assert resp.json() == {"slug": "coordinator", "cleared": 2}


def _by_inbox_rows(phase: str, nulls: bool) -> Callable[[], list[tuple[str, Any]]]:
    task_cols = [
        "dbos_workflow_id",
        "agent_id",
        "phase",
        "started_at",
        "completed_at",
        "error_code",
        "error_msg",
        "created_at",
        "inbox_message_id",
        "metadata",
    ]

    def make() -> list[tuple[str, Any]]:
        task = sample_row(TaskTracking, only=task_cols)
        task["phase"] = phase
        task["metadata"] = {
            "agent_payload": {"prompt": "p"},
            "current_run_id": "7300000000000000001",
            "agent_result": {"content": "c"},
            "assigned_at": "2026-09-24T01:02:03+00:00",
            "dispatch_attempt": 2,
            "workforce_workflow_id": "workforce-t-2",
        }
        outbox = sample_row(
            AgentOutbox,
            only=[
                "id",
                "sender_agent_id",
                "message_type",
                "payload",
                "created_at",
                "delivered",
                "delivered_at",
            ],
        )
        if nulls:
            task = _nulled(task, TaskTracking) | {"phase": phase, "metadata": {}}
            outbox = _nulled(outbox, AgentOutbox)
        return [
            (
                "agent_inbox",
                {
                    "id": UUID(int=9),
                    "recipient_agent_id": AGENT,
                    "sender_kind": "user",
                    "sender_user_id": UUID(USER),
                    "sender_agent_id": None,
                    "reply_to_message_id": None,
                },
            ),
            ("task_tracking", task),
            ("agent_outbox", [outbox]),
        ]

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,nulls", [("done", False), ("done", True), ("in_progress", False)]
)
async def test_task_by_inbox(client, monkeypatch, phase, nulls):
    inbox_id = UUID(int=9)
    reset = _script(monkeypatch, _by_inbox_rows(phase, nulls))
    resp = await client.get(f"{BASE}/tasks/by-inbox/{inbox_id}")
    reset()
    raw = await mod.get_task_by_inbox(inbox_message_id=inbox_id, auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
    assert (resp.json()["outbox_response"] is None) is (phase == "in_progress")


@pytest.mark.asyncio
async def test_task_by_inbox_before_the_task_exists(client, monkeypatch):
    inbox_id = UUID(int=9)

    def make() -> list[tuple[str, Any]]:
        rows = _by_inbox_rows("done", False)()
        return [rows[0], ("task_tracking", None)]

    reset = _script(monkeypatch, make)
    resp = await client.get(f"{BASE}/tasks/by-inbox/{inbox_id}")
    reset()
    raw = await mod.get_task_by_inbox(inbox_message_id=inbox_id, auth=AUTH)
    assert (
        resp.json()
        == jsonable_encoder(raw)
        == {
            "inbox_message_id": str(inbox_id),
            "task": None,
            "outbox_response": None,
        }
    )


class _Pool:
    async def inflight_count(self) -> int:
        return 3


@pytest.mark.asyncio
@pytest.mark.parametrize("reachable", [True, False])
async def test_healthz(client, monkeypatch, reachable):
    async def _age() -> float:
        return 12.5

    monkeypatch.setattr(mod, "dbos_is_launched", lambda: True)
    monkeypatch.setattr(mod, "DbosAgentWorkforcePool", _Pool)
    monkeypatch.setattr(mod, "_oldest_undispatched_age_s", _age)
    if reachable:
        _script(
            monkeypatch,
            lambda: [("ai_agents", 2), ("agent_inbox", 5), ("agent_inbox", 1)],
        )
    else:

        @asynccontextmanager
        async def _broken(*a, **kw):
            raise ConnectionError("down")
            yield  # pragma: no cover

        monkeypatch.setattr(mod, "read_scope", _broken)
    app.dependency_overrides.pop(get_auth, None)
    resp = await client.get(f"{BASE}/healthz")
    if reachable:
        _script(
            monkeypatch,
            lambda: [("ai_agents", 2), ("agent_inbox", 5), ("agent_inbox", 1)],
        )
    raw = await mod.workforce_healthz()
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
    expected_keys = (
        {"reachable", "persistent_agents", "recent_processed_5m", "pending_depth"}
        if reachable
        else {"reachable", "error"}
    )
    assert set(resp.json()["supabase"]) == expected_keys


class _Workforce:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task

    async def get_task(self, task_id: UUID) -> dict[str, Any]:
        return self.task

    async def update_task_status(self, **kw: Any) -> bool:
        return True


class _Runs:
    async def request_cancel(self, run_id: str, *, user_id: Any = None) -> bool:
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task",
    [
        {"lifecycle_status": "queued", "current_run_id": None},
        {"lifecycle_status": "in_progress", "current_run_id": "1"},
        {"lifecycle_status": "done", "current_run_id": None},
    ],
)
async def test_task_cancel(client, monkeypatch, task):
    tid = UUID(int=7)
    monkeypatch.setattr(mod, "get_agent_workforce_repository", lambda: _Workforce(task))
    monkeypatch.setattr(mod, "get_agent_runs_repository", lambda: _Runs())
    resp = await client.post(f"{BASE}/tasks/{tid}/cancel")
    raw = await mod.cancel_task(task_id=tid, auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
