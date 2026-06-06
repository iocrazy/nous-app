"""Integration tests for IssueRepositoryOrm (Phase 2 M batch) against real PG.

THE M-BATCH UUID HOT SPOT. Proves the REST → ORM swap is invisible AND that
STRATEGY-C value-type parity holds on issues — with a dedicated test that the
``created_by_user_id`` / ``assignee_user_id`` columns are returned as STR so the
three app-layer authz compares (``row["created_by_user_id"] == str(user_id)``)
keep matching. A native uuid.UUID there is a SILENT KILLER: it compares unequal
to the str user_id forever, with no error and no log → 404/4001 for the
legitimate owner.

Parity surface:
  - created_by_user_id / assignee_user_id (uuid) → STR (REQUIRED — authz ==).
  - created_by_agent_id / assignee_agent_id (uuid) → STR (shape parity).
  - id / issue_number / team_id / project_id / ai_session_id (bigint) → native
    int (the 5.3 trap).
  - status / priority / identifier (Text, NOT Enum) → native str.
  - created_at / updated_at / started_at / ... (timestamptz) → ISO str.
  - execution_state (jsonb) → native dict.
  - atomic_create keeps counter-UPDATE + INSERT atomic via the same
    issue_create_atomic SECURITY DEFINER proc, inside write_scope().

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_issue_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_TITLE_PREFIX = "__test_orm_issue_"


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
    """Ensure the issue_sequence 'global' row exists (issue_create_atomic
    RAISEs without it). Leaves it in place if it already existed."""
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
async def auth_users(integration_db_url):
    """Yield two REAL auth.users ids (issues.created_by_user_id /
    assignee_user_id FK auth.users). Skips if the DB has fewer than two."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        rows = await conn.fetch("SELECT id FROM auth.users LIMIT 2")
        if len(rows) < 2:
            pytest.skip("need >=2 auth.users rows to satisfy creator/assignee FKs")
        yield rows[0]["id"], rows[1]["id"]
    finally:
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
    from app.repositories.issue_repository_orm import IssueRepositoryOrm

    return IssueRepositoryOrm()


def _title() -> str:
    return f"{_TITLE_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── atomic_create (COMMIT + atomic proc) ───────────────────────────────


async def test_atomic_create_commit_and_parity(
    integration_db_url, patched_engine, issue_seq, cleanup_issues, auth_users
):
    """atomic_create goes through the issue_create_atomic proc, COMMITS, and
    returns a parity dict: bigint ids native int, uuids str, ts ISO str."""
    creator, _assignee = auth_users
    created = await _repo().atomic_create(
        {
            "title": _title(),
            "created_by_user_id": str(creator),
            "status": "backlog",
            "priority": "medium",
        }
    )
    assert created is not None
    assert type(created["id"]) is int  # bigint id stays int (5.3 trap)
    assert type(created["issue_number"]) is int
    assert isinstance(created["identifier"], str) and created["identifier"].startswith(
        "MH-"
    )
    # uuid → str (the authz hot-spot column).
    assert type(created["created_by_user_id"]) is str
    assert created["created_by_user_id"] == str(creator)
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT count(*) FROM issues WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == 1  # committed (proc + write_scope, no silent rollback)


# ─── ★ THE SILENT-KILLER PROOF: uuid columns must be str for authz == ───


async def test_uuid_authz_columns_are_str_for_visibility_compare(
    patched_engine, issue_seq, cleanup_issues, auth_users
):
    """The CORE M-batch guarantee. The three app-layer authz checks do:

        user_id = str(auth.user_id)
        if row["created_by_user_id"] == user_id: ...

    If the ORM returned a native uuid.UUID, that == is ALWAYS False (silent
    404/4001 for the owner). Prove every read path returns these columns as STR
    so the == still matches."""
    creator, assignee = auth_users
    created = await _repo().atomic_create(
        {
            "title": _title(),
            "created_by_user_id": str(creator),
            "assignee_user_id": str(assignee),
        }
    )
    issue_id = created["id"]

    # Simulate the router's exact compare against a STRING user_id.
    creator_str = str(creator)
    assignee_str = str(assignee)

    for getter in (
        lambda: _repo().get_by_id(issue_id),
        lambda: _repo().get_by_identifier(created["identifier"]),
    ):
        row = await getter()
        assert row is not None
        # The exact silent-killer compare from _assert_visibility / ws_router:
        assert row["created_by_user_id"] == creator_str
        assert row["assignee_user_id"] == assignee_str
        # ws_router does `user_id in (a, b)` — prove that membership holds too.
        assert creator_str in (
            row.get("created_by_user_id"),
            row.get("assignee_user_id"),
        )
        # Agent-id columns are str for shape parity (None here).
        assert row["created_by_agent_id"] is None
        assert row["assignee_agent_id"] is None
        # Type assertions: these are str, not uuid.UUID.
        assert type(row["created_by_user_id"]) is str
        assert type(row["assignee_user_id"]) is str

    # list_for_user (own OR assignee) returns the same str shape.
    items, total = await _repo().list_for_user(creator_str)
    assert total >= 1
    assert any(i["created_by_user_id"] == creator_str for i in items)
    # And visible to the assignee too.
    a_items, a_total = await _repo().list_for_user(assignee_str)
    assert a_total >= 1
    assert any(i["assignee_user_id"] == assignee_str for i in a_items)


# ─── update / transition (COMMIT) ───────────────────────────────────────


async def test_update_commit(
    integration_db_url, patched_engine, issue_seq, cleanup_issues, auth_users
):
    creator, _assignee = auth_users
    created = await _repo().atomic_create(
        {"title": _title(), "created_by_user_id": str(creator)}
    )
    updated = await _repo().update(created["id"], {"description": "new desc"})
    assert updated["description"] == "new desc"
    # uuid str parity preserved on the update RETURNING row too.
    assert updated["created_by_user_id"] == str(creator)

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT description FROM issues WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == "new desc"


async def test_update_not_found_raises(patched_engine):
    with pytest.raises(ValueError):
        await _repo().update(1, {"description": "x"})


async def test_transition_status_sets_lifecycle_ts(
    patched_engine, issue_seq, cleanup_issues, auth_users
):
    creator, _assignee = auth_users
    created = await _repo().atomic_create(
        {"title": _title(), "created_by_user_id": str(creator)}
    )
    transitioned = await _repo().transition_status(created["id"], "in_progress")
    assert transitioned["status"] == "in_progress"
    assert transitioned["started_at"] is not None
    assert type(transitioned["started_at"]) is str  # ts → ISO str


async def test_soft_delete_sets_hidden_at(
    patched_engine, issue_seq, cleanup_issues, auth_users
):
    creator, _assignee = auth_users
    created = await _repo().atomic_create(
        {"title": _title(), "created_by_user_id": str(creator)}
    )
    deleted = await _repo().soft_delete(created["id"])
    assert deleted["hidden_at"] is not None
    # Hidden issues are excluded from the default list.
    items, _ = await _repo().list_for_user(str(creator))
    assert all(i["id"] != created["id"] for i in items)
    items_h, _ = await _repo().list_for_user(str(creator), include_hidden=True)
    assert any(i["id"] == created["id"] for i in items_h)


async def test_list_for_user_validates_uuid(patched_engine):
    with pytest.raises(ValueError):
        await _repo().list_for_user("not-a-uuid")


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.issue_repository import (
        IssueRepository,
        get_issue_repository,
    )

    with patch("app.core.config.settings.USE_ORM_ISSUE", False):
        assert type(get_issue_repository()) is IssueRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.issue_repository_orm import IssueRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_ISSUE", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.issue_repository import get_issue_repository

        assert type(get_issue_repository()) is IssueRepositoryOrm
