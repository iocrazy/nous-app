"""AI Library agent / skill routes: wire parity after they gained response
models (OpenAPI P4) — status, dashboard, usage, and version history.

Rows come from the ORM columns each SELECT names (``sample_row(..., only=)``)
so they carry native types: BIGINT ids above 2**53, microsecond UTC
datetimes, ``Decimal`` money. See ``tests/api/ai_library_wire_helpers.py``
for how parity is measured.
"""

from __future__ import annotations

import sys
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth
from app.main import app
from app.models import (
    AgentRuns,
    AiAgentVersions,
    SkillFileVersions,
    SkillVersions,
    TaskTracking,
)
from app.schemas.ai_library_responses import (
    AgentDashboardLatestRun,
    AgentDashboardRecentRun,
    AgentTaskShape,
    AgentVersionDetail,
    SkillVersionDetail,
)
from tests.api.ai_library_wire_helpers import AUTH, ScriptedScope, fake_auth
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_row

r = sys.modules["app.api.ai_library_router"]

pytestmark = pytest.mark.unit

BASE = "/api/v1/ai-library"
AGENT_ID = "00000000-0000-0000-0000-0000000000a1"
SKILL_ID = 7300000000000000321


@pytest.fixture(autouse=True)
def _caller_in_row_scope(monkeypatch):
    """The agent/skill row-scope guard has its own tests
    (``tests/api/test_ai_library_row_scope.py``); here every caller is in scope."""
    import sys

    monkeypatch.setattr(
        sys.modules["app.api.ai_library_router"],
        "_in_row_scope",
        AsyncMock(return_value=True),
    )


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


def _agent(**over: Any) -> Dict[str, Any]:
    row = {
        "id": AGENT_ID,
        "slug": "ceo",
        "name": "CEO",
        "icon": "crown",
        "model": "qwen-max",
        "persistent": True,
        "paused_reason": None,
        "current_version": 4,
        "is_system_preset": False,
    }
    row.update(over)
    return row


def _agent_repo(**methods: Any) -> MagicMock:
    repo = MagicMock()
    for name, value in methods.items():
        setattr(repo, name, value)
    return repo


def _skill_repo(skill: Dict[str, Any], **methods: Any) -> MagicMock:
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=skill)
    for name, value in methods.items():
        setattr(repo, name, value)
    return repo


def _skill(**over: Any) -> Dict[str, Any]:
    row = {
        "id": SKILL_ID,
        "slug": "my-skill",
        "is_public": False,
        "project_id": 42,
        "current_version": 6,
    }
    row.update(over)
    return row


async def _parity(client, method, url, handler, results, repos, **kwargs):
    """Call ``handler`` directly, then the same route over HTTP, each against
    a fresh copy of ``results``; return ``(response, raw)``."""
    with (
        patch("app.db.session.read_scope", ScriptedScope(results)),
        patch.multiple(r, **repos),
    ):
        raw = await handler(**kwargs)
    with (
        patch("app.db.session.read_scope", ScriptedScope(results)),
        patch.multiple(r, **repos),
    ):
        resp = await client.request(method, f"{BASE}{url}")
    return resp, raw


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent, results",
    [
        (_agent(paused_reason="budget"), []),
        (_agent(), [3]),
        (_agent(), [0]),
    ],
)
async def test_status_every_branch(client, agent, results) -> None:
    repo = _agent_repo(get_by_slug=AsyncMock(return_value=agent))
    resp, raw = await _parity(
        client,
        "GET",
        "/agents/ceo/status",
        r.get_agent_status,
        results,
        {"get_agent_repository": lambda: repo},
        slug="ceo",
        auth=AUTH,
    )
    assert_wire_unchanged(resp, raw)


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #

_RUN_14D = (
    "id",
    "status",
    "trigger",
    "model",
    "started_at",
    "ended_at",
    "prompt_tokens",
    "completion_tokens",
    "cost_cents",
    "own_cost_cents",
)
_RUN_LATEST = (
    "id",
    "status",
    "trigger",
    "model",
    "started_at",
    "ended_at",
    "prompt_tokens",
    "completion_tokens",
    "cost_cents",
    "input_summary",
    "output_summary",
    "error_code",
    "error_message",
)
_RUN_RECENT = _RUN_14D[:-1]
_TASK_14D = ("dbos_workflow_id", "phase", "created_at", "title", "metadata")
_TASK_RECENT = (
    "dbos_workflow_id",
    "phase",
    "created_at",
    "started_at",
    "completed_at",
    "title",
    "error_code",
    "error_msg",
    "metadata",
)


def _run(cols, **over) -> Dict[str, Any]:
    row = sample_row(AgentRuns, only=cols)
    row["status"] = "completed"
    row.update(over)
    return row


def _task(cols, **over) -> Dict[str, Any]:
    row = sample_row(TaskTracking, only=cols)
    row["metadata"] = {
        "agent_payload": {"brief": "x"},
        "current_run_id": "7300000000000000999",
        "agent_result": {"ok": True, "n": 2},
        "assigned_at": "2026-09-24T01:02:03.456789+00:00",
        "dispatch_attempt": 2,
        "workforce_workflow_id": "wf-1",
    }
    row.update(over)
    return row


def test_dashboard_run_models_match_the_selects() -> None:
    """The latest / recent run models are exactly the columns each SELECT
    reads (the 14-day one only feeds the aggregates)."""
    assert set(AgentDashboardLatestRun.model_fields) == set(_RUN_LATEST)
    assert set(AgentDashboardRecentRun.model_fields) == set(_RUN_RECENT)
    assert set(_RUN_14D) <= column_names(AgentRuns)


@pytest.mark.asyncio
async def test_dashboard_full(client) -> None:
    results = [
        [_run(_RUN_14D), _run(_RUN_14D, status="failed", ended_at=None)],
        [_run(_RUN_LATEST, error_code="boom")],
        [_task(_TASK_14D), _task(_TASK_14D, phase=None, metadata=None)],
        [_task(_TASK_RECENT), _task(_TASK_RECENT, metadata={})],
        [_run(_RUN_RECENT), _run(_RUN_RECENT, cost_cents=None, model=None)],
    ]
    repo = _agent_repo(get_by_slug=AsyncMock(return_value=_agent()))
    resp, raw = await _parity(
        client,
        "GET",
        "/agents/ceo/dashboard",
        r.get_agent_dashboard,
        results,
        {"get_agent_repository": lambda: repo},
        slug="ceo",
        auth=AUTH,
    )
    assert_wire_unchanged(resp, raw)
    body = resp.json()
    # The task mapper's every key, not only the ones the card reads.
    assert set(body["recent_tasks"][0]) == set(AgentTaskShape.model_fields)
    assert body["latest_run"]["id"] == str(_run(("id",))["id"])


@pytest.mark.asyncio
async def test_dashboard_never_run(client) -> None:
    repo = _agent_repo(
        get_by_slug=AsyncMock(
            return_value=_agent(icon=None, model=None, persistent=None)
        )
    )
    resp, raw = await _parity(
        client,
        "GET",
        "/agents/ceo/dashboard",
        r.get_agent_dashboard,
        [[], [], [], [], []],
        {"get_agent_repository": lambda: repo},
        slug="ceo",
        auth=AUTH,
    )
    assert_wire_unchanged(resp, raw)
    assert resp.json()["latest_run"] is None


# --------------------------------------------------------------------------- #
# usage ("Used by")
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_usage(client) -> None:
    results = [
        [{"trigger": "chat", "count": 5}, {"trigger": None, "count": 1}],
        2,
        1,
    ]
    repo = _agent_repo(get_by_slug=AsyncMock(return_value=_agent(slug="analyze")))
    resp, raw = await _parity(
        client,
        "GET",
        "/agents/analyze/usage",
        r.get_agent_usage,
        results,
        {"get_agent_repository": lambda: repo},
        slug="analyze",
        auth=AUTH,
    )
    assert_wire_unchanged(resp, raw)
    assert resp.json()["modules"], "analyze has registry entries"


# --------------------------------------------------------------------------- #
# version history
# --------------------------------------------------------------------------- #


def _version_rows(orm_model, cols, **over) -> list[Dict[str, Any]]:
    first = sample_row(orm_model, only=cols)
    second = {**first, "notes": None, "created_by": None, **over}
    return [first, second]


@pytest.mark.asyncio
async def test_agent_versions_list(client) -> None:
    cols = (
        "id",
        "version_number",
        "model",
        "temperature",
        "max_tokens",
        "notes",
        "created_by",
        "created_at",
    )
    rows = _version_rows(AiAgentVersions, cols, model=None, temperature=None)
    repo = _agent_repo(get_by_slug=AsyncMock(return_value=_agent()))
    resp, raw = await _parity(
        client,
        "GET",
        "/agents/ceo/versions",
        r.list_agent_versions,
        [rows],
        {"get_agent_repository": lambda: repo},
        slug="ceo",
        auth=AUTH,
        limit=50,
    )
    assert_wire_unchanged(resp, raw)
    # Native datetimes keep the +00:00 form the bare dict had.
    assert resp.json()["items"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_agent_version_detail_every_column(client) -> None:
    assert set(AgentVersionDetail.model_fields) == column_names(AiAgentVersions)
    repo = _agent_repo(get_by_slug=AsyncMock(return_value=_agent()))
    resp, raw = await _parity(
        client,
        "GET",
        "/agents/ceo/versions/3",
        r.get_agent_version,
        [[sample_row(AiAgentVersions)]],
        {"get_agent_repository": lambda: repo},
        slug="ceo",
        version_number=3,
        auth=AUTH,
    )
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_skill_versions_list_and_detail(client) -> None:
    cols = ("id", "version_number", "notes", "created_by", "created_at")
    repos = {"get_skill_repository": lambda: _skill_repo(_skill())}
    resp, raw = await _parity(
        client,
        "GET",
        "/skills/my-skill/versions",
        r.list_skill_versions,
        [_version_rows(SkillVersions, cols)],
        repos,
        slug="my-skill",
        auth=AUTH,
        limit=50,
    )
    assert_wire_unchanged(resp, raw)

    assert set(SkillVersionDetail.model_fields) == column_names(SkillVersions)
    full = sample_row(SkillVersions)
    for row in (full, {**full, "frontmatter_json": None, "body_md": None}):
        resp, raw = await _parity(
            client,
            "GET",
            "/skills/my-skill/versions/2",
            r.get_skill_version,
            [[row]],
            repos,
            slug="my-skill",
            version_number=2,
            auth=AUTH,
        )
        assert_wire_unchanged(resp, raw)
        assert resp.json()["skill_id"] == row["skill_id"]


@pytest.mark.asyncio
async def test_skill_file_versions_list(client) -> None:
    cols = (
        "id",
        "version_number",
        "path",
        "file_type",
        "notes",
        "created_by",
        "created_at",
    )
    file_row = {"id": UUID(int=9), "current_version": 3}
    resp, raw = await _parity(
        client,
        "GET",
        "/skills/my-skill/files/references/a.md/versions",
        r.list_skill_file_versions,
        [[file_row], _version_rows(SkillFileVersions, cols, file_type=None)],
        {"get_skill_repository": lambda: _skill_repo(_skill())},
        slug="my-skill",
        path="references/a.md",
        auth=AUTH,
        limit=50,
    )
    assert_wire_unchanged(resp, raw)


# --------------------------------------------------------------------------- #
# rollback
# --------------------------------------------------------------------------- #

_SNAP_COLS = ("identity_md", "soul_md", "agent_md", "model", "temperature")


@pytest.mark.asyncio
async def test_agent_rollback_writes_the_old_content(client) -> None:
    """Rollback used to call ``update_fields_versioned`` with keyword names the
    repository does not take (``agent_uuid`` / ``editor_user_id``), so every
    call was a TypeError → 500. It now passes the real signature and reports
    the new version from a re-read."""
    snap = sample_row(AiAgentVersions, only=(*_SNAP_COLS, "max_tokens"))
    update = AsyncMock(return_value=None)
    repo = _agent_repo(
        get_by_slug=AsyncMock(side_effect=[_agent(), _agent(current_version=5)]),
        update_fields_versioned=update,
    )
    with (
        patch("app.db.session.read_scope", ScriptedScope([[snap]])),
        patch.object(r, "get_agent_repository", lambda: repo),
    ):
        resp = await client.post(f"{BASE}/agents/ceo/rollback/3")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "rolled_back_to": 3,
        "new_version": 5,
        "notes": "rollback of v3",
    }
    args, kwargs = update.call_args
    assert args[0] == UUID(AGENT_ID)
    assert args[1] == {k: snap[k] for k in (*_SNAP_COLS, "max_tokens")}
    assert kwargs == {"created_by": UUID(AUTH.user_id), "notes": "rollback of v3"}


@pytest.mark.asyncio
async def test_agent_rollback_refuses_a_system_preset(client) -> None:
    update = AsyncMock()
    repo = _agent_repo(
        get_by_slug=AsyncMock(return_value=_agent(is_system_preset=True)),
        update_fields_versioned=update,
    )
    with patch.object(r, "get_agent_repository", lambda: repo):
        resp = await client.post(f"{BASE}/agents/ceo/rollback/3")
    assert resp.status_code == 403
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_skill_rollback_parity(client) -> None:
    snap = sample_row(SkillVersions, only=("body_md", "frontmatter_json"))

    def _repos():
        repo = _skill_repo(
            _skill(), update_fields_versioned=AsyncMock(return_value=None)
        )
        return {"get_skill_repository": lambda: repo}

    with (
        patch("app.db.session.read_scope", ScriptedScope([[snap]])),
        patch.multiple(r, **_repos()),
    ):
        raw = await r.rollback_skill(slug="my-skill", version_number=2, auth=AUTH)
    with (
        patch("app.db.session.read_scope", ScriptedScope([[snap]])),
        patch.multiple(r, **_repos()),
    ):
        resp = await client.post(f"{BASE}/skills/my-skill/rollback/2")
    assert_wire_unchanged(resp, raw)
