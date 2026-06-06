"""Integration tests for ApprovalRequestsRepositoryOrm (Phase 2 H batch) vs PG.

THE FROZEN-DATACLASS PARITY PROOF — the INVERSE-uuid case. This repo's dataclass
fields id / user_id / agent_id / decided_by are typed ``uuid.UUID`` (NOT str),
because the legacy ``ApprovalRequest.from_row`` wraps them back via
``UUID(str(...))`` — and the router authz check compares ``existing.user_id !=
user_uuid`` where ``user_uuid`` is a NATIVE UUID. So here str()ing the uuid would
BREAK parity (the opposite of the commitment repo). The ORM reuses the exact
``from_row`` builder via a REST-shaped dict, so the UUID type is restored.

Field parity surface (the dataclass spec):
  - id / user_id / agent_id / decided_by (uuid) → uuid.UUID (native) — AUTHZ:
    router does ``existing.user_id != user_uuid`` (UUID == UUID).
  - session_id / run_id (bigint|NULL) → Optional[str] (builder str()'d).
  - status (text) → native str (router ``status != 'pending'``).
  - payload (jsonb) → native dict.
  - created_at / expires_at / decided_at (tstz) → aware datetime.

Write round-trip (create COMMITS, decide flips pending→terminal w/ defensive
owner filter), v3 date-filter boundary (mark_expired binds a native aware
datetime cutoff), factory on/off.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_approval_requests_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_HOOK_PREFIX = "__test_orm_approval_"


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
async def auth_users(integration_db_url):
    """Two REAL auth.users ids — agent_approval_requests.user_id has a FK →
    auth.users(id) ON DELETE CASCADE. Skips if the DB has fewer than two."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        rows = await conn.fetch("SELECT id FROM auth.users LIMIT 2")
        if len(rows) < 2:
            pytest.skip("need >=2 auth.users rows to satisfy the user_id FK")
        yield rows[0]["id"], rows[1]["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_approvals(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM agent_approval_requests WHERE hook_name LIKE $1",
            _HOOK_PREFIX + "%",
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.approval_requests_repository_orm import (
        ApprovalRequestsRepositoryOrm,
    )

    return ApprovalRequestsRepositoryOrm()


def _hook() -> str:
    return f"{_HOOK_PREFIX}{uuid.uuid4().hex[:8]}"


async def _create(repo, **over):
    base = dict(
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        hook_name=_hook(),
        reason="agent wants to spend $X",
        payload={"amount": 5},
    )
    base.update(over)
    return await repo.create(**base)


# ─── create (COMMIT) + field-by-field dataclass parity ──────────────────


async def test_create_commits_and_field_parity(
    integration_db_url, patched_engine, cleanup_approvals, auth_users
):
    from app.repositories.approval_requests_repository import ApprovalRequest

    user_id, _ = auth_users
    agent_id = uuid.uuid4()
    created = await _create(
        _repo(),
        user_id=user_id,
        agent_id=agent_id,
        session_id="9123456789012345678",
        run_id="9123456789012345679",
    )
    assert isinstance(created, ApprovalRequest)
    # uuid fields → NATIVE UUID (the inverse case — NOT str).
    assert type(created.id) is uuid.UUID
    assert type(created.user_id) is uuid.UUID
    assert created.user_id == user_id
    assert type(created.agent_id) is uuid.UUID
    assert created.agent_id == agent_id
    # bigint session_id / run_id → str (builder str()'d).
    assert type(created.session_id) is str
    assert created.session_id == "9123456789012345678"
    assert type(created.run_id) is str
    # status → native str.
    assert created.status == "pending"
    assert type(created.status) is str
    # payload → native dict.
    assert created.payload == {"amount": 5}
    # timestamps → aware datetime.
    assert isinstance(created.created_at, datetime)
    assert created.created_at.tzinfo is not None
    assert isinstance(created.expires_at, datetime)
    assert created.decided_at is None
    assert created.decided_by is None

    conn = await asyncpg.connect(integration_db_url)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM agent_approval_requests WHERE id = $1", created.id
        )
    finally:
        await conn.close()
    assert n == 1  # committed


async def test_user_id_is_uuid_for_authz_compare(
    patched_engine, cleanup_approvals, auth_users
):
    """The router authz check does ``existing.user_id != user_uuid`` where
    user_uuid is a NATIVE UUID (from _coerce_user_uuid). So user_id MUST be a
    native UUID — a str here would 404 the legitimate owner. (Inverse of the
    commitment repo, where user_id is str.)"""
    user_id, _ = auth_users
    created = await _create(_repo(), user_id=user_id)
    fetched = await _repo().get_by_id(created.id)
    assert fetched is not None
    # The exact router compare against a native UUID:
    assert not (fetched.user_id != user_id)  # UUID == UUID
    assert fetched.user_id == user_id
    assert type(fetched.user_id) is uuid.UUID


# ─── list_pending_for_user ──────────────────────────────────────────────


async def test_list_pending_for_user(patched_engine, cleanup_approvals, auth_users):
    user_id, _ = auth_users
    a = await _create(_repo(), user_id=user_id)
    b = await _create(_repo(), user_id=user_id)
    rows = await _repo().list_pending_for_user(user_id, limit=200)
    ids = {r.id for r in rows}
    assert a.id in ids and b.id in ids
    assert all(r.status == "pending" for r in rows)


# ─── decide (COMMIT) + defensive owner filter ───────────────────────────


async def test_decide_flips_to_approved(patched_engine, cleanup_approvals, auth_users):
    user_id, _ = auth_users
    created = await _create(_repo(), user_id=user_id)
    ok = await _repo().decide(
        created.id, owner_user_id=user_id, approve=True, note="lgtm"
    )
    assert ok is True
    fetched = await _repo().get_by_id(created.id)
    assert fetched.status == "approved"
    assert fetched.decided_by == user_id  # native UUID round-trip
    assert fetched.decision_note == "lgtm"
    assert fetched.decided_at is not None


async def test_decide_defensive_owner_filter_noop_for_intruder(
    patched_engine, cleanup_approvals, auth_users
):
    """A wrong owner_user_id matches no row → the status stays pending. NOTE
    (inert): decide() still returns True (legacy quirk — does NOT check
    rowcount). We assert the DB state is unchanged, not the bool."""
    owner, intruder = auth_users
    created = await _create(_repo(), user_id=owner)
    await _repo().decide(created.id, owner_user_id=intruder, approve=True)
    fetched = await _repo().get_by_id(created.id)
    # The defensive SQL owner filter prevented the cross-user mutation.
    assert fetched.status == "pending"


# ─── mark_expired (v3 native-datetime cutoff) ───────────────────────────


async def test_mark_expired_boundary(
    integration_db_url, patched_engine, cleanup_approvals, auth_users
):
    user_id, _ = auth_users
    # Create one pending row, then back-date its expires_at into the past via a
    # direct UPDATE (create computes a future expires_at = now + ttl).
    created = await _create(_repo(), user_id=user_id)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "UPDATE agent_approval_requests SET expires_at = now() - interval "
            "'1 hour' WHERE id = $1",
            created.id,
        )
    finally:
        await conn.close()

    # expires_at in the future → must NOT expire
    fresh = await _create(_repo(), user_id=user_id)

    now = datetime.now(timezone.utc)
    count = await _repo().mark_expired(now=now)
    assert count >= 1

    expired = await _repo().get_by_id(created.id)
    assert expired.status == "expired"
    still_pending = await _repo().get_by_id(fresh.id)
    assert still_pending.status == "pending"


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.approval_requests_repository import (
        ApprovalRequestsRepository,
        get_approval_requests_repository,
    )

    with patch("app.core.config.settings.USE_ORM_APPROVAL", False):
        assert type(get_approval_requests_repository()) is ApprovalRequestsRepository


def test_factory_on_returns_orm():
    from unittest.mock import patch

    from app.repositories.approval_requests_repository_orm import (
        ApprovalRequestsRepositoryOrm,
    )

    with (
        patch("app.core.config.settings.USE_ORM_APPROVAL", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.approval_requests_repository import (
            get_approval_requests_repository,
        )

        assert type(get_approval_requests_repository()) is ApprovalRequestsRepositoryOrm
