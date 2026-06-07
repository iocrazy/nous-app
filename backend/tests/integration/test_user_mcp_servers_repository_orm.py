"""Integration tests for UserMCPServersRepositoryOrm against real PG.

The public return shape is the frozen dataclass ``UserMCPServer`` with:
  - ``id`` / ``user_id`` as native ``uuid.UUID`` OBJECTS (strategy-C);
  - ``bearer_token`` DECRYPTED at read (encrypt-at-write + decrypt-at-read
    via secret_box — P7 compliance verified end-to-end);
  - M3 defense-in-depth: update / delete WHERE user_id == owner_user_id.

Tests pin:
  - bearer_token round-trips: plaintext in → encrypted in DB → decrypted on
    read → same plaintext out (proves encrypt-at-write + decrypt-at-read);
  - list_for_user only_enabled filter (enabled=False rows NOT returned);
  - get_by_id happy path + miss returns None;
  - update partial-patch semantics (only provided fields mutated);
  - update with wrong owner_user_id does NOT mutate the row (M3 SQL filter);
  - delete with correct owner removes the row;
  - returned objects are UserMCPServer instances with uuid.UUID id/user_id;
  - factory returns ORM impl when flag=True + engine configured (flag parity).

Setup: requires ``INTEGRATION_DATABASE_URL``. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_user_mcp_servers_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


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
async def test_user(integration_db_url):
    """A real auth.users id — required because user_mcp_servers.user_id is
    a FK → auth.users(id) ON DELETE CASCADE. Row and all MCP servers for
    this user are cleaned up after each test."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if not uid:
            pytest.skip("No auth.users rows — cannot satisfy FK for user_mcp_servers")
        # Start clean — remove any leftover servers for this user.
        await conn.execute("DELETE FROM user_mcp_servers WHERE user_id = $1", uid)
    finally:
        await conn.close()
    yield uid
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM user_mcp_servers WHERE user_id = $1", uid)
    finally:
        await conn.close()


def _repo():
    from app.repositories.user_mcp_servers_repository_orm import (
        UserMCPServersRepositoryOrm,
    )

    return UserMCPServersRepositoryOrm()


# ─── Strategy-C: return type is UserMCPServer with uuid.UUID fields ────────


async def test_returned_type_is_user_mcp_server_dataclass(
    integration_db_url, patched_engine, test_user
):
    """create() returns a UserMCPServer with uuid.UUID id and user_id."""
    from app.repositories.user_mcp_servers_repository import UserMCPServer

    repo = _repo()
    created = await repo.create(
        user_id=uuid.UUID(str(test_user)),
        name="myserver",
        url="https://mcp.example.com/jsonrpc",
    )

    assert isinstance(created, UserMCPServer)
    assert isinstance(created.id, uuid.UUID)
    assert isinstance(created.user_id, uuid.UUID)
    assert created.user_id == uuid.UUID(str(test_user))
    assert created.name == "myserver"
    assert created.url == "https://mcp.example.com/jsonrpc"
    assert created.enabled is True
    assert created.bearer_token is None
    assert created.description is None


# ─── P7: bearer_token encrypt-at-write / decrypt-at-read ──────────────────


async def test_bearer_token_roundtrips(integration_db_url, patched_engine, test_user):
    """Plaintext token in → encrypted in DB → decrypted on read == original.

    This is the core P7 compliance test: proves that create() encrypts and
    get_by_id() returns the original plaintext via from_row's decrypt path.
    """
    plaintext = "super-secret-bearer-token-42"
    repo = _repo()
    created = await repo.create(
        user_id=uuid.UUID(str(test_user)),
        name="tokensrv",
        url="https://mcp.example.com/jsonrpc",
        bearer_token=plaintext,
    )

    assert created is not None
    # The returned object from create() is built via from_row after RETURNING —
    # so it should already be decrypted.
    assert created.bearer_token == plaintext

    # Independently verify: DB stores it ENCRYPTED (not plaintext).
    conn = await asyncpg.connect(integration_db_url)
    try:
        stored = await conn.fetchval(
            "SELECT bearer_token FROM user_mcp_servers WHERE id = $1",
            created.id,
        )
    finally:
        await conn.close()

    assert stored is not None
    assert stored != plaintext, "DB must store ciphertext, not plaintext"
    assert stored.startswith("g"), "Fernet ciphertext starts with base64 char"

    # Re-read via get_by_id also decrypts correctly.
    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.bearer_token == plaintext


async def test_update_bearer_token_encrypted_in_db(
    integration_db_url, patched_engine, test_user
):
    """update() also encrypts the new bearer_token at write."""
    original_token = "original-token"
    new_token = "updated-token-xyz"
    repo = _repo()
    created = await repo.create(
        user_id=uuid.UUID(str(test_user)),
        name="updatesrv",
        url="https://mcp.example.com/jsonrpc",
        bearer_token=original_token,
    )

    ok = await repo.update(
        created.id,
        owner_user_id=uuid.UUID(str(test_user)),
        bearer_token=new_token,
    )
    assert ok is True

    # DB must store new ciphertext (not plaintext).
    conn = await asyncpg.connect(integration_db_url)
    try:
        stored = await conn.fetchval(
            "SELECT bearer_token FROM user_mcp_servers WHERE id = $1",
            created.id,
        )
    finally:
        await conn.close()

    assert stored != new_token, "DB must store new ciphertext, not plaintext"

    # get_by_id decrypts the updated token.
    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.bearer_token == new_token


# ─── list_for_user: only_enabled filter ────────────────────────────────────


async def test_list_for_user_only_enabled_filter(
    integration_db_url, patched_engine, test_user
):
    """only_enabled=True (default) excludes disabled rows; =False includes all."""
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    await repo.create(
        user_id=uid,
        name="enabled_srv",
        url="https://mcp.example.com/jsonrpc",
        enabled=True,
    )
    await repo.create(
        user_id=uid,
        name="disabled_srv",
        url="https://mcp.example.com/jsonrpc",
        enabled=False,
    )

    enabled_only = await repo.list_for_user(uid, only_enabled=True)
    names_enabled = [r.name for r in enabled_only]
    assert "enabled_srv" in names_enabled
    assert "disabled_srv" not in names_enabled

    all_servers = await repo.list_for_user(uid, only_enabled=False)
    names_all = [r.name for r in all_servers]
    assert "enabled_srv" in names_all
    assert "disabled_srv" in names_all

    # All returned items are UserMCPServer with uuid.UUID id/user_id.
    from app.repositories.user_mcp_servers_repository import UserMCPServer

    for row in all_servers:
        assert isinstance(row, UserMCPServer)
        assert isinstance(row.id, uuid.UUID)
        assert isinstance(row.user_id, uuid.UUID)


async def test_list_for_user_ordered_by_name(
    integration_db_url, patched_engine, test_user
):
    """list_for_user returns rows ordered by name."""
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    await repo.create(user_id=uid, name="zebra", url="https://mcp.example.com/jsonrpc")
    await repo.create(user_id=uid, name="alpha", url="https://mcp.example.com/jsonrpc")

    rows = await repo.list_for_user(uid, only_enabled=False)
    names = [r.name for r in rows]
    assert names == sorted(names), f"Expected alphabetical order, got {names}"


# ─── get_by_id ────────────────────────────────────────────────────────────


async def test_get_by_id_happy_path(integration_db_url, patched_engine, test_user):
    """get_by_id returns the correct row."""
    repo = _repo()
    created = await repo.create(
        user_id=uuid.UUID(str(test_user)),
        name="fetchme",
        url="https://mcp.example.com/jsonrpc",
        description="test description",
    )

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "fetchme"
    assert fetched.description == "test description"


async def test_get_by_id_missing_returns_none(
    integration_db_url, patched_engine, test_user
):
    """get_by_id returns None for a non-existent id."""
    repo = _repo()
    result = await repo.get_by_id(uuid.uuid4())
    assert result is None


# ─── update: partial-patch semantics ────────────────────────────────────────


async def test_update_partial_patch_only_mutates_provided_fields(
    integration_db_url, patched_engine, test_user
):
    """update() with only url= leaves description unchanged."""
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    created = await repo.create(
        user_id=uid,
        name="patchsrv",
        url="https://mcp.example.com/jsonrpc",
        description="keep this",
        enabled=True,
    )

    ok = await repo.update(
        created.id,
        owner_user_id=uid,
        url="https://mcp.newurl.com/jsonrpc",
    )
    assert ok is True

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.url == "https://mcp.newurl.com/jsonrpc"
    assert fetched.description == "keep this"  # not mutated
    assert fetched.enabled is True  # not mutated


async def test_update_no_fields_returns_false(
    integration_db_url, patched_engine, test_user
):
    """update() with no fields provided returns False without touching the DB."""
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    created = await repo.create(
        user_id=uid, name="nopatch", url="https://mcp.example.com/jsonrpc"
    )

    result = await repo.update(created.id, owner_user_id=uid)
    assert result is False


# ─── M3: update with wrong owner_user_id does NOT mutate ──────────────────


async def test_update_wrong_owner_does_not_mutate(
    integration_db_url, patched_engine, test_user
):
    """M3: update() with a different owner_user_id must not modify the row.

    The SQL WHERE user_id == owner_user_id clause prevents cross-user mutation.
    The ORM impl returns True (no exception) but the row is unchanged because
    the WHERE filter matched zero rows.
    """
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    wrong_owner = uuid.uuid4()

    created = await repo.create(
        user_id=uid,
        name="protectedsrv",
        url="https://mcp.example.com/jsonrpc",
        enabled=True,
    )
    original_url = created.url

    # Update with wrong owner — the SQL WHERE will match zero rows.
    await repo.update(
        created.id,
        owner_user_id=wrong_owner,  # NOT the real owner
        url="https://mcp.hacked.com/jsonrpc",
    )

    # Row must be unchanged (direct asyncpg read bypasses the ORM).
    conn = await asyncpg.connect(integration_db_url)
    try:
        stored_url = await conn.fetchval(
            "SELECT url FROM user_mcp_servers WHERE id = $1", created.id
        )
    finally:
        await conn.close()

    assert (
        stored_url == original_url
    ), "M3 VIOLATION: update() with wrong owner mutated the row"


# ─── delete ───────────────────────────────────────────────────────────────


async def test_delete_removes_row(integration_db_url, patched_engine, test_user):
    """delete() with correct owner removes the row."""
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    created = await repo.create(
        user_id=uid, name="deleteme", url="https://mcp.example.com/jsonrpc"
    )

    ok = await repo.delete(created.id, owner_user_id=uid)
    assert ok is True

    fetched = await repo.get_by_id(created.id)
    assert fetched is None


async def test_delete_wrong_owner_does_not_remove(
    integration_db_url, patched_engine, test_user
):
    """M3: delete() with wrong owner_user_id does NOT remove the row."""
    repo = _repo()
    uid = uuid.UUID(str(test_user))
    wrong_owner = uuid.uuid4()

    created = await repo.create(
        user_id=uid, name="keepme", url="https://mcp.example.com/jsonrpc"
    )

    ok = await repo.delete(created.id, owner_user_id=wrong_owner)
    # Returns True (no exception) but the row must still exist.
    assert ok is True

    # Verify row is still present.
    conn = await asyncpg.connect(integration_db_url)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM user_mcp_servers WHERE id = $1", created.id
        )
    finally:
        await conn.close()

    assert count == 1, "M3 VIOLATION: delete() with wrong owner removed the row"


# ─── Factory flag parity ───────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    """Flag=False → REST UserMCPServersRepository (not ORM)."""
    from app.core.config import settings
    from app.repositories import user_mcp_servers_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_USER_MCP_SERVERS", False)
    repo = mod.get_user_mcp_servers_repository()
    assert type(repo) is mod.UserMCPServersRepository

    from app.repositories.user_mcp_servers_repository_orm import (
        UserMCPServersRepositoryOrm,
    )

    assert not isinstance(repo, UserMCPServersRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    """Flag=True + engine configured → ORM UserMCPServersRepositoryOrm."""
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import user_mcp_servers_repository as mod
    from app.repositories.user_mcp_servers_repository_orm import (
        UserMCPServersRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_USER_MCP_SERVERS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_user_mcp_servers_repository()
    assert isinstance(repo, UserMCPServersRepositoryOrm)
