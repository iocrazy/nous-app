"""Tests for the batch agent-stats endpoint.

GET /api/v1/ai-library/agents/stats?days=7 powers the B1 gallery: one
request replaces the per-agent /dashboard fan-out. It merges four
aggregates keyed by agent id — 7d run count, 7d tokens, live run count,
needs-input count — plus a fault descriptor.

Pinned here:
    - Four aggregates merge onto the right agent id
    - An agent with no activity anywhere still appears, all-zero, fault null
    - paused_reason='budget' → fault carries an ACTIONABLE detail string
      (spec: "故障徽章必须携带一行可操作原因" — a bare 'fault' is useless)
    - dead/stuck runs surface as a fault when the agent isn't paused
    - The aggregates are batch queries, not a per-agent loop (N+1 guard)
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

BASE = "/api/v1/ai-library"
FAKE_USER_ID = str(uuid4())

AGENT_A = str(uuid4())
AGENT_B = str(uuid4())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _agent(agent_id: str, slug: str, **extra):
    return {"id": agent_id, "slug": slug, "name": slug, **extra}


def _stub_stack(
    *,
    agents,
    usage=None,
    running=None,
    needs_input=None,
    dead=None,
):
    """Patch the router's repo factories for one request.

    Returns the (agent_repo, runs_repo, issue_repo) mocks so a test can
    assert on call shape (the N+1 guard).
    """
    agent_repo = AsyncMock()
    agent_repo.list_accessible.return_value = agents

    runs_repo = AsyncMock()
    runs_repo.usage_by_agent_since.return_value = usage or {}
    runs_repo.running_counts_by_agent.return_value = running or {}
    runs_repo.dead_run_reasons_by_agent.return_value = dead or {}

    issue_repo = AsyncMock()
    issue_repo.count_needs_input_by_agent.return_value = needs_input or {}

    return agent_repo, runs_repo, issue_repo


def _patches(agent_repo, runs_repo, issue_repo):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.api.ai_library_router.get_agent_repository",
            return_value=agent_repo,
        )
    )
    stack.enter_context(
        patch(
            "app.api.ai_library_router.get_agent_runs_repository",
            return_value=runs_repo,
        )
    )
    stack.enter_context(
        patch(
            "app.api.ai_library_router.get_issue_repository",
            return_value=issue_repo,
        )
    )
    stack.enter_context(
        patch(
            "app.api.ai_library_router._fetch_user_team_ids",
            AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch(
            "app.api.ai_library_router._fetch_user_project_ids",
            AsyncMock(return_value=[]),
        )
    )
    return stack


@pytest.mark.asyncio
async def test_merges_four_aggregates_by_agent_id(client: AsyncClient) -> None:
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai"), _agent(AGENT_B, "summarize")],
        usage={AGENT_A: {"runs": 12, "tokens": 34567}},
        running={AGENT_A: 2},
        needs_input={AGENT_B: 3},
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats?days=7")

    assert resp.status_code == 200
    items = resp.json()["items"]

    assert items[AGENT_A]["runs_7d"] == 12
    assert items[AGENT_A]["tokens_7d"] == 34567
    assert items[AGENT_A]["running_count"] == 2
    assert items[AGENT_A]["needs_input_count"] == 0

    assert items[AGENT_B]["needs_input_count"] == 3
    assert items[AGENT_B]["running_count"] == 0


@pytest.mark.asyncio
async def test_agent_with_no_activity_is_all_zero(client: AsyncClient) -> None:
    """Absent from every aggregate ≠ absent from the response — the gallery
    renders a card per agent and must not have to guess at missing keys."""
    repos = _stub_stack(agents=[_agent(AGENT_A, "translate")])
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats")

    item = resp.json()["items"][AGENT_A]
    assert item == {
        "runs_7d": 0,
        "tokens_7d": 0,
        "cost_cents_7d": 0,
        "running_count": 0,
        "needs_input_count": 0,
        "fault": None,
    }


@pytest.mark.asyncio
async def test_spend_is_reported_for_the_same_window(client: AsyncClient) -> None:
    """The workbench's "this week" row shows runs / tokens / spend together.

    Spend used to be missing here, so the UI could only offer a 14-day figure
    from /dashboard — a different window under a "this week" label. It rides
    the same GROUP BY as runs and tokens (see the N+1 guard below), so this
    asserts the router surfaces it rather than dropping it on the floor.
    """
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai")],
        usage={AGENT_A: {"runs": 4, "tokens": 900, "cost_cents": 57}},
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats?days=7")

    assert resp.json()["items"][AGENT_A]["cost_cents_7d"] == 57


@pytest.mark.asyncio
async def test_runs_with_no_recorded_cost_report_zero_not_null(
    client: AsyncClient,
) -> None:
    """``cost_cents`` is nullable per run (a provider may not price a call).

    The repo coalesces the SUM, but a usage row predating that — or one built
    by another caller — can still omit the key. Zero keeps the chip rendering;
    ``None`` would blow up the int() cast.
    """
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai")],
        usage={AGENT_A: {"runs": 2, "tokens": 100}},
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats")

    assert resp.json()["items"][AGENT_A]["cost_cents_7d"] == 0


@pytest.mark.asyncio
async def test_budget_pause_carries_actionable_detail(client: AsyncClient) -> None:
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai", paused_reason="budget")],
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats")

    fault = resp.json()["items"][AGENT_A]["fault"]
    assert fault["kind"] == "budget"
    assert fault["detail"]
    assert "budget" in fault["detail"].lower()


@pytest.mark.asyncio
async def test_manual_pause_reports_manual_kind(client: AsyncClient) -> None:
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai", paused_reason="manual")],
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats")

    fault = resp.json()["items"][AGENT_A]["fault"]
    assert fault["kind"] == "manual"
    assert fault["detail"]


@pytest.mark.asyncio
async def test_dead_runs_surface_as_fault_with_truncated_reason(
    client: AsyncClient,
) -> None:
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai")],
        dead={AGENT_A: "x" * 400},
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats")

    fault = resp.json()["items"][AGENT_A]["fault"]
    assert fault["kind"] == "dead_runs"
    assert len(fault["detail"]) <= 120


@pytest.mark.asyncio
async def test_pause_wins_over_dead_runs(client: AsyncClient) -> None:
    """A paused agent's actionable step is 'resume/raise budget', not the
    stale error from whatever died before the pause."""
    repos = _stub_stack(
        agents=[_agent(AGENT_A, "script_ai", paused_reason="budget")],
        dead={AGENT_A: "boom"},
    )
    with _patches(*repos):
        resp = await client.get(f"{BASE}/agents/stats")

    assert resp.json()["items"][AGENT_A]["fault"]["kind"] == "budget"


@pytest.mark.asyncio
async def test_aggregates_are_batched_not_per_agent(client: AsyncClient) -> None:
    """N+1 guard: four repo calls total, regardless of agent count."""
    agents = [_agent(str(uuid4()), f"agent-{i}") for i in range(12)]
    agent_repo, runs_repo, issue_repo = _stub_stack(agents=agents)
    with _patches(agent_repo, runs_repo, issue_repo):
        resp = await client.get(f"{BASE}/agents/stats")

    assert resp.status_code == 200
    assert runs_repo.usage_by_agent_since.await_count == 1
    assert runs_repo.running_counts_by_agent.await_count == 1
    assert runs_repo.dead_run_reasons_by_agent.await_count == 1
    assert issue_repo.count_needs_input_by_agent.await_count == 1


def test_router_does_not_query_inside_a_loop() -> None:
    """Source-level guard — no aggregate call may sit inside ANY loop body.

    The runtime guard above only proves it for the agent counts the test
    happens to stage; this one holds for every future edit. Walks the AST
    rather than grepping text so a reordering of the function can't make it
    vacuously pass.
    """
    import ast
    import importlib
    import textwrap

    # importlib, not `from app.api import ai_library_router` — the package
    # __init__ rebinds that name to the APIRouter instance.
    module = importlib.import_module("app.api.ai_library_router")

    aggregates = {
        "usage_by_agent_since",
        "running_counts_by_agent",
        "dead_run_reasons_by_agent",
        "count_needs_input_by_agent",
    }
    tree = ast.parse(textwrap.dedent(inspect.getsource(module.get_agents_stats)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in aggregates
            ):
                pytest.fail(f"{inner.func.attr} is called inside a loop (N+1)")
