"""AI Library run, usage and chat-attachment routes: wire parity after they
gained response models (OpenAPI P4). Also pins that the SSE chat stream is
declared as ``text/event-stream`` rather than a JSON body it never sends.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth
from app.main import app
from app.models import AgentRuns, AgentRunTranscriptEvents, AiAgents
from app.schemas.ai_library_responses import LiveRunItem, RunTranscriptEvent
from app.services.ai.billing.token_billing import (
    DailyUsage,
    UsageSummary,
    UsageSummaryRow,
)
from tests.api.ai_library_wire_helpers import AUTH, ScriptedScope, fake_auth
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    SAMPLE_TS,
    assert_wire_unchanged,
    sample_row,
)

r = sys.modules["app.api.ai_library_router"]
fork_mod = sys.modules.get("app.services.issues.issue_fork") or __import__(
    "app.services.issues.issue_fork", fromlist=["fork_run"]
)

pytestmark = pytest.mark.unit

BASE = "/api/v1/ai-library"
RUN_ID = str(SAMPLE_BIGINT)
AGENT_ID = UUID(int=0xA1)


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _runs_repo(**methods: Any) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value={"id": SAMPLE_BIGINT})
    for name, value in methods.items():
        setattr(repo, name, value)
    return repo


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #

_LIVE_COLS = (
    "id",
    "agent_id",
    "status",
    "trigger",
    "model",
    "started_at",
    "prompt_tokens",
    "completion_tokens",
    "cost_cents",
    "input_summary",
    "task_id",
)


def test_live_run_item_is_the_select_plus_agent_labels() -> None:
    assert set(LiveRunItem.model_fields) == set(_LIVE_COLS) | {
        "agent_slug",
        "agent_name",
        "agent_icon",
    }


@pytest.mark.asyncio
async def test_live_runs(client) -> None:
    known = sample_row(AgentRuns, only=_LIVE_COLS)
    known.update(agent_id=AGENT_ID, status="running")
    orphan = {
        **known,
        "id": SAMPLE_BIGINT + 9,
        "agent_id": UUID(int=0xB2),
        "cost_cents": None,
        "task_id": None,
        "model": None,
        "input_summary": None,
    }
    agent = sample_row(AiAgents, only=("id", "slug", "name", "icon"))
    agent["id"] = AGENT_ID
    results = [[known, orphan], [agent]]
    with patch("app.db.session.read_scope", ScriptedScope(results)):
        raw = await r.list_live_runs(auth=AUTH)
    with patch("app.db.session.read_scope", ScriptedScope(results)):
        resp = await client.get(f"{BASE}/runs/live")
    assert_wire_unchanged(resp, raw)
    items = resp.json()["items"]
    assert items[0]["agent_slug"] == "slug-value" and items[1]["agent_slug"] is None


@pytest.mark.asyncio
async def test_live_runs_empty(client) -> None:
    with patch("app.db.session.read_scope", ScriptedScope([[]])):
        resp = await client.get(f"{BASE}/runs/live")
    assert resp.status_code == 200 and resp.json() == {"items": [], "count": 0}


@pytest.mark.asyncio
async def test_run_events(client) -> None:
    cols = tuple(RunTranscriptEvent.model_fields)
    full = sample_row(AgentRunTranscriptEvents, only=cols)
    full["event_type"] = "assistant"
    bare = {**full, "seq": full["seq"] + 1, "turn": None, "step": None}
    repo = _runs_repo()
    for results in ([[full, bare]], [[]]):
        with patch.object(r, "get_agent_runs_repository", lambda: repo):
            with patch("app.db.session.read_scope", ScriptedScope(results)):
                raw = await r.list_run_events(
                    run_id=RUN_ID,
                    auth=AUTH,
                    after_seq=0,
                    limit=500,
                    types="",
                    upto_seq=None,
                )
            with patch("app.db.session.read_scope", ScriptedScope(results)):
                resp = await client.get(f"{BASE}/runs/{RUN_ID}/events")
        assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_run_view_at(client) -> None:
    rows = [{"seq": 3, "event_type": "llm_retry", "payload": {"attempt": 2}}]
    repo = _runs_repo(list_transcript_events=AsyncMock(return_value=rows))
    with patch.object(r, "get_agent_runs_repository", lambda: repo):
        raw = await r.get_run_view_at(run_id=RUN_ID, auth=AUTH, seq=3)
        resp = await client.get(f"{BASE}/runs/{RUN_ID}/view-at?seq=3")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_run_fork(client) -> None:
    result = {
        "run_id": None,
        "session_id": str(SAMPLE_BIGINT + 1),
        "workflow_id": "issue-7-wf",
        "issue_id": SAMPLE_BIGINT + 2,
        "forked_from": {"run_id": SAMPLE_BIGINT, "at_seq": 4},
    }
    with (
        patch.object(fork_mod, "fork_run", AsyncMock(return_value=result)),
        patch.object(fork_mod, "default_deps", lambda: None),
    ):
        resp = await client.post(f"{BASE}/runs/{RUN_ID}/fork", json={"at_seq": 4})
    assert_wire_unchanged(resp, result, status=201)


@pytest.mark.asyncio
async def test_run_forks(client) -> None:
    rows = [
        {
            "id": SAMPLE_BIGINT + 3,
            "fork_at_seq": 4,
            "created_at": SAMPLE_TS,
            "status": "running",
        },
        {
            "id": SAMPLE_BIGINT + 4,
            "fork_at_seq": None,
            "created_at": SAMPLE_TS,
            "status": "failed",
        },
    ]
    repo = _runs_repo(list_forks=AsyncMock(return_value=rows))
    with patch.object(r, "get_agent_runs_repository", lambda: repo):
        raw = await r.list_run_forks(run_id=RUN_ID, auth=AUTH)
        resp = await client.get(f"{BASE}/runs/{RUN_ID}/forks")
    assert_wire_unchanged(resp, raw)
    assert resp.json()["items"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_run_cancel(client) -> None:
    repo = _runs_repo(request_cancel=AsyncMock(return_value=True))
    with patch.object(r, "get_agent_runs_repository", lambda: repo):
        raw = await r.cancel_run(run_id=RUN_ID, auth=AUTH)
        resp = await client.post(f"{BASE}/runs/{RUN_ID}/cancel")
    assert_wire_unchanged(resp, raw, status=202)


# --------------------------------------------------------------------------- #
# usage
# --------------------------------------------------------------------------- #

_ADMIN_COLS = (
    "id",
    "user_id",
    "agent_id",
    "model",
    "provider",
    "status",
    "trigger",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_cents",
    "started_at",
    "ended_at",
    "error_code",
)


def test_usage_runs_projection_is_the_repository_one() -> None:
    from app.repositories.agent_runs_repository import AgentRunsRepository

    assert AgentRunsRepository._ADMIN_LIST_COLS == _ADMIN_COLS


@pytest.mark.asyncio
async def test_usage_runs(client) -> None:
    """``agent_slug`` / ``agent_name`` are absent when the agent lookup finds
    nothing — the route keeps that absence rather than sending nulls."""
    known = sample_row(AgentRuns, only=_ADMIN_COLS)
    known["agent_id"] = AGENT_ID
    orphan = {
        **known,
        "id": SAMPLE_BIGINT + 1,
        "agent_id": UUID(int=0xB2),
        "ended_at": None,
        "cost_cents": None,
        "total_tokens": None,
    }
    headless = {**known, "id": SAMPLE_BIGINT + 2, "agent_id": None}
    runs_repo = MagicMock()
    runs_repo.list_runs_admin = AsyncMock(
        return_value={"items": [known, orphan, headless], "total": 3}
    )
    agent_repo = MagicMock()

    async def _get_by_id(agent_id):
        return {"slug": "ceo", "name": None} if agent_id == AGENT_ID else None

    agent_repo.get_by_id = _get_by_id
    with (
        patch.object(r, "get_agent_runs_repository", lambda: runs_repo),
        patch.object(r, "get_agent_repository", lambda: agent_repo),
    ):
        raw = await r.get_usage_runs(
            auth=AUTH,
            page=1,
            page_size=25,
            model=None,
            status=None,
            days=30,
            month=None,
        )
        resp = await client.get(f"{BASE}/usage/runs")
    assert_wire_unchanged(resp, raw)
    items = resp.json()["items"]
    assert items[0]["agent_slug"] == "ceo" and items[0]["agent_name"] is None
    assert "agent_slug" not in items[1] and "agent_slug" not in items[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("group_by", ["model", "agent"])
async def test_usage_daily(client, group_by) -> None:
    rows = [
        {
            "date": dt.date(2026, 9, 24),
            "key": str(AGENT_ID) if group_by == "agent" else "qwen-max",
            "requests": 4,
            "failed_requests": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost_cents": 12.345678,
        },
        {
            "date": dt.date(2026, 9, 25),
            "key": None,
            "requests": 1,
            "failed_requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_cents": 0,
        },
    ]
    runs_repo = MagicMock()
    runs_repo.daily_usage = AsyncMock(return_value=rows)
    with (
        patch.object(r, "get_agent_runs_repository", lambda: runs_repo),
        patch.object(
            r, "_agent_labels", AsyncMock(return_value={str(AGENT_ID): "CEO"})
        ),
    ):
        raw = await r.get_usage_daily(auth=AUTH, days=30, group_by=group_by, month=None)
        resp = await client.get(f"{BASE}/usage/daily?group_by={group_by}")
        assert_wire_unchanged(resp, raw)
        raw = await r.get_usage_daily(
            auth=AUTH, days=30, group_by=group_by, month="2026-09"
        )
        resp = await client.get(f"{BASE}/usage/daily?group_by={group_by}&month=2026-09")
        assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_usage_summary(client) -> None:
    summary = UsageSummary(
        window_start=SAMPLE_TS,
        window_end=SAMPLE_TS + dt.timedelta(days=30),
        overall_total_tokens=120,
        overall_cost_points=3.25,
        overall_run_count=2,
        by_model=[
            UsageSummaryRow(
                model="qwen-max", total_tokens=120, cost_points=3.25, run_count=2
            )
        ],
        by_day=[
            DailyUsage(
                date="2026-09-24", total_tokens=120, cost_points=3.25, run_count=2
            )
        ],
    )
    with patch(
        "app.services.ai.billing.token_billing.summarize_user_usage",
        AsyncMock(return_value=summary),
    ):
        raw = await r.get_usage_summary(auth=AUTH, days=30)
        resp = await client.get(f"{BASE}/usage/summary")
    assert_wire_unchanged(resp, raw)


# --------------------------------------------------------------------------- #
# chat attachments / chat stream
# --------------------------------------------------------------------------- #

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["image/png", None])
async def test_chat_attachment_upload(client, content_type) -> None:
    saved: Dict[str, Any] = {
        "resource_id": str(SAMPLE_BIGINT),
        "file_path": "user/abc/temp/shot.png",
        "kind": "image",
        "mime": content_type or "",
        "filename": "shot.png",
        "size_bytes": len(_PNG),
    }
    file_part = ("shot.png", _PNG, content_type) if content_type else ("shot.png", _PNG)
    with patch(
        "app.services.library.chat_upload.save_chat_temp_upload",
        AsyncMock(return_value=saved),
    ):
        resp = await client.post(
            f"{BASE}/chat-attachments/upload", files={"file": file_part}
        )
    assert resp.status_code == 200, resp.text
    # What the handler builds from ``saved`` (mime is the part's own header).
    mime = resp.json()["mime"]
    raw = {
        "kind": "image",
        "resource_id": saved["resource_id"],
        "file_path": saved["file_path"],
        "url": saved["file_path"],
        "size_bytes": saved["size_bytes"],
        "mime": mime,
        "filename": "shot.png",
    }
    assert_wire_unchanged(resp, raw)
    if content_type:
        assert mime == content_type


def test_chat_stream_is_declared_as_sse_not_json() -> None:
    op = app.openapi()["paths"]["/api/v1/ai-library/sessions/{session_id}/chat-stream"][
        "post"
    ]
    content = op["responses"]["200"]["content"]
    assert set(content) == {"text/event-stream"}
