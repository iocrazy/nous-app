"""Integration tests for ApiKeyRepositoryOrm (Phase 2 H batch — SECRET) vs real PG.

★ THE SECRET BATCH. ★ Proves the REST → ORM swap is invisible for the
``api_keys`` table AND that the secret-handling boundary is reproduced EXACTLY:

  - HASH-ON-WRITE / LOOKUP-BY-HASH: create() stores key_hash=SHA-256(full_key);
    validate_key(full_key) finds the row by hash. Round-trip proven.
  - ONE-TIME REVEAL: create() returns the full plaintext key ONCE via
    ``secret_key``; subsequent reads do NOT add secret_key.
  - EXPOSURE PARITY (the leak-prevention test): get_user_keys / get_by_key_id
    return the raw SELECT * which — per migration 039 + ApiKeyResponse.key_value
    — DOES include key_value (full plaintext) and key_hash. We assert the ORM
    reproduces that EXACT exposure (does NOT narrow it to a mask, does NOT
    widen it), and that key_prefix is the masked "dk_xxxxxxxx..." display form.
  - STRATEGY-C value-type parity: user_id (uuid) → STR (the get_api_key authz
    != consumer + AuthContext.user_id str field), id (bigint) → native int
    (5.3 trap), status (Enum) → bare str ("active"/"revoked"), scopes (jsonb)
    → native list, timestamps → ISO str.
  - validate_key status + expiry checks (inherited, ISO-str expiry parse).
  - update_usage atomic-increment RPC; revoke; delete (COMMIT); count.
  - factory on/off.

Setup: requires INTEGRATION_DATABASE_URL + >=1 auth.users row (api_keys.user_id
is a uuid). Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_api_key_repository_orm.py -v
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_NAME_PREFIX = "__test_orm_apikey_"


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
async def a_user(integration_db_url):
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if not row:
            pytest.skip("need >=1 auth.users row")
        yield row["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_keys(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM api_keys WHERE name LIKE $1", _NAME_PREFIX + "%"
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.api_key_repository_orm import ApiKeyRepositoryOrm

    return ApiKeyRepositoryOrm()


def _name() -> str:
    return f"{_NAME_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── ★ HASH-ON-WRITE + ONE-TIME REVEAL + EXPOSURE PARITY ─────────────────


async def test_create_hashes_reveals_once_and_exposure_parity(
    integration_db_url, patched_engine, cleanup_keys, a_user
):
    user_id = str(a_user)
    created = await _repo().create(
        user_id=user_id,
        name=_name(),
        scopes=["read:media", "write:media"],
        description="test key",
    )

    # One-time reveal: the full plaintext key is returned ONCE.
    full_key = created["secret_key"]
    assert full_key.startswith("dk_") and len(full_key) == 67

    # HASH-ON-WRITE: the stored key_hash is SHA-256(full_key) — never the key.
    expected_hash = hashlib.sha256(full_key.encode()).hexdigest()
    assert created["key_hash"] == expected_hash

    # MASK: key_prefix is the truncated display form, not the full key.
    assert created["key_prefix"].startswith("dk_") and created["key_prefix"].endswith(
        "..."
    )
    assert full_key not in created["key_prefix"]

    # Strategy-C value-type parity on the returned dict.
    assert type(created["user_id"]) is str and created["user_id"] == user_id
    assert type(created["id"]) is int  # bigint → native int (5.3 trap)
    assert created["status"] == "active"  # Enum → bare str (not ApiKeyStatus.ACTIVE)
    assert type(created["status"]) is str
    assert isinstance(created["scopes"], list)  # jsonb array → native list
    assert created["scopes"] == ["read:media", "write:media"]
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    # LOOKUP-BY-HASH: validate_key(full_key) finds the row by hash.
    validated = await _repo().validate_key(full_key)
    assert validated is not None
    assert validated["key_id"] == created["key_id"]
    assert validated["status"] == "active"

    # EXPOSURE PARITY: get_by_key_id / get_user_keys return the raw SELECT *,
    # which (migration 039) INCLUDES key_value (full plaintext) + key_hash.
    # The ORM must reproduce that exact exposure — NOT narrow it to a mask.
    by_id = await _repo().get_by_key_id(created["key_id"])
    assert by_id is not None
    assert by_id["key_value"] == full_key  # full plaintext returned (over-exposure
    #                                        is pre-existing — reproduced, NOT fixed)
    assert by_id["key_hash"] == expected_hash
    assert "secret_key" not in by_id  # secret_key is create()-only (one-time)
    assert type(by_id["user_id"]) is str  # authz != consumer needs str

    listed = await _repo().get_user_keys(user_id)
    mine = [k for k in listed if k["key_id"] == created["key_id"]]
    assert len(mine) == 1
    assert mine[0]["key_value"] == full_key  # list also returns full plaintext
    assert mine[0]["status"] == "active"


# ─── ★ user_id is STR for the authz != consumer ─────────────────────────


async def test_user_id_is_str_for_authz_compare(
    integration_db_url, patched_engine, cleanup_keys, a_user
):
    """api_key_router.get_api_key does ``if key_data['user_id'] != auth.user_id``
    where auth.user_id is AuthContext.user_id: str. A native uuid.UUID there is
    ALWAYS != a str → a legit owner gets 403 on their own key. Prove the read
    returns user_id as STR so the compare matches."""
    user_id = str(a_user)
    created = await _repo().create(user_id=user_id, name=_name(), scopes=[])

    key_data = await _repo().get_by_key_id(created["key_id"])
    assert key_data is not None
    assert type(key_data["user_id"]) is str
    # The exact silent-killer compare from get_api_key (str path):
    assert not (key_data["user_id"] != user_id)  # owner matches → no 403


# ─── validate_key: status + expiry parity ───────────────────────────────


async def test_validate_key_status_and_expiry(
    integration_db_url, patched_engine, cleanup_keys, a_user
):
    user_id = str(a_user)

    # Revoked key → validate returns None (status check).
    revoked = await _repo().create(user_id=user_id, name=_name(), scopes=[])
    await _repo().revoke(revoked["key_id"], user_id)
    assert await _repo().validate_key(revoked["secret_key"]) is None

    # Expired key → validate returns None (expiry check on ISO-str expires_at).
    expired = await _repo().create(
        user_id=user_id,
        name=_name(),
        scopes=[],
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    # expires_at must round-trip as an ISO str (parity), so the inherited
    # validate_key's str-parse branch runs exactly as under REST.
    assert isinstance(expired["expires_at"], str) and "T" in expired["expires_at"]
    assert await _repo().validate_key(expired["secret_key"]) is None

    # Bad format → None without a DB hit.
    assert await _repo().validate_key("not-a-dk-key") is None


# ─── update_usage atomic RPC + count ────────────────────────────────────


async def test_update_usage_rpc_and_count(
    integration_db_url, patched_engine, cleanup_keys, a_user
):
    user_id = str(a_user)
    created = await _repo().create(user_id=user_id, name=_name(), scopes=[])

    # Atomic-increment RPC (increment_api_key_usage) — usage_count 0 → 1,
    # last_used_at stamped. Committed.
    await _repo().update_usage(created["key_id"])
    row = await _repo().get_by_key_id(created["key_id"])
    assert row["usage_count"] == 1
    assert row["last_used_at"] is not None

    # count_user_keys counts only active keys for the user.
    before = await _repo().count_user_keys(user_id)
    assert before >= 1
    await _repo().revoke(created["key_id"], user_id)
    after = await _repo().count_user_keys(user_id)
    assert after == before - 1  # revoked no longer counted


# ─── update + delete (COMMIT) ───────────────────────────────────────────


async def test_update_and_delete_commit(
    integration_db_url, patched_engine, cleanup_keys, a_user
):
    user_id = str(a_user)
    created = await _repo().create(user_id=user_id, name=_name(), scopes=["read"])

    new_name = _name()
    updated = await _repo().update(
        created["key_id"], user_id, {"name": new_name, "scopes": ["read", "write"]}
    )
    assert updated is not None
    assert updated["name"] == new_name
    assert updated["scopes"] == ["read", "write"]

    # Wrong user_id → no match → None (ownership gate).
    other = await _repo().update(created["key_id"], str(uuid.uuid4()), {"name": "x"})
    assert other is None

    # delete (hard) committed.
    ok = await _repo().delete(created["key_id"], user_id)
    assert ok is True
    assert await _repo().get_by_key_id(created["key_id"]) is None


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.api_key_repository import (
        ApiKeyRepository,
        get_api_key_repository,
    )

    with patch("app.core.config.settings.USE_ORM_API_KEY", False):
        assert type(get_api_key_repository()) is ApiKeyRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.api_key_repository_orm import ApiKeyRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_API_KEY", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.api_key_repository import get_api_key_repository

        assert type(get_api_key_repository()) is ApiKeyRepositoryOrm
