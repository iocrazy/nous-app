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


def _binds(stmt) -> dict:
    return dict(stmt.compile().params)


def test_view_at_binds_seq_as_the_inclusive_bound_and_filters_to_folded_types():
    captured: list = []
    with (
        patch.object(router_mod, "get_agent_runs_repository") as g,
        patch("app.db.session.read_scope", _rows_scope(EVENTS, captured)),
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seq"] == 3 and body["cost"]["spent_cents"] == 0.5
    sql = str(captured[0].compile())
    binds = _binds(captured[0])
    # the bound is a bind of exactly 3 (an off-by-one would bind 2 or 4)
    assert "seq <= " in sql and 3 in binds.values(), (sql, binds)
    # only registered fold types are fetched — assistant/tool_call bodies stay home
    from app.services.ai.runner.run_projection import registered_types

    in_values = [v for v in binds.values() if isinstance(v, (list, tuple))]
    assert in_values and set(in_values[0]) == set(registered_types()), binds


def test_view_at_folds_exactly_the_rows_it_gets():
    # the DB does the bounding; given rows 1-4 the fold must include seq 4
    with (
        patch.object(router_mod, "get_agent_runs_repository") as g,
        patch("app.db.session.read_scope", _rows_scope(EVENTS)),
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=4")
    assert r.json()["view"]["budget"] == {"pct": 90, "state": "warn", "spent_cents": 9}


def test_view_at_tolerates_a_null_payload_row():
    rows = [{"seq": 1, "event_type": "step_start", "payload": None}]
    with (
        patch.object(router_mod, "get_agent_runs_repository") as g,
        patch("app.db.session.read_scope", _rows_scope(rows)),
    ):
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=1")
    assert r.status_code == 200


def test_view_at_requires_seq_and_404s_foreign_runs():
    with patch.object(router_mod, "get_agent_runs_repository") as g:
        g.return_value.get_by_id = AsyncMock(return_value=None)
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=3")
    assert r.status_code == 404
    with patch.object(router_mod, "get_agent_runs_repository") as g:
        g.return_value.get_by_id = AsyncMock(return_value={"id": RUN_ID})
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at")
        assert r.status_code == 422
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/view-at?seq=-1")
        assert r.status_code == 422
