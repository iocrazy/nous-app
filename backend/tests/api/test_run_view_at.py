"""GET /ai-library/runs/{id}/view-at?seq=N — the folded view AS OF seq (phase 2b-1
replay). Server-side fold through run_projection.replay so the frontend never
duplicates a fold."""

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


def _rows_scope(rows, captured=None):
    class _R:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _S:
        async def execute(self, stmt):
            if captured is not None:
                captured.append(stmt)
            return _R()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    return _rs


EVENTS = [
    {"seq": 1, "event_type": "user", "payload": {"content": "go"}},
    {
        "seq": 2,
        "event_type": "step_start",
        "payload": {"turn": 1, "step": 1, "model": "m"},
    },
    {
        "seq": 3,
        "event_type": "step_end",
        "payload": {
            "turn": 1,
            "step": 1,
            "model": "m",
            "usage": {"prompt": 10, "completion": 5},
            "cost_cents": 0.5,
            "duration_ms": 100,
            "finish_reason": "stop",
        },
    },
    {
        "seq": 4,
        "event_type": "budget_check",
        "payload": {
            "pct": 90.0,
            "action": "warn",
            "spent_cents": 9,
            "budget_cents": 10,
        },
    },
]


def test_view_at_folds_only_events_up_to_seq():
    captured: list = []
    with (
        patch.object(router_mod, "get_agent_runs_repository") as g,
        patch("app.db.session.read_scope", _rows_scope(EVENTS[:3], captured)),
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seq"] == 3
    assert body["view"]["budget"] is None  # seq 4 not folded
    assert body["cost"]["spent_cents"] == 0.5
    # the query itself is bounded — not "fetch everything then slice"
    sql = str(captured[0].compile())
    assert "seq <= " in sql, sql


def test_view_at_requires_seq_and_404s_foreign_runs():
    with patch.object(router_mod, "get_agent_runs_repository") as g:
        g.return_value.get_by_id = AsyncMock(return_value=None)
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=3")
    assert r.status_code == 404
    with patch.object(router_mod, "get_agent_runs_repository") as g:
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at")
    assert r.status_code == 422
