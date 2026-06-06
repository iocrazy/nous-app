"""Integration tests for CookiesRepositoryOrm (Phase 2 H batch — SECRET) vs real PG.

★ THE SECRET BATCH. ★ Proves the REST → ORM swap is invisible for the
``user_cookies`` table AND that the secret-handling boundary is reproduced
EXACTLY:

  - ROUND-TRIP: a cookie written via upsert reads back as the SAME plaintext
    (no encryption on write, no decryption on read — the legacy stores raw, the
    ORM stores raw; double-encrypt or accidental encrypt would corrupt this).
  - NO MASKING: get_all_by_user / get_by_user_and_platform return the FULL
    plaintext cookie (exposure parity — the repo never masks; the consumers
    need the raw value). We assert the returned secret == what we wrote.
  - STRATEGY-C value-type parity: user_id (uuid) → STR, id (bigint) → native
    int (5.3 trap), created_at/updated_at (timestamptz) → ISO str, is_valid
    (bool) → native bool.
  - upsert ON CONFLICT (user_id, platform) merge; mark_invalid; delete (COMMIT).
  - factory on/off.

Setup: requires INTEGRATION_DATABASE_URL + >=1 auth.users row (user_cookies has
no FK to auth.users in the model, but we use a real uuid to be safe). Skips
cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_cookies_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_orm_cookie_"


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
    """Yield one REAL auth.users id (defensive — keeps user_id a valid uuid even
    if a FK is added later). Skips if the DB has none."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if not row:
            pytest.skip("need >=1 auth.users row")
        yield row["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_cookies(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM user_cookies WHERE platform LIKE $1", _PLATFORM_PREFIX + "%"
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.cookies_repository_orm import CookiesRepositoryOrm

    return CookiesRepositoryOrm()


def _platform() -> str:
    return f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── ★ SECRET ROUND-TRIP: written plaintext reads back identical ─────────


async def test_secret_roundtrip_plaintext_no_encryption(
    integration_db_url, patched_engine, cleanup_cookies, a_user
):
    """The cookie (the secret) is written raw and read back RAW — no
    encryption/decryption, no masking. Prove the exact plaintext survives the
    write→read round-trip (a double-encrypt or accidental encrypt would corrupt
    it; a mask would truncate it)."""
    user_id = str(a_user)
    platform = _platform()
    secret_cookie = "sessionid=SECRET_ABC123; ttwid=XYZ-Δ-987"  # noqa: E501
    secret_headers = "Referer: https://www.douyin.com/"

    saved = await _repo().upsert(
        user_id,
        platform,
        {"cookie_text": secret_cookie, "custom_headers": secret_headers},
    )
    assert saved is not None
    # Exposure parity: the FULL plaintext secret is returned (no masking).
    assert saved["cookie_text"] == secret_cookie
    assert saved["custom_headers"] == secret_headers
    assert saved["is_valid"] is True  # upsert forces is_valid=True
    assert saved["error_message"] is None
    # Strategy-C value-type parity on the returned dict.
    assert type(saved["user_id"]) is str and saved["user_id"] == user_id
    assert type(saved["id"]) is int  # bigint id → native int (5.3 trap)
    assert type(saved["created_at"]) is str and "T" in saved["created_at"]
    assert type(saved["updated_at"]) is str and "T" in saved["updated_at"]

    # Read back via BOTH read methods — same plaintext, no decryption needed.
    fetched = await _repo().get_by_user_and_platform(user_id, platform)
    assert fetched is not None
    assert fetched["cookie_text"] == secret_cookie  # round-trip exact
    assert type(fetched["user_id"]) is str

    all_rows = await _repo().get_all_by_user(user_id)
    mine = [r for r in all_rows if r["platform"] == platform]
    assert len(mine) == 1
    assert mine[0]["cookie_text"] == secret_cookie  # list also returns full plaintext

    # The DB column itself holds the RAW plaintext (no encryption at rest) —
    # confirm the ORM did not transform the secret on write.
    conn = await asyncpg.connect(integration_db_url)
    try:
        raw = await conn.fetchval(
            "SELECT cookie_text FROM user_cookies WHERE user_id = $1 AND platform = $2",
            a_user,
            platform,
        )
    finally:
        await conn.close()
    assert raw == secret_cookie  # stored raw, exactly as the legacy did


# ─── upsert ON CONFLICT merge + updated_at refresh ──────────────────────


async def test_upsert_on_conflict_updates_and_refreshes(
    integration_db_url, patched_engine, cleanup_cookies, a_user
):
    user_id = str(a_user)
    platform = _platform()

    first = await _repo().upsert(user_id, platform, {"cookie_text": "v1"})
    assert first is not None and first["cookie_text"] == "v1"
    first_ts = first["updated_at"]

    # Second upsert on the SAME (user_id, platform) → UPDATE, not a 2nd row.
    second = await _repo().upsert(user_id, platform, {"cookie_text": "v2-changed"})
    assert second is not None
    assert second["cookie_text"] == "v2-changed"
    assert second["id"] == first["id"]  # same row (conflict → update)
    assert second["updated_at"] >= first_ts  # updated_at refreshed on save

    conn = await asyncpg.connect(integration_db_url)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM user_cookies WHERE user_id = $1 AND platform = $2",
            a_user,
            platform,
        )
    finally:
        await conn.close()
    assert count == 1  # committed, single row


# ─── mark_invalid + delete (COMMIT) ─────────────────────────────────────


async def test_mark_invalid_and_delete_commit(
    integration_db_url, patched_engine, cleanup_cookies, a_user
):
    user_id = str(a_user)
    platform = _platform()
    await _repo().upsert(user_id, platform, {"cookie_text": "v1"})

    await _repo().mark_invalid(user_id, platform, "expired cookie")
    row = await _repo().get_by_user_and_platform(user_id, platform)
    assert row is not None
    assert row["is_valid"] is False  # mark_invalid committed
    assert row["error_message"] == "expired cookie"

    ok = await _repo().delete(user_id, platform)
    assert ok is True
    gone = await _repo().get_by_user_and_platform(user_id, platform)
    assert gone is None  # delete committed (no silent rollback)


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.cookies_repository import (
        CookiesRepository,
        get_cookies_repository,
    )

    with patch("app.core.config.settings.USE_ORM_COOKIES", False):
        assert type(get_cookies_repository()) is CookiesRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.cookies_repository_orm import CookiesRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_COOKIES", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.cookies_repository import get_cookies_repository

        assert type(get_cookies_repository()) is CookiesRepositoryOrm
