"""GET /ai-library/runs/{run_id}/events ``types`` filter (harness phase 2, W1 read side).

Polling for todo/retry progress should not drag every assistant body across
the wire. ``types`` is a CSV of event types; empty/absent means everything,
so the consumers that predate it (useRunToolActivity …) keep working with no
change — the "no filter" test is the load-bearing one.
"""

from __future__ import annotations

import contextlib
import importlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

router_mod = importlib.import_module(
    "app.api.ai_library_router"
)  # not the APIRouter re-export
router = router_mod.router

pytestmark = pytest.mark.unit
USER_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "310819108761481"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    from app.core.deps import get_auth

    class _Auth:
        user_id = USER_ID
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant
    return app


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def _capturing_read_scope(captured: list):
    class _S:
        async def execute(self, stmt):
            captured.append(stmt)
            return _Result([])

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    return _rs


def _compiled(stmt) -> tuple[str, dict]:
    c = stmt.compile()
    return str(c), dict(c.params)


def _in_values(params: dict) -> set:
    """The IN clause binds one expanding param whose value is the list."""
    out: set = set()
    for k, v in params.items():
        if k.startswith("event_type"):
            out |= set(v) if isinstance(v, (list, tuple)) else {v}
    return out


@pytest.fixture
def client():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value={"id": int(RUN_ID)})
    with patch.object(router_mod, "get_agent_runs_repository", return_value=repo):
        yield TestClient(_app())


def _get(client, captured, qs=""):
    with patch("app.db.session.read_scope", _capturing_read_scope(captured)):
        r = client.get(f"/api/v1/ai-library/runs/{RUN_ID}/events{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def test_no_types_means_no_filter_so_existing_consumers_are_untouched(client):
    captured: list = []
    _get(client, captured)
    sql, _ = _compiled(captured[0])
    assert "event_type IN" not in sql and "event_type =" not in sql, sql


def test_empty_types_is_the_same_as_absent(client):
    captured: list = []
    _get(client, captured, "?types=")
    sql, _ = _compiled(captured[0])
    assert "event_type IN" not in sql, sql


def test_types_csv_narrows_to_exactly_those_types(client):
    captured: list = []
    _get(client, captured, "?types=todo_write,llm_retry")
    sql, params = _compiled(captured[0])
    assert "event_type IN" in sql, sql
    assert _in_values(params) == {"todo_write", "llm_retry"}, params


def test_whitespace_and_empty_tokens_are_dropped(client):
    captured: list = []
    _get(client, captured, "?types=%20todo_write%20,,")
    sql, params = _compiled(captured[0])
    assert "event_type IN" in sql
    assert _in_values(params) == {"todo_write"}


def test_unknown_type_is_a_quiet_empty_set_not_a_500(client):
    captured: list = []
    body = _get(client, captured, "?types=no_such_event")
    assert body == {"items": [], "count": 0}


def test_foreign_run_is_still_404(client):
    router_mod.get_agent_runs_repository().get_by_id.return_value = None
    r = client.get(f"/api/v1/ai-library/runs/{RUN_ID}/events?types=todo_write")
    assert r.status_code == 404


# ── phase 2b-1 replay: upto_seq is an inclusive upper bound ─────────────────


def test_upto_seq_adds_an_inclusive_upper_bound(client):
    captured: list = []
    _get(client, captured, "?after_seq=3&upto_seq=9")
    sql, params = _compiled(captured[0])
    assert "seq > " in sql and "seq <= " in sql, sql
    assert 3 in params.values() and 9 in params.values(), params


def test_upto_seq_absent_keeps_the_old_query_shape(client):
    captured: list = []
    _get(client, captured)
    sql, _ = _compiled(captured[0])
    assert "seq <= " not in sql, sql
