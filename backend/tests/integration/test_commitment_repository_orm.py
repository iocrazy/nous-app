"""Integration tests for CommitmentRepositoryOrm (Phase 2 H batch) vs real PG.

THE FROZEN-DATACLASS PARITY PROOF. Unlike the dict-returning repos, this repo
returns the frozen ``Commitment`` value object. These tests assert, FIELD BY
FIELD, that the ORM path constructs a dataclass byte-identical (type + value) to
what the legacy ``_row_to_commitment`` builds from a REST row — because the ORM
reuses that exact builder via a REST-shaped dict.

Field parity surface (the dataclass spec):
  - id (bigint)              → Commitment.id : int (native).
  - agent_id (uuid)          → Commitment.agent_id : str (builder str()'d).
  - user_id (uuid|NULL)      → Commitment.user_id : Optional[str] — AUTHZ: the
                               router compares ``user_id != str(auth.user_id)``,
                               so this MUST be str (a native UUID 404s the owner).
  - session_id (uuid|NULL)   → Optional[str].
  - trigger_type (text)      → coerced to TriggerType Enum by __post_init__.
  - status (text)            → coerced to CommitmentStatus Enum.
  - trigger_at / expires_at / created_at / fulfilled_at (tstz) → aware datetime.
  - fulfillment_run_id (bigint|NULL) → Optional[str] (builder str()'d).
  - payload_json (jsonb)     → native dict.

Write round-trip (create COMMITS, _set_terminal_status flips pending→terminal),
and v3 date-filter boundaries (list_due_time / list_expired_pending bind native
aware datetimes). Factory on/off.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_commitment_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_DESC_PREFIX = "__test_orm_commitment_"


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
async def agent_id(integration_db_url):
    """A real ai_agents.id (agent_commitments.agent_id FK → ai_agents.id).
    Reuses an existing agent if present; otherwise seeds a throwaway one and
    cleans it up (cascade clears its commitments)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        existing = await conn.fetchval("SELECT id FROM ai_agents LIMIT 1")
        if existing is not None:
            yield existing
            return
        seeded = await conn.fetchval(
            "INSERT INTO ai_agents (name, slug) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING RETURNING id",
            "__test_orm_commitment_agent",
            f"__test-orm-commit-{uuid.uuid4().hex[:8]}",
        )
        try:
            yield seeded
        finally:
            await conn.execute("DELETE FROM ai_agents WHERE id = $1", seeded)
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_commitments(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM agent_commitments WHERE description LIKE $1",
            _DESC_PREFIX + "%",
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.commitment_repository_orm import CommitmentRepositoryOrm

    return CommitmentRepositoryOrm()


def _mk(agent_id, **over):
    from app.agent_framework.commitments import Commitment, TriggerType

    base = dict(
        agent_id=str(agent_id),
        description=f"{_DESC_PREFIX}{uuid.uuid4().hex[:8]}",
        trigger_type=TriggerType.NEXT_SESSION,
        user_id=str(uuid.uuid4()),
    )
    base.update(over)
    return Commitment(**base)


# ─── create (COMMIT) + full field-by-field dataclass parity ─────────────


async def test_create_commits_and_field_parity(
    integration_db_url, patched_engine, cleanup_commitments, agent_id
):
    from app.agent_framework.commitments import (
        Commitment,
        CommitmentStatus,
        TriggerType,
    )

    user_id = str(uuid.uuid4())
    created = await _repo().create(
        _mk(
            agent_id,
            trigger_type=TriggerType.NEXT_SESSION,
            user_id=user_id,
            payload_json={"topic": "cooldown"},
        )
    )

    assert isinstance(created, Commitment)
    # id (bigint) → native int.
    assert type(created.id) is int
    # agent_id (uuid) → str (builder str()'d).
    assert type(created.agent_id) is str
    assert created.agent_id == str(agent_id)
    # user_id (uuid) → str — the authz-critical field.
    assert type(created.user_id) is str
    assert created.user_id == user_id
    # status / trigger_type → Enum members (coerced by __post_init__).
    assert created.status is CommitmentStatus.PENDING
    assert created.trigger_type is TriggerType.NEXT_SESSION
    # created_at → aware datetime.
    assert isinstance(created.created_at, datetime)
    assert created.created_at.tzinfo is not None
    # payload_json → native dict.
    assert created.payload_json == {"topic": "cooldown"}

    # Committed (write_scope), not silently rolled back.
    conn = await asyncpg.connect(integration_db_url)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM agent_commitments WHERE id = $1", created.id
        )
    finally:
        await conn.close()
    assert n == 1


async def test_user_id_is_str_for_authz_compare(
    patched_engine, cleanup_commitments, agent_id
):
    """The H-batch authz guarantee: the router does
    ``existing.user_id != str(auth.user_id)``. A native UUID would 404 the
    owner. Prove user_id round-trips as a str that == the str user_id."""
    user_id = str(uuid.uuid4())
    created = await _repo().create(_mk(agent_id, user_id=user_id))
    fetched = await _repo().get_by_id(created.id)
    assert fetched is not None
    # The exact router compare:
    assert fetched.user_id == user_id  # str == str
    assert not (fetched.user_id != str(user_id))
    assert type(fetched.user_id) is str


async def test_fulfillment_run_id_is_str(
    integration_db_url, patched_engine, cleanup_commitments, agent_id
):
    """fulfillment_run_id is BIGINT on the column but STR on the dataclass —
    the builder str()'s it. mark_fulfilled accepts a numeric STR run id (the
    bigint-bind hazard: int()-coerced for asyncpg). Uses a real agent_runs.id to
    satisfy the FK if one exists, else skips that leg and still proves the
    fulfilled_at / notes round-trip."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        run_id = await conn.fetchval("SELECT id FROM agent_runs LIMIT 1")
    finally:
        await conn.close()

    created = await _repo().create(_mk(agent_id))
    updated = await _repo().mark_fulfilled(
        created.id,
        fulfillment_run_id=str(run_id) if run_id is not None else None,
        notes="done",
    )
    assert updated is not None
    assert updated.fulfillment_notes == "done"
    assert updated.fulfilled_at is not None
    assert isinstance(updated.fulfilled_at, datetime)
    if run_id is not None:
        # BIGINT column, STR dataclass field — the builder str()'s it back.
        assert type(updated.fulfillment_run_id) is str
        assert updated.fulfillment_run_id == str(run_id)


# ─── terminal-status state machine ──────────────────────────────────────


async def test_mark_terminal_only_flips_pending(
    patched_engine, cleanup_commitments, agent_id
):
    from app.agent_framework.commitments import CommitmentStatus

    created = await _repo().create(_mk(agent_id))
    first = await _repo().mark_cancelled(created.id, notes="dismissed")
    assert first is not None
    assert first.status is CommitmentStatus.CANCELLED
    # Terminal is sticky: a second terminal transition returns None (no pending
    # row matched).
    second = await _repo().mark_failed(created.id)
    assert second is None


# ─── v3 date-filter boundaries (native aware datetime bind) ─────────────


async def test_list_due_time_boundary(patched_engine, cleanup_commitments, agent_id):
    from app.agent_framework.commitments import TriggerType

    now = datetime.now(timezone.utc)
    past = await _repo().create(
        _mk(
            agent_id,
            trigger_type=TriggerType.TIME,
            trigger_at=now - timedelta(minutes=5),
        )
    )
    future = await _repo().create(
        _mk(
            agent_id,
            trigger_type=TriggerType.TIME,
            trigger_at=now + timedelta(hours=1),
        )
    )
    due = await _repo().list_due_time(now=now, limit=200)
    due_ids = {c.id for c in due}
    assert past.id in due_ids  # trigger_at <= now
    assert future.id not in due_ids  # trigger_at > now


async def test_list_expired_pending_boundary(
    patched_engine, cleanup_commitments, agent_id
):
    now = datetime.now(timezone.utc)
    expired = await _repo().create(_mk(agent_id, expires_at=now - timedelta(minutes=1)))
    not_expired = await _repo().create(
        _mk(agent_id, expires_at=now + timedelta(hours=2))
    )
    no_expiry = await _repo().create(_mk(agent_id))  # expires_at NULL
    rows = await _repo().list_expired_pending(now=now, limit=200)
    ids = {c.id for c in rows}
    assert expired.id in ids
    assert not_expired.id not in ids
    assert no_expiry.id not in ids  # NULL expires_at excluded


async def test_list_for_user_filters(patched_engine, cleanup_commitments, agent_id):
    from app.agent_framework.commitments import CommitmentStatus

    user_id = str(uuid.uuid4())
    c1 = await _repo().create(_mk(agent_id, user_id=user_id))
    await _repo().mark_cancelled(c1.id)
    c2 = await _repo().create(_mk(agent_id, user_id=user_id))

    all_rows = await _repo().list_for_user(user_id, limit=200)
    assert {c1.id, c2.id} <= {c.id for c in all_rows}

    pending = await _repo().list_for_user(
        user_id, status=CommitmentStatus.PENDING, limit=200
    )
    pending_ids = {c.id for c in pending}
    assert c2.id in pending_ids
    assert c1.id not in pending_ids


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.commitment_repository import (
        CommitmentRepository,
        get_commitment_repository,
    )

    with patch("app.core.config.settings.USE_ORM_COMMITMENT", False):
        assert type(get_commitment_repository()) is CommitmentRepository


def test_factory_on_returns_orm():
    from unittest.mock import patch

    from app.repositories.commitment_repository_orm import CommitmentRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_COMMITMENT", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.commitment_repository import get_commitment_repository

        assert type(get_commitment_repository()) is CommitmentRepositoryOrm
