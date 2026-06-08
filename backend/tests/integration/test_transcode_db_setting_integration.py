"""Integration test for the §2.4b async-hoisted transcode system_settings read.

Proves ``TranscodeService._get_db_setting`` reads a row from the real
``system_settings`` table async-native via the SQLAlchemy engine — awaited on
the caller's loop instead of bridged through a fresh-loop ``run_async`` shim.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_transcode_db_setting_integration.py -v
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import patch

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
async def seed_setting(integration_db_url):
    """A throwaway system_settings row with a unique key. Cleans on teardown.
    Yields (key:str, value_str:str)."""
    key = f"__test_transcode_{uuid.uuid4().hex[:8]}"
    value_str = "__tier_a,__tier_b"
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO system_settings (key, value, updated_at) "
            "VALUES ($1, $2::jsonb, now())",
            key,
            json.dumps(value_str),
        )
        yield key, value_str
    finally:
        await conn.execute("DELETE FROM system_settings WHERE key = $1", key)
        await conn.close()


async def test_get_db_setting_reads_async_native(
    integration_db_url, patched_engine, seed_setting
):
    from app.services.media.transcode.transcode_service import TranscodeService

    key, value_str = seed_setting
    # Awaited directly on this test's loop — the async-native engine read.
    result = await TranscodeService._get_db_setting(key)
    assert result is not None
    # Robust to jsonb decode shape (str vs already-decoded): the seeded marker
    # round-trips either way.
    assert "__tier_a" in str(result)


async def test_get_db_setting_missing_key_returns_none(
    integration_db_url, patched_engine
):
    from app.services.media.transcode.transcode_service import TranscodeService

    result = await TranscodeService._get_db_setting(f"__nope_{uuid.uuid4().hex}")
    assert result is None
