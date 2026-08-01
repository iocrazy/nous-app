"""GET /api/v1/issues/needs-input — Task Center 'agent waiting on your
answer' feed (Spec-4 needs_input first-class, Task 2).

Filtering (status='needs_followup' AND execution_state.agent_outcome ==
'needs_input', D6.1 team visibility fold) happens in SQL inside
``IssueRepository.list_needs_input`` — so the three brief scenarios are
proven against a REAL Postgres (gated by INTEGRATION_DATABASE_URL, same
pattern as tests/integration/test_issue_repository_orm.py) rather than a
mocked repository, which would only prove the router echoes whatever it's
handed. The router's OWN logic (question/asked_at/id extraction + BIGINT
str-serialization) is covered by a lighter router-only test with the
repository faked, matching this directory's usual convention (see
test_project_assets_router.py).
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_TITLE_PREFIX = "__test_needs_input_"


# ─── real-DB fixtures (mirrors tests/integration/test_issue_repository_orm.py) ──


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def issue_seq(integration_db_url):
    """issue_create_atomic RAISEs without the 'global' counter row."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        existed = await conn.fetchval(
            "SELECT count(*) FROM issue_sequence WHERE scope = 'global'"
        )
        if not existed:
            await conn.execute(
                "INSERT INTO issue_sequence (scope, counter, prefix) "
                "VALUES ('global', 0, 'MH') ON CONFLICT DO NOTHING"
            )
        yield
    finally:
        await conn.close()


@pytest.fixture
async def teams_and_users(integration_db_url):
    """Two teams (A/B) and two auth.users: ME is a member of team A only.
    _OTHER team's issue must never leak to ME (D6.1 铁边界)."""
    conn = await asyncpg.connect(integration_db_url)
    me = uuid.uuid4()
    other_user = uuid.uuid4()
    team_a = None
    team_b = None
    try:
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2), ($3, $4)",
            me,
            f"{me.hex[:8]}@test.local",
            other_user,
            f"{other_user.hex[:8]}@test.local",
        )
        team_a = await conn.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) "
            "VALUES ($1, $2, $3) RETURNING id",
            "__test_team_a__",
            me,
            f"ta{uuid.uuid4().hex[:10]}",
        )
        team_b = await conn.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) "
            "VALUES ($1, $2, $3) RETURNING id",
            "__test_team_b__",
            other_user,
            f"tb{uuid.uuid4().hex[:10]}",
        )
        # teams_add_owner_trigger (add_owner_as_member) already inserted ME /
        # other_user into team_members as owner of their own team — no manual
        # INSERT needed (and one would 23505 duplicate-key against it).
        yield {"me": me, "other_user": other_user, "team_a": team_a, "team_b": team_b}
    finally:
        if team_a is not None:
            await conn.execute("DELETE FROM teams WHERE id = $1", team_a)
        if team_b is not None:
            await conn.execute("DELETE FROM teams WHERE id = $1", team_b)
        await conn.execute("DELETE FROM auth.users WHERE id = ANY($1)", [me, other_user])
        await conn.close()


@pytest.fixture
async def cleanup_issues(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM issues WHERE title LIKE $1", _TITLE_PREFIX + "%"
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.issue_repository import IssueRepository

    return IssueRepository()


def _title() -> str:
    return f"{_TITLE_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── the three brief scenarios ──────────────────────────────────────────


@pytest.mark.integration
async def test_needs_input_row_appears_with_question_and_asked_at(
    patched_engine, issue_seq, teams_and_users, cleanup_issues
):
    me = str(teams_and_users["me"])
    team_a = teams_and_users["team_a"]
    created = await _repo().atomic_create(
        {
            "title": _title(),
            "created_by_user_id": me,
            "team_id": team_a,
            "status": "needs_followup",
        }
    )
    # execution_state / dbos_workflow_id are service_role-write-allowlisted
    # (mig-170) — write via the same SET LOCAL ROLE helper the router uses.
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        "UPDATE public.issues SET execution_state = CAST(:state AS jsonb) "
        "WHERE id = :iid",
        {
            "state": '{"agent_outcome": "needs_input", '
            '"outcome_reason": "which color scheme?"}',
            "iid": created["id"],
        },
    )

    rows = await _repo().list_needs_input(me)
    match = next((r for r in rows if r["id"] == created["id"]), None)
    assert match is not None, "needs_input row must appear in the list"
    assert match["execution_state"]["outcome_reason"] == "which color scheme?"
    assert match["updated_at"] is not None


@pytest.mark.integration
async def test_plain_needs_followup_without_agent_outcome_excluded(
    patched_engine, issue_seq, teams_and_users, cleanup_issues
):
    """A needs_followup issue with no pending agent_outcome (e.g. never
    reached that state, or already resumed) must NOT show up — the human
    isn't being asked anything."""
    me = str(teams_and_users["me"])
    team_a = teams_and_users["team_a"]
    created = await _repo().atomic_create(
        {
            "title": _title(),
            "created_by_user_id": me,
            "team_id": team_a,
            "status": "needs_followup",
        }
    )
    rows = await _repo().list_needs_input(me)
    assert all(r["id"] != created["id"] for r in rows)


@pytest.mark.integration
async def test_empty_output_agent_outcome_excluded(
    patched_engine, issue_seq, teams_and_users, cleanup_issues
):
    """I3 (final review): an EMPTY_OUTPUT stall (agent_outcome='empty_output',
    set so a reply CAN resume it — see route_finish_outcome) must not appear
    in the needs-your-answer feed — the agent produced nothing, it never
    actually asked the human a question. Only the literal 'needs_input'
    value belongs in this list."""
    me = str(teams_and_users["me"])
    team_a = teams_and_users["team_a"]
    created = await _repo().atomic_create(
        {
            "title": _title(),
            "created_by_user_id": me,
            "team_id": team_a,
            "status": "needs_followup",
        }
    )
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        "UPDATE public.issues SET execution_state = CAST(:state AS jsonb) "
        "WHERE id = :iid",
        {
            "state": '{"agent_outcome": "empty_output", '
            '"outcome_reason": "Agent produced no output (EMPTY_OUTPUT)"}',
            "iid": created["id"],
        },
    )

    rows = await _repo().list_needs_input(me)
    assert all(r["id"] != created["id"] for r in rows)


@pytest.mark.integration
async def test_other_team_issue_excluded(
    patched_engine, issue_seq, teams_and_users, cleanup_issues
):
    """D6.1 铁边界: a needs_input issue on a team ME doesn't belong to (and
    didn't create/get assigned) must never leak into ME's list."""
    other_user = str(teams_and_users["other_user"])
    team_b = teams_and_users["team_b"]
    me = str(teams_and_users["me"])
    created = await _repo().atomic_create(
        {
            "title": _title(),
            "created_by_user_id": other_user,
            "team_id": team_b,
            "status": "needs_followup",
        }
    )
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        "UPDATE public.issues SET execution_state = CAST(:state AS jsonb) "
        "WHERE id = :iid",
        {
            "state": '{"agent_outcome": "needs_input", "outcome_reason": "x"}',
            "iid": created["id"],
        },
    )

    rows = await _repo().list_needs_input(me)
    assert all(r["id"] != created["id"] for r in rows)


# ─── router-shape test (repo faked — matches this dir's usual convention) ──


async def test_router_maps_repo_row_to_response_shape(monkeypatch):
    """Router-only: given a repo row shaped like list_needs_input would
    return, the endpoint extracts question/asked_at/ids as str per the
    BIGINT-precision convention. No DB involved — the repo call is faked."""
    import importlib

    issues_router = importlib.import_module("app.api.issues_router")

    async def _fake_list(user_id, *, limit=50):
        assert user_id == "11111111-1111-4111-8111-111111111111"
        return [
            {
                "id": 4242,
                "title": "Need clarification",
                "project_id": 55,
                "team_id": 42,
                "updated_at": "2026-08-01T10:00:00+00:00",
                "execution_state": {
                    "agent_outcome": "needs_input",
                    "outcome_reason": "which color scheme?",
                },
            }
        ]

    monkeypatch.setattr(issues_router.issue_repository, "list_needs_input", _fake_list)

    class _Auth:
        user_id = "11111111-1111-4111-8111-111111111111"

    resp = await issues_router.list_needs_input(_Auth())
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.issue_id == "4242"
    assert item.project_id == "55"
    assert item.team_id == "42"
    assert item.question == "which color scheme?"
